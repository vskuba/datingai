"""История разговора уезжает агенту переменной, а не живёт сессией у соседа.

## ⚠⚠ Агент видел одну строку

Шаг `session_messages` у соседа умеет отдавать историю, но берёт её по
`session_uuid`, а мы его не передавали: на каждый вызов рождалась новая сессия, и
история в ней была пуста. Секция «История переписки» в промпте стоит под `%if%`,
поэтому не появлялась вовсе — и агент отвечал, видя **только** ту реплику, на
которую отвечает. Ни своего приглашения, которым начался разговор, ни того, что
человек писал вчера.

Проверено по базе соседа: у чат-агента три сессии за всё время, последняя от 2
сентября — следы ручных проверок из админки, а не работа.

## ⚠ Сессию у соседа держать не нужно

Запрос к LLM прошлого не помнит сам, а история у нас уже есть — в `message`,
вместе с перенесёнными приглашениями (`chat._invites_to_history`). Переменной она
уезжает туда, где нужна, и остаётся там, где ей место: в нашей базе, под нашими
правилами чистки.
"""

import uuid
from datetime import datetime, timedelta

import pytest
from mysql_.mysql_ import mysql_get_db_async

pytestmark = pytest.mark.chat

STAND_SITE = 999_343
UID = 'hist-707'


@pytest.fixture
async def girl(auth_client):
    profile_id = int((await auth_client.post('/api/dating/profile', json={
        'site_id': STAND_SITE,
        'login': f'talk-{uuid.uuid4().hex[:8]}@example.com',
        'password': 'x'})).json()['result'])

    yield profile_id

    async with mysql_get_db_async() as db:
        await db.execute('DELETE FROM message WHERE profile_id = %s', (profile_id,))
        await db.execute('DELETE FROM profile WHERE id = %s', (profile_id,))
        await db.connection.commit()


class TestTheLine:
    """Как выглядит одна реплика в промпте."""

    def test_ours_is_she(self):
        from src.dating.talk_history import talk_history_line

        got = talk_history_line({'direction': 'out', 'text': 'Привет!',
                                 'created_at': datetime(2026, 9, 16, 21, 4)})

        assert got == '[16.09 21:04] она: Привет!'

    def test_theirs_is_he(self):
        from src.dating.talk_history import talk_history_line

        got = talk_history_line({'direction': 'in', 'text': 'Улыбнулся',
                                 'created_at': datetime(2026, 9, 17, 8, 12)})

        assert got == '[17.09 08:12] он: Улыбнулся'

    def test_time_is_there(self):
        """⚠⚠ Время обязательно: без него «ответил через минуту» не отличить от
        «ответил через неделю», а это разные разговоры."""
        from src.dating.talk_history import talk_history_line

        assert '[' in talk_history_line(
            {'direction': 'in', 'text': 'да', 'created_at': datetime.now()})

    def test_newlines_are_flattened(self):
        """⚠ Перенос строки внутри реплики разорвал бы разметку промпта: он
        читается построчно, и одна реплика в две строки стала бы двумя."""
        from src.dating.talk_history import talk_history_line

        got = talk_history_line({'direction': 'in', 'text': 'первая\nвторая',
                                 'created_at': datetime(2026, 9, 17, 8, 12)})

        assert '\n' not in got
        assert 'первая вторая' in got

    def test_a_long_reply_is_cut(self):
        from src.dating.talk_history import TALK_HISTORY_TEXT_MAX, talk_history_line

        got = talk_history_line({'direction': 'in', 'text': 'я' * 5000,
                                 'created_at': datetime(2026, 9, 17, 8, 12)})

        assert len(got) < TALK_HISTORY_TEXT_MAX + 40


class TestTheOrder:
    """⚠⚠ Старое сверху: так читают диалог и так его понимает модель."""

    async def test_oldest_first(self, girl):
        from src.dating.talk_history import talk_history_text

        await _talk(girl, [('out', 'Спорим, ты улыбнулся?', 3),
                           ('in', 'Улыбнулся', 2),
                           ('out', 'Так и знала', 1)])

        lines = (await talk_history_text(girl, UID)).split('\n')

        assert 'Спорим' in lines[0], 'первой стоит не самая старая реплика'
        assert 'Так и знала' in lines[-1]

    async def test_the_tail_is_taken_not_the_head(self, girl):
        """⚠ Берём **последние** реплики, а не первые.

        `LIMIT` без `DESC` отобрал бы начало переписки — то есть приглашения
        месячной давности вместо живого разговора.
        """
        from src.dating.talk_history import talk_history_text

        await _talk(girl, [('out', f'реплика {n}', 20 - n) for n in range(20)])

        got = await talk_history_text(girl, UID, limit=3)

        assert 'реплика 19' in got, 'не взят конец разговора'
        assert 'реплика 0' not in got, 'взято начало вместо конца'

    async def test_the_limit_holds(self, girl):
        from src.dating.talk_history import talk_history_text

        await _talk(girl, [('in', f'строка {n}', 10 - n) for n in range(10)])

        assert len((await talk_history_text(girl, UID, limit=4)).split('\n')) == 4


class TestEdges:
    """Законные пустоты и сбои."""

    async def test_no_talk_is_empty(self, girl):
        """Первый разговор без приглашения — истории нет, и это не беда."""
        from src.dating.talk_history import talk_history_text

        assert await talk_history_text(girl, UID) == ''

    async def test_an_empty_uid_asks_nothing(self, girl):
        from src.dating.talk_history import talk_history_rows

        assert await talk_history_rows(girl, '') == []

    async def test_a_broken_database_gives_empty(self, girl, monkeypatch):
        """⚠ Автоответ без истории хуже, чем с историей, но лучше молчания."""
        from src.dating import talk_history

        def _broken():
            raise RuntimeError('база отвалилась')

        monkeypatch.setattr('mysql_.mysql_.mysql_get_db_async', _broken)

        assert await talk_history.talk_history_text(girl, UID) == ''

    async def test_another_companion_is_not_mixed_in(self, girl):
        """Разговор с другим человеком в эту историю не попадает."""
        from src.dating.talk_history import talk_history_text

        await _talk(girl, [('in', 'моё', 1)])
        await _talk(girl, [('in', 'чужое', 1)], uid='other-808')

        got = await talk_history_text(girl, UID)

        assert 'моё' in got and 'чужое' not in got


class TestItReachesTheAgent:
    """⚠ История и ключ собеседника обязаны доехать до вызова агента."""

    def test_the_answer_sends_history(self):
        import inspect

        from src.dating import answer

        code = inspect.getsource(answer.answer_run)

        assert 'talk_history_text(profile_id, uid)' in code, \
            'агент снова отвечает, видя одну реплику'

    def test_the_variables_are_named(self):
        import inspect

        from src.dating import answer

        code = inspect.getsource(answer._agent_ask)

        assert "'talk_history'" in code
        assert "'companion_uid'" in code, \
            'без ключа собеседника факты обо всех мужчинах сольются в один документ'

    async def test_the_length_is_a_site_setting(self, auth_client):
        got = (await auth_client.get('/api/dating/site')).json()['result']
        known = {row['key']: row for row in got['known']}

        assert 'chat_history_max' in known
        assert known['chat_history_max']['default'] == '20'


async def _talk(profile_id: int, rows: list, uid: str = UID) -> None:
    """Кладёт разговор: `(direction, text, часов назад)`."""
    async with mysql_get_db_async() as db:
        for direction, text, ago in rows:
            await db.execute(
                'INSERT INTO message'
                ' (profile_id, sender_uid, sender_name, text, message_uid,'
                '  direction, created_at)'
                " VALUES (%s, %s, '', %s, %s, %s, %s)",
                (int(profile_id), uid, text, f'm-{uuid.uuid4().hex[:12]}', direction,
                 datetime.now() - timedelta(hours=ago)))
        await db.connection.commit()
