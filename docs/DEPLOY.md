# Despliegue automático a la VM (DigitalOcean)

Al fusionar a `main`, si el CI termina en verde, el workflow `.github/workflows/deploy.yml` sube ese commit a la VM y
reconstruye los contenedores. Si la versión nueva no queda saludable, vuelve sola a la anterior. Es una alternativa al
túnel de `docs/RUNBOOK.md`: la VM sirve el mismo `docker-compose.yml` detrás de Caddy con HTTPS.

## Cómo funciona

```
merge a main ─► CI (verde) ─► Deploy ─► git archive | ssh (clave restringida) ─► /usr/local/bin/chat-deploy
                                                                                  ├─ respaldo del código y de las imágenes (:prev)
                                                                                  ├─ docker compose up -d --build --wait
                                                                                  ├─ comprueba / y /v1/meta
                                                                                  └─ si falla: restaura lo anterior (exit 1)
```

- **La VM no tiene acceso a GitHub.** El runner envía el código por SSH; no hay token del repositorio en el servidor.
- **La clave de CI solo ejecuta `chat-deploy`** (`restrict,command="/usr/local/bin/chat-deploy"` en `authorized_keys`):
  sin shell, sin pty, sin reenvíos. El script es de root; la clave no puede modificarlo. El script (`deploy/chat-deploy.sh`)
  solo acepta un SHA de 40 caracteres y no imprime variables de entorno ni logs de la aplicación (el log del Action es público).
- **Se conservan en el servidor** `.env`, `backend/.env`, `docker-compose.override.yml`, `.expected_data_source`, `backend/data/snapshot` y `.local/`
  (secretos, ofertas aceptadas y snapshot de datos).
- **No se da por bueno un deploy que cambie la fuente de datos.** Tras levantar, el script compara `data_source` de
  `/v1/meta` con lo esperado: el contenido de `/home/deploy/app/.expected_data_source` (`organizer_snapshot` o `team_fixture`)
  o, si no existe, lo que servía la VM antes de desplegar. Si no coincide, revierte. Así un snapshot perdido no hace caer
  la demo al conjunto inventado sin avisar (`config.py` hace ese cambio en silencio cuando falta `customers.parquet`).
- Solo se despliega desde un `push` a `main` del propio repositorio (no desde PR ni forks) y se omite un commit si `main`
  ya avanzó. Un deploy a la vez (`concurrency`).
- Registro en el servidor: `/home/deploy/deploy.log`. Commit en producción: `/home/deploy/app/.deployed_sha`.

## Configuración en GitHub (una sola vez; requiere ser admin del repositorio)

Settings > Secrets and variables > Actions:

| Tipo | Nombre | Valor |
|---|---|---|
| Secret | `DEPLOY_HOST` | IP de la VM |
| Secret | `DEPLOY_SSH_KEY` | clave **privada** ed25519 creada solo para este fin (archivo completo, con las líneas BEGIN/END) |
| Secret | `DEPLOY_KNOWN_HOSTS` | salida de `ssh-keyscan -t ed25519 <IP>` (fija la huella del servidor) |
| Variable | `DEPLOY_ENABLED` | `true` para activar; cualquier otro valor lo apaga |
| Variable | `DEPLOY_URL` | enlace público (opcional): tras el deploy se comprueba `/v1/meta` |

Para pausar los despliegues (p. ej. VM apagada o evaluación en curso) basta cambiar `DEPLOY_ENABLED`. También se puede
lanzar a mano desde Actions > Deploy > Run workflow (solo sobre `main`).

## Preparar una VM nueva

1. Ubuntu 24.04 con Docker y el plugin compose, usuario `deploy` en el grupo `docker`, `rsync`, `curl`, Caddy como proxy HTTPS
   hacia `127.0.0.1:8080` y firewall con 22/80/443.
2. Código en `/home/deploy/app` con `.env` (`CHAT_ENV`, `CHAT_API_KEY`, `FRONTEND_PORT`) y `backend/.env` (ver
   `backend/.env.example`; con `CHAT_ENV=prod` son obligatorias `CHAT_ADMIN_API_KEYS` y `CHAT_JWT_SECRET`). Un
   `docker-compose.override.yml` debe publicar el frontend solo en loopback, porque Docker salta UFW:
   ```yaml
   services:
     chat-frontend:
       ports: !override
         - "127.0.0.1:8080:8080"
   ```
3. `mkdir -p .local/offers && chown 10001:10001 .local/offers` (el contenedor es de solo lectura y escribe ahí).
   **Snapshot de gold** (sin él la demo usa `data/fixture`, 21 clientes inventados): generarlo en una máquina con el export
   (`python backend/scripts/build_snapshot.py --gold-only`, ver `docs/DATA.md`), copiar `backend/data/snapshot/` a
   `/home/deploy/app/.local/snapshot/` (directorio 755, parquet 644) y montarlo en el `docker-compose.override.yml`:
   ```yaml
   services:
     chat-backend:
       volumes:
         - ./.local/snapshot:/data/snapshot:ro
       environment:
         CHAT_DATA_DIR: /data/snapshot
   ```
   Luego `echo organizer_snapshot > /home/deploy/app/.expected_data_source` y `docker compose up -d`. Cada export gold nuevo
   exige regenerar y volver a copiar el snapshot, y reiniciar el backend. Las ofertas aceptadas (`.local/offers`) se
   sincronizan aparte con `backend/scripts/sync_credit_offers.py`.
   **Datos directos de Databricks (alternativa al snapshot)**: en `backend/.env` del servidor poner
   `CHAT_REPOSITORY=databricks`, `CHAT_DATABRICKS_WAREHOUSE_ID`, `DATABRICKS_HOST` y `DATABRICKS_TOKEN` (de un service
   principal con `SELECT` en las tablas de silver/gold que lee el backend y `MODIFY` solo en `credit_offers`; ver
   `backend/README.md`), y `echo databricks > /home/deploy/app/.expected_data_source`. No hace falta el volumen del
   snapshot. Las ofertas aceptadas se escriben solas en `gold.credit_offers`; el JSONL sigue de respaldo.
4. Instalar el script como root: `install -o root -g root -m 755 deploy/chat-deploy.sh /usr/local/bin/chat-deploy`.
5. Añadir la clave pública de CI a `/home/deploy/.ssh/authorized_keys`:
   `restrict,command="/usr/local/bin/chat-deploy" ssh-ed25519 AAAA… github-actions-deploy`

## Probar sin esperar a un merge

```bash
git archive --format=tar origin/main | ssh -i <clave-de-ci> -T deploy@<IP> "$(git rev-parse origin/main)"
```

Un archivo roto (por ejemplo un backend que falla al arrancar) debe terminar con código 1 y la versión anterior en pie.

## Límites

- Una sola VM y una réplica: durante el reinicio del backend hay unos segundos sin servicio y las sesiones en memoria se pierden.
- `rsync --delete` borra del servidor lo que ya no está en el repositorio, salvo los archivos conservados arriba.
- El rollback usa las imágenes `:prev` y la copia `/home/deploy/app.prev` del deploy inmediatamente anterior; no hay historial más largo.
