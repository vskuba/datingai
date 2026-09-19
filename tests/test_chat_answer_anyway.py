"""Первые реплики разговора получают ответ обязательно.

## ⚠⚠ Зачем

Судья у соседа вправе промолчать — «реальная девушка отвечает не на всё», и в
середине разговора это верно. Но в его начале молчание стоит дорого: **позвали мы
сами**, человек ответил на инвайт и не услышал ничего. Разговор, который мы же и
начали, обрывается на первой реплике.

Поймано на живом прогоне (`agent_workflow_trace #23133`): первый ответ на инвайт,
судья выбрал молчание, и он был в своём праве.

Workflow не меняли — ему добавили переменную `answer_anyway`. Ноль (и отсутствие)
ведёт себя как раньше; единица закрывает судье путь молчания, **не отменяя
проверки**: язык и суть он смотрит по-прежнему и при нужде переписывает ответ.

⚠ **Наружу сюита не ходит** (`testing.md`): агент подменяется, и проверяется то,
что мы ему передали.
"""

import uuid

import pytest

from mysql_.mysql_ import mysql_get_db_async

from src.dating import answer as answer_mod

pytestmark = pytest.mark.chat

STAND_SITE = 999_343


@pytest.fixture
async def girl(auth_client):
    profile_id = int((await auth_client.post('/api/dating/profile', json={
        'site_id': STAND_SITE,
        'login': f'anyway-{uuid.uuid4().hex[:8]}@example.com',
        'password': 'x'})).json()['result'])

    yield profile_id

    async with mysql_get_db_async() as db:
        await db.execute('DELETE FROM chat_reply WHERE profile_id = %s', (profile_id,))
        await db.execute('DELETE FROM message WHERE profile_id = %s', (profile_id,))
        await db.execute('DELETE FROM profile WHERE id = %s', (profile_id,))
        await db.connection.commit()


async def _said(profile_id: int, uid: str, direction: str = 'in') -> int:
    async with mysql_get_db_async() as db:
        await db.execute(
            'INSERT INTO message (profile_id, sender_uid, sender_name, text,'
            '  message_uid, direction) VALUES (%s, %s, %s, %s, %s, %s)',
            (int(profile_id), uid, 'Brian', 'привет',
             f'zz-{uuid.uuid4().hex[:12]}', direction))
        await db.connection.commit()

        return int(db.lastrowid)


class TestTheCount:
    """Кого считаем обязательным — `_anyway_here`."""

    async def test_his_first_reply_is_forced(self, girl):
        await _said(girl, 'u-1')

        assert await answer_mod._anyway_here(girl, 'u-1', 5) is True

    async def test_past_the_threshold_it_stops(self, girl):
        for _ in range(6):
            await _said(girl, 'u-1')

        assert await answer_mod._anyway_here(girl, 'u-1', 5) is False

    async def test_the_boundary_is_inclusive(self, girl):
        """Пятая реплика при пороге 5 — ещё обязательная, шестая уже нет."""
        for _ in range(5):
            await _said(girl, 'u-1')

        assert await answer_mod._anyway_here(girl, 'u-1', 5) is True

    async def test_zero_turns_it_off(self, girl):
        await _said(girl, 'u-1')

        assert await answer_mod._anyway_here(girl, 'u-1', 0) is False

    async def test_our_own_replies_do_not_count(self, girl):
        """⚠⚠ Считаем **его** реплики.

        Наших в паре бывает сколько угодно — инвайт, потом ещё инвайт, — а
        разговором это становится с первого его слова. Считай мы всё подряд, порог
        съедался бы нашими же сообщениями.
        """
        for _ in range(9):
            await _said(girl, 'u-1', direction='out')
        await _said(girl, 'u-1')

        assert await answer_mod._anyway_here(girl, 'u-1', 5) is True

    async def test_each_pair_counts_apart(self, girl):
        """⚠ По паре, а не по анкете: у каждого собеседника свой счёт."""
        for _ in range(6):
            await _said(girl, 'u-old')
        await _said(girl, 'u-new')

        assert await answer_mod._anyway_here(girl, 'u-old', 5) is False
        assert await answer_mod._anyway_here(girl, 'u-new', 5) is True


class TestItReachesTheAgent:
    """⚠⚠ Флаг уезжает соседу, и уезжает строкой."""

    @pytest.fixture
    def asked(self, monkeypatch):
        got = {}

        async def _chat_ask(agent_id, question, companion_id=0, variables=None):
            got.update(variables or {})

            return 'ответ'

        monkeypatch.setattr(answer_mod, 'rhapsody_api_chat_ask', _chat_ask)

        return got

    async def test_the_variable_is_sent(self, monkeypatch, asked, girl):
        async with mysql_get_db_async() as db:
            await db.execute('SELECT * FROM profile WHERE id = %s', (girl,))
            profile = dict(await db.fetchone())

        await answer_mod._agent_ask(7, 'вопрос', 'en', profile, anyway=True)

        assert asked['answer_anyway'] == '1'

    async def test_off_is_a_zero_string(self, monkeypatch, asked, girl):
        """⚠⚠ Именно `'0'`, а не `False`.

        У соседа `%if%` ложно для пустого, `'0'` и отсутствия
        (`ai_workflow_variable_truthy`). `False` доехал бы туда строкой `'False'` —
        то есть непустой, то есть **истинной**, и выключенный флаг работал бы как
        включённый.
        """
        async with mysql_get_db_async() as db:
            await db.execute('SELECT * FROM profile WHERE id = %s', (girl,))
            profile = dict(await db.fetchone())

        await answer_mod._agent_ask(7, 'вопрос', 'en', profile, anyway=False)

        assert asked['answer_anyway'] == '0'
        assert asked['answer_anyway'] != 'False'


class TestTheSetting:
    """Настройка сайта, задающая порог."""

    def test_it_is_registered(self):
        from src.uvicorn.routers.dating.site_ import SITE_KNOWN

        one = SITE_KNOWN['chat_answer_anyway_first']

        assert one['kind'] == 'number'
        assert one['default'] == '5'
        assert one['min'] == 0, 'ноль обязан быть допустим — это «выключено»'
        assert one['group'] == 'invite', 'настройка про инвайты, и место ей там же'

    async def test_the_default_is_used_when_unset(self):
        """У стенда настройки нет — берём умолчание реестра."""
        got = await answer_mod._anyway_first(STAND_SITE)

        assert got == answer_mod.ANSWER_ANYWAY_FIRST_DEFAULT
