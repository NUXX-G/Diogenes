"""Genera tablas Markdown a partir de los JSON de resultados/ de run_bateria.py.

Ejemplos:
    python resumen.py resultados/20261026-*.json
    python resumen.py resultados/*_q8.json > tablas-q8.md
"""

import json
import statistics
import sys
from pathlib import Path


def cargar(rutas):
    return [json.loads(Path(r).read_text(encoding="utf-8")) for r in rutas]


def todas_las_repeticiones(datos):
    return [rep for p in datos["resultados"] for rep in p["repeticiones"]]


def tabla(cabecera, filas):
    lineas = ["| " + " | ".join(cabecera) + " |", "|" + "---|" * len(cabecera)]
    lineas += ["| " + " | ".join(str(c) for c in fila) + " |" for fila in filas]
    return "\n".join(lineas)


def media(valores):
    valores = [v for v in valores if v is not None]
    return round(statistics.mean(valores), 2) if valores else "—"


def main(rutas):
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    modelos = cargar(rutas)
    nombres = [m["modelo"]["nombre"] for m in modelos]

    print("## Configuración\n")
    print(tabla(
        ["Modelo", "Parámetros", "Cuantización", "Digest", "Ollama", "think", "Opciones", "Fecha"],
        [[m["modelo"]["nombre"], m["modelo"]["parametros"], m["modelo"]["cuantizacion"],
          f"`{(m['modelo']['digest'] or '')[:12]}`", m["ollama_version"], m["think"],
          f"`{json.dumps(m['opciones'])}`", m["fecha"]] for m in modelos],
    ))

    print("\n## Aciertos por prompt (de N repeticiones)\n")
    ids = [p["id"] for p in modelos[0]["resultados"]]
    filas = []
    for i, id_ in enumerate(ids):
        fila = [f"`{id_}`"]
        for m in modelos:
            p = m["resultados"][i]
            fila.append(f"{p['aciertos']}/{len(p['repeticiones'])}")
        filas.append(fila)
    filas.append(["**Total**"] + [
        f"**{sum(p['aciertos'] for p in m['resultados'])}/{len(todas_las_repeticiones(m))}**" for m in modelos
    ])
    print(tabla(["Prompt"] + nombres, filas))

    print("\n## Consistencia entre repeticiones\n")
    print("Formato: mismo veredicto / misma llamada a herramienta / similitud media del texto (0–1).\n")
    filas = []
    for i, id_ in enumerate(ids):
        fila = [f"`{id_}`"]
        for m in modelos:
            c = m["resultados"][i]["consistencia"]
            fila.append(f"{'sí' if c['mismo_veredicto'] else 'no'} / {'sí' if c['misma_llamada'] else 'no'} / "
                        f"{c['similitud_texto']:.2f}")
        filas.append(fila)
    filas.append(["**Similitud media**"] + [
        f"**{media([p['consistencia']['similitud_texto'] for p in m['resultados']])}**" for m in modelos
    ])
    filas.append(["**Prompts con veredicto estable**"] + [
        f"**{sum(p['consistencia']['mismo_veredicto'] for p in m['resultados'])}/{len(m['resultados'])}**"
        for m in modelos
    ])
    print(tabla(["Prompt"] + nombres, filas))

    print("\n## Velocidad\n")
    filas = []
    for m in modelos:
        reps = todas_las_repeticiones(m)
        tps = [r["tokens_por_s"] for r in reps if r["tokens_por_s"]]
        tiempos = [r["tiempo_total_s"] for r in reps]
        filas.append([
            m["modelo"]["nombre"], media(tps), round(min(tps), 2), round(max(tps), 2),
            media(tiempos), round(max(tiempos), 2),
            media([r["tokens_entrada"] for r in reps]), media([r["tokens_salida"] for r in reps]),
            m["calentamiento_s"],
        ])
    print(tabla(["Modelo", "tok/s medio", "tok/s mín", "tok/s máx", "Tiempo medio (s)", "Tiempo máx (s)",
                 "Tokens entrada (media)", "Tokens salida (media)", "Carga inicial (s)"], filas))

    print("\n## RAM y CPU del contenedor (docker stats)\n")
    print(tabla(
        ["Modelo", "Contenedor reiniciado", "Memoria según Ollama (MiB)", "RAM máx (MiB)", "RAM media (MiB)",
         "CPU media (%)", "CPU máx (%)", "Muestras"],
        [[m["modelo"]["nombre"], "sí" if m.get("contenedor_reiniciado") else "no",
          (m.get("memoria_ollama") or {}).get("size_mib", "—"), r.get("ram_max_mib", "—"),
          r.get("ram_media_mib", "—"), r.get("cpu_media_pct", "—"), r.get("cpu_max_pct", "—"), r["muestras"]]
         for m in modelos for r in [m["recursos_contenedor"]]],
    ))

    print("\n## Tool calling\n")
    filas = []
    for i, id_ in enumerate(ids):
        if not id_.startswith("tool_"):
            continue
        fila = [f"`{id_}`"]
        for m in modelos:
            p = m["resultados"][i]
            motivos = sorted({r["motivo"] for r in p["repeticiones"]})
            fila.append(f"{p['aciertos']}/{len(p['repeticiones'])} ({'; '.join(motivos)})")
        filas.append(fila)
    print(tabla(["Prompt"] + nombres, filas))


if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit("Uso: python resumen.py resultados/<archivo>.json [...]")
    main(sys.argv[1:])
