# Diogenes

<p align="center">
  <img src="img/diogenes.png" alt="Diógenes en su tinaja frente a Alejandro Magno" width="700">
</p>

> *«Apártate, que me tapas el sol.»* — Diógenes a Alejandro Magno

**Diogenes** es un agente personal con IA local. Igual que el filósofo que vivía en una tinaja y no necesitaba nada del rey más poderoso del mundo, Diogenes funciona con lo mínimo: **0 €, sin GPU obligatoria y sin depender de APIs de pago**. Y tampoco obedece a cualquiera: aunque un correo le ordene algo, no ejecuta ninguna acción sin tu confirmación.

LLM ejecutado **en local**, accesible por **Telegram**, que consulta **Gmail y Google Calendar de prueba**, controla un **simulador doméstico** y gestiona **tareas**. Todo se levanta con **Docker Compose**.

- **Equipo:** Nelson Karlsson · Yeray Trejo
- **Presupuesto:** 0 €
- **Entrega:** 11 de diciembre
- **Estado:** Fase 0 — definición

> Este repo **nunca** contiene modelos, secretos, `.env` reales, tokens ni datos personales. Ver [Seguridad](#seguridad-reglas-del-repo).

---

## Por qué Diogenes

| Diógenes | Diogenes (el agente) |
|---|---|
| Vivía en una tinaja con lo imprescindible | Corre en un portátil normal, 0 €, modelo de 4B |
| No se dejaba mandar ni por Alejandro | Ignora órdenes inyectadas en correos: la confirmación la impone el código, no el prompt |
| Buscaba con su lámpara a un hombre honesto | Busca en tu correo y tu calendario, y te dice lo que encuentra sin inventar |
| Rechazaba lo superfluo | Permisos mínimos: solo lectura salvo confirmación explícita |

---

## Arquitectura

```
Usuario (Telegram)
        │  long polling
        ▼
┌───────────────────── docker compose (portátil local) ─────────────────────┐
│                                                                            │
│   [n8n]  AI Agent + Telegram + aprobación humana                           │
│      │                         │                                           │
│      ▼                         ▼  MCP                                      │
│   [ollama]                  [tools-mcp]  servidor MCP propio (Python)      │
│   qwen3:4b-instruct (volumen) ├─ gmail_buscar()       (solo lectura)       │
│                               ├─ calendar_listar()    (solo lectura)       │
│                               ├─ calendar_crear()     (requiere confirmar) │
│                               ├─ tareas_*()           → SQLite (volumen)   │
│                               └─ casa_*()             → [casa-sim]         │
│                                                                            │
│   [casa-sim]  simulador doméstico + vista web (127.0.0.1)                  │
└──────────────────────────────────│─────────────────────────────────────────┘
                                   ▼
                    Gmail / Calendar (cuenta de prueba)

Raspberry Pi 400 / móvil antiguo ──túnel SSH──► vista web de casa-sim
```

El modelo **propone** acciones. La plataforma **decide** si se ejecutan.

---

## Decisiones

| Decisión | Elección | Motivo |
|---|---|---|
| Entorno | **Híbrido**: desarrollo en portátiles propios, validación en el aula | Modelo más capaz sin perder la compatibilidad con el aula |
| Plataforma | **n8n** + servidor MCP propio en Python (`tools-mcp`) | Telegram, agente y aprobación humana nativos; tools con control propio |
| Modelo principal | **`qwen3:4b-instruct-2507`** (`q4_K_M`; 2,5 GB en disco, ~3,7 GiB cargado con `num_ctx` 8192) | Mejor de la batería: 30/33, tool calling 15/15 y resiste la orden inyectada en un correo ([comparativa](modelos/comparativa.md)) |
| Modelos de comparación | **`qwen3.5:4b`** y **`llama3.2:3b`** | Candidato inicial (propuso la acción inyectada) y contraste más ligero |
| Cuantización | `qwen3:4b-instruct-2507` en **`q4_K_M` vs `q8_0`** | Mismo modelo, distinta calidad y consumo |
| Modo thinking | **No aplica**: el modelo solo tiene modo sin thinking ([model card](https://huggingface.co/Qwen/Qwen3-4B-Instruct-2507)) | Sin bloques de razonamiento ni su latencia en CPU. Ollama lo anuncia como capacidad, así que la batería envía `think: false` por si acaso |
| Motor | **`ollama/ollama:0.40.11`** (fijada en oct. 2026) | Soporte de Qwen3.5 y API compatible con OpenAI |
| n8n | **`n8nio/n8n:2.44.7`** (fijada en oct. 2026) | Versión reproducible, nada de `:latest` |
| MCP | **Sí**, contenedor `tools-mcp` | Objetivo de aprendizaje y control de permisos |
| Gmail / Calendar | **API directa** desde `tools-mcp` | Scopes mínimos y confirmación impuesta en código |
| Ampliación | **Simulador doméstico con vista web** + **tareas en SQLite** | Demostrable por Telegram y en pantalla, 0 €, aislado |
| Confirmación de escritura | **Dos capas**: aprobación por Telegram en n8n **y** token de confirmación en `tools-mcp` | Resiste prompt injection aunque el modelo proponga la acción |
| Portátiles de demo | **Ambos**: el de Nelson como principal y el de Yeray como respaldo, con el modelo ya descargado | Si uno falla, el otro levanta el mismo compose. Nunca los dos con el bot activo a la vez |
| Pantalla del simulador | **Raspberry Pi 400** (y móvil antiguo opcional) por **túnel SSH** a `127.0.0.1` | Cumple la regla de no exponer puertos a la red. Si falla, la vista se muestra en el propio portátil |
| Días de ampliación | **Solo colchón para incidencias** (OAuth, bugs, red), no para funcionalidades nuevas | El plan cierra el 11 de diciembre |

> **Decisión abierta — Telegram en n8n (se resuelve en la Fase 4).**
> El *Telegram Trigger* de n8n necesita un **webhook HTTPS público**, lo que choca con la regla de «puertos solo en `127.0.0.1`» y con el long polling previsto (ver [`investigacion/decisiones.md`](investigacion/decisiones.md)).
> Alternativas que hay que validar:
> 1. Leer `getUpdates` desde n8n con un **Schedule Trigger + HTTP Request**.
> 2. Un **nodo de la comunidad** que haga polling.
>
> **Exponer n8n a internet queda descartado.**

---

## Calendario

| Semana | Fechas | Fase | Hito |
|---|---|---|---|
| 1 | 16 – 25 oct | Fase 0 | Repo, Trello, cuentas, Docker funcionando en aula y portátiles |
| 2 | 20 oct – 1 nov | Fase 1 | Comparativa de agentes cerrada |
| 3 | 27 oct – 8 nov | Fase 2 | Ollama + 3 modelos comparados, tool calling probado |
| 4 | 3 – 15 nov | Fase 3 | Guía de parámetros |
| 5 | 10 – 22 nov | Fase 4 | Diogenes respondiendo por Telegram con el modelo local |
| 6–7 | 17 nov – 2 dic | Fase 5 | `tools-mcp`: Gmail, Calendar, tareas y simulador |
| 8 | 30 nov – 8 dic | Fase 6 | Prompt injection, riesgos, grabación de respaldo |
| — | 9 dic | Entrega | Renovar OAuth, tag `v1.0.0` y Release |
| — | 10 dic | Ensayo | Demo completa en los dos portátiles |
| — | **11 dic** | **Demo** | — |

---

## Roadmap

Leyenda: `[ ]` pendiente · `[x]` hecho · **N** = Nelson · **Y** = Yeray · **N+Y** = ambos

### Fase 0 — Setup del proyecto
- [ ] Crear repo remoto privado `diogenes` y dar acceso al profesor — **N**
- [ ] Tablero Trello (TO DO / DOING / DONE) compartido en solo lectura con el profesor — **Y**
- [ ] Flujo Git: `main` protegida, ramas `feat/…` y `docs/…`, PR revisada por el otro — **N+Y**
- [x] `.gitignore`, `.env.example` y estructura de carpetas — **N**
- [ ] Cuenta Google **exclusiva** del proyecto con datos ficticios (correos y eventos) — **Y**
- [ ] Bot de Telegram con @BotFather (token solo en `.env` local) — **N**
- [ ] Docker + WSL2 en el aula y en ambos portátiles, documentando el límite de memoria de WSL2 — **N+Y**
- [ ] Bloqueos de red del centro (Telegram, Google OAuth, Docker Hub, Ollama) — **N+Y**
- [x] Fijar el tag exacto de Ollama — **N** (`ollama/ollama:0.40.11`)

### Fase 1 — Investigación de agentes personales
- [x] Investigar ≥ 3 soluciones (≥ 2 agentes personales reales) — **N+Y**
- [x] Clasificar: asistente conversacional / agente personal / agente de programación / framework / automatización
- [x] Tabla comparativa con fuentes → `investigacion/comparativa-agentes.md`
- [x] Justificar la elección de n8n + MCP propio → `investigacion/decisiones.md`

| Solución | Categoría | Por qué mirarla |
|---|---|---|
| OpenClaw | Agente personal | Propuesta del enunciado, foco en canales tipo Telegram |
| n8n | Automatización | Imagen Docker oficial, nodos de Telegram, Ollama y MCP |
| Open WebUI | Interfaz + herramientas | Integración directa con Ollama |
| OpenCode | Agente de programación | Para justificar por qué **no** encaja |

### Fase 2 — Modelos locales
- [x] Ollama en contenedor con volumen para los pesos — **N**
- [x] Probar `qwen3.5:4b`, `qwen3:4b-instruct` y `llama3.2:3b` — **N+Y**
- [x] Batería común de prompts con **≥ 1 prueba de tool calling** — **Y**
- [x] Tabla de calidad, consistencia, velocidad, RAM/CPU y tool calling → `modelos/comparativa.md`
- [x] Confirmar `qwen3.5:4b` o justificar el cambio según los resultados (cambiado a `qwen3:4b-instruct-2507`, ver `modelos/comparativa.md`)

> **Medido solo en CPU.** El portátil de las pruebas tiene una RTX 5060, pero el contenedor de Ollama no tiene acceso a la GPU (`100% CPU` en `ollama ps`), para que los resultados sean comparables con el aula y con el otro portátil. Activar la GPU queda como **mejora opcional**, no necesaria para el proyecto.

### Fase 3 — Parámetros
- [ ] Temperatura, top-p y top-k — **Y**
- [ ] Ventana de contexto **real configurada** (`num_ctx`) + conversación larga — **Y**
- [ ] Cuantización: `qwen3:4b-instruct-2507-q4_K_M` frente a `qwen3:4b-instruct-2507-q8_0` — **Y**
- [ ] Máximo de tokens de salida — **Y**
- [ ] Guía con definiciones, configuración, ejemplos y conclusiones → `modelos/parametros.md` — **Y**
- [ ] Pendiente de la Fase 1: medir RAM y latencia de `qwen3:4b-instruct-2507` con **64k de contexto**, que es el mínimo que piden OpenClaw y Hermes con modelos locales, frente al `num_ctx` elegido — **Y**

### Fase 4 — Agente + Telegram
- [ ] Servicio `n8n` en `docker-compose.yml` conectado a `ollama` — **N**
- [ ] Telegram por **long polling** — **Y**
- [ ] Restringir el bot a nuestros chat IDs — **Y**
- [ ] Prompt de sistema de Diogenes → `configuracion/agente.example.json`
- [ ] Documentar → `integraciones/telegram.md`

### Fase 5 — Herramientas externas
- [ ] Pendiente de la Fase 1: comprobar qué transporte MCP acepta el *MCP Client Tool* de nuestra versión de n8n (la documentación solo menciona un endpoint SSE) antes de elegir el de `tools-mcp` — **N**
- [ ] Esqueleto de `tools-mcp` (Python + SDK MCP) conectado al MCP Client de n8n — **N**
- [ ] Proyecto Google Cloud + OAuth en modo prueba, scopes **mínimos** (lectura primero) — **Y**
- [ ] `gmail_buscar()`: buscar correo ficticio por asunto → `integraciones/gmail.md` — **Y**
- [ ] `calendar_listar()`: próximos eventos → `integraciones/calendar.md` — **N**
- [ ] `calendar_crear()` con confirmación en dos capas — **N+Y**
- [ ] `tareas_*()` sobre SQLite — **Y**
- [ ] `casa-sim`: estado en SQLite + vista web, y `casa_*()` en `tools-mcp` — **N**
- [ ] Túnel SSH desde la Raspberry Pi 400 a la vista de `casa-sim` — **N**
- [ ] Petición combinada: *«Busca el correo sobre la reunión del proyecto, comprueba cuándo es y si tengo algún compromiso a esa hora»* — **N+Y**

### Fase 6 — Seguridad y cierre
- [ ] Análisis de riesgos: datos que entran y salen, qué ve cada contenedor, secretos → `seguridad/riesgos-y-medidas.md` — **N+Y**
- [ ] Prueba de **prompt injection** con correo ficticio: la acción no se ejecuta aunque el modelo la proponga — **N+Y**
- [ ] Grabación de respaldo de la petición combinada (< 25 MiB, o adjunta a la release) — **Y**
- [ ] Renovar OAuth en los dos portátiles antes de la demo — **N+Y**
- [ ] Tag anotado `v1.0.0` + Release con versiones de modelo, cuantización, motor y plataforma — **N**
- [ ] **Cierre seguro:** revocar token de Telegram y OAuth, borrar proyecto GCP, `.env` y volúmenes en todos los equipos — **N+Y**

---

## Puesta en marcha (se completará en Fase 4)

```bash
git clone <repo>
cd diogenes
cp .env.example .env        # rellenar con tus credenciales locales
docker compose up -d
docker compose exec ollama ollama pull qwen3:4b-instruct-2507-q4_K_M
```

Ollama del contenedor desde el host:

- Se publica en `http://127.0.0.1:11435` (configurable con `OLLAMA_HOST_PORT` en `.env`), no en el 11434 habitual.
- Así no choca con un **Ollama nativo de Windows**, si lo tenéis instalado: ese sigue en el 11434.
- La batería de pruebas de las Fases 2 y 3 debe apuntar al **11435**. Si apunta al 11434, estaréis midiendo el Ollama nativo y no el del contenedor.
- Comprobación rápida: `curl http://127.0.0.1:11435/api/version` debe devolver la versión fijada en `docker-compose.yml`.
- Dentro de Docker, n8n y `tools-mcp` usan `http://ollama:11434` por la red interna.

Vista del simulador desde la Raspberry Pi 400:

```bash
ssh -L 8080:127.0.0.1:8080 <usuario>@<ip-del-portatil>
# abrir http://localhost:8080 en el navegador de la Pi
```

---

## Estructura

```
diogenes/
├── README.md
├── .gitignore
├── .env.example
├── docker-compose.yml
├── docs/
│   └── diogenes.png        # imagen del README
├── docker/
│   ├── tools-mcp/          # servidor MCP propio (Python)
│   └── casa-sim/           # simulador doméstico + vista web
├── investigacion/          # comparativa-agentes.md, decisiones.md
├── modelos/                # comparativa.md, parametros.md
├── configuracion/          # *.example.json (sin secretos)
├── integraciones/          # telegram.md, gmail.md, calendar.md
├── seguridad/              # riesgos-y-medidas.md
└── demo/                   # clips < 25 MiB (opcional)
```

---

## Seguridad (reglas del repo)

- Secretos **solo** en `.env` local o en secrets de Compose. Nunca en Git, imágenes ni Dockerfiles.
- Si un secreto llega a subirse: **revocar y sustituir**. Borrarlo en otro commit no basta.
- Cuenta Google exclusiva con datos ficticios. Nada de cuentas personales ni del centro.
- Puertos publicados solo en `127.0.0.1`. No montar el disco entero ni `/var/run/docker.sock`.
- Imágenes oficiales con **versión fijada** (nada de `:latest`).
- Escritura (enviar, crear, borrar) → confirmación impuesta por la plataforma y por `tools-mcp`.
- En equipos del aula: secretos en el perfil propio y borrarlos al terminar la sesión.
- Un solo proceso de long polling por token de Telegram a la vez: el portátil de respaldo solo arranca `n8n` si el principal está parado.

---

## Créditos de la imagen

Pintura de Diógenes y Alejandro Magno (firmada en el lienzo, siglo XVIII), obra de dominio público. Usada como imagen de portada del proyecto.
