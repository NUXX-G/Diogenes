# Configuración

Ficheros de configuración de ejemplo (`*.example.json`), como el prompt de sistema del agente. Solo placeholders: nunca secretos ni chat IDs reales.

## `confirmaciones.json` (modo personal)

Se copia desde `confirmaciones.example.json` (el real no se versiona). Dos partes:

- `herramientas`: herramientas que piden un «sí» humano antes de ejecutarse (`core/safety.py`).
- `navegador`: reglas para `browser.*`, comparadas con el nombre accesible del elemento, sin acentos y por palabra completa.
  - `verbos_irreversibles`: pulsar algo así queda pendiente de confirmación (enviar, publicar, borrar, suscribirse…).
  - `terminos_de_pago`: solo lo que implica dinero. Se para siempre, ni un «sí» lo desbloquea.
  - `campos_de_busqueda`: nombres de campos que son buscadores (escribir con Enter ahí no es enviar un formulario).
  - `avisos_minimos`: botón con el que se cierra un aviso que tapa la página (rechazar; nunca aceptar).
  - `criterios_de_orden`: ordenaciones y filtros de la web por criterio (`visto`, `barato`, `caro`, `reciente`, `valorado`,
    `vendido`). Sin haber pulsado uno de ellos en el turno, la respuesta no puede decir «el más barato», «el más visto»…

Si tu `confirmaciones.json` no trae la sección `navegador`, se usa la del `.example`.
