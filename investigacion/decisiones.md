# Decisiones: n8n + servidor MCP propio

Fase 1 del proyecto Diogenes. Justificamos la plataforma elegida a partir de [comparativa-agentes.md](comparativa-agentes.md), cuyos números de fuente (`[n]`) se mantienen aquí. Fecha: 26-10-2026.

## Decisión

**n8n** como plataforma (canal de Telegram, AI Agent conectado a Ollama y aprobación humana) + **`tools-mcp`**, un servidor MCP propio en Python con las herramientas de Gmail, Calendar, tareas y simulador doméstico, que impone en código la confirmación de las escrituras.

## Qué pedimos a la plataforma

Los requisitos salen del README del proyecto:

1. LLM **local** con Ollama y modelos de ~4B en un portátil sin GPU obligatoria.
2. Acceso por **Telegram** sin exponer puertos a la red.
3. Herramientas **propias** con permisos mínimos (Gmail y Calendar en solo lectura salvo `calendar_crear()`).
4. **Confirmación de las escrituras impuesta por el código**, no por el prompt ni por una opción de configuración.
5. Todo con **Docker Compose** y 0 €.
6. Que sea **defendible** en clase: que podamos explicar cada pieza porque la hemos construido o configurado nosotros.

## Por qué n8n + MCP propio

- **Separa el agente de las herramientas.** n8n se encarga del canal, del modelo y del flujo; `tools-mcp` es lo único que toca Gmail, Calendar y SQLite. Así los permisos se controlan en un solo sitio que escribimos nosotros, y el modelo solo ve las herramientas que `tools-mcp` expone. n8n permite además limitar qué herramientas del servidor MCP recibe el agente («Selected» / «All Except») [21].
- **Aprobación humana nativa por herramienta.** El AI Agent de n8n puede exigir aprobación antes de ejecutar herramientas concretas, con Telegram como canal, y si se deniega la acción se cancela [22]. Es la primera capa de confirmación.
- **La segunda capa es nuestra.** La propuesta pendiente + token de un solo uso de `tools-mcp` (ver `CLAUDE.md`) no depende de que n8n esté bien configurado: aunque alguien quite la aprobación del flujo, `calendar_crear()` no escribe sin token.
- **Encaja con el stack.** Imagen oficial `n8nio/n8n` [18], nodo de Ollama [19] y cliente MCP [21] sin instalar nada aparte.
- **Objetivo de aprendizaje.** Construir el servidor MCP nos obliga a entender el protocolo, OAuth con scopes mínimos y la confirmación, que es lo que hay que defender en la demo.
- **Madurez.** Es el proyecto más antiguo de la comparativa (2019) y publica versiones de forma continua [36].

## Desventajas de n8n (siendo honestos)

1. **Telegram por webhook, no por long polling.** La documentación del *Telegram Trigger* solo describe el webhook y exige una URL pública HTTPS [20]; no hemos encontrado modo polling. Telegram, además, solo admite webhooks en HTTPS por los puertos 443, 80, 88 u 8443, y `getUpdates` deja de funcionar si hay un webhook configurado [35]. Esto **choca con dos decisiones del README**: «Telegram por long polling» y «puertos solo en 127.0.0.1». OpenClaw y Hermes usan long polling por defecto [6][14]. **Propuesta, a validar en la Fase 4:** no usar el *Telegram Trigger* y leer los mensajes con `getUpdates` desde n8n (un disparador periódico + petición HTTP), o desde un pequeño servicio propio, manteniendo todo en local.
2. **No es open source.** Es *fair-code* con la *Sustainable Use License* [16][17]: permite uso interno, personal o no comercial, que es nuestro caso, pero no es una licencia OSI y algunas funciones (archivos `.ee`) requieren licencia Enterprise [17].
3. **No es un agente personal.** Memoria, lista blanca de chats, prompt de sistema y aprobaciones hay que montarlas como flujo. OpenClaw y Hermes las traen hechas [6][14].
4. **Documentación irregular en lo que usamos.** La página del nodo de Ollama todavía lista modelos Llama 2 y no dice nada de *tool calling* [19]; la del *MCP Client Tool* solo menciona un «SSE Endpoint» [21]. En la Fase 5 habrá que comprobar qué transporte acepta nuestra versión antes de elegir el de `tools-mcp`.
5. **Aprobaciones por Telegram con fallos reportados.** En el foro hay un caso de aprobación que se quedaba en «No action required» [24]. La documentación de HITL no dice si usa botones [22]. Hay que probarlo pronto (Fase 4) y no darlo por hecho.
6. **Flujos visuales.** Los flujos se guardan en la base de datos de n8n y en Git solo se pueden versionar como JSON exportado, que se revisa peor que código en una PR. *(Valoración propia, no sale de una fuente.)*
7. **Cambia rápido.** El README fija `n8nio/n8n:2.42.7`, pero la última versión publicada el 26-10-2026 es `n8n@2.44.7` [36]. Fijar la versión es correcto; solo hay que corregir la frase «última estable a oct. 2026».

## Por qué no las demás

### OpenClaw

Es la opción que más se acerca a Diogenes «de serie», y lo reconocemos: Docker [3], Telegram por long polling [6], cliente MCP [7] y Ollama [5]. La descartamos como plataforma porque:

- **La confirmación es configuración, no código.** Las aprobaciones de ejecución vienen desactivadas por defecto y hay un modo «YOLO» que las salta [8]. Nuestro requisito 4 es que la confirmación no dependa de una opción.
- **Mucha más superficie de la que necesitamos.** Ejecuta comandos y toca archivos, y sin *sandbox* las herramientas de la sesión principal corren en el host [1]. Diogenes solo necesita cinco familias de herramientas cerradas.
- **Contexto exigente para un 4B en CPU.** Ollama recomienda al menos 64k tokens de contexto con modelos locales [5]. Con `qwen3.5:4b` en un portátil sin GPU, eso puede disparar la memoria y la latencia. *(Inferencia nuestra; se puede medir en la Fase 3.)*
- **Aprendemos menos.** Usaríamos un agente hecho en lugar de construir la capa de herramientas y permisos, que es lo que queremos defender.

Sigue siendo la referencia con la que comparar en la memoria y en la defensa.

### Hermes Agent

Muy completo en seguridad (lista de comandos peligrosos, bloqueos fijos, denegación por tiempo) [15], con Docker [12], Telegram por long polling [14] y MCP [10]. Lo descartamos porque:

- **Exige al menos 64.000 tokens de contexto** con herramientas y rechaza ventanas menores al arrancar [13]. Es el mismo problema que OpenClaw, pero obligatorio.
- **La aprobación por defecto la decide un LLM auxiliar** (modo `smart`) y se puede desactivar [15]; en Telegram se aprueba escribiendo «yes» [14]. Preferimos una confirmación determinista en código.
- Mismo argumento de superficie y aprendizaje que con OpenClaw: es un agente generalista con terminal y más de 40 herramientas [10].

### Open WebUI

Es una buena interfaz para Ollama [26], pero:

- **No tiene Telegram** [26], que es el canal obligatorio del proyecto.
- **La aprobación de herramientas es experimental** [28].
- **Su MCP nativo es solo Streamable HTTP** y la propia documentación prefiere OpenAPI [27].

Nos puede servir como herramienta auxiliar para probar modelos en la Fase 2, no como plataforma.

### OpenCode: por qué no encaja

- **Es un agente de programación** [30]: trabaja sobre un directorio de proyecto desde la terminal, una app de escritorio o el IDE [31].
- **No tiene canal de mensajería.** No hemos encontrado integración con Telegram ni casos de uso fuera de la programación [31].
- **Sus herramientas son las de un programador** (leer y editar archivos, ejecutar `bash`, buscar en el código) y **por defecto son permisivas** [34]. Es lo contrario de lo que queremos: un agente que solo lee correo y calendario y no escribe sin confirmación.
- **Su imagen Docker es interactiva** (`docker run -it`) [31], pensada para una sesión de terminal y no para un servicio que atiende un bot.

Soporta Ollama [32] y MCP [33], así que técnicamente se podría forzar, pero acabaríamos usando una herramienta de programación para algo que no es programar.

## Riesgos que nos llevamos de esta fase

| Riesgo | Fase | Qué haremos |
|---|---|---|
| El *Telegram Trigger* necesita webhook HTTPS público [20] | 4 | Validar `getUpdates` en local antes de construir el resto del flujo |
| Transporte del *MCP Client Tool* (la doc solo menciona SSE) [21] | 5 | Comprobarlo en nuestra versión de n8n antes de implementar `tools-mcp` |
| Aprobación por Telegram con fallos reportados [24] | 4 | Probarla al principio de la fase |
| *Tool calling* de Ollama en n8n sin documentar [19] | 2 y 4 | Incluirlo en la batería de pruebas de modelos |
