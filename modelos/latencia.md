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
