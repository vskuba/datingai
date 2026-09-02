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

import asyncio
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

from mysql_.mysql_ import mysql_get_db_async, mysql_pool_close  # noqa: E402

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


# ── Сторож сети ──────────────────────────────────────────────────────────────
#
# ⚠ **Сюита не имеет права ходить наружу. Ни к соседу, ни на сайт знакомств, ни
# куда-либо ещё.**
#
# Тест, которому нужен живой RhapsodyAi, проверяет не нас: он краснеет от чужого
# перезапуска, от занятого пула вкладок, от переименованного сайта — и молчит про
# то, ради чего написан. Тест, ходящий на сайт знакомств, вдобавок **делает** там
# что-то: занимает вкладку, входит под анкетой, шлёт сообщение живому человеку.
#
# Запретить это обещанием нельзя: заглушку забывают, а забытая заглушка не видна —
# тест зеленеет, просто медленнее. Поэтому сторож стоит на сокете и падает на
# попытке уйти на чужой адрес, называя тест и адрес.
#
# Разрешено ровно три вещи, и все три — своя установка:
#   * панель по HTTP (`BASE_URL`) — часть сюиты ходит в неё, это документировано;
#   * своя MySQL и свой Redis — база настоящая намеренно, подделка проверяла бы
#     подделку.
# Всё прочее — ошибка, даже если адрес выглядит безобидно.
_NET_ALLOWED_PORTS = {3306, 3313, 6379}


def _net_allowed(address) -> bool:
    """Свой ли это адрес. Всё, что не своё, — запрещено."""
    if not isinstance(address, tuple) or len(address) < 2:
        # Юникс-сокеты и прочее не сетевое: наружу по ним не уйти.
        return True

    host, port = str(address[0]), int(address[1] or 0)

    if port in _NET_ALLOWED_PORTS:
        return True

    # Панель: порт из `BASE_URL`, хост только локальный.
    if host in {'127.0.0.1', 'localhost', '::1'} and port == _BASE_PORT:
        return True

    return False


_BASE_PORT = int((BASE_URL.rsplit(':', 1) + ['80'])[1].split('/')[0])


@pytest.fixture(autouse=True)
def no_outside_calls(monkeypatch, request):
    """Падает, если тест пытается открыть соединение за пределы своей установки.

    Сторож в `conftest`, а не в отдельном тесте, по той же причине, что и запрет
    записи в живую переписку: нарушение здесь **невидимо**. Забытая заглушка не
    красит тест — он проходит, только идёт к чужому проекту и делает там работу.
    """
    real_connect = socket.socket.connect
    real_connect_ex = socket.socket.connect_ex

    def _check(address):
        if not _net_allowed(address):
            raise AssertionError(
                f'тест «{request.node.name}» полез наружу: {address}. '
                'Внешние вызовы обязаны идти через заглушку — `rhapsody_fake.py` '
                'для соседа, `site_api_fake.py` для API сайта; свой транспорт '
                'подменяется по образцу этих двух.')

    def _connect(self, address):
        _check(address)

        return real_connect(self, address)

    def _connect_ex(self, address):
        _check(address)

        return real_connect_ex(self, address)

    monkeypatch.setattr(socket.socket, 'connect', _connect)
    monkeypatch.setattr(socket.socket, 'connect_ex', _connect_ex)


# Анкеты, чью переписку сюита не имеет права трогать. ⚠ **Все, что были в базе до
# прогона.**
#
# Сторож ниже сверяет с этим списком, и вот зачем он завёлся: тест «строка без
# колонки читается как ведём» подменял прогон сценария, но не запись отправленного
# — и каждый прогон сюиты добавлял в переписку живой анкеты ещё одно «привет». К
# десятому прогону человек увидел десять приветов в диалоге и принял это за поломку
# автоответа. Смотрели в код автоответа, а виноваты были тесты.
#
# Заполняется один раз, при первом обращении: список анкет за прогон не меняется —
# кроме тех, что тесты заводят сами, а им писать и можно.
_PROFILES_LIVE: set[int] = set()


@pytest.fixture(autouse=True)
async def no_live_writes():
    """⚠ **Сюита не пишет в переписку живых анкет.**

    Проверяет после каждого теста: не появилось ли в `dating_message` строк с
    номером анкеты, которая была в базе до прогона. Своим анкетам (заведённым
    тестом) писать можно — их номера выше всех прежних.

    Сторож в `conftest`, а не в отдельном тесте, потому что нарушение здесь
    **невидимо**: тест зеленеет, база тихо обрастает мусором, и замечает это
    человек, глядя на живой диалог. Один раз так и вышло.

    Работает счётом строк, а не разбором: `SELECT` по индексу на 25 тысячах реплик
    — доли миллисекунды, и платить их за каждый тест дешевле, чем ещё раз объяснять
    десять приветов в живой переписке.
    """
    before = await _live_messages()

    yield

    after = await _live_messages()
    if after > before:
        # Не `assert`: падать должен тест, который написал, и с внятной причиной.
        # Номера в тексте — чтобы искать не пришлось.
        raise AssertionError(
            f'тест написал в переписку живой анкеты: строк было {before}, стало {after}. '
            'Подмените `_message_save` (см. фикстуру `saved` в test_profile_enabled.py) '
            'и берите выдуманный номер анкеты, а не живой.')


async def _live_messages() -> int:
    """Сколько сообщений у анкет, которые были в базе до прогона."""
    async with mysql_get_db_async() as db:
        if not _PROFILES_LIVE:
            await db.execute('SELECT id FROM dating_profile')
            _PROFILES_LIVE.update(int(row['id']) for row in await db.fetchall() or [])

        if not _PROFILES_LIVE:
            return 0

        await db.execute('SELECT COUNT(*) AS n FROM dating_message '
                         f"WHERE profile_id IN ({','.join(['%s'] * len(_PROFILES_LIVE))})",
                         tuple(_PROFILES_LIVE))

        return int(((await db.fetchone()) or {}).get('n') or 0)


@pytest.fixture(autouse=True)
async def db_pool():
    """Гасит фоновые задачи и закрывает пул соединений после каждого теста.

    ⚠ **Пул привязан к циклу событий, а цикл у каждого теста свой.** Пул живёт
    глобальной переменной в `mysql_`: первый тест, позвавший базу, создаёт его на
    своём цикле, а второй получает соединения от цикла, которого уже нет, — и
    падает с «Event loop is closed» на ровном месте. Так и было, сразу же, как
    тесты стали звать обработчики в процессе.

    ⚠ **Сначала задачи, потом пул, и порядок здесь не переставить.** Ручки заводят
    фоновую работу намеренно (сверка списка сайтов, снятие списков собеседников) и
    не ждут её — в этом весь смысл. Брошенная задача держит соединение, а закрытие
    пула ждёт, пока его вернут: сюита вставала намертво, без единого сообщения.
    Поймано на первом же прогоне тестов снимка.

    Цена — новое соединение на тест; база локальная, это доли миллисекунды.
    Тестов, которые её вовсе не трогают, это не касается: гасить и закрывать нечего.
    """
    yield

    await _tasks_stop()
    await mysql_pool_close()


async def _tasks_stop() -> None:
    """Отменяет всё, что тест оставил работать в фоне, и дожидается конца.

    Ждать после отмены обязательно: отмена доходит до задачи только на следующем
    шаге цикла, и без ожидания она успевает пожаловаться уже после того, как тест
    прошёл.
    """
    mine = asyncio.current_task()
    tasks = [task for task in asyncio.all_tasks() if task is not mine and not task.done()]
    for task in tasks:
        task.cancel()
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)


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
