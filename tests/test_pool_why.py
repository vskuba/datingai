"""Шапка пула объясняет простой — и сама при этом открывается.

⚠ **Написано по поломке, которую сюита пропустила.** Правка добавила в три ручки
пулов поле `send_why`, а в одну из них — без импорта: вкладка «Фавориты →
Приставание» отвечала 500 на каждое открытие. Поймал это заказчик, а не тест,
потому что **ни один тест не открывал эти ручки** — ни ту, ни две соседние.

Отсюда и содержание файла: сперва «отвечает ли она вообще», и только потом
тонкости. Проверка «ручка не падает» выглядит наивной ровно до того дня, когда
падает ручка.
"""

import pytest

pytestmark = pytest.mark.mail

# Живой сайт поддельного соседа: настройки у него есть, и `send_why` считается
# по-настоящему. ⚠ Ничего не пишем — только читаем.
SITE = 343

POOLS = ('/api/dating/invite', '/api/dating/mailing', '/api/dating/favorite-mail')


class TestPoolsOpenAtAll:
    """⚠ Самое дешёвое, чего не было: ручка отвечает, а не падает."""

    @pytest.mark.parametrize('path', POOLS)
    async def test_it_answers(self, auth_client, path):
        got = await auth_client.get(path, params={'site_id': SITE})

        assert got.status_code == 200, f'{path} → {got.status_code}: {got.text[:200]}'

    @pytest.mark.parametrize('path', POOLS)
    async def test_it_answers_without_a_site(self, auth_client, path):
        """Сайт не выбран — пустой ответ, а не отказ: так открывается страница."""
        got = await auth_client.get(path, params={'site_id': 0})

        assert got.status_code == 200
        assert got.json()['result'] == []


class TestEachPoolExplainsItself:
    """Почему из пула ничего не уходит — вместе со списком, у всех троих."""

    @pytest.mark.parametrize('path', POOLS)
    async def test_the_reason_comes_with_the_list(self, auth_client, path):
        got = (await auth_client.get(path, params={'site_id': SITE})).json()

        assert 'send_why' in got, f'{path} не объясняет простой'
        assert isinstance(got['send_why'], str)

    @pytest.mark.parametrize('path', POOLS)
    async def test_no_site_no_reason(self, auth_client, path):
        """Без сайта объяснять нечего — но поле на месте: страница читает его всегда."""
        got = (await auth_client.get(path, params={'site_id': 0})).json()

        assert got.get('send_why') == ''

    async def test_the_favourites_keep_both_reasons(self, auth_client):
        """⚠ У избранных их **две**, и путать нельзя: `why` — почему пул пуст
        (набор), `send_why` — почему полный пул не уходит (отправка)."""
        got = (await auth_client.get('/api/dating/favorite-mail',
                                     params={'site_id': SITE})).json()

        assert 'why' in got and 'send_why' in got
