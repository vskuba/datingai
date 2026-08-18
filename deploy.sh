#!/usr/bin/env bash
#
# Выкладка DatingAi на прод.
#
# Зачем скрипт, а не три команды руками: их три только на бумаге. На деле каждый
# раз всплывает одно и то же — не забыть исключить `.env.prod` (он на сервере свой),
# не утащить логи и `.git`, собрать образ до подъёма контейнеров, дождаться ответа
# панели. Забытый шаг заметен не сразу, а через минуту в виде «почему не обновилось».
#
# Сервер один на два проекта: DatingAi живёт в `~/datingai`, RhapsodyAi — рядом.
# Наружу панель смотрит через его Caddy, поэтому здесь портов не публикуется.
#
# Использование:
#   ./deploy.sh              — выложить и перезапустить
#   ./deploy.sh --no-build   — только код, без пересборки образа (быстро)
#
# `rsync` на сервере нет — переносим архивом.

set -euo pipefail

HOST="${DEPLOY_HOST:-rhapsody-prod}"
REMOTE_DIR="${DEPLOY_DIR:-~/datingai}"
COMPOSE="docker compose -f docker-compose.yml -f docker-compose.prod.yml"
PANEL_URL="${DEPLOY_PANEL_URL:-https://92.5.17.195.sslip.io/dating/health}"

BUILD=1
[ "${1:-}" = "--no-build" ] && BUILD=0

ARCHIVE="$(mktemp -t datingai-XXXX.tar.gz)"
trap 'rm -f "$ARCHIVE"' EXIT

echo "[1/4] Собираю архив…"
# `.env.prod` исключён намеренно: на сервере он свой — с паролем базы, токеном
# соседа и ключом сессии. Перезаписать его отсюда значит уронить прод.
tar czf "$ARCHIVE" \
    --exclude='.git' \
    --exclude='py_modules/.git' \
    --exclude='__pycache__' \
    --exclude='*.pyc' \
    --exclude='data/log' \
    --exclude='data/*.log*' \
    --exclude='.env.prod' \
    --exclude='.env.local' \
    .
echo "      $(du -h "$ARCHIVE" | cut -f1)"

echo "[2/4] Копирую на $HOST:$REMOTE_DIR…"
ssh "$HOST" "mkdir -p $REMOTE_DIR && tar xzf - -C $REMOTE_DIR" < "$ARCHIVE"

if [ "$BUILD" = "1" ]; then
    echo "[3/4] Собираю образ…"
    ssh "$HOST" "cd $REMOTE_DIR && $COMPOSE build dating_uvicorn" | tail -3
else
    echo "[3/4] Сборка пропущена (--no-build)"
fi

echo "[4/4] Поднимаю сервисы…"
# Только два: база и очередь на проде чужие — их поднимать нечего.
ssh "$HOST" "cd $REMOTE_DIR && $COMPOSE up -d --force-recreate dating_uvicorn dating_worker" | tail -4

echo
echo "Проверяю панель…"
for attempt in $(seq 1 10); do
    code="$(curl -s -o /dev/null -w '%{http_code}' --max-time 20 "$PANEL_URL" || true)"
    if [ "$code" = "200" ]; then
        echo "  панель отвечает: HTTP 200"
        exit 0
    fi
    echo "  попытка $attempt: HTTP $code, жду…"
    sleep 6
done

echo "  панель не ответила — смотрите: ssh $HOST 'docker logs dating_uvicorn --tail 40'"
exit 1
