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
- **Se conservan en el servidor** `.env`, `backend/.env`, `docker-compose.override.yml` y `.local/` (secretos y ofertas aceptadas).
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
