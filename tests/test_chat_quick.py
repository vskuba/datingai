"""Быстрый ответ: шаблон в паузу, агент — по-своему.

## ⚠⚠ Зачем сюжет и почему тестов много

Правило: его реплика висит без ответа `chat_quick_after` секунд — в чат уходит
случайный шаблон («hi», смайлик). Это **не ответ**: агент продолжает сочинять
своим кругом, и два ответа на одно входящее — законный исход. Соседу-агенту о
быстрой строке не сообщается никогда.

Тест держит две половины правила, и вторая важнее первой:

1. **отбор** — кому шаблон уходит и кому не вправе (`chat_quick._waiting_pairs`:
   окно `[N, N + 300]`, никаких наших строк после повода — в том числе быстрых);
2. **агент не сломан** — быстрая строка лежит в `message` той же таблицей, что
   ответы, и если отбор автоответа её увидит, пара перестанет ждать настоящего
   ответа: «последняя реплика наша» скроет повод от `_waiting_find`. Именно за
   этим сторожем (`TestAgentStillAnswers`) затевалась вся правка источников
   (`source = 'quick'` + `source <> 'quick'` в отборах агента).

Наружу не ходит ничего: `send_message`, Redis и выключатели подменены, к базе
тест говорит своими запросами (`testing.md`).
"""

import uuid

import pytest

from mysql_.mysql_ import mysql_get_db_async

from round_ import round_slot as slot

from src.dating import answer as answer_mod
from src.dating import chat_quick as quick
from src.dating import chat_quick_round as quick_round

pytestmark = pytest.mark.chat

STAND_SITE = 999_343

# Интервал настройки в тестах отбора: 60 с, реплики кладём на 90 — в окне.
AFTER = 60


async def _said(profile_id: int, uid: str, direction: str = 'in',
                source: str = 'socket', age_sec: int = 0,
                text: str = 'привет') -> int:
    """Строка переписки с возрастом в секундах. Отдает её номер.

    ⚠ Входящая — `is_answered = 0` по умолчанию: она и есть «ждёт ответа».
    """
    async with mysql_get_db_async() as db:
        await db.execute(
            'INSERT INTO message (profile_id, sender_uid, sender_name, text,'
            '  message_uid, direction, source, by_agent,'
            '  created_at)'
            ' VALUES (%s, %s, %s, %s, %s, %s, %s, %s,'
            '         NOW() - INTERVAL %s SECOND)',
            (int(profile_id), uid, 'Brian' if direction == 'in' else 'Girl',
             text, f'zq-{uuid.uuid4().hex[:12]}', direction, source,
             1 if source == 'socket' and direction == 'out' else 0,
             int(age_sec)))
        await db.connection.commit()

        return int(db.lastrowid)


async def _setting(site_id: int, seconds) -> None:
    """Значение настройки прямо в `site.settings` — тот же вход, что у формы."""
    async with mysql_get_db_async() as db:
        await db.execute(
            'UPDATE site SET settings = JSON_SET(COALESCE(settings, JSON_OBJECT()),'
            " '$.chat_quick_after', %s) WHERE site_id = %s",
            (str(seconds), int(site_id)))
        await db.connection.commit()


class _FakeRedis:
    """Redis ровно на два вкуса: замок отметки (`set(nx, ex)`) и слой круга
    (`get`/`setex`/`ttl`) — больше от него никто в сюжете не просит."""

    def __init__(self):
        self.store: dict[str, str] = {}
        self.expires: dict[str, int] = {}
        self.broken = False

    def _down(self):
        if self.broken:
            raise RuntimeError('redis лёг')

    def set(self, key, value, ex=None, nx=False):
        self._down()

        if nx and key in self.store:
            return None

        self.store[key] = value

        return True

    def get(self, key):
        self._down()

        return self.store.get(key)

    def setex(self, key, seconds, value):
        self._down()

        self.store[key] = value
        self.expires[key] = int(seconds)

    def ttl(self, key):
        self._down()

        return int(self.expires.get(key, -2))

    def delete(self, key):
        self._down()

        self.store.pop(key, None)
        self.expires.pop(key, None)


@pytest.fixture
async def clean():
    """Стендовый сайт без шаблонов и без настройки — до теста и после."""
    async def _wipe():
        async with mysql_get_db_async() as db:
            await db.execute('DELETE FROM chat_quick WHERE site_id = %s',
                             (STAND_SITE,))
            await db.execute(
                "UPDATE site SET settings = JSON_REMOVE("
                " COALESCE(settings, JSON_OBJECT()), '$.chat_quick_after')"
                ' WHERE site_id = %s', (STAND_SITE,))
            await db.connection.commit()

    await _wipe()

    yield

    await _wipe()


@pytest.fixture
def env(monkeypatch, clean):
    """Мир без наружных вызовов. Возвращает, до чего дошло.

    Подменный `send_message` кладёт в базу исходящую строку так, как это делает
    настоящий быстрый путь (`source = 'quick'`, `by_agent = 0`), но **не трогает**
    `is_answered` — именно в этом смысл занятия: ответ не засчитан.
    """
    state = {'calls': [], 'sent_ok': True, 'blocked': [], 'answer_on': True,
             'redis': _FakeRedis()}

    async def _send(profile, presence, to_uid, text, to_history=True,
                    may_invite=False, since=None, by_agent=False, photo=None,
                    quick: bool = False):
        state['calls'].append({'uid': str(to_uid), 'text': str(text),
                               'quick': bool(quick), 'since': since,
                               'may_invite': bool(may_invite),
                               'by_agent': bool(by_agent)})

        if not state['sent_ok']:
            return {'ok': False, 'error': 'сайт молчит', 'status': 'error'}

        async with mysql_get_db_async() as db:
            await db.execute(
                'INSERT INTO message (profile_id, sender_uid, sender_name,'
                '  text, message_uid, direction, source, by_agent)'
                " VALUES (%s, %s, '', %s, %s, 'out', 'quick', 0)",
                (int(profile['id']), str(to_uid), str(text),
                 f'zqs-{uuid.uuid4().hex[:12]}'))
            await db.connection.commit()

        return {'ok': True, 'status': 'sent', 'error': ''}

    async def _on(site_id):
        return bool(state['answer_on'])

    async def _blocked(site_id, channel):
        return list(state['blocked'])

    async def _presence(site_id):
        return {}

    monkeypatch.setattr(quick, 'send_message', _send)
    monkeypatch.setattr(quick, 'answer_on', _on)
    monkeypatch.setattr(quick, 'block_pairs', _blocked)
    monkeypatch.setattr(quick, 'site_presence', _presence)
    monkeypatch.setattr(quick, 'feed_write', lambda *a, **kw: None)
    monkeypatch.setattr(quick, 'redis_conn_get', lambda: state['redis'])

    async def run(profile_uid: str = 'u-quick'):
        """Шаблон заведён, настройка стоит — заход по стендовому сайту."""
        await _setting(STAND_SITE, AFTER)

        return await quick.chat_quick_run(STAND_SITE)

    state['run'] = run

    return state


class TestSelection:
    """Кому шаблон уходит, а кому нет."""

    async def test_the_silence_sends_a_template(self, env, girl_make):
        """Его реплика старше интервала — уходит шаблон, ответ не засчитан."""
        girl = await girl_make(online=True)
        await quick.chat_quick_add(STAND_SITE, 'hi')
        message_id = await _said(girl, 'u-quick', age_sec=90)

        got = await env['run']()

        assert got['sent'] == 1
        assert len(env['calls']) == 1
        assert env['calls'][0]['quick'] is True
        assert env['calls'][0]['by_agent'] is False

        async with mysql_get_db_async() as db:
            await db.execute('SELECT is_answered FROM message WHERE id = %s',
                             (message_id,))
            assert (await db.fetchone())['is_answered'] == 0, \
                'быстрая строка не имеет права закрывать входящее'

    async def test_before_the_interval_nothing(self, env, girl_make):
        """Моложе интервала — ждём: обещание звучит «через N секунд»."""
        girl = await girl_make(online=True)
        await quick.chat_quick_add(STAND_SITE, 'hi')
        await _said(girl, 'u-quick', age_sec=20)

        assert (await env['run']())['sent'] == 0

    async def test_past_the_window_nothing(self, env, girl_make):
        """Старше интервала + окна — поздно: «hi» вчерашнему вопросу не ответ."""
        girl = await girl_make(online=True)
        await quick.chat_quick_add(STAND_SITE, 'hi')
        await _said(girl, 'u-quick', age_sec=AFTER + quick.QUICK_WINDOW_SEC + 60)

        assert (await env['run']())['sent'] == 0

    async def test_second_tick_does_not_repeat(self, env, girl_make):
        """⚠ Не больше одной попытки на входящее — чем бы она ни кончилась."""
        girl = await girl_make(online=True)
        await quick.chat_quick_add(STAND_SITE, 'hi')
        await _said(girl, 'u-quick', age_sec=90)

        assert (await env['run']())['sent'] == 1

        assert (await env['run']())['sent'] == 0
        assert len(env['calls']) == 1

    async def test_failure_consumes_the_attempt_too(self, env, girl_make):
        """⚠ Отправка отказала — повторного стука нет: отметка снята **до** неё.

        Долбить сайт отказом каждые пять секунд — ровно то, чего правило
        бережёт; отказ — строка в журнал.
        """
        girl = await girl_make(online=True)
        await quick.chat_quick_add(STAND_SITE, 'hi')
        await _said(girl, 'u-quick', age_sec=90)
        env['sent_ok'] = False

        assert (await env['run']())['sent'] == 0
        assert (await env['run']())['sent'] == 0
        assert len(env['calls']) == 1

    async def test_broken_redis_does_not_send(self, env, girl_make):
        """⚠ Сбой Redis — **не слать**: без отметки стучались бы каждым тиком.

        Агент от Redis не зависит — его путь этот сбой не трогает вовсе.
        """
        girl = await girl_make(online=True)
        await quick.chat_quick_add(STAND_SITE, 'hi')
        await _said(girl, 'u-quick', age_sec=90)
        env['redis'].broken = True

        assert (await env['run']())['sent'] == 0
        assert env['calls'] == []

    async def test_our_answer_after_it_silences_quick(self, env, girl_make):
        """Наш ответ (agent) после его реплики — тишины нет, шаблон не нужен."""
        girl = await girl_make(online=True)
        await quick.chat_quick_add(STAND_SITE, 'hi')
        await _said(girl, 'u-quick', age_sec=90)
        await _said(girl, 'u-quick', direction='out', source='socket', age_sec=10)

        assert (await env['run']())['sent'] == 0

    async def test_our_quick_row_after_it_too(self, env, girl_make):
        """⚠ И наша **быстрая** строка после повода гасит занятие.

        «Последняя реплика» считается по всем строкам, quick в этом отборе
        участвует: ушли «hi» минуту назад — второй «hi» не нужен.
        """
        girl = await girl_make(online=True)
        await quick.chat_quick_add(STAND_SITE, 'hi')
        await _said(girl, 'u-quick', age_sec=90)
        await _said(girl, 'u-quick', direction='out', source='quick', age_sec=40)

        assert (await env['run']())['sent'] == 0

    async def test_no_templates_nothing(self, env, girl_make):
        """Пустой список — выключено молча: слать нечего, журнал молчит."""
        girl = await girl_make(online=True)
        await _said(girl, 'u-quick', age_sec=90)

        assert (await env['run']())['sent'] == 0

    async def test_zero_interval_is_off(self, env, girl_make):
        """Настройка 0 — занятие выключено, каким бы ни был список."""
        girl = await girl_make(online=True)
        await quick.chat_quick_add(STAND_SITE, 'hi')
        await _said(girl, 'u-quick', age_sec=90)
        await _setting(STAND_SITE, 0)

        assert (await quick.chat_quick_run(STAND_SITE))['sent'] == 0

    async def test_agent_switch_off_stops_it(self, env, girl_make):
        """⚠ Снятая «отправка ответов» гасит и быстрый: он тень ответа."""
        girl = await girl_make(online=True)
        await quick.chat_quick_add(STAND_SITE, 'hi')
        await _said(girl, 'u-quick', age_sec=90)
        env['answer_on'] = False

        assert (await env['run']())['sent'] == 0

    async def test_blocked_pair_gets_nothing(self, env, girl_make):
        """Запрет «не пишем» держит и шаблон: «hi» закрышемуся — повод для жалобы."""
        girl = await girl_make(online=True)
        await quick.chat_quick_add(STAND_SITE, 'hi')
        await _said(girl, 'u-quick', age_sec=90)
        env['blocked'] = [(girl, 'u-quick')]

        assert (await env['run']())['sent'] == 0

    async def test_offline_profile_gets_nothing(self, env, girl_make):
        """Анкета не в сети — и тишину считать не должна: транспорт мёртв."""
        girl = await girl_make(online=False)
        await quick.chat_quick_add(STAND_SITE, 'hi')
        await _said(girl, 'u-quick', age_sec=90)

        assert (await env['run']())['sent'] == 0

    async def test_answered_line_gets_nothing(self, env, girl_make):
        """Входящее с ответом (или с решением молчать) — не повод."""
        girl = await girl_make(online=True)
        await quick.chat_quick_add(STAND_SITE, 'hi')
        message_id = await _said(girl, 'u-quick', age_sec=90)

        async with mysql_get_db_async() as db:
            await db.execute('UPDATE message SET is_answered = 1 WHERE id = %s',
                             (message_id,))
            await db.connection.commit()

        assert (await env['run']())['sent'] == 0


class TestAgentStillAnswers:
    """⚠⚠ Главный сторож: быстрая строка не ломает отбор автоответа.

    `message` общая, и без `source <> 'quick'` в запросах агента «последняя
    реплика» стала бы нашей, пара перестала бы ждать настоящего ответа — и
    ничего бы не вспухло ошибкой: просто тишина вместо ответа.
    """

    async def test_pair_still_waits_after_quick(self, girl_make):
        """Его реплика + ушедший «hi» — `_waiting_find` берёт пару как брал."""
        girl = await girl_make(online=True)
        message_id = await _said(girl, 'u-quick', age_sec=90)
        await _said(girl, 'u-quick', direction='out', source='quick', age_sec=40)

        got = await answer_mod._waiting_find(girl, 10, [], False)

        assert [one['id'] for one in got if one['sender_uid'] == 'u-quick'] \
            == [message_id]

    async def test_quick_is_not_our_last_word(self, girl_make):
        """`_followup_state`: шаблонное «hi» не выдаёт тишину за наш реплик."""
        girl = await girl_make(online=True)
        await _said(girl, 'u-quick', age_sec=90)
        await _said(girl, 'u-quick', direction='out', source='quick', age_sec=40)

        state = await answer_mod._followup_state(girl, 'u-quick')

        assert state['ours_last'] is False
        assert state['followups'] == 0

    async def test_real_answer_is_our_last_word(self, girl_make):
        """Та же проверка в зеркале: настоящий ответ — «наше последнее слово»."""
        girl = await girl_make(online=True)
        await _said(girl, 'u-quick', age_sec=90)
        await _said(girl, 'u-quick', direction='out', source='socket', age_sec=40)

        assert (await answer_mod._followup_state(girl, 'u-quick'))['ours_last'] \
            is True

    async def test_anyway_quota_ignores_it(self, girl_make):
        """Квота первых обязательных ответов не тратится на шаблоны."""
        girl = await girl_make(online=True)
        await _said(girl, 'u-quick')

        for _ in range(5):
            await _said(girl, 'u-quick', direction='out', source='quick')

        assert await answer_mod._anyway_here(girl, 'u-quick', 5) is True


class TestRound:
    """Круг: темп свой, заход не бросает ничего."""

    async def test_round_does_not_throw(self, monkeypatch):
        """Первый тест круга — «не бросает»: слот, шлюз, обход сайтов."""
        seen = []

        async def _sites(**kwargs):
            return [{'site_id': STAND_SITE}]

        async def _work(site_id, minutes=0):
            seen.append(int(site_id))

            return {'checked': 0, 'sent': 0}

        monkeypatch.setattr(quick_round, 'round_sites', _sites)
        monkeypatch.setattr(quick_round, 'chat_quick_run', _work)
        monkeypatch.setattr(slot, 'redis_conn_get', lambda: _FakeRedis())

        got = await quick_round.chat_quick_round_run()

        assert seen == [STAND_SITE]
        assert got['sent'] == 0


class TestDoor:
    """Ручка: список, добавление, удаление. Наружу — только наш же API."""

    async def test_add_list_drop(self, auth_client, clean):
        got = await auth_client.post('/api/dating/chat-quick',
                                     json={'site_id': STAND_SITE,
                                           'body': '  hi   there  '})

        assert got.status_code == 200, got.text
        row = got.json()
        assert row['body'] == 'hi there', 'шаблон обязан быть одной репликой'

        got = await auth_client.get(f'/api/dating/chat-quick?site_id={STAND_SITE}')
        assert got.status_code == 200

        body = got.json()
        assert [r['id'] for r in body['result']] == [row['id']]
        assert body['after_sec'] == 50, 'умолчание реестра видно в той же ручке'

        got = await auth_client.delete(f"/api/dating/chat-quick/{row['id']}")
        assert got.status_code == 200

        got = await auth_client.get(f'/api/dating/chat-quick?site_id={STAND_SITE}')
        assert got.json()['result'] == []

    async def test_drop_twice_is_404(self, auth_client, clean):
        got = await auth_client.post('/api/dating/chat-quick',
                                     json={'site_id': STAND_SITE, 'body': 'hi'})
        row_id = got.json()['id']

        assert (await auth_client.delete(f'/api/dating/chat-quick/{row_id}')).status_code == 200
        assert (await auth_client.delete(f'/api/dating/chat-quick/{row_id}')).status_code == 404

    async def test_empty_body_refused(self, auth_client, clean):
        """Пробелы — не шаблон: 409 из домена, а не пустая строка в списке."""
        got = await auth_client.post('/api/dating/chat-quick',
                                     json={'site_id': STAND_SITE,
                                           'body': '   '})

        assert got.status_code == 409

    async def test_site_zero_is_empty(self, auth_client, clean):
        """Без сайта — пустой список, а не шаблоны всех сайтов разом."""
        got = await auth_client.get('/api/dating/chat-quick?site_id=0')

        assert got.json() == {'result': [], 'after_sec': 0}
