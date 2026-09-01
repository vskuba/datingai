"""Общая обвязка тестов.

Сюита ходит по HTTP в **запущенную** панель, а не поднимает приложение в памяти.
Причина простая: половина того, что стоит проверять, живёт не в коде — сессия в
куке, срез префикса прокси, права на странице. Тест против объекта `app` их не
увидит.

Поэтому перед прогоном панель должна работать: `docker compose up -d`. Адрес
задаётся `BASE_URL` (по умолчанию локальный порт из compose).

⚠ **К соседу сюита не ходит.** Ни один тест не должен зависеть от того, поднят ли
RhapsodyAi: чужой перезапуск красил бы наши тесты в красный, а прогон сценария ещё
и занимал бы вкладку в его общем пуле — то есть сюита писала бы в чужую систему.
Всё, что упирается в соседа, проверяется с поддельным (`rhapsody_fake.py`) и зовёт
обработчики **в процессе**: панель живёт в контейнере, и подменить в ней что-либо
снаружи нельзя.

Отсюда правило для новых тестов: **ручку, которая ходит к соседу, по HTTP не
дёргают.** По HTTP у таких ручек проверяется только то, что решается до входа в
обработчик, — вход и права.
"""

import os
import socket

# ⚠ **База: с хоста имя контейнера не резолвится.**
#
# `MYSQL_HOST` в `.env` — имя в сети compose (`dating_mysql`), и внутри контейнера
# оно верное. Тесты же гоняются с хоста, а там этого имени нет; наружу база выходит
# опубликованным портом (`MYSQL_PORT_HOST`).
#
# Правится **до всякого импорта**: `mysql_.mysql_` читает адрес на уровне модуля,
# то есть при первом же импорте чего угодно из `src/`. Поправь мы позже — поправили
# бы уже прочитанное.
#
# Проверкой, а не безусловно: в контейнере (если сюиту однажды запустят там) имя
# резолвится, и подменять его нечем и незачем.
def _mysql_reachable() -> None:
    """Адрес базы, годный оттуда, откуда её зовут."""
    host = os.getenv('MYSQL_HOST', 'mysql')
    try:
        socket.getaddrinfo(host, None)
    except socket.gaierror:
        os.environ['MYSQL_HOST'] = '127.0.0.1'
        os.environ['MYSQL_PORT'] = os.getenv('MYSQL_PORT_HOST', '3313')


_mysql_reachable()

import httpx  # noqa: E402 — после правки адреса базы
import pytest  # noqa: E402

from mysql_.mysql_ import mysql_pool_close  # noqa: E402

# Поддельный сосед. Фикстура объявлена здесь, чтобы её было видно из любого теста
# без импорта: то же удобство, что у `auth_client`.
from tests.rhapsody_fake import rhapsody  # noqa: E402,F401 — фикстура

# Поддельное API сайта знакомств. По той же причине и на тех же правах: наружу
# сюита не ходит ни в соседний проект, ни на сам сайт.
from tests.site_api_fake import site_api  # noqa: E402,F401 — фикстура

# Куда стучаться. На проде — `https://dating.92.5.17.195.sslip.io`: своё имя,
# корень домена, `BASE_PATH` при этом пуст — как локально.
BASE_URL = os.getenv('BASE_URL', 'http://localhost:8021').rstrip('/')

# Учётка панели. Значения по умолчанию — из `.env.dist`; на рабочей установке
# передаются окружением, чтобы пароль не лежал в репозитории.
ADMIN_USERNAME = os.getenv('ADMIN_USERNAME', 'admin')
ADMIN_PASSWORD = os.getenv('ADMIN_PASSWORD', 'change-me')

# Оператор анкет: вторая роль. Ему доступна страница анкет и её ручки, но не
# seed, бэкапы и база.
DATING_USERNAME = os.getenv('DATING_USERNAME', 'dating')
DATING_PASSWORD = os.getenv('DATING_PASSWORD', 'change-me')

# Сколько ждём ответа. Ручки, которые водят браузер соседа, сюда не входят —
# их сюита не трогает: они стоят минуту и требуют живого сайта знакомств.
TIMEOUT = 30.0


@pytest.fixture(autouse=True)
async def db_pool():
    """Закрывает пул соединений после каждого теста.

    ⚠ **Пул привязан к циклу событий, а цикл у каждого теста свой.** Пул живёт
    глобальной переменной в `mysql_`: первый тест, позвавший базу, создаёт его на
    своём цикле, а второй получает соединения от цикла, которого уже нет, — и
    падает с «Event loop is closed» на ровном месте. Так и было, сразу же, как
    тесты стали звать обработчики в процессе.

    Цена — новое соединение на тест; база локальная, это доли миллисекунды.
    Тестов, которые её вовсе не трогают, это не касается: закрывать нечего.
    """
    yield

    await mysql_pool_close()


@pytest.fixture
async def client():
    """Клиент без входа — им проверяется, что закрытое закрыто."""
    async with httpx.AsyncClient(base_url=BASE_URL, timeout=TIMEOUT,
                                 follow_redirects=False) as http:
        yield http


@pytest.fixture
async def auth_client():
    """Клиент с сессией. Не сошлось — тест падает здесь, а не в середине проверки."""
    async with httpx.AsyncClient(base_url=BASE_URL, timeout=TIMEOUT,
                                 follow_redirects=False) as http:
        response = await http.post('/auth/login', json={
            'username': ADMIN_USERNAME, 'password': ADMIN_PASSWORD})
        assert response.status_code == 200, (
            f'не вышло войти под {ADMIN_USERNAME}: {response.status_code} {response.text[:200]}')
        yield http


@pytest.fixture
async def dating_client():
    """Клиент под оператором анкет — им проверяется, что админское закрыто.

    Учётка заводится сама при старте панели (`auth_seed_operator`). Не вышло
    войти — тест падает здесь: «оператор не может открыть бэкапы» одинаково
    выглядит и когда права работают, и когда учётки просто нет.
    """
    async with httpx.AsyncClient(base_url=BASE_URL, timeout=TIMEOUT,
                                 follow_redirects=False) as http:
        response = await http.post('/auth/login', json={
            'username': DATING_USERNAME, 'password': DATING_PASSWORD})
        assert response.status_code == 200, (
            f'не вышло войти под {DATING_USERNAME}: {response.status_code} {response.text[:200]}')
        yield http
