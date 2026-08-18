# Деплой на прод

## 1. Где живёт

Один сервер Oracle Cloud, тот же, что у RhapsodyAi (`ssh rhapsody-prod`), каталог
`~/datingai`. Домен тоже общий — `92.5.17.195.sslip.io`, и делится он **по URI**:

| Путь | Проект |
|------|--------|
| `/` и всё остальное | RhapsodyAi |
| `/dating*` | **DatingAi** |

## 2. Чем прод отличается от локальной установки

Локально четыре сервиса, на проде два: своих MySQL и Redis там нет.

| | локально | прод |
|---|---|---|
| MySQL | свой контейнер `dating_mysql` | сервер RhapsodyAi, **своя база** `dating` со своим пользователем |
| Redis | свой контейнер `dating_redis` | тот же Redis, **своя база по номеру** (`/1`) |
| адрес соседа | `http://uvicorn:8000` | `https://92.5.17.195.sslip.io` |
| `BASE_PATH` | пусто | `/dating` |
| порт наружу | `8021` | не публикуется — только через Caddy |

Поднимать второй MySQL со вторым Redis на одном сервере значило бы платить памятью
за то, что уже работает. Данные при этом остаются раздельными: своя база, свой
пользователь, свои бэкапы — общий только процесс.

Разница описана в `docker-compose.prod.yml`; локальный `docker-compose.yml` не
меняется.

## 3. Как выложить

```bash
# 1. Код на сервер (rsync на сервере нет — архивом)
tar czf /tmp/datingai.tar.gz --exclude='.git' --exclude='py_modules/.git' \
  --exclude='__pycache__' --exclude='*.pyc' --exclude='data/log' .
ssh rhapsody-prod "mkdir -p ~/datingai && tar xzf - -C ~/datingai" < /tmp/datingai.tar.gz

# 2. Сборка и запуск (только два сервиса)
ssh rhapsody-prod "cd ~/datingai && \
  docker compose -f docker-compose.yml -f docker-compose.prod.yml build dating_uvicorn && \
  docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d dating_uvicorn dating_worker"
```

Схема накатывается сама при старте (`mysql_migration_up`), первый пользователь —
из `ADMIN_USERNAME` / `ADMIN_PASSWORD` в `.env.prod`, если таблица пуста.

`.env.prod` в репозиторий не попадает: там пароль базы, токен соседа и ключ сессии.
На сервер он кладётся вручную, один раз.

## 4. Маршрут в Caddy

Прокси у RhapsodyAi, конфиг — `docker/caddy/Caddyfile` **в его репозитории**:

```
handle_path /dating* {
    reverse_proxy dating_uvicorn:8000 {
        lb_try_duration 90s
        lb_try_interval 2s
    }
}
```

`handle_path`, а не `handle`: префикс **срезается**, и приложение видит обычные пути
от корня. Обратная сторона — в разметке пути обязаны идти с префиксом, иначе браузер
попросит статику у админки; этим занимается `BASE_PATH`.

Оба контейнера должны быть в общей сети `rhapsodyai_os-net` — она внешняя для
DatingAi и объявлена в его compose.

## 5. Грабли, на которые уже наступили

**Bind-mount одного файла ломается при замене.** Caddyfile смонтирован файлом, а не
каталогом. Если файл на хосте **заменить** (`cat >`, `sed -i`, копирование), меняется
inode — и контейнер продолжает видеть старое содержимое. `caddy reload` при этом
честно отвечает «config is unchanged». Лечится перезапуском контейнера:
`docker restart caddy`.

**Backend в репозитории — не тот, что на сервере.** `bluegreen.sh` переписывает
`reverse_proxy` под активный слот (`uvicorn` ↔ `uvicorn_green`). Копировать Caddyfile
из репозитория поверх серверного нельзя: админка уедет на погашенный слот и повиснет
на 90 секунд ретраев. Править надо **точечно**, сохраняя текущую строку backend.

**`${REDIS_URL}` в `command` берётся не из `env_file`.** Compose подставляет
переменные из `.env` проекта, про `.env.prod` он не знает. Адрес должна разворачивать
оболочка внутри контейнера: `sh -c 'rq worker --url "$$REDIS_URL" dating'`.

**`replicas: 0` не убирает зависимость.** Сервис гаснет, но `depends_on` на него
остаётся, и compose отказывается поднимать — «is missing dependency». Нужно снимать
и саму ссылку: `depends_on: !reset []`.
