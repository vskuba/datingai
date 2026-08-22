"""Общая обвязка тестов.

Сюита ходит по HTTP в **запущенную** панель, а не поднимает приложение в памяти.
Причина простая: половина того, что стоит проверять, живёт не в коде — сессия в
куке, срез префикса прокси, ответ соседа. Тест против объекта `app` их не увидит.

Поэтому перед прогоном панель должна работать: `docker compose up -d`. Адрес
задаётся `BASE_URL` (по умолчанию локальный порт из compose).
"""

import os

import httpx
import pytest

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
