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


class TestTheCompanionIsNamed:
    """⚠⚠ Агент узнаёт, кому пишет: ключ и имя уезжают вызовом.

    До этого сосед подставлял имя из своего справочника по `companion_id` — одно
    на всех, — и агент написал живому Gerard'у «ok vadim». Тем же путём факты обо
    всех мужчинах сайта ложились в один документ Qdrant.
    """

    @pytest.fixture
    def asked(self, monkeypatch):
        got = {}

        async def _chat_ask(agent_id, question, companion_id=0, variables=None):
            got.update(variables or {})
            got['_companion_id'] = companion_id

            return 'ответ'

        monkeypatch.setattr(answer_mod, 'rhapsody_api_chat_ask', _chat_ask)

        return got

    async def test_the_name_and_key_are_sent(self, asked, girl):
        async with mysql_get_db_async() as db:
            await db.execute('SELECT * FROM profile WHERE id = %s', (girl,))
            profile = dict(await db.fetchone())

        await answer_mod._agent_ask(7, 'вопрос', 'en', profile,
                                    uid='128282', name='Gerard')

        assert asked['companion_title'] == 'Gerard', 'агент не знает имени адресата'
        assert asked['companion_name'] == f'dating_{STAND_SITE}_128282', \
            'ключ фактов не уехал — факты всех мужчин сольются в один документ'

    async def test_the_record_is_ours(self, asked, girl):
        """⚠ `companion_id` больше не единица: единица — «Vadim» из справочника."""
        async with mysql_get_db_async() as db:
            await db.execute('SELECT * FROM profile WHERE id = %s', (girl,))
            profile = dict(await db.fetchone())

        await answer_mod._agent_ask(7, 'вопрос', 'en', profile, uid='1', name='X')

        assert asked['_companion_id'] == answer_mod.ANSWER_COMPANION_ID
        assert asked['_companion_id'] != 1


class TestForeignNameIsNotSent:
    """⚠⚠ Ответ с чужим именем не уходит и в пул не ложится."""

    async def test_the_round_refuses_it(self, monkeypatch, girl):
        from src.dating.chat_reply import chat_reply_waiting

        async def _ask(agent_id, question, language, profile, history='', uid='',
                       anyway=False, name=''):
            # Ровно тот ответ, что ушёл с прода.
            return "ok vadim, let's go. what's on your mind today?", ''

        sent = []

        async def _send(profile, presence, uid, text):
            sent.append(text)

            return {'ok': True, 'error': ''}

        monkeypatch.setattr(answer_mod, '_agent_ask', _ask)
        monkeypatch.setattr(answer_mod, 'send_message', _send)
        monkeypatch.setattr(answer_mod, 'online_check',
                            lambda site_id, uid: {'online': True, 'known': True})

        message_id = await _said(girl, 'u-1')
        async with mysql_get_db_async() as db:
            await db.execute('SELECT * FROM profile WHERE id = %s', (girl,))
            profile = dict(await db.fetchone())

        got = await answer_mod.answer_run(profile, {}, 7)

        assert sent == [], 'ответ с чужим именем ушёл живому человеку'
        assert got['drafted'] == 0, 'брак лёг в пул и ушёл бы следующим кругом'
        assert await chat_reply_waiting(girl, message_id) == {}
        assert any('чужое имя' in one for one in got['errors']), got['errors']


class TestInitiativeSetting:
    """⚠ Настройка «через сколько проявлять инициативу» — заведена впрок.

    Работы за ней пока нет, и подсказка говорит об этом прямо: настройка без
    объяснения читается как рабочая, человек ставит число и ждёт, что девушка
    начнёт писать сама.
    """

    def test_it_is_registered(self):
        from src.uvicorn.routers.dating.site_ import SITE_KNOWN

        one = SITE_KNOWN['chat_initiative_after']

        assert one['kind'] == 'number'
        assert one['default'] == '60'
        assert one['min'] == 0, 'ноль обязан быть допустим — это «выключено»'
        assert one['group'] == 'invite', 'место ей на вкладке «Инвайты и чат»'

    def test_the_note_admits_it_does_nothing_yet(self):
        """⚠⚠ Пока работы нет, об этом должно быть написано.

        Убери оговорку вместе с заведением работы — тест напомнит.
        """
        from src.uvicorn.routers.dating.site_ import SITE_KNOWN

        note = SITE_KNOWN['chat_initiative_after']['note']

        assert 'работа не сделана' in note, \
            'настройка молча притворяется рабочей'

    def test_nothing_reads_it_yet(self):
        """⚠ И правда не читается: иначе оговорка врала бы.

        Когда работу сделают, тест упадёт — и вместе с ним снимут оговорку выше.
        """
        import pathlib

        корень = pathlib.Path(__file__).resolve().parents[1]
        # ⚠ Ищем **чтение**, а не имя: упомянуть ключ в документации модуля
        # законно — иначе следующий читатель не найдёт, откуда берётся срок.
        # Читается настройка одним способом на весь проект — `site_seconds`.
        читают = []
        for one in (корень / 'src').rglob('*.py'):
            if one.name == 'site_.py':
                continue

            текст = one.read_text(encoding='utf-8')
            for строка in текст.split('\n'):
                if 'chat_initiative_after' in строка and 'site_seconds' in строка:
                    читают.append(str(one))

        assert not читают, f'настройку уже читают: {читают}'
