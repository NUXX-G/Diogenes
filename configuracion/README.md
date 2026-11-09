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

Si tu `confirmaciones.json` no trae la sección `navegador`, se usa la del `.example`. Lo mismo vale para las
secciones siguientes.

- `intencion` (`core/intent.py`): la puerta de intención. Lo que el usuario nombra en su mensaje se ejecuta directo; lo que
  solo propone el modelo pide confirmación mostrando el destino.
  - `verbos`: grupos de verbos (`abrir`, `buscar`, `navegar`, `cerrar`). `abr*` es un prefijo; sin `*`, palabra exacta.
  - `herramientas`: qué herramientas de acción vigila, con qué grupos de verbos y qué debe nombrarse (`app`, `url`, `sitio`).
  - `libres_en_web`: herramientas del navegador que van directas dentro de una web que el usuario pidió.
  - `palabras_genericas`: palabras de un nombre de app que no cuentan al compararlo con lo que dijo el usuario.
- `cierre` (`hands/system/closer.py`): `apps.close` y `apps.force_close`.
  - `confirmar_cierre_suave`: con `true` (por defecto) el cierre como con el botón X va directo si nombras la app y solo
    coincide una abierta; pregunta si la propone el modelo, si coinciden varias o si es de App Paths. Con `false`, nunca.
  - `espera_segundos`: cuánto espera a que la app se cierre antes de decir que sigue abierta.
  - `procesos_protegidos`: ejecutables que nunca se cierran. Además se protegen siempre el proceso de hands, su padre y el
    navegador de Diogenes mientras esté abierto. Forzar el cierre (`apps.force_close`) está en `herramientas`, así que
    siempre pide su propia confirmación.
- `ejecutables` (`hands/apps/policy.py`): `extensiones` de archivos que `apps.launch` solo abre tras un «sí», aunque el
  usuario los nombre (`.exe`, `.bat`, `.ps1`, `.msi`…). Un `.lnk` se juzga por su destino: va directo si apunta a algo no
  ejecutable o a un `.exe` del menú Inicio, y pregunta si apunta a un script o a un `.exe` desde otro sitio (escritorio) o
  si no se puede leer. `interpretes`: ejecutables que con argumentos equivalen a un script (`powershell -File x.ps1`).
  Añade `.lnk` a `extensiones` si quieres que todos los accesos pregunten.

## `atajos.json`

Frases que el router (`core/router.py`) resuelve sin LLM: teclas multimedia y volumen (`media.key`). Se copia desde
`atajos.example.json`. Solo cuenta si **toda** la frase es un atajo (tras quitar el `relleno`: «Diogenes», «por favor»…);
`veces` es cuántas pulsaciones da (el volumen sube de 2 en 2 %).

`boton_panico` es el atajo global que cancela la tarea en curso, vacía la cola y suelta el navegador sin cerrar ventanas
(por defecto `ctrl+alt+esc`). Admite `ctrl`, `alt`, `shift`, `win` más una tecla. En el CLI también vale `/parar`.

## Variables de entorno de hands

- `DIOGENES_INDEX_CACHE_MINUTES` (10): cuánto vale la caché del índice de apps en `data/apps-index.json`.
- `DIOGENES_INDEX_CACHE`: ruta alternativa de esa caché.
