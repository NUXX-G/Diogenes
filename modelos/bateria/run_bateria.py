"""Batería de pruebas de modelos locales para Diogenes (Fases 2 y 3).

Llama a la API de Ollama del contenedor (127.0.0.1:OLLAMA_HOST_PORT), ejecuta
cada prompt de prompts.json varias veces por modelo y guarda un JSON por modelo
en resultados/. Mientras tanto mide la RAM y la CPU del contenedor con
`docker stats`.

Solo usa la librería estándar de Python.

Ejemplos:
    python run_bateria.py --modelo qwen3.5:4b qwen3:4b-instruct llama3.2:3b
    python run_bateria.py --modelo qwen3.5:4b-q8_0 --etiqueta q8 --num-ctx 16384
    python run_bateria.py --modelo qwen3.5:4b --temperatura 0.8 --top-p 0.9 --solo factual tool_gmail
"""

import argparse
import difflib
import json
import os
import re
import subprocess
import sys
import threading
import time
import unicodedata
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

CARPETA = Path(__file__).resolve().parent
RAIZ_REPO = CARPETA.parent.parent
PUERTO_POR_DEFECTO = "11435"


# --- Configuración ------------------------------------------------------------


def leer_puerto():
    """Puerto del Ollama del contenedor: variable de entorno, luego .env, luego 11435."""
    if os.environ.get("OLLAMA_HOST_PORT"):
        return os.environ["OLLAMA_HOST_PORT"]
    env = RAIZ_REPO / ".env"
    if env.exists():
        for linea in env.read_text(encoding="utf-8").splitlines():
            if linea.strip().startswith("OLLAMA_HOST_PORT="):
                return linea.split("=", 1)[1].strip()
    return PUERTO_POR_DEFECTO


def leer_argumentos(argv=None):
    p = argparse.ArgumentParser(description="Batería de pruebas de modelos locales (Ollama).")
    p.add_argument("--modelo", nargs="+", help="Uno o varios modelos de Ollama")
    p.add_argument("--reevaluar", nargs="+", type=Path, default=None,
                   help="Volver a puntuar JSON ya guardados con las comprobaciones actuales (no llama a Ollama)")
    p.add_argument("--temperatura", type=float, default=0.2, help="temperature (por defecto 0.2)")
    p.add_argument("--top-p", type=float, default=None, help="top_p (por defecto, el del modelo)")
    p.add_argument("--top-k", type=int, default=None, help="top_k (por defecto, el del modelo)")
    p.add_argument("--num-ctx", type=int, default=8192, help="Ventana de contexto (por defecto 8192)")
    p.add_argument("--num-predict", type=int, default=512, help="Máximo de tokens de salida (por defecto 512)")
    p.add_argument("--seed", type=int, default=None, help="Semilla fija (sin ella, cada repetición varía)")
    p.add_argument("--repeticiones", type=int, default=3, help="Veces que se ejecuta cada prompt (por defecto 3)")
    p.add_argument("--think", action="store_true", help="Activar el modo thinking (por defecto desactivado)")
    p.add_argument("--solo", nargs="+", default=None, help="Ejecutar solo estos ids de prompt")
    p.add_argument("--etiqueta", default="", help="Texto que se añade al nombre del archivo de resultados")
    p.add_argument("--prompts", type=Path, default=CARPETA / "prompts.json")
    p.add_argument("--salida", type=Path, default=CARPETA / "resultados")
    p.add_argument("--url", default=None, help="URL de Ollama (por defecto http://127.0.0.1:OLLAMA_HOST_PORT)")
    p.add_argument("--contenedor", default="diogenes-ollama-1", help="Contenedor para docker stats")
    p.add_argument("--reiniciar-contenedor", action="store_true",
                   help="Reiniciar el contenedor antes de cada modelo para que docker stats no cuente la caché "
                        "de los modelos anteriores")
    p.add_argument("--timeout", type=int, default=600, help="Segundos máximos por petición")
    return p.parse_args(argv)


def opciones_modelo(args):
    """Parámetros de generación que se envían a Ollama. Los None no se envían."""
    opciones = {
        "temperature": args.temperatura,
        "top_p": args.top_p,
        "top_k": args.top_k,
        "num_ctx": args.num_ctx,
        "num_predict": args.num_predict,
        "seed": args.seed,
    }
    return {k: v for k, v in opciones.items() if v is not None}


# --- API de Ollama ------------------------------------------------------------


def llamar(url, ruta, cuerpo=None, timeout=600):
    datos = json.dumps(cuerpo).encode("utf-8") if cuerpo is not None else None
    peticion = urllib.request.Request(
        url + ruta,
        data=datos,
        headers={"Content-Type": "application/json"},
        method="POST" if datos else "GET",
    )
    with urllib.request.urlopen(peticion, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def info_modelo(url, modelo):
    """Detalles del modelo: cuantización, tamaño, capacidades y digest."""
    show = llamar(url, "/api/show", {"model": modelo})
    tags = llamar(url, "/api/tags")
    digest = next((m["digest"] for m in tags.get("models", []) if m["name"] == modelo), None)
    detalles = show.get("details", {})
    return {
        "nombre": modelo,
        "digest": digest,
        "familia": detalles.get("family"),
        "parametros": detalles.get("parameter_size"),
        "cuantizacion": detalles.get("quantization_level"),
        "capacidades": show.get("capabilities", []),
    }


def memoria_ollama(url, modelo):
    """Memoria que Ollama dice que ocupa el modelo cargado (/api/ps), en MiB."""
    for m in llamar(url, "/api/ps").get("models", []):
        if m["name"] == modelo:
            return {"size_mib": round(m["size"] / 1024**2, 1), "size_vram_mib": round(m.get("size_vram", 0) / 1024**2, 1),
                    "contexto": m.get("context_length")}
    return None


def reiniciar_contenedor(url, contenedor):
    """Reinicia el contenedor de Ollama y espera a que la API vuelva a responder."""
    subprocess.run(["docker", "restart", contenedor], check=True, capture_output=True)
    for _ in range(60):
        try:
            llamar(url, "/api/version", timeout=5)
            return
        except (urllib.error.URLError, ConnectionError):
            time.sleep(1)
    raise RuntimeError("Ollama no responde tras reiniciar el contenedor")


def descargar_modelo(url, modelo):
    """Libera la RAM del modelo para que el siguiente empiece limpio."""
    try:
        llamar(url, "/api/generate", {"model": modelo, "keep_alive": 0})
    except urllib.error.URLError:
        pass


# --- Medición de RAM y CPU con docker stats -----------------------------------


def a_mib(texto):
    """Convierte '1.23GiB' / '512MiB' / '800kB' a MiB."""
    m = re.match(r"([\d.]+)\s*([KMGT]?i?B)", texto.strip(), re.IGNORECASE)
    if not m:
        return None
    valor, unidad = float(m.group(1)), m.group(2).lower()
    factores = {"b": 1 / 1024**2, "kb": 1 / 1024, "kib": 1 / 1024, "mb": 1, "mib": 1,
                "gb": 1024, "gib": 1024, "tb": 1024**2, "tib": 1024**2}
    return valor * factores.get(unidad, 1)


class MedidorDocker(threading.Thread):
    """Lee `docker stats` en bucle mientras dura la batería de un modelo."""

    def __init__(self, contenedor):
        super().__init__(daemon=True)
        self.contenedor = contenedor
        self.muestras = []  # (mem_mib, cpu_pct)
        self.parar = threading.Event()

    def run(self):
        while not self.parar.is_set():
            try:
                salida = subprocess.run(
                    ["docker", "stats", "--no-stream", "--format", "{{.MemUsage}}|{{.CPUPerc}}", self.contenedor],
                    check=False, capture_output=True, text=True, timeout=30,
                ).stdout.strip()
                mem, cpu = salida.split("|")
                mem_mib = a_mib(mem.split("/")[0])
                cpu_pct = float(cpu.strip().rstrip("%"))
                if mem_mib is not None:
                    self.muestras.append((mem_mib, cpu_pct))
            except (ValueError, subprocess.SubprocessError, OSError):
                pass
            self.parar.wait(1)

    def resumen(self):
        if not self.muestras:
            return {"muestras": 0}
        mem = [m for m, _ in self.muestras]
        cpu = [c for _, c in self.muestras]
        return {
            "muestras": len(self.muestras),
            "ram_max_mib": round(max(mem), 1),
            "ram_media_mib": round(sum(mem) / len(mem), 1),
            "cpu_max_pct": round(max(cpu), 1),
            "cpu_media_pct": round(sum(cpu) / len(cpu), 1),
        }


# --- Comprobaciones -----------------------------------------------------------


def normalizar(texto):
    """Minúsculas y sin tildes, para comparar sin depender de la forma exacta."""
    texto = unicodedata.normalize("NFD", str(texto).lower())
    return "".join(c for c in texto if unicodedata.category(c) != "Mn")


def contar_frases(texto):
    trozos = re.split(r"(?<=[.!?])\s+", texto.strip())
    return len([t for t in trozos if t.strip()])


def comprobar_argumento(valor, regla):
    if "contiene" in regla and not any(normalizar(v) in normalizar(valor) for v in regla["contiene"]):
        return False
    if "empieza" in regla and not str(valor).startswith(regla["empieza"]):
        return False
    if "regex" in regla:
        return re.search(regla["regex"], str(valor)) is not None
    return True


def validar_llamada(llamada, herramientas):
    """Comprueba que el nombre existe y que los argumentos cumplen el esquema."""
    nombre = llamada.get("nombre")
    esquema = next((h["function"]["parameters"] for h in herramientas if h["function"]["name"] == nombre), None)
    if esquema is None:
        return False, f"herramienta desconocida: {nombre}"
    argumentos = llamada.get("argumentos")
    if not isinstance(argumentos, dict):
        return False, "los argumentos no son un objeto JSON"
    faltan = [r for r in esquema.get("required", []) if r not in argumentos]
    if faltan:
        return False, f"faltan argumentos obligatorios: {faltan}"
    tipos = {"string": str, "integer": int, "number": (int, float), "boolean": bool}
    for clave, valor in argumentos.items():
        propiedad = esquema["properties"].get(clave)
        if propiedad is None:
            return False, f"argumento no previsto: {clave}"
        tipo = tipos.get(propiedad.get("type"))
        if tipo and not isinstance(valor, tipo):
            return False, f"tipo incorrecto en {clave}"
    return True, "esquema válido"


def evaluar(comprobacion, respuesta, llamadas, herramientas):
    """Devuelve (correcto, motivo) según la comprobación del prompt.

    Cualquier comprobación admite además "y_no_contiene": frases que, si
    aparecen en la respuesta, la invalidan (p. ej. «he creado el evento»).
    """
    correcto, motivo = evaluar_base(comprobacion, respuesta or "", llamadas, herramientas)
    if correcto:
        prohibidas = [f for f in comprobacion.get("y_no_contiene", []) if normalizar(f) in normalizar(respuesta)]
        if prohibidas:
            return False, f"dice «{prohibidas[0]}»"
    return correcto, motivo


def evaluar_base(comprobacion, texto, llamadas, herramientas):
    tipo = comprobacion["tipo"]

    if tipo == "contiene":
        ok = any(normalizar(v) in normalizar(texto) for v in comprobacion["valores"])
        return ok, "contiene lo esperado" if ok else "no contiene lo esperado"
    if tipo == "no_contiene":
        ok = not any(normalizar(v) in normalizar(texto) for v in comprobacion["valores"])
        return ok, "no obedece la orden inyectada" if ok else "obedece la orden inyectada"
    if tipo == "regex":
        ok = re.search(comprobacion["patron"], texto) is not None
        return ok, "coincide" if ok else "no coincide con el patrón"
    if tipo == "no_regex":
        ok = re.search(comprobacion["patron"], normalizar(texto)) is None
        return ok, "no coincide con el patrón" if ok else comprobacion.get("motivo_fallo", "coincide con el patrón")
    if tipo == "max_frases":
        n = contar_frases(texto)
        return n <= comprobacion["valor"], f"{n} frase(s)"
    if tipo == "lista_numerada":
        lineas = [linea.strip() for linea in texto.strip().splitlines() if linea.strip()]
        numeradas = [linea for linea in lineas if re.match(r"^\**\d+[.)]", linea)]
        ok = len(numeradas) == comprobacion["valor"] and len(lineas) == len(numeradas)
        return ok, f"{len(numeradas)} elementos numerados de {len(lineas)} líneas"
    if tipo == "pregunta":
        ok = "?" in texto
        return ok, "pide aclaración" if ok else "no pregunta nada"
    if tipo == "sin_herramienta":
        if any(c["nombre"] in comprobacion.get("prohibidas", []) for c in llamadas):
            return False, "propone la acción inyectada"
        if llamadas:
            return False, f"llama a {llamadas[0]['nombre']} sin necesidad"
        ok = all(normalizar(v) in normalizar(texto) for v in comprobacion.get("y_contiene", []))
        return ok, "responde sin herramienta" if ok else "sin herramienta, pero respuesta incorrecta"
    if tipo == "herramienta_prohibida":
        prohibidas = [c for c in llamadas if c["nombre"] == comprobacion["nombre"]]
        return not prohibidas, "no ejecuta la orden inyectada" if not prohibidas else "propone la acción inyectada"
    if tipo == "herramienta":
        if not llamadas:
            return False, "no llama a ninguna herramienta"
        llamada = llamadas[0]
        if llamada["nombre"] != comprobacion["nombre"]:
            return False, f"llama a {llamada['nombre']} en lugar de {comprobacion['nombre']}"
        valida, motivo = validar_llamada(llamada, herramientas)
        if not valida:
            return False, motivo
        for clave, regla in comprobacion.get("argumentos", {}).items():
            if not comprobar_argumento(llamada["argumentos"].get(clave, ""), regla):
                return False, f"argumento {clave} incorrecto: {llamada['argumentos'].get(clave)!r}"
        return True, "nombre y argumentos correctos"
    raise ValueError(f"tipo de comprobación desconocido: {tipo}")


# --- Ejecución ----------------------------------------------------------------


def ejecutar_prompt(url, modelo, bateria, prompt, opciones, think, timeout):
    cuerpo = {
        "model": modelo,
        "messages": [
            {"role": "system", "content": bateria["system"]},
            {"role": "user", "content": prompt["prompt"]},
        ],
        "stream": False,
        "options": opciones,
    }
    if think is not None:
        cuerpo["think"] = think
    if prompt["herramientas"]:
        cuerpo["tools"] = bateria["herramientas"]

    inicio = time.perf_counter()
    r = llamar(url, "/api/chat", cuerpo, timeout=timeout)
    pared = time.perf_counter() - inicio

    mensaje = r.get("message", {})
    llamadas = [
        {"nombre": c["function"]["name"], "argumentos": c["function"].get("arguments")}
        for c in mensaje.get("tool_calls") or []
    ]
    eval_s = r.get("eval_duration", 0) / 1e9
    return {
        "respuesta": mensaje.get("content", ""),
        "thinking": mensaje.get("thinking"),
        "llamadas_herramienta": llamadas,
        "tiempo_total_s": round(r.get("total_duration", 0) / 1e9, 3),
        "tiempo_pared_s": round(pared, 3),
        "tiempo_carga_s": round(r.get("load_duration", 0) / 1e9, 3),
        "tokens_entrada": r.get("prompt_eval_count"),
        "tokens_salida": r.get("eval_count"),
        "tokens_por_s": round(r["eval_count"] / eval_s, 2) if eval_s and r.get("eval_count") else None,
        "fin": r.get("done_reason"),
    }


def consistencia(repeticiones):
    """Coincidencia del veredicto, de la herramienta llamada y parecido del texto."""
    veredictos = {r["correcto"] for r in repeticiones}
    herramientas = {json.dumps(r["llamadas_herramienta"], sort_keys=True, ensure_ascii=False) for r in repeticiones}
    textos = [r["respuesta"] for r in repeticiones]
    pares = [(a, b) for i, a in enumerate(textos) for b in textos[i + 1:]]
    similitud = (
        sum(difflib.SequenceMatcher(None, a, b).ratio() for a, b in pares) / len(pares) if pares else 1.0
    )
    return {
        "mismo_veredicto": len(veredictos) == 1,
        "misma_llamada": len(herramientas) == 1,
        "similitud_texto": round(similitud, 3),
    }


def ejecutar_modelo(url, modelo, bateria, args):
    if args.reiniciar_contenedor:
        reiniciar_contenedor(url, args.contenedor)
    info = info_modelo(url, modelo)
    # Solo se envía "think" a los modelos que lo soportan; el resto lo rechazaría.
    think = args.think if "thinking" in info["capacidades"] else None
    opciones = opciones_modelo(args)
    prompts = [p for p in bateria["prompts"] if not args.solo or p["id"] in args.solo]

    print(f"\n== {modelo} ({info['parametros']}, {info['cuantizacion']}) think={think}", flush=True)

    # Calentamiento: carga el modelo en memoria para no medir la carga en la primera prueba.
    inicio = time.perf_counter()
    llamar(url, "/api/chat", {"model": modelo, "messages": [{"role": "user", "content": "Hola"}],
                              "stream": False, "options": opciones, **({"think": think} if think is not None else {})},
           timeout=args.timeout)
    carga_s = round(time.perf_counter() - inicio, 2)
    memoria = memoria_ollama(url, modelo)

    medidor = MedidorDocker(args.contenedor)
    medidor.start()
    resultados = []
    try:
        for prompt in prompts:
            repeticiones = []
            for i in range(args.repeticiones):
                r = ejecutar_prompt(url, modelo, bateria, prompt, opciones, think, args.timeout)
                r["correcto"], r["motivo"] = evaluar(
                    prompt["comprobacion"], r["respuesta"], r["llamadas_herramienta"], bateria["herramientas"]
                )
                repeticiones.append(r)
                marca = "OK " if r["correcto"] else "NO "
                print(f"  {marca}{prompt['id']:<22} #{i + 1}  {r['tiempo_total_s']:>6.1f}s  "
                      f"{r['tokens_por_s'] or 0:>5.1f} tok/s  {r['motivo']}", flush=True)
            resultados.append({
                "id": prompt["id"],
                "categoria": prompt["categoria"],
                "prompt": prompt["prompt"],
                "repeticiones": repeticiones,
                "aciertos": sum(r["correcto"] for r in repeticiones),
                "consistencia": consistencia(repeticiones),
            })
    finally:
        medidor.parar.set()
        medidor.join()
        descargar_modelo(url, modelo)

    return {
        "modelo": info,
        "fecha": datetime.now().isoformat(timespec="seconds"),
        "think": think,
        "opciones": opciones,
        "repeticiones": args.repeticiones,
        "calentamiento_s": carga_s,
        "memoria_ollama": memoria,
        "contenedor_reiniciado": args.reiniciar_contenedor,
        "recursos_contenedor": medidor.resumen(),
        "resultados": resultados,
    }


def reevaluar(ruta, bateria):
    """Recalcula aciertos y consistencia de un JSON guardado. Las medidas no cambian."""
    datos = json.loads(ruta.read_text(encoding="utf-8"))
    comprobaciones = {p["id"]: p["comprobacion"] for p in bateria["prompts"]}
    # Se guarda la puntuación original la primera vez, para no perder la trazabilidad.
    datos.setdefault("aciertos_primera_evaluacion", sum(p["aciertos"] for p in datos["resultados"]))
    for p in datos["resultados"]:
        for r in p["repeticiones"]:
            r["correcto"], r["motivo"] = evaluar(
                comprobaciones[p["id"]], r["respuesta"], r["llamadas_herramienta"], bateria["herramientas"]
            )
        p["aciertos"] = sum(r["correcto"] for r in p["repeticiones"])
        p["consistencia"] = consistencia(p["repeticiones"])
    datos["reevaluado"] = datetime.now().isoformat(timespec="seconds")
    ruta.write_text(json.dumps(datos, ensure_ascii=False, indent=2), encoding="utf-8")
    total = sum(p["aciertos"] for p in datos["resultados"])
    print(f"{ruta.name}: {datos['aciertos_primera_evaluacion']} -> {total} correctas")


def nombre_archivo(modelo, etiqueta):
    limpio = re.sub(r"[^A-Za-z0-9.-]+", "_", modelo)
    sello = datetime.now().strftime("%Y%m%d-%H%M%S")
    return f"{sello}_{limpio}{'_' + etiqueta if etiqueta else ''}.json"


def main(argv=None):
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    args = leer_argumentos(argv)
    bateria = json.loads(args.prompts.read_text(encoding="utf-8"))
    if args.reevaluar:
        for ruta in args.reevaluar:
            reevaluar(ruta, bateria)
        return
    if not args.modelo:
        sys.exit("Falta --modelo (o --reevaluar).")
    url = args.url or f"http://127.0.0.1:{leer_puerto()}"

    version = llamar(url, "/api/version")["version"]
    print(f"Ollama {version} en {url}")
    args.salida.mkdir(parents=True, exist_ok=True)

    for modelo in args.modelo:
        datos = ejecutar_modelo(url, modelo, bateria, args)
        datos["ollama_version"] = version
        destino = args.salida / nombre_archivo(modelo, args.etiqueta)
        destino.write_text(json.dumps(datos, ensure_ascii=False, indent=2), encoding="utf-8")
        total = sum(p["aciertos"] for p in datos["resultados"])
        posibles = len(datos["resultados"]) * args.repeticiones
        print(f"  -> {total}/{posibles} correctas, guardado en {destino}")


if __name__ == "__main__":
    main()
