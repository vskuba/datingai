# Быстрый старт

## 1. Что нужно до запуска

Поднятый **RhapsodyAi**: без него панель откроется, но работать не сможет — вход
на сайт, ответы агента и адрес чат-сокета берутся оттуда. Проверить связь можно
после старта (см. раздел 4).

Общая сеть тоже его: `rhapsodyai_os-net` создаётся его `docker-compose`. Если
сеть не поднята, `docker compose up` здесь упадёт на её отсутствии — это не сбой
конфигурации, а честное «соседа нет».

## 2. Запуск

```bash
cp .env.dist .env          # заполнить RHAPSODY_TOKEN и SESSION_SECRET_KEY
docker compose up -d
```

Схема накатывается на старте (`mysql_migration_up`): установка из чистого
репозитория поднимается одной командой, отдельного шага «накатить базу» нет.

Первый пользователь заводится тогда же и **только если таблица пуста** —
из `ADMIN_USERNAME` / `ADMIN_PASSWORD`. Не «создать или обновить»: иначе правка
пароля в `.env` молча меняла бы его у живого пользователя, а забытый в образце
`change-me` возвращался бы после каждого перезапуска.

Панель: `http://localhost:8021` (порт задаётся `UVICORN_PORT`).

## 3. Переменные окружения

Полный список — в `.env.dist`, здесь только то, что нужно понимать.

| Ключ | Зачем |
|------|-------|
| `RHAPSODY_URL` | Адрес соседа: имя сервиса в общей сети, не `localhost` |
| `RHAPSODY_TOKEN` | `user_access_token` учётки `dating_service` в RhapsodyAi |
| `REDIS_URL` | **Именно URL.** Общая библиотека читает эту переменную и по умолчанию берёт `redis://redis:6379` — Redis соседа. Без явного значения смены держателя уходят в чужую очередь |
| `MYSQL_HOST` | `dating_mysql` — своя база, не соседа |
| `SESSION_SECRET_KEY` | Подпись сессионной куки; на рабочей установке свой |
| `ADMIN_USERNAME` / `ADMIN_PASSWORD` | Первый пользователь панели |

Права токена в RhapsodyAi: `browser:view`, `browser:create` (водить браузер),
`agent:view`, `agent:create` (заводить агента анкеты). Больше не нужно.

## 4. Проверка после запуска

```bash
# сервисы
docker compose ps

# связь с соседом изнутри контейнера
docker exec dating_uvicorn python3 -c "
import asyncio
from src.rhapsody.rhapsody_api import rhapsody_api_site_list
print(asyncio.run(rhapsody_api_site_list()))"

# вход в панель
curl -s -c /tmp/c.txt -X POST http://localhost:8021/auth/login \
  -H 'Content-Type: application/json' \
  -d '{"username":"admin","password":"<пароль>"}'

# список анкет
curl -s -b /tmp/c.txt http://localhost:8021/api/dating/girl
```

Смена держателя сокетов ставится сама при старте сервера. Проверить, что она
идёт:

```bash
docker exec dating_redis redis-cli get "chat_worker:flight"   # 1 — держатель работает
docker logs dating_worker --tail 20
```

## 5. Частые грабли

**«Сообщение не принимается: собеседник вышел из чата».** Присутствие в чате
держит воркер своим heartbeat. Если смена не идёт, сайт считает анкету вне чата и
отклоняет отправку. Смотреть `chat_worker:flight` и журнал воркера.

**Первое письмо не пришло.** Сообщение, отправленное до подключения сокета, лежит
в очереди сайта и забирается запросом истории по списку собеседников. У анкеты,
которой никто не писал, список пуст — поэтому он собирается и из `dating_contact`
(тех, кого сайт показал в чате), а не только из тех, кто уже писал.

**Изменения `.env` не подхватились.** `docker compose restart` сохраняет прежнее
окружение — нужен `docker compose up -d --force-recreate`.
