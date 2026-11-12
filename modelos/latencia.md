# Latencia de Diogenes en el PC del aula

Medido con `python -m core.bench` (fase 4 de la hoja de ruta). El banco ejecuta cinco órdenes típicas dos veces seguidas
en la misma sesión, con hands-mcp de verdad y dobles solo en lo más bajo (ventanas, lanzador, multimedia y un sitio de
vídeos falso), así que **nunca toca el PC**. Hay dos modos:

- **Simulado** (`python -m core.bench`): un LLM con guion que sigue el camino bueno y calcula cuánto habría tardado. No
  necesita Ollama y se repite igual siempre: sirve para comparar versiones del código.
- **Real** (`python -m core.bench --real`): `qwen3:4b-instruct` en Ollama decide de verdad. Mide el tiempo real, pero el
  modelo puede equivocarse (y se equivoca: ver «Hrom» abajo).

Los ficheros JSON de `modelos/latencia/` guardan cada fila (herramientas usadas, respuesta, tokens y segundos).

## Lo que cuesta el modelo en este PC (medido el 2026-11-12)

CPU, 6 hilos, sin GPU, `qwen3:4b-instruct` ya cargado. Datos de `prompt_eval_duration` y `eval_duration` de Ollama:

| Situación | Tokens del prompt | Leer el prompt | Generar |
|---|---|---|---|
| Prompt nuevo (sistema + 16 herramientas), nada en caché | 3708 | 113 s | 21 tok en 6,4 s |
| La misma petición otra vez (todo en caché) | 3708 | 0,3 s | 21 tok en 6,4 s |
| La conversación crece un turno | 3728 | 4,8 s | 8 tok en 2,2 s |
| Se quitan las 6 herramientas del navegador (las últimas) | 3256 | 3,6 s | 8 tok en 2,1 s |
| Se vuelven a poner | 3728 | 21,3 s | 8 tok en 2,2 s |

Conclusiones que guían el código:

1. **Generar es lo caro**: ~3,4 tokens/s, unos 0,3 s por token. Una frase final de 20 tokens son 6 s. Por eso la
   respuesta de una acción simple ya comprobada la escribe el código.
2. **Leer el prompt solo es caro si cambia el principio**: Ollama reutiliza el prompt anterior hasta la primera
   diferencia (~33 tokens/s para lo nuevo). Ojo: `prompt_eval_count` cuenta el prompt entero aunque salga de la caché;
   el coste real es `prompt_eval_duration` (el CLI lo enseña como «prompt X s»).
3. **Recortar el historial por el principio en cada turno** obligaba a releer toda la conversación (30-60 s). Ahora se
   recorta a saltos, hasta la mitad de los topes, así que pasa una vez cada varios turnos.
4. **Enseñar menos herramientas no compensa**: la plantilla de Qwen pone las herramientas justo después del prompt de
   sistema, y volver a enseñarlas cuesta ~20 s. `DIOGENES_HERRAMIENTAS=relevantes` existe, pero viene desactivado. En el
   simulador, con las 10 órdenes: 387 s con todas y 510 s con las relevantes.
5. **Cambiar el prompt de sistema** (cualquier cambio de código en él) cuesta ~2 min la primera vez; después va a caché.

El simulador usa estas cifras: 0,3 s fijos + tokens nuevos / 33 + tokens generados / 3,4.

## Línea base (antes de la fase 4)

### Simulada (código del commit `306ee03`)

| Pasada | Orden | Llamadas al modelo | Tiempo |
|---|---|---|---|
| 1 | abre Spotify | 3 | 137,2 s (incluye el prompt en frío) |
| 1 | cierra la calculadora | 2 | 12,1 s |
| 1 | búscame en YouTube cómo hacer mochis | 2 | 22,6 s |
| 1 | pausa el vídeo de YouTube | 2 | 15,1 s |
| 1 | dame el vídeo más visto del canal Hrom | 7 | 86,9 s |
| 2 | abre Spotify | 3 | 23,9 s |
| 2 | cierra la calculadora | 2 | 12,1 s |
| 2 | búscame en YouTube cómo hacer mochis | 2 | 22,6 s |
| 2 | pausa el vídeo de YouTube | 2 | 64,3 s (recorte del historial: relee la conversación) |
| 2 | dame el vídeo más visto del canal Hrom | 7 | 167,4 s (ídem) |
| | **Total** | **32** | **564,3 s** |

### Real (`qwen3:4b-instruct`, una pasada)

| Orden | Llamadas al modelo | Tiempo | Qué hizo |
|---|---|---|---|
| abre Spotify | 3 | 31,3 s | buscar → lanzar → frase final |
| cierra la calculadora | 3 | 37,9 s | buscó en vez de cerrar y contestó «no está abierta… he cerrado la calculadora» (falso) |
| búscame en YouTube cómo hacer mochis | 2 | 33,6 s | bien |
| pausa el vídeo de YouTube | 3 | 39,9 s | listó sesiones y luego pausó |
| dame el vídeo más visto del canal Hrom | 5 | 94,1 s | **falló**: abrió el canal y se quedó a medias |
| **Total** | **16** | **236,9 s** | |

## Después de la fase 4

### Simulada (mismo modelo de latencia, mismas órdenes)

| Pasada | Orden | Antes: llamadas · tiempo | Solo bloque 1 (sin recetas) | Con recetas y Hrom enseñada |
|---|---|---|---|---|
| 1 | abre Spotify | 3 · 137,2 s | 1 · 127,2 s | 1 · 127,2 s (prompt en frío) |
| 1 | cierra la calculadora | 2 · 12,1 s | 1 · 10,1 s | 1 · 10,1 s |
| 1 | búscame en YouTube cómo hacer mochis | 2 · 22,6 s | 1 · 13,1 s | 1 · 13,1 s |
| 1 | pausa el vídeo de YouTube | 2 · 15,1 s | 1 · 15,3 s | 1 · 15,3 s |
| 1 | dame el vídeo más visto del canal Hrom | 7 · 86,9 s | 7 · 90,7 s | **0 · receta** |
| 2 | abre Spotify | 3 · 23,9 s | 1 · 8,3 s | **0 · receta** |
| 2 | cierra la calculadora | 2 · 12,1 s | 1 · 10,1 s | **0 · receta** |
| 2 | búscame en YouTube cómo hacer mochis | 2 · 22,6 s | 1 · 13,1 s | **0 · receta** |
| 2 | pausa el vídeo de YouTube | 2 · 64,3 s | 1 · 8,2 s | **0 · receta** |
| 2 | dame el vídeo más visto del canal Hrom | 7 · 167,4 s | 7 · 90,7 s | **0 · receta** |
| | **Total** | **32 · 564,3 s** | **22 · 387,0 s** | **4 · 165,8 s** |

Sin el arranque en frío de la primera orden (que paga cualquier versión una vez tras cambiar el prompt), el total pasa de
~427 s a ~39 s. En el banco las herramientas son dobles y tardan milisegundos; en el PC de verdad, abrir Chrome o una
app añade sus segundos, igual antes que después.

### Real (`qwen3:4b-instruct`, con `--calentar`, recetas y la de Hrom enseñada antes)

| Pasada | Orden | Antes (una pasada) | Después |
|---|---|---|---|
| 1 | abre Spotify | 3 · 31,3 s | 1 · 9,5 s |
| 1 | cierra la calculadora | 3 · 37,9 s (y mintió) | 1 · 12,3 s |
| 1 | búscame en YouTube cómo hacer mochis | 2 · 33,6 s | 1 · 16,3 s |
| 1 | pausa el vídeo de YouTube | 3 · 39,9 s | 1 · 20,7 s |
| 1 | dame el vídeo más visto del canal Hrom | 5 · 94,1 s (**falló**) | **0 · receta** (llega al más visto) |
| | **Total pasada 1** | **16 · 236,9 s** | **4 · 58,8 s** |
| 2 | abre Spotify | — | 0 · receta |
| 2 | cierra la calculadora | — | 1 · 53,6 s (planificador: ver nota) |
| 2 | búscame en YouTube… / pausa… / Hrom | — | 0 · receta |

Notas: el modelo real cerró la calculadora con dos llamadas a `apps.close` (la primera no salió bien), así que ese
turno no cumple «todo salió bien» y no se aprendió: en la 2.ª pasada vuelve al planificador. Esos 53,6 s incluyen un
recorte del historial (relee la conversación una vez). La enseñanza de Hrom en el banco la hace un guion simulado; con el
canal real queda como prueba manual.

## Segunda ronda (recetas robustas, páginas cortas, uso)

### Simulada, seis órdenes (se añade «dame el vídeo más visto del canal Lofi Girl», enseñada con Hrom)

En el sitio falso, el enlace al canal de Hrom dice «Ir al canal Hrom» y el de Lofi Girl es su perfil
«@LofiGirl · 1,5 M de suscriptores», como pasó en el aula.

| Pasada | Orden | Antes · llamadas · tiempo | Después |
|---|---|---|---|
| 1 | dame el vídeo más visto del canal Lofi Girl | planificador · 5 · 96,3 s (la receta falló en el paso 2) | **receta · 0** |
| 2 | dame el vídeo más visto del canal Lofi Girl | planificador · 5 · 126,1 s | **receta · 0** |
| | **Total de las 12** | **14 · 388,3 s** | **4 · 181,9 s** |

Coste nuevo: `apps.abiertas` y su línea en el prompt suman ~200 tokens al prompt de sistema. La primera orden en
frío sube de 127 s a 133 s y «pausa el vídeo» de 15 s a 25 s (se relee lo que viene después de las herramientas).

### Páginas más cortas (planificador, sin recetas, páginas tan largas como las de YouTube)

| Orden (pasada 1) | Antes · llamadas · tiempo | Páginas recortadas y búsqueda con página |
|---|---|---|
| dame el vídeo más visto del canal Hrom | 7 · 149,2 s | 6 · 109,8 s |
| dame el vídeo más visto del canal Lofi Girl | 7 · 156,1 s | 6 · 103,4 s |
| Total de las 12 órdenes (2 pasadas) | 36 · 876,8 s | 32 · 690,4 s |

### Real (`qwen3:4b-instruct`, `--calentar`, recetas, Hrom enseñada; seis órdenes, dos pasadas)

| | Llamadas al modelo | Tiempo | Lofi Girl |
|---|---|---|---|
| Antes de esta ronda (primera ronda, cinco órdenes) | 5 | 112,6 s | no estaba; en el aula la receta fallaba en el paso 2 |
| Después | 6 | 139,6 s | **receta, 0 llamadas, en las dos pasadas** |
| Después, solo la 2.ª pasada | **0** | 0 s de modelo | receta |

En la 1.ª pasada el modelo real miró si la calculadora estaba abierta antes de cerrarla (2 llamadas, 40,6 s) y listó
las sesiones antes de pausar (2 llamadas, 71,3 s). Al primer intento de esta medición, mirar con `apps.abiertas` se
daba por respuesta y no cerraba: corregido y medido otra vez (esta tabla es la segunda medición).

## Hilos, ventana de contexto y precarga (2026-11-12)

Rama `perf/hilos-y-contexto`. Mismo PC del aula (i5-8600, 6 núcleos, RAM en doble canal a 2667 MT/s), Ollama 0.40.16
nativo y `qwen3:4b-instruct` Q4_K_M. Banco real, una pasada, **sin recetas** (todo por el planificador, para ver la
velocidad del modelo) y con `--calentar`:
`python -m core.bench --real --calentar --pasadas 1`.

### Hilos: los que elige Ollama frente a `num_thread` 6 (`num_ctx` 8192)

| Orden | Antes (hilos de Ollama) | Después (6 hilos) |
|---|---|---|
| abre Spotify | 1 · 10,3 s | 1 · 6,4 s |
| cierra la calculadora | 2 · 24,5 s | 2 · 15,7 s |
| búscame en YouTube cómo hacer mochis | 1 · 16,9 s | 1 · 11,3 s |
| pausa el vídeo de YouTube | 2 · 53,2 s | 2 · 36,4 s |
| dame el vídeo más visto del canal Hrom | 5 · 124,7 s | 5 · 75,6 s |
| dame el vídeo más visto del canal Lofi Girl | 4 · 94,0 s | 4 · 58,1 s |
| **Total** | **15 · 323,7 s** | **15 · 203,5 s (−37 %)** |

Las mismas llamadas, las mismas herramientas y las mismas respuestas: solo cambia la velocidad. Filas en
`latencia/hilos-antes-real.json` y `latencia/hilos-6-ctx8192-real.json`.

Medido a mano con la API de Ollama, para entender de dónde sale:

| Prueba | Leer el prompt | Generar |
|---|---|---|
| Prompt de 4215 tokens, hilos de Ollama | 33,1 tok/s | 2,5 tok/s |
| Prompt de 4215 tokens, 6 hilos | 52,6 tok/s | 4,2 tok/s |
| Prompt de sistema solo (2086 tokens), 6 hilos | 66 tok/s | — |
| Generar con 256 / 1067 / 2117 / 4215 tokens de contexto, 6 hilos | — | 9,0 / 8,7 / 6,8 / 4,2 tok/s |

Los ~3,4 tok/s que se medían antes no eran el límite del PC: eran los hilos de Ollama más un contexto de ~4k tokens.
**Generar se hunde con el contexto**: recortar el prompt acelera también cada token de salida.

### `num_ctx` 4096: todavía no cabe

Con 4096, Ollama rechaza ya la primera orden: `request (4109 tokens) exceeds the available context size (4096 tokens)`.
Cada llamada del banco manda entre ~4000 y ~5300 tokens (prompt de sistema 2086 + herramientas ~1650 + historial y
página), así que se queda en **8192** hasta que el prompt adelgace (prompt de sistema ≤ 700 tokens y herramientas
≤ 900). La ventana reservada apenas cambia la velocidad (lo que cuenta es el contexto que se usa de verdad); bajarla
ahorra RAM: ~144 KB por token de caché KV, ~0,6 GB de 8192 a 4096. Si un prompt no cabe, el error ahora dice qué
variable subir.

### Arranque en frío y precarga (`num_ctx` 8192, modelo descargado con `ollama stop`)

| Configuración | Precarga (en segundo plano) | Primera orden («abre Spotify») |
|---|---|---|
| Hilos de Ollama, sin precarga (antes) | — | 134,2 s |
| 6 hilos, sin precarga | — | 83,9 s |
| 6 hilos, con precarga | 80,1 s | **6,2 s** |

Al arrancar, el CLI lanza la precarga en segundo plano: carga el modelo y deja en la caché de Ollama el prompt de
sistema y las herramientas generando un solo token. Si la primera orden llega antes de que acabe, Ollama la atiende
justo después y ya no relee el prefijo. Se desactiva con `DIOGENES_PRECARGA=0`.
