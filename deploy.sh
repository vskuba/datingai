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
# Без `-f`: базовый `docker-compose.yml` и есть прод. Локальные особенности живут
# в `docker-compose.override.yml`, которого на сервере нет — он в `.gitignore`, а
# из архива исключён ниже. Попади он туда, compose подхватил бы его сам, и прод
# поднялся бы с пустой локальной базой.
COMPOSE="docker compose"
PANEL_URL="${DEPLOY_PANEL_URL:-https://92.5.17.195.sslip.io/dating/health}"

BUILD=1
[ "${1:-}" = "--no-build" ] && BUILD=0

ARCHIVE="$(mktemp -t datingai-XXXX.tar.gz)"
trap 'rm -f "$ARCHIVE"' EXIT

echo "[1/4] Собираю архив…"
# `.env.prod` исключён намеренно: на сервере он свой — с паролем базы, токеном
# соседа и ключом сессии. Перезаписать его отсюда значит уронить прод.
#
# `.env` исключён по другой причине: он машинный, и на проде не нужен вовсе —
# базовый compose читает только `.env.prod`. Пока `.env` туда уезжал, на сервер
# вместе с ним ехали локальные токены разработчика, а прод-значение могло молча
# прийти из него: так на проде оказался локальный адрес phpMyAdmin.
tar czf "$ARCHIVE" \
    --exclude='.git' \
    --exclude='py_modules/.git' \
    --exclude='__pycache__' \
    --exclude='*.pyc' \
    --exclude='data/logs' \
    --exclude='data/*.log*' \
    --exclude='data/backups' \
    --exclude='data/seeds' \
    --exclude='.env.prod' \
    --exclude='.env.local' \
    --exclude='.env' \
    --exclude='docker-compose.override.yml' \
    .
echo "      $(du -h "$ARCHIVE" | cut -f1)"

echo "[2/4] Копирую на $HOST:$REMOTE_DIR…"
ssh "$HOST" "mkdir -p $REMOTE_DIR && tar xzf - -C $REMOTE_DIR" < "$ARCHIVE"

# Архив разворачивается поверх прежнего каталога и лишних файлов не убирает.
# Локальная накладка на сервере означала бы прод с пустой локальной базой, а
# `docker-compose.prod.yml` остался с тех пор, когда прод описывался накладкой;
# теперь он мёртвый и только путает того, кто зайдёт на сервер руками.
ssh "$HOST" "cd $REMOTE_DIR && rm -f docker-compose.override.yml docker-compose.prod.yml"

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
panel_ok=0
for attempt in $(seq 1 10); do
    code="$(curl -s -o /dev/null -w '%{http_code}' --max-time 20 "$PANEL_URL" || true)"
    if [ "$code" = "200" ]; then
        echo "  панель отвечает: HTTP 200"
        panel_ok=1
        break
    fi
    echo "  попытка $attempt: HTTP $code, жду…"
    sleep 6
done

if [ "$panel_ok" != "1" ]; then
    echo "  панель не ответила — смотрите: ssh $HOST 'docker logs dating_uvicorn --tail 40'"
    exit 1
fi

# Смена держателя сокетов после перезапуска.
#
# Само по себе восстановление есть (`chat_worker_recover` на старте сервера), но
# после выкладки его не хватает: пересоздание контейнеров обрывает текущее задание,
# а его ключ single-flight живёт ещё две с половиной минуты — и восстановление,
# увидев занятый ключ, честно ничего не делает. Задание при этом уже мёртвое
# («AbandonedJobError»), и живой чат молчит до следующего перезапуска.
#
# Проверено на живом проде: после `deploy.sh` смена не поднялась, и сокеты стояли.
#
# Ждать истечения ключа не годится — это две с половиной минуты простоя живого
# чата. Снимаем ключ сами: **здесь** это безопасно, потому что воркер только что
# пересоздан, и никакая прежняя смена его пережить не могла. В любом другом месте
# так делать нельзя — снятый ключ у работающей смены поднял бы вторую, и обе
# держали бы вторые соединения на те же анкеты, а сайт вышибает оба.
echo
echo "Поднимаю смену держателя сокетов…"
ssh "$HOST" "docker exec dating_uvicorn python3 -c \"
from src.worker.dating.chat_worker import (chat_worker_enqueue, chat_worker_flight_busy,
                                           chat_worker_flight_release)
was = chat_worker_flight_busy()
chat_worker_flight_release()
print('  ключ от прежней смены:', 'снят' if was else 'уже был свободен',
      '| смена поставлена:', chat_worker_enqueue())\"" || \
    echo "  не вышло поставить смену — проверьте: ssh $HOST 'docker logs dating_worker --tail 20'"
