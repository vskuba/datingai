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

import httpx
import pytest

# Поддельный сосед. Фикстура объявлена здесь, чтобы её было видно из любого теста
# без импорта: то же удобство, что у `auth_client`.
from tests.rhapsody_fake import rhapsody  # noqa: F401 — фикстура

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
