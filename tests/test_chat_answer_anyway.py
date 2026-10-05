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
from src.dating.site_setting import SITE_KNOWN

pytestmark = pytest.mark.chat

STAND_SITE = 999_343


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

        async def _chat_talk(agent_id, question, variables=None):
            got.update(variables or {})

            return {'text': 'ответ', 'message_id': 0}

        monkeypatch.setattr(answer_mod, 'rhapsody_api_chat_talk', _chat_talk)

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

        async def _chat_talk(agent_id, question, variables=None):
            got.update(variables or {})

            return {'text': 'ответ', 'message_id': 0}

        monkeypatch.setattr(answer_mod, 'rhapsody_api_chat_talk', _chat_talk)

        return got

    async def test_the_name_and_key_are_sent(self, asked, girl):
        async with mysql_get_db_async() as db:
            await db.execute('SELECT * FROM profile WHERE id = %s', (girl,))
            profile = dict(await db.fetchone())

        await answer_mod._agent_ask(7, 'вопрос', 'en', profile,
                                    uid='128282', name='Gerard')

        assert asked['companion_title'] == 'Gerard', 'агент не знает имени адресата'
        assert asked['companion_name'] == 'gerard', \
            'ключ фактов не уехал — факты всех мужчин сольются в один документ'
        assert asked['companion_id'] == '128282', \
            'номер уехал не тот — склейка у соседа ждёт его слева'

    async def test_the_key_the_neighbour_will_build(self, asked, girl):
        """⚠⚠ Склейку делает сосед, и проверить её можно только собрав ту же.

        Шаг «Переменные» (позиция 0 у `dating_talk`) клеит
        `companion_unique_id` из `%companion_id%_%companion_name%`. Выкройка та
        же, что у девушки (`%girl_id%_%girl_name%` → `643694_diana`), и ключ
        обязан читаться человеком: `128282_gerard`.

        Прежде здесь выходило `10_dating_999343_128282` — мёртвая константа,
        приставка, сайт, номер и ни одного имени.
        """
        async with mysql_get_db_async() as db:
            await db.execute('SELECT * FROM profile WHERE id = %s', (girl,))
            profile = dict(await db.fetchone())

        await answer_mod._agent_ask(7, 'вопрос', 'en', profile,
                                    uid='128282', name='Gerard')

        key = f"{asked['companion_id']}_{asked['companion_name']}"

        assert key == '128282_gerard'
        # Мёртвая десятка и приставка ушли из ключа совсем.
        assert not key.startswith('10_')
        assert 'dating' not in key
        assert str(STAND_SITE) not in key

    async def test_the_history_is_not_sent_from_here(self, asked, girl):
        """⚠⚠ Историю разговора отсюда **не шлём**: у неё один дом, и он у соседа.

        Обе ленты — чат и письма — собирают два шага `dating_talk` из
        `companion_session`, и туда же он пишет каждый обмен. Пока ленту считали
        здесь (из пяти наших таблиц) и присылали готовым текстом, разговор жил в
        двух местах: у нас разборно, у соседа слепками промпта.

        ⚠ Вернись отправка — домов снова станет два, и разойдутся они на первой
        правке формата. Своего кода истории у панели больше нет вовсе
        (`talk_history.py` удалён).

        ⚠⚠ А вот **ключи пары** уезжать обязаны, и это соседний тест: без них
        сосед не найдёт, чью переписку читать, и напишет человеку, которого «не
        помнит».
        """
        async with mysql_get_db_async() as db:
            await db.execute('SELECT * FROM profile WHERE id = %s', (girl,))
            profile = dict(await db.fetchone())

        await answer_mod._agent_ask(7, 'вопрос', 'en', profile,
                                    uid='128282', name='Gerard')

        assert 'talk_history_chat' not in asked, \
            'история чата снова уезжает отсюда — её собирает сосед'
        assert 'talk_history_mail' not in asked, \
            'история писем снова уезжает отсюда — её собирает сосед'


class TestForeignNameIsNotSent:
    """⚠⚠ Ответ с чужим именем не уходит и в пул не ложится."""

    async def test_the_round_refuses_it(self, monkeypatch, girl):
        from src.dating.chat_reply import chat_reply_waiting

        async def _ask(agent_id, question, language, profile, history='', uid='',
                       anyway=False, name='', **_):
            # Ровно тот ответ, что ушёл с прода.
            return "ok vadim, let's go. what's on your mind today?", '', 0

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
    """⚠ Срок инициативы — **интервал**, и обе его границы в реестре.

    Прежде настройка была заведена впрок и подпись говорила «работа не сделана».
    Работа сделана (`initiative.py`), и границ стало две: «от» и «до», между ними
    жребий.
    """

    def test_both_bounds_are_registered(self):
        low = SITE_KNOWN['chat_initiative_after']
        high = SITE_KNOWN['chat_initiative_after_max']

        assert low['kind'] == high['kind'] == 'number'
        assert low['default'] == '60' and high['default'] == '120'
        assert low['min'] == 0, 'ноль обязан быть допустим — это «выключено»'
        assert high['min'] == 0, 'ноль в «до» — это «без разброса»'
        assert low['group'] == high['group'] == 'invite', \
            'место им на вкладке «Инвайты и чат»'

    def test_the_bounds_are_read_as_a_pair(self):
        """⚠⚠ Обе границы читает **одна** дверь, и она же приводит их к порядку.

        Читай их два места порознь, и «до» ниже «от» разошлось бы по коду двумя
        разными решениями: одно писало бы раньше срока, другое ровно по «от».
        """
        from src.dating.initiative import (INITIATIVE_AFTER_MAX_SETTING,
                                           INITIATIVE_AFTER_SETTING,
                                           _after_range, _due_sec)

        assert INITIATIVE_AFTER_SETTING == 'chat_initiative_after'
        assert INITIATIVE_AFTER_MAX_SETTING == 'chat_initiative_after_max'
        assert callable(_after_range) and callable(_due_sec)

    def test_the_draw_does_not_lean_on_the_process(self):
        """⚠⚠ Жребий срока не вправе зависеть от процесса.

        `hash()` солится на каждый процесс (`PYTHONHASHSEED`), а воркеров
        несколько и перезапускаются они часто: пара получала бы новый срок от
        каждого — то есть ровно тот развал, от которого жребий уводится в
        устойчивый. То же у `random`: он про «каждый раз по-новому».

        ⚠ Сторож смотрит **вызовы**, а не текст: про обе ловушки написано в самом
        модуле, и поиск подстроки краснел бы на объяснении (`pytest_guard.py`
        общего слоя — тот самый случай, он там и записан).
        """
        from tests_.tests_guard import tests_guard_calls

        from src.dating import initiative as module

        called = tests_guard_calls(module)

        assert 'hash' not in called, 'жребий съехал на солёный hash()'
        assert not {one for one in called if one.startswith('random.')}, \
            'жребий съехал на случай — он перестал быть устойчивым'


class TestAlwaysMeansAlways:
    """⚠⚠ «Отвечать всегда» значит всегда — без оговорок про возраст.

    Обещание не исполнялось: порог свежести отсекал пару **до** того, как
    считалась обязательность. На проде первая и единственная реплика Брайана
    значилась обязательной, а отбор её не брал.

    Сперва я завёл послабление — «обязательному ответу порог не помеха». Потом
    убрали сам порог, и послабление отпало вместе с ним: сущность, заведённая ради
    обхода другой сущности, исчезает вместе с ней.
    """

    async def _panel_said(self, profile_id: int, uid: str, hours_ago: int) -> int:
        async with mysql_get_db_async() as db:
            await db.execute(
                'INSERT INTO message (profile_id, sender_uid, sender_name, text,'
                "  message_uid, direction, source, created_at)"
                " VALUES (%s, %s, 'Brian', 'вопрос', %s, 'in', 'panel',"
                '  NOW() - INTERVAL %s HOUR)',
                (int(profile_id), uid, f'zz-{uuid.uuid4().hex[:12]}',
                 int(hours_ago)))
            await db.connection.commit()

            return int(db.lastrowid)

    async def test_a_stale_first_reply_is_taken(self, girl):
        """Двухдневная реплика берётся в работу — возраст больше не помеха."""
        message_id = await self._panel_said(girl, 'u-1', hours_ago=49)

        got = await answer_mod._waiting_find(girl, 10, [], False)

        assert [one['id'] for one in got if one['sender_uid'] == 'u-1'] \
            == [message_id]

    async def test_and_it_is_mandatory(self, girl):
        """⚠ И она же обязательна: первая из пяти. Оба правила наконец сходятся."""
        await self._panel_said(girl, 'u-1', hours_ago=49)

        assert await answer_mod._anyway_here(girl, 'u-1', 5) is True

    async def test_an_answered_pair_is_not_taken_twice(self, girl):
        """⚠⚠ Возраст не нужен как сторож, потому что **ответ один**.

        `is_answered` закрывает пару: десять раз одному человеку не ответишь,
        сколько бы ни лежала его реплика.
        """
        message_id = await self._panel_said(girl, 'u-1', hours_ago=49)

        async with mysql_get_db_async() as db:
            await db.execute('UPDATE message SET is_answered = 1 WHERE id = %s',
                             (message_id,))
            await db.connection.commit()

        got = await answer_mod._waiting_find(girl, 10, [], False)

        assert [one for one in got if one['sender_uid'] == 'u-1'] == []
