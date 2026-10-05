#!/usr/bin/env bash
# Comando forzado de la clave de GitHub Actions (restrict,command="/usr/local/bin/chat-deploy").
#   stdin                = tar del repositorio (git archive) del commit a desplegar
#   SSH_ORIGINAL_COMMAND = SHA de 40 caracteres de ese commit
# Conserva .env, backend/.env, docker-compose.override.yml y .local/ (secretos y datos del servidor).
# No imprime variables de entorno ni logs de la aplicacion: la salida va al log publico del workflow.
set -euo pipefail

APP=/home/deploy/app
PREV=/home/deploy/app.prev
LOG=/home/deploy/deploy.log
KEEP=(--exclude='/.env' --exclude='/backend/.env' --exclude='/docker-compose.override.yml' --exclude='/.local' --exclude='/.deployed_sha')

log() { echo "$(date -u +%FT%TZ) $*" | tee -a "$LOG"; }

SHA="${SSH_ORIGINAL_COMMAND:-}"
[[ "$SHA" =~ ^[0-9a-f]{40}$ ]] || { echo "deploy: SHA invalido" >&2; exit 2; }

exec 9>/home/deploy/.deploy.lock
flock -n 9 || { echo "deploy: ya hay otro deploy en curso" >&2; exit 3; }

STAGE=$(mktemp -d /home/deploy/.stage.XXXXXX)
trap 'rm -rf "$STAGE"' EXIT
tar -x -f - --no-same-owner --no-same-permissions -C "$STAGE"
for f in docker-compose.yml backend/Dockerfile frontend/Dockerfile backend/requirements.txt; do
  [ -f "$STAGE/$f" ] || { log "deploy $SHA: archivo incompleto (falta $f), no se toca nada"; exit 4; }
done

healthy() {
  curl -fsS -m 10 -o /dev/null http://127.0.0.1:8080/ \
    && curl -fsS -m 10 -o /dev/null http://127.0.0.1:8080/v1/meta
}

cd "$APP"
mkdir -p "$PREV" && chmod 700 "$PREV"
rsync -rlt --delete "${KEEP[@]}" "$APP"/ "$PREV"/
for img in chat-backend chat-frontend; do
  docker image inspect "$img:0.1.0" >/dev/null 2>&1 && docker tag "$img:0.1.0" "$img:prev" || true
done

rsync -rlt --delete "${KEEP[@]}" "$STAGE"/ "$APP"/
log "deploy $SHA: construyendo y levantando"
if docker compose up -d --build --wait --wait-timeout 180 >>"$LOG" 2>&1 && healthy; then
  echo "$SHA" > "$APP/.deployed_sha"
  docker image prune -f >/dev/null 2>&1 || true
  log "deploy $SHA: OK"
  exit 0
fi

log "deploy $SHA: FALLO, volviendo a la version anterior ($(cat "$APP/.deployed_sha" 2>/dev/null || echo desconocida))"
docker compose ps 2>&1 | tee -a "$LOG" || true
rsync -rlt --delete "${KEEP[@]}" "$PREV"/ "$APP"/
for img in chat-backend chat-frontend; do
  docker image inspect "$img:prev" >/dev/null 2>&1 && docker tag "$img:prev" "$img:0.1.0" || true
done
if docker compose up -d --no-build --wait --wait-timeout 120 >>"$LOG" 2>&1 && healthy; then
  log "deploy $SHA: version anterior restaurada"
else
  log "deploy $SHA: la version anterior tampoco responde, revisar el servidor"
fi
exit 1
