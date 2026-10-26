# Checklist Fase 0 — tareas manuales

Tareas de la Fase 0 que solo podéis hacer vosotros (cuentas, accesos, equipos). La parte de código (estructura, `.gitignore`, `.env.example`, `docker-compose.yml`, script de entorno y hook de secretos) ya está en el repo.

Leyenda: **N** = Nelson · **Y** = Yeray · **N+Y** = ambos

## Repositorio y organización

- [ ] Crear el repo remoto **privado** `diogenes`, subir `main` y dar acceso al profesor — **N**
- [ ] Proteger `main` en el remoto: sin push directo, PR obligatoria con revisión del otro — **N**
- [ ] Tablero Trello (TO DO / DOING / DONE) compartido en solo lectura con el profesor — **Y**

## Cuentas

- [ ] Cuenta Google **exclusiva** del proyecto (nada personal ni del centro) — **Y**
- [ ] Rellenarla con datos ficticios: varios correos (incluido el de «la reunión del proyecto» y uno con una orden inyectada para la Fase 6) y eventos en Calendar — **Y**
- [ ] Crear el bot con @BotFather y guardar el token **solo** en el `.env` local de cada portátil — **N**
- [ ] Apuntar los chat IDs de los dos en el `.env` local (`TELEGRAM_ALLOWED_CHAT_IDS`), nunca en el repo — **N+Y**

## Equipos (aula y los dos portátiles)

- [ ] Docker Desktop + WSL2 instalados y arrancados — **N+Y**
- [ ] Ejecutar `powershell -ExecutionPolicy Bypass -File .\scripts\check-entorno.ps1` y guardar el resultado de cada equipo — **N+Y**
- [ ] Documentar el límite de memoria de WSL2 de cada equipo (y crear `%USERPROFILE%\.wslconfig` con `memory=` si Docker tiene menos de 8 GB) — **N+Y**
- [ ] En el aula: anotar qué bloquea la red del centro (Docker Hub, registro de Ollama, Telegram, Google OAuth) — **N+Y**
- [ ] `cp .env.example .env` y rellenarlo (generar `N8N_ENCRYPTION_KEY` y `CONFIRM_TOKEN_SECRET` distintos) — **N+Y**
- [ ] Instalar el hook de secretos: `pip install pre-commit` y `pre-commit install` en la raíz del repo — **N+Y**
- [ ] Comprobar que Ollama arranca: `docker compose up -d` y `docker compose ps` (estado `healthy`) — **N+Y**

## Revisión de decisiones

- [x] Revisar y aprobar el tag de Ollama fijado (`ollama/ollama:0.40.11`, verificado el 26-10-2026) — **N**

## Al terminar cada sesión en el aula

- [ ] Borrar `.env`, credenciales y volúmenes del perfil del aula (`docker compose down -v`) — **N+Y**
