# Comparativa de agentes

Fase 1 del proyecto Diogenes. Comparamos cinco soluciones que podrían servir de base para un agente personal con LLM local, accesible por Telegram y con herramientas propias.

- **Fecha de consulta:** 26-10-2026. Todos los datos salen de la web oficial, la documentación o el repositorio de cada proyecto (ver [Fuentes](#fuentes)). Los números entre corchetes enlazan a la fuente de cada dato.
- **"No encontrado"** significa que no aparece en las fuentes oficiales consultadas, no que la función no exista.
- **Estrellas y versiones** cambian a diario: son una foto del 26-10-2026.

## Clasificación

| Solución | Categoría |
|---|---|
| OpenClaw | Agente personal |
| Hermes Agent (Nous Research) | Agente personal |
| n8n | Automatización |
| Open WebUI | Asistente conversacional (interfaz de chat con herramientas) |
| OpenCode | Agente de programación |

Ninguna de las cinco es un *framework* (librería para programar agentes); se menciona la categoría porque la pide el enunciado.

## Tabla comparativa

| | OpenClaw | Hermes Agent | n8n | Open WebUI | OpenCode |
|---|---|---|---|---|---|
| **Categoría** | Agente personal | Agente personal | Automatización | Asistente conversacional | Agente de programación |
| **Licencia** | MIT [1] | MIT [10] | *Sustainable Use License* + *Enterprise License* para archivos `.ee`; se define como *fair-code*, no open source [16][17] | *Open WebUI License*: cláusulas tipo BSD + prohibición de quitar la marca por encima de 50 usuarios [25] | MIT [30] |
| **Autoalojable con Docker** | Sí: `ghcr.io/openclaw/openclaw` y `openclaw/openclaw`, con Compose [3] | Sí, plataforma *Tier 1*: `nousresearch/hermes-agent`, con Compose [11][12] | Sí: `n8nio/n8n` [18] | Sí: `ghcr.io/open-webui/open-webui` [26] | Sí: `ghcr.io/anomalyco/opencode` (interactivo, `docker run -it`) [31] |
| **Modelos locales (Ollama)** | Sí, como proveedor personalizado [4]; Ollama ofrece `ollama launch openclaw` y recomienda ≥ 64k de contexto con modelos locales [5] | Sí, mediante endpoint compatible con OpenAI; **exige ≥ 64.000 tokens de contexto** con herramientas [13] | Sí, nodo *Ollama Chat Model* (la página aún lista modelos Llama 2) [19] | Sí, soporte nativo de Ollama y APIs compatibles con OpenAI [26] | Sí, Ollama vía `@ai-sdk/openai-compatible` [32] |
| **Telegram** | Sí, «production-ready»; **long polling por defecto**, webhook opcional [6] | Sí; **long polling por defecto**; lista blanca `TELEGRAM_ALLOWED_USERS` [14] | Sí, nodo *Telegram Trigger*; la doc exige **URL pública HTTPS** para el webhook [20]. Modo polling: no encontrado | No encontrado (solo en un producto aparte, *Open WebUI Computer*) [26] | No encontrado [31] |
| **MCP / herramientas** | Cliente MCP (Streamable HTTP, SSE, stdio) y servidor MCP; *skills* y plugins [7] | Más de 40 herramientas y cliente MCP [10] | Nodo *MCP Client Tool* para el AI Agent; la doc solo describe un «SSE Endpoint» [21] | MCP nativo **solo Streamable HTTP** (≥ v0.6.31); OpenAPI como vía preferida [27] | MCP local y remoto [33] |
| **Aprobación humana de acciones** | *Exec approvals*, pero **desactivadas por defecto** (`ask: off`); modo «YOLO» disponible [8] | Aprobación de comandos peligrosos (modo `smart` por defecto, con un LLM auxiliar); en Telegram se responde con texto «yes/no»; se puede desactivar [15][14] | *Human-in-the-loop* por herramienta del AI Agent, con Telegram entre los canales; si se deniega, se cancela y se informa al modelo [22] | Modo «Ask for approval» por llamada a herramienta, **experimental** (v0.11.1) [28] | Permisos `allow` / `ask` / `deny`; **por defecto permisivo** [34] |
| **Requisitos de hardware** | Node 24.16+ o 26.1+ [2]; ≥ 6 GB de RAM solo para compilar la imagen desde el código [3]; RAM en uso: no encontrado | 1 GB de RAM mínimo, 2–4 GB recomendado; 1–2 núcleos [12] | CPU modesta; la memoria es el factor principal. Cifra para autoalojado: no encontrado [23] | Imagen `:slim` ~300 MiB de RAM en reposo [26]; resto: no encontrado | No encontrado [31] |
| **Madurez** (API de GitHub [36]) | Creado nov. 2025 · ~393k ★ · `v2026.10.7` (24-10-2026) | Creado jul. 2025 · ~257k ★ · `v0.22.5` (23-10-2026) | Creado jun. 2019 · ~209k ★ · `n8n@2.44.7` (26-10-2026) | Creado oct. 2023 · ~157k ★ · `v0.12.4` (25-10-2026) | Creado abr. 2025 · ~222k ★ · `v1.19.4` (22-10-2026) |

## Soluciones

### OpenClaw

Agente personal que se describe como «an open-source AI assistant that runs on your own computer» y que trabaja en los canales de chat que ya usas [1]. Todo pasa por un *Gateway* local que gestiona sesiones, herramientas y canales [1]. Es el agente personal más popular de la comparativa (~393k estrellas en menos de un año) [36] y Ollama lo integra directamente con `ollama launch openclaw` [5]. Cumple casi todo lo que pide Diogenes sin programar nada: Docker con Compose [3], Telegram por long polling con emparejamiento y listas blancas [6], cliente MCP [7] y aprobaciones de ejecución [8]. En contra: las aprobaciones vienen **desactivadas por defecto** y existe un modo «YOLO» que las salta [8]; sin *sandbox*, las herramientas de la sesión principal se ejecutan en el host [1]; y con modelos locales se recomienda un contexto de al menos 64k tokens [5].

### Hermes Agent

Agente personal de Nous Research: «The self-improving AI agent» con memoria entre sesiones y tareas programadas [10]. Docker es una plataforma *Tier 1* con imagen oficial y ejemplo de Compose [11][12], y sus requisitos son bajos (1 GB de RAM mínimo) [12]. Funciona por Telegram con long polling y lista blanca de usuarios [14], y es cliente MCP [10]. Su sistema de seguridad es el más elaborado: lista de comandos peligrosos que piden aprobación, comandos bloqueados siempre y denegación por tiempo agotado [15]. En Telegram, la aprobación se da respondiendo «yes» o «no» con texto, no con botones [14], y el modo por defecto (`smart`) decide el riesgo con un LLM auxiliar [15]. Para modelos locales **exige al menos 64.000 tokens de contexto** y rechaza ventanas menores al arrancar [13].

### n8n

Plataforma de automatización *fair-code* con capacidades de IA y más de 400 integraciones [16]. Se autoaloja con la imagen `n8nio/n8n` [18] y tiene nodos para Ollama [19], Telegram [20], cliente MCP para el AI Agent [21] y *human-in-the-loop* por herramienta, con Telegram como canal de aprobación [22]. Es el proyecto más veterano (2019) [36]. No es un agente personal: el agente hay que montarlo como un flujo. Tres puntos débiles para Diogenes: la licencia no es open source y limita el uso a fines internos, personales o no comerciales [17]; el *Telegram Trigger* necesita una URL pública HTTPS [20], lo que choca con la regla de no exponer puertos; y la documentación del nodo de Ollama está desactualizada (lista modelos Llama 2) [19]. Además, hay un hilo del foro sobre aprobaciones por Telegram que se quedaban colgadas [24].

### Open WebUI

Interfaz de chat autoalojada, pensada para funcionar sin conexión [26], con soporte nativo de Ollama [26]. Admite MCP de forma nativa, pero solo por Streamable HTTP, y recomienda OpenAPI para la mayoría de despliegues [27]. Desde la v0.11.1 puede pedir aprobación antes de cada llamada a una herramienta, aunque la función es experimental [28]. No tiene integración con Telegram [26]: está pensado para usarse desde el navegador. Su licencia prohíbe quitar la marca «Open WebUI» por encima de 50 usuarios [25], algo que no nos afecta.

### OpenCode

Agente de programación: «The open source AI coding agent» [30]. Funciona en terminal, como app de escritorio o extensión de IDE [31], y soporta Ollama [32] y servidores MCP [33]. Sus permisos (`allow` / `ask` / `deny`) vienen permisivos por defecto [34]. No encontramos integración con mensajería ni casos de uso fuera de la programación [31]. Lo incluimos para justificar por qué **no** encaja (ver [decisiones.md](decisiones.md)).

## Fuentes

Consultadas el 26-10-2026.

**OpenClaw**
1. [Repositorio openclaw/openclaw](https://github.com/openclaw/openclaw)
2. [Instalación](https://docs.openclaw.ai/install)
3. [Instalación con Docker](https://docs.openclaw.ai/install/docker)
4. [Proveedores de modelos](https://docs.openclaw.ai/concepts/model-providers)
5. [Ollama: integración con OpenClaw](https://docs.ollama.com/integrations/openclaw)
6. [Canal de Telegram](https://docs.openclaw.ai/channels/telegram)
7. [Servidores MCP](https://docs.openclaw.ai/tools/mcp)
8. [Exec approvals](https://docs.openclaw.ai/tools/exec-approvals)
9. [Seguridad del Gateway](https://docs.openclaw.ai/gateway/security)

**Hermes Agent**

10. [Repositorio NousResearch/hermes-agent](https://github.com/NousResearch/hermes-agent)
11. [Plataformas soportadas](https://hermes-agent.nousresearch.com/docs/getting-started/platform-support)
12. [Docker](https://hermes-agent.nousresearch.com/docs/user-guide/docker)
13. [Proveedores](https://hermes-agent.nousresearch.com/docs/integrations/providers)
14. [Telegram](https://hermes-agent.nousresearch.com/docs/user-guide/messaging/telegram)
15. [Seguridad](https://hermes-agent.nousresearch.com/docs/user-guide/security)

**n8n**

16. [Repositorio n8n-io/n8n (README)](https://github.com/n8n-io/n8n)
17. [LICENSE.md (Sustainable Use License)](https://github.com/n8n-io/n8n/blob/master/LICENSE.md)
18. [Instalación con Docker](https://docs.n8n.io/hosting/installation/docker/)
19. [Nodo Ollama Chat Model](https://docs.n8n.io/integrations/builtin/cluster-nodes/sub-nodes/n8n-nodes-langchain.lmchatollama/)
20. [Telegram Trigger: problemas comunes](https://docs.n8n.io/integrations/builtin/trigger-nodes/n8n-nodes-base.telegramtrigger/common-issues/)
21. [Nodo MCP Client Tool](https://docs.n8n.io/integrations/builtin/cluster-nodes/sub-nodes/n8n-nodes-langchain.toolmcp/)
22. [Human-in-the-loop para herramientas de IA](https://docs.n8n.io/advanced-ai/human-in-the-loop-tools/)
23. [Requisitos (despliegue OEM)](https://docs.n8n.io/deploy/host-n8n/deploy-as-an-oem-integration/prerequisites)
24. [Foro: HITL con Telegram «No action required»](https://community.n8n.io/t/human-in-the-loop-with-agent-tools-telegram-no-action-required-issue-when-using-send-and-wait-for-response/79769)

**Open WebUI**

25. [LICENSE de open-webui/open-webui](https://github.com/open-webui/open-webui/blob/main/LICENSE)
26. [Documentación](https://docs.openwebui.com/)
27. [MCP](https://docs.openwebui.com/features/extensibility/mcp/)
28. [Blog v0.11.1: aprobación de herramientas](https://openwebui.com/blog/v0-11-1-the-model-learns-to-stop-and-ask)

**OpenCode**

30. [Repositorio anomalyco/opencode](https://github.com/anomalyco/opencode)
31. [Documentación: instalación](https://opencode.ai/docs/)
32. [Proveedores](https://opencode.ai/docs/providers/)
33. [Servidores MCP](https://opencode.ai/docs/mcp-servers/)
34. [Permisos](https://opencode.ai/docs/permissions/)

**Comunes**

35. [Telegram Bot API](https://core.telegram.org/bots/api)
36. API de GitHub (estrellas, fecha de creación y última release): [openclaw](https://api.github.com/repos/openclaw/openclaw) · [hermes-agent](https://api.github.com/repos/NousResearch/hermes-agent) · [n8n](https://api.github.com/repos/n8n-io/n8n) · [open-webui](https://api.github.com/repos/open-webui/open-webui) · [opencode](https://api.github.com/repos/anomalyco/opencode)

[1]: https://github.com/openclaw/openclaw
[2]: https://docs.openclaw.ai/install
[3]: https://docs.openclaw.ai/install/docker
[4]: https://docs.openclaw.ai/concepts/model-providers
[5]: https://docs.ollama.com/integrations/openclaw
[6]: https://docs.openclaw.ai/channels/telegram
[7]: https://docs.openclaw.ai/tools/mcp
[8]: https://docs.openclaw.ai/tools/exec-approvals
[9]: https://docs.openclaw.ai/gateway/security
[10]: https://github.com/NousResearch/hermes-agent
[11]: https://hermes-agent.nousresearch.com/docs/getting-started/platform-support
[12]: https://hermes-agent.nousresearch.com/docs/user-guide/docker
[13]: https://hermes-agent.nousresearch.com/docs/integrations/providers
[14]: https://hermes-agent.nousresearch.com/docs/user-guide/messaging/telegram
[15]: https://hermes-agent.nousresearch.com/docs/user-guide/security
[16]: https://github.com/n8n-io/n8n
[17]: https://github.com/n8n-io/n8n/blob/master/LICENSE.md
[18]: https://docs.n8n.io/hosting/installation/docker/
[19]: https://docs.n8n.io/integrations/builtin/cluster-nodes/sub-nodes/n8n-nodes-langchain.lmchatollama/
[20]: https://docs.n8n.io/integrations/builtin/trigger-nodes/n8n-nodes-base.telegramtrigger/common-issues/
[21]: https://docs.n8n.io/integrations/builtin/cluster-nodes/sub-nodes/n8n-nodes-langchain.toolmcp/
[22]: https://docs.n8n.io/advanced-ai/human-in-the-loop-tools/
[23]: https://docs.n8n.io/deploy/host-n8n/deploy-as-an-oem-integration/prerequisites
[24]: https://community.n8n.io/t/human-in-the-loop-with-agent-tools-telegram-no-action-required-issue-when-using-send-and-wait-for-response/79769
[25]: https://github.com/open-webui/open-webui/blob/main/LICENSE
[26]: https://docs.openwebui.com/
[27]: https://docs.openwebui.com/features/extensibility/mcp/
[28]: https://openwebui.com/blog/v0-11-1-the-model-learns-to-stop-and-ask
[30]: https://github.com/anomalyco/opencode
[31]: https://opencode.ai/docs/
[32]: https://opencode.ai/docs/providers/
[33]: https://opencode.ai/docs/mcp-servers/
[34]: https://opencode.ai/docs/permissions/
[35]: https://core.telegram.org/bots/api
[36]: https://api.github.com/repos/openclaw/openclaw
