# CLAUDE.md

## Project Overview

**DatingAi** — панель анкет на сайтах знакомств. Один вход: `main_uvicorn.py`
(FastAPI + серверные страницы) и один воркер (`rq`, очередь `dating`).

Проект делает ровно одно: **держит учётки девушек, показывает их страницей и ведёт
живой чат вебсокетом**. Всё, что требует браузера и мозгов, живёт в соседнем
проекте **RhapsodyAi** и приходит сюда по HTTP:

| Что | Где живёт | Как получаем |
|-----|-----------|--------------|
| сценарии сайтов, пул Chromium | RhapsodyAi | `POST /api/browser/scenario/{id}/run` |
| закладки сайтов и `presence` | RhapsodyAi | `GET /api/browser/site/{id}` |
| агенты, workflow, факты | RhapsodyAi | `POST /agent/create`, `GET /agent/name/{name}` |
| ответы в чате (LLM) | RhapsodyAi | `POST /chat/ask` |
| учётки, переписка, журнал | **здесь** | своя база `dating` |

Единственная дверь наружу — `src/rhapsody/rhapsody_api.py`. Прямых обращений к
чужим таблицам в коде нет и быть не должно: своей копии сайтов, сценариев и
агентов здесь не заводится.

`py_modules/` — общая библиотека (отдельный репозиторий) | `src/` — код проекта

См. `.claude/docs/quickstart.md` — запуск и переменные окружения.

**Универсальные правила — в `.claude/docs-common/`** (индекс:
`.claude/docs-common/readme.md`). Там правила без привязки к проекту: именование,
контракт API, права, схема БД, деплой, тесты, формат документации.
`.claude/docs/` описывает, как это реализовано именно здесь. При расхождении
конкретика из `.claude/docs/` — актуальнее.

## Development Patterns

- **Новая ручка API** → `.claude/docs/api.md`
- **Обращение к RhapsodyAi** → `.claude/docs/rhapsody_api.md`
- **Правка страницы** → `.claude/docs/dashboard_frontend.md`
- **Изменение схемы** → `.claude/docs/migration.md` (новый файл в `migrations/`, применённые не правят)
- **Работа воркера** → `.claude/docs/worker.md`
- **Проверка после правок** → `.claude/docs/testing.md`

## Key Files

| File | Purpose |
|------|---------|
| `main_uvicorn.py` | Точка входа: middleware, схема на старте, роутеры |
| `src/rhapsody/rhapsody_api.py` | **Единственная** дверь к RhapsodyAi |
| `src/dating/dating_chat.py` | Приём сообщений вебсокетом |
| `src/dating/dating_chat_hold.py` | Держатель сокетов: присутствие и автоответ |
| `src/dating/dating_send.py` | Отправка сообщения кадром сокета |
| `src/dating/dating_answer.py` | Ответ агента: кого спросить и что отправить |
| `src/dating/dating_agent.py` | Агент анкеты: имя, биография, заведение у соседа |
| `src/dating/dating_check.py` | Полная проверка учётки |
| `src/dating/dating_login.py` | Вход под анкетой: сценарий, состояние, проверка |
| `src/dating/dating_revive.py` | Автовосстановление входа: лестница попыток |
| `src/dating/dating_json.py` | Общее чтение JSON-столбцов базы |
| `src/dating/dating_export.py` | Выгрузка анкет: сшивка `extract` сценария в таблицу |
| `src/uvicorn/routers/dating/dating_profile.py` | Все ручки анкет |
| `src/uvicorn/routers/dating/dating_export.py` | Ручка экспорта: сценарий выгрузки у соседа |
| `src/uvicorn/routers/dashboard.py` | Страницы панели |
| `src/uvicorn/routers/seed.py` | Seed SQL: выгрузка таблиц и заливка обратно |
| `src/uvicorn/routers/backup.py` | Бэкапы: дамп базы и восстановление |
| `src/uvicorn/routers/users.py` | Пользователи: учётки, роли, токены доступа |
| `data/uvicorn/template/base.html` | Шапка панели: разделы и подпункты |
| `src/auth/auth.py` | Вход в панель: сессия или токен, две роли |
| `src/auth/auth_users.py` | Управление людьми: кого и что менять нельзя |
| `src/worker/dating/chat_worker.py` | Смена держателя, очередь `dating` |
| `migrations/20260101000000_initial_schema.sql` | Начальная схема БД; заморожена, изменения — новыми файлами рядом |

## Docs Reference

Две папки: `.claude/docs-common/` — универсальные правила (переносимы в любой
проект), `.claude/docs/` — устройство этой инсталляции.

### Проектная документация — `.claude/docs/`

| File | Contents |
|------|----------|
| `quickstart.md` | Запуск, порты, переменные окружения, первый вход |
| `architecture.md` | Из чего состоит проект, границы с RhapsodyAi, потоки данных |
| `rhapsody_api.md` | Контракт с соседом: какие ручки, каким токеном, что при отказе |
| `auth.md` | Вход в панель, токены, две роли и где они проверяются |
| `database.md` | Схема, порядок таблиц, `site_id` без внешнего ключа |
| `dating.md` | Предметная часть: присутствие, чат, отправка, диалоги, агент |
| `worker.md` | Смена держателя сокетов, single-flight, очередь |
| `dashboard_frontend.md` | Страницы панели: шапка, раскладка анкет, обновление, контракты |
| `api.md` | Все ручки проекта и их поведение |
| `migration.md` | Как менять схему и что делать с данными |
| `deploy.md` | Прод: URI на общем домене, чужие MySQL и Redis, маршрут в Caddy, грабли |
| `testing.md` | Сюита по HTTP, переменные прогона, что она уже нашла |
