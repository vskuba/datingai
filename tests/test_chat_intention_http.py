"""Ручки вкладки «Намерения и действия».

Две ручки на три колонки: пары отдельно, строки — одной ручкой с доводом
`status`. Своя дверь, а не хвост чата: вкладка живёт в «Чате», но намерения
приезжают и из писем.
"""
import uuid

import pytest

from src.dating.chat_intention import chat_intention_put
from tests.conftest import TEST_STAND_SITE

pytestmark = pytest.mark.core

PROMISE = 'she_promised_send_photo'
PAIRS = '/api/dating/chat-intention/pairs'
ROWS = '/api/dating/chat-intention'


def _uid() -> str:
    return f'cih-{uuid.uuid4().hex[:8]}'


class TestThePairsHandle:
    """`GET /api/dating/chat-intention/pairs`."""

    async def test_it_answers_with_the_pager_shape(self, auth_client, girl):
        await chat_intention_put(girl, TEST_STAND_SITE, _uid(), [PROMISE])

        got = await auth_client.get(
            f'{PAIRS}?site_id={TEST_STAND_SITE}&profile_id={girl}')

        assert got.status_code == 200
        body = got.json()
        assert body['total'] == 1
        assert body['page'] == 1 and body['pages'] == 1
        assert body['result'][0]['open_count'] == 1

    async def test_without_a_site_it_is_empty_not_an_error(self, auth_client):
        """⚠ Ноль — «сайт не выбран», а не поломка: селект пуст, пока едет его
        список, и красная ошибка на пустом месте пугала бы зря."""
        got = await auth_client.get(PAIRS)

        assert got.status_code == 200
        assert got.json()['result'] == []


class TestTheRowsHandle:
    """`GET /api/dating/chat-intention`."""

    async def test_status_open_gives_the_pool(self, auth_client, girl):
        uid = _uid()
        await chat_intention_put(girl, TEST_STAND_SITE, uid, [PROMISE])

        got = await auth_client.get(
            f'{ROWS}?site_id={TEST_STAND_SITE}&profile_id={girl}&status=open')

        assert got.status_code == 200
        body = got.json()
        assert [row['intention'] for row in body['result']] == [PROMISE]
        assert body['result'][0]['uid'] == uid

    async def test_status_done_is_empty_while_nobody_executes(self, auth_client,
                                                              girl):
        """Исполнителя нет — исполненных не бывает. Вкладка об этом и говорит."""
        await chat_intention_put(girl, TEST_STAND_SITE, _uid(), [PROMISE])

        got = await auth_client.get(
            f'{ROWS}?site_id={TEST_STAND_SITE}&profile_id={girl}&status=done')

        assert got.json()['result'] == []

    async def test_it_narrows_to_one_companion(self, auth_client, girl):
        mine = _uid()
        await chat_intention_put(girl, TEST_STAND_SITE, mine, [PROMISE])
        await chat_intention_put(girl, TEST_STAND_SITE, _uid(), [PROMISE])

        got = await auth_client.get(
            f'{ROWS}?site_id={TEST_STAND_SITE}&profile_id={girl}&uid={mine}')

        assert [row['uid'] for row in got.json()['result']] == [mine]

    async def test_a_stranger_is_turned_away(self, client, girl):
        """Без входа — отказ. Права как у пулов: всем **вошедшим**."""
        got = await client.get(f'{ROWS}?site_id={TEST_STAND_SITE}')

        assert got.status_code in (401, 403)


DO_GET = f'/api/dating/site/{TEST_STAND_SITE}/chat-intention-do'
DO_PUT = f'/api/dating/site/{TEST_STAND_SITE}/chat-intention-do-on'


class TestTheDoHandles:
    """Выключатель «выполнять действия»: прочитать и переключить."""

    async def test_it_reads_the_flag(self, auth_client):
        await auth_client.put(DO_PUT, json={'chat_intention_do_on': False})

        got = await auth_client.get(DO_GET)

        assert got.status_code == 200
        assert got.json()['result'] == {'site_id': TEST_STAND_SITE,
                                        'chat_intention_do_on': False}

    async def test_it_raises_and_lowers(self, auth_client):
        try:
            put = await auth_client.put(DO_PUT, json={'chat_intention_do_on': True})

            assert put.status_code == 200
            assert (await auth_client.get(DO_GET)).json()['result'][
                'chat_intention_do_on'] is True
        finally:
            await auth_client.put(DO_PUT, json={'chat_intention_do_on': False})

        assert (await auth_client.get(DO_GET)).json()['result'][
            'chat_intention_do_on'] is False

    async def test_a_site_without_a_row_is_off_not_404(self, auth_client):
        """Нет строки сайта — выключено, а не «такого сайта нет»."""
        got = await auth_client.get('/api/dating/site/999111/chat-intention-do')

        assert got.status_code == 200
        assert got.json()['result']['chat_intention_do_on'] is False

    async def test_the_column_is_in_the_white_list(self):
        """⚠⚠ Имя столбца уезжает в SQL текстом, и `site_flag_put` проверяет его
        утверждением. Забудь мы имя в списке — ручка падала бы 500-й, а тест на
        ответ 200 этого бы не поймал: он падал бы по другой причине."""
        from src.uvicorn.routers.dating.site_switch import SITE_FLAGS

        assert 'chat_intention_do_on' in SITE_FLAGS

    async def test_a_stranger_is_turned_away(self, client):
        got = await client.get(DO_GET)

        assert got.status_code in (401, 403)
