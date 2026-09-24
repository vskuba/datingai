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
        # ⚠ И письма: канала два, и мусор второго так же мешал бы соседним
        # проверкам, как мусор первого.
        for table in ('mail_in', 'mail_reply', 'mailing', 'favorite_mail'):
            await db.execute(f'DELETE FROM {table} WHERE profile_id = %s',
                             (profile_id,))
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
        """⚠ Обрезок свой у каждого канала: реплика в чате короткая, письмо —
        три абзаца, и общая тысяча знаков отрезала бы письму конец."""
        from src.dating.talk_history import (TALK_HISTORY_CHANNELS,
                                             TALK_HISTORY_CHAT,
                                             talk_history_line)

        cut = TALK_HISTORY_CHANNELS[TALK_HISTORY_CHAT]['text_max']
        got = talk_history_line({'direction': 'in', 'text': 'я' * 5000,
                                 'created_at': datetime(2026, 9, 17, 8, 12)}, cut)

        assert len(got) < cut + 40


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

    def test_both_histories_are_sent(self):
        """⚠⚠ Обе истории, и **всегда** — чат и письма.

        С человеком говорят двумя каналами, а отношения одни. Отдай мы половину,
        письмо писалось бы, не помня вчерашнего чата, а ответ в чат — не помня
        переписки.
        """
        import inspect

        from src.dating import answer

        code = inspect.getsource(answer._agent_ask)

        assert "'talk_history_chat'" in code, 'история чата не уезжает агенту'
        assert "'talk_history_mail'" in code, 'история писем не уезжает агенту'
        # ⚠⚠ Ключ собеседника здесь **не проверяется, и это правка, а не
        # послабление**. Прежде уходил `companion_uid` — номер человека на сайте
        # знакомств. У соседа своя запись собеседника, и её номер он знает из
        # поля запроса (`companion_id`); два ключа на одного человека расходятся
        # при первой же правке, а факты ключуются тем, что у соседа.
        assert "'companion_uid'" not in code, \
            'номер с сайта снова уезжает агенту — ключ фактов у соседа свой'

    def test_the_history_is_taken_in_one_place(self):
        """⚠ Историю берёт сам `_agent_ask`, а не тот, кто его зовёт.

        Звали три места — чат, инициатива, письмо, — и каждое спрашивало её
        само. С появлением второго канала три копии разошлись бы: одно место
        отдавало бы обе истории, другое по-прежнему чат.
        """
        import inspect

        from src.dating import answer

        for name in ('answer_run', 'answer_mail_write', 'answer_mail_reply'):
            code = inspect.getsource(getattr(answer, name))

            assert 'talk_history_text(' not in code, \
                f'{name} снова добывает историю сам — копия разойдётся'

    async def test_the_length_is_a_site_setting(self, auth_client):
        got = (await auth_client.get('/api/dating/site')).json()['result']
        known = {row['key']: row for row in got['known']}

        assert 'chat_history_max' in known
        assert known['chat_history_max']['default'] == '20'


class TestTheMailChannel:
    """⚠⚠ Письма — второй канал, и собирается он из четырёх источников.

    Знание переехало из `test_mail_reply.TestHistory`: прежде переписку собирал
    круг ответов своим запросом, знавшим про три таблицы. Четвёртой —
    `favorite_mail` — он не знал вовсе, хотя письма избранным уходят каждый день:
    отвечая на входящее, агент не видел собственного письма, которым этот
    разговор и начался.
    """

    async def test_incoming_and_our_replies(self, girl):
        """⚠ Из двух сторон: возьми мы только входящие, агент не знал бы, что уже
        сказал, и здоровался бы второй раз."""
        from src.dating.talk_history import TALK_HISTORY_MAIL, talk_history_text

        await _mail(girl, [('mail_in', 'привет', 3),
                           ('mail_reply', 'и тебе привет', 2),
                           ('mail_in', 'как дела?', 1)])

        lines = (await talk_history_text(girl, UID, TALK_HISTORY_MAIL)).split('\n')

        # ⚠ Роль ищем по «] он:» / «] она:», а не разбором по двоеточию: оно
        # есть и во времени, и первая версия проверки читала минуты как роль.
        who = ['она' if '] она:' in one else 'он' for one in lines]

        assert who == ['он', 'она', 'он'], 'наш ответ выпал из переписки'

    async def test_the_mailing_letter_starts_it(self, girl):
        """⚠ Разговор чаще всего начинали мы: ушло письмо рассылки, он ответил.

        Без него агент видит переписку с середины — «Moto is my life» без
        вопроса, на который это ответ.
        """
        from src.dating.talk_history import TALK_HISTORY_MAIL, talk_history_text

        await _mail(girl, [('mailing', 'Чем увлекаешься?', 3),
                           ('mail_in', 'Moto is my life', 2)])

        got = await talk_history_text(girl, UID, TALK_HISTORY_MAIL)

        assert got.split('\n')[0].endswith('Чем увлекаешься?'), \
            'письмо рассылки не попало в историю или встало не первым'

    async def test_the_favourite_letter_is_there(self, girl):
        """⚠⚠ Четвёртый источник, которого прежний сбор не знал вовсе."""
        from src.dating.talk_history import TALK_HISTORY_MAIL, talk_history_text

        await _mail(girl, [('favorite_mail', 'Давно не писал', 3),
                           ('mail_in', 'Замотался', 2)])

        got = await talk_history_text(girl, UID, TALK_HISTORY_MAIL)

        assert 'Давно не писал' in got, \
            'письмо избранному не попало в переписку — агент ответит вслепую'

    async def test_a_draft_is_not_said(self, girl):
        """⚠⚠ Только **ушедшее**. Черновик из пула человек не читал, и считать
        его сказанным значило бы отвечать на разговор, которого не было."""
        from src.dating.talk_history import TALK_HISTORY_MAIL, talk_history_text

        await _mail(girl, [('mailing', 'черновик в пуле', 3, 'draft'),
                           ('mail_in', 'Moto is my life', 2)])

        got = await talk_history_text(girl, UID, TALK_HISTORY_MAIL)

        assert 'черновик в пуле' not in got, \
            'неотправленное письмо попало в разговор'

    async def test_the_channels_do_not_mix(self, girl):
        """⚠ Каналы не сливаются: у чата свой список, у писем свой."""
        from src.dating.talk_history import (TALK_HISTORY_CHAT,
                                             TALK_HISTORY_MAIL,
                                             talk_history_text)

        await _talk(girl, [('in', 'реплика в чате', 2)])
        await _mail(girl, [('mail_in', 'письмо на почте', 2)])

        chat = await talk_history_text(girl, UID, TALK_HISTORY_CHAT)
        mail = await talk_history_text(girl, UID, TALK_HISTORY_MAIL)

        assert 'реплика в чате' in chat and 'письмо на почте' not in chat
        assert 'письмо на почте' in mail and 'реплика в чате' not in mail


class TestTheLimitIsCapped:
    """⚠⚠ Потолок поверх настройки: в реестре у обеих стоит только `min: 1`.

    Человек вправе вписать 500, и промпт стал бы простынёй, на которую модель
    отвечает первым, что увидит, — платили бы мы за это на каждом вызове.
    """

    async def test_a_huge_setting_is_capped(self, monkeypatch):
        from src.dating.talk_history import (TALK_HISTORY_CHANNELS,
                                             TALK_HISTORY_MAIL,
                                             talk_history_max)
        import src.uvicorn.routers.dating.site_ as site_mod

        async def huge(site_id, key):
            return 5000

        monkeypatch.setattr(site_mod, 'site_seconds', huge)

        got = await talk_history_max(STAND_SITE, TALK_HISTORY_MAIL, by_profile=False)

        assert got == TALK_HISTORY_CHANNELS[TALK_HISTORY_MAIL]['cap']


async def _mail(profile_id: int, rows: list, uid: str = UID) -> None:
    """Кладёт переписку письмами: `(таблица, текст, часов назад[, статус])`."""
    last_in = None
    async with mysql_get_db_async() as db:
        for row in rows:
            table, body, ago = row[0], row[1], row[2]
            status = row[3] if len(row) > 3 else 'sent'
            at = datetime.now() - timedelta(hours=ago)

            if table == 'mail_in':
                await db.execute(
                    'INSERT INTO mail_in (site_id, profile_id, message_id, uid,'
                    ' name, body, date_post)'
                    " VALUES (%s, %s, %s, %s, '', %s, %s)",
                    (STAND_SITE, int(profile_id),
                     int(uuid.uuid4().int % 10**9), uid, body, at))
                last_in = db.lastrowid
            elif table == 'mail_reply':
                # ⚠ Ответ ссылается на письмо, на которое отвечает
                # (`fk_dreply_in`): без ссылки строка не заводится вовсе.
                await db.execute(
                    'INSERT INTO mail_reply (site_id, profile_id, mail_in_id,'
                    ' uid, name, body, status, sent_at, created_at)'
                    " VALUES (%s, %s, %s, %s, '', %s, %s, %s, %s)",
                    (STAND_SITE, int(profile_id), last_in, uid, body, status,
                     at if status == 'sent' else None, at))
            else:
                await db.execute(
                    f'INSERT INTO {table} (site_id, profile_id, uid, name, body,'
                    ' status, sent_at, created_at)'
                    " VALUES (%s, %s, %s, '', %s, %s, %s, %s)",
                    (STAND_SITE, int(profile_id), uid, body, status,
                     at if status == 'sent' else None, at))
        await db.connection.commit()

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
