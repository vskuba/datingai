"""Круг инициативы: кого берём, чем пишем и когда отправляем.

## Зачем занятие

Разговор кончается не словом «пока», а молчанием: мы ответили, он не ответил.
Автоответ такую пару не видит — он отвечает на входящее, а входящего нет.

## ⚠⚠ Приём выбирает панель, а не модель

Соблазн написать агенту «будь разной» уже проверен на инвайтах: три шаблона из
трёх начинались с «you're trouble». Модель держит один тон и возвращается к нему
всегда. Поэтому приём приходит готовым — жребием по весам, — а шаг у соседа
получает одну задачу.

⚠ **Наружу сюита не ходит** (`testing.md`): агент и отправка подменяются.
"""

import uuid

import pytest

from mysql_.mysql_ import mysql_get_db_async

from src.dating import initiative as initiative_mod
from src.dating import initiative_round as round_mod
from src.dating.chat_initiative import chat_initiative_put, chat_initiative_waiting

pytestmark = pytest.mark.chat

STAND_SITE = 999_343


@pytest.fixture
async def girl(auth_client):
    profile_id = int((await auth_client.post('/api/dating/profile', json={
        'site_id': STAND_SITE,
        'login': f'init-round-{uuid.uuid4().hex[:8]}@example.com',
        'password': 'x'})).json()['result'])

    async with mysql_get_db_async() as db:
        await db.execute("UPDATE profile SET status = 'online' WHERE id = %s",
                         (profile_id,))
        await db.connection.commit()

    yield profile_id

    async with mysql_get_db_async() as db:
        await db.execute('DELETE FROM chat_initiative WHERE profile_id = %s',
                         (profile_id,))
        await db.execute('DELETE FROM message WHERE profile_id = %s', (profile_id,))
        await db.execute('DELETE FROM profile WHERE id = %s', (profile_id,))
        await db.connection.commit()


async def _said(profile_id: int, uid: str, direction: str = 'out',
                minutes_ago: int = 0) -> int:
    """Реплика в переписке.

    ⚠ Время считает **база** (`NOW() - INTERVAL`): MySQL живёт по UTC, и
    `datetime.now()` положил бы строку в будущее.
    """
    async with mysql_get_db_async() as db:
        await db.execute(
            'INSERT INTO message (profile_id, sender_uid, sender_name, text,'
            '  message_uid, direction, created_at)'
            ' VALUES (%s, %s, %s, %s, %s, %s, NOW() - INTERVAL %s MINUTE)',
            (int(profile_id), uid, 'Brian', 'текст',
             f'zz-{uuid.uuid4().hex[:12]}', direction, int(minutes_ago)))
        await db.connection.commit()

        return int(db.lastrowid)


async def _card_sent(profile_id: int, uid: str, minutes_ago: int) -> None:
    """Ушедшая карточка инициативы — тем же счётом, каким её видит отбор.

    ⚠ Время считает база: дневной предел мерится её `NOW()`.
    """
    async with mysql_get_db_async() as db:
        await db.execute(
            'INSERT INTO chat_initiative (site_id, profile_id, uid, name, body,'
            "  try_no, status, sent_at)"
            " VALUES (%s, %s, %s, 'Brian', 'заход', 1, 'sent',"
            '  NOW() - INTERVAL %s MINUTE)',
            (STAND_SITE, int(profile_id), uid, int(minutes_ago)))
        await db.connection.commit()


async def _last_at(profile_id: int, uid: str):
    """Время последней реплики пары — ключ жребия срока."""
    async with mysql_get_db_async() as db:
        await db.execute(
            'SELECT MAX(created_at) AS last_at FROM message'
            ' WHERE profile_id = %s AND sender_uid = %s',
            (int(profile_id), uid))

        return ((await db.fetchone()) or {}).get('last_at')


class TestWhoIsQuiet:
    """⚠⚠ Что считается затихшим разговором."""

    async def test_our_last_word_and_long_ago(self, girl):
        """Мы ответили, он молчит дольше срока — берём."""
        await _said(girl, 'u-1', direction='in', minutes_ago=200)
        await _said(girl, 'u-1', direction='out', minutes_ago=180)

        got = await initiative_mod._quiet(girl, 3600, 3600, 2)

        assert [one['uid'] for one in got] == ['u-1']

    async def test_his_word_waiting_for_an_answer_is_not_ours(self, girl):
        """⚠ Последним говорил он и ответа ещё ждёт — это работа автоответа.

        Влезь сюда инициатива, вышли бы два наших сообщения подряд на одну реплику.
        """
        await _said(girl, 'u-1', direction='out', minutes_ago=200)
        await _said(girl, 'u-1', direction='in', minutes_ago=180)

        assert await initiative_mod._quiet(girl, 3600, 3600, 2) == []

    async def test_deliberate_silence_is_silence(self, girl):
        """⚠⚠ Последним говорил он, но агент **решил промолчать** — это тишина.

        Без этого случая пара выпадала из обоих занятий сразу: автоответ такую
        реплику не берёт (решение молчать держит его отбор), а инициатива не брала,
        потому что последним говорил он. Разговор оставался мёртвым навсегда.

        Поймано на проде: Nadezhda ↔ Semen, тридцать три часа тишины после
        намеренного молчания.
        """
        await _said(girl, 'u-1', direction='out', minutes_ago=200)
        message_id = await _said(girl, 'u-1', direction='in', minutes_ago=180)

        async with mysql_get_db_async() as db:
            await db.execute(
                'UPDATE message SET answer_silent_at = NOW() - INTERVAL 179 MINUTE'
                ' WHERE id = %s', (message_id,))
            await db.connection.commit()

        got = await initiative_mod._quiet(girl, 3600, 3600, 2)

        assert [one['uid'] for one in got] == ['u-1'], \
            'пара после намеренного молчания осталась мёртвой'

    async def test_the_silence_must_be_on_the_last_word(self, girl):
        """⚠ Метка смотрится у **последней** реплики, а не у любой.

        Он промолчал вчера, а сегодня написал снова — сегодняшнее ждёт ответа, и
        это снова работа автоответа.
        """
        old = await _said(girl, 'u-1', direction='in', minutes_ago=400)
        async with mysql_get_db_async() as db:
            await db.execute('UPDATE message SET answer_silent_at = NOW()'
                             ' WHERE id = %s', (old,))
            await db.connection.commit()

        await _said(girl, 'u-1', direction='in', minutes_ago=180)

        assert await initiative_mod._quiet(girl, 3600, 3600, 2) == []

    async def test_fresh_silence_waits(self, girl):
        """Срок не вышел — ждём: час тишины это ещё не конец разговора."""
        await _said(girl, 'u-1', direction='in', minutes_ago=30)
        await _said(girl, 'u-1', direction='out', minutes_ago=20)

        assert await initiative_mod._quiet(girl, 3600, 3600, 2) == []

    async def test_a_silent_invite_is_not_ours(self, girl):
        """⚠ Он не сказал ни слова — это неотвеченный инвайт.

        Возвращаться к нему — работа инвайтов (`invite_repeat_after`), и делать это
        вдвоём значило бы звать человека дважды из двух мест.
        """
        await _said(girl, 'u-1', direction='out', minutes_ago=300)

        assert await initiative_mod._quiet(girl, 3600, 3600, 2) == []

    async def test_the_repeat_limit_stops_it(self, girl):
        """⚠⚠ Молчание — тоже ответ.

        Три подряд «ты тут?» выдают автомат надёжнее любого текста, поэтому предел
        заходов важнее срока.
        """
        await _said(girl, 'u-1', direction='in', minutes_ago=400)
        await _said(girl, 'u-1', direction='out', minutes_ago=300)

        async with mysql_get_db_async() as db:
            for n in (1, 2):
                await db.execute(
                    'INSERT INTO chat_initiative (site_id, profile_id, uid, name,'
                    "  body, try_no, status, sent_at)"
                    " VALUES (%s, %s, 'u-1', 'Brian', 'заход', %s, 'sent',"
                    '  NOW() - INTERVAL %s MINUTE)',
                    (STAND_SITE, girl, n, 200 - n))
            await db.connection.commit()

        assert await initiative_mod._quiet(girl, 3600, 3600, 2) == [], \
            'третий заход при пределе два'

    async def test_his_answer_resets_the_count(self, girl):
        """⚠⚠ Его ответ счёт «подряд» **обнуляет** — живой разговор не замирает.

        Это тот самый случай, ради которого правило менялось: инициатива ушла, он
        ответил, мы ответили ему в чате, разговор затих снова. Наша очередь
        проявить инициативу — и при пределе «один подряд» она **уходит**, потому
        что прошлый заход закрыт его ответом.

        ⚠⚠ Прежде тест стоял с обратным знаком: предел был дневным и ответом не
        обнулялся, то есть пара замирала до завтра при живом разговоре. Довод за
        дневной («отвечающий через раз получит инициативу двадцать раз в сутки»)
        верен буквально, но описывает не беду: двадцать раз ответивший человек —
        это разговор, а не писание в пустоту. От выливания реплик в молчание
        защищает сам счёт: новый заход требует **его реплики**, а сверху стоит
        дневной предохранитель (`TestTheDailyBackstop`).
        """
        await _said(girl, 'u-1', direction='in', minutes_ago=400)
        await _card_sent(girl, 'u-1', minutes_ago=350)

        # Он ответил после того захода, мы ответили ему, и всё снова затихло.
        await _said(girl, 'u-1', direction='in', minutes_ago=300)
        await _said(girl, 'u-1', direction='out', minutes_ago=250)

        got = await initiative_mod._quiet(girl, 3600, 3600, 1)

        assert [one['uid'] for one in got] == ['u-1'], \
            'его ответ не обнулил счёт — живой разговор замер до завтра'
        assert [one['try_no'] for one in got] == [1], \
            'номер захода считается по счёту «подряд», а тот обнулён ответом'

    async def test_silence_after_our_call_is_not_reset(self, girl):
        """⚠ А вот молчание счёт **не** обнуляет — иначе предела не было бы вовсе.

        Отличие от соседнего теста ровно одно: после нашего захода он не сказал
        ничего. Значит промежуток тишины тот же, и второй раз звать нельзя.
        """
        await _said(girl, 'u-1', direction='in', minutes_ago=400)
        await _said(girl, 'u-1', direction='out', minutes_ago=350)
        await _card_sent(girl, 'u-1', minutes_ago=300)

        assert await initiative_mod._quiet(girl, 3600, 3600, 1) == [], \
            'позвали дважды в одну тишину — предел «подряд» не держит'

    async def test_an_old_silence_is_still_one_silence(self, girl):
        """⚠⚠ У счёта «подряд» **нет окна по времени**, и это намеренно.

        Заход позапрошлых суток, после которого он так и не ответил, — часть
        того же промежутка тишины. Ограничь счёт сутками, и трёхдневное молчание
        обнуляло бы его само, без всякого ответа: девушка звала бы молчащего
        каждый день по разу вечно.
        """
        await _said(girl, 'u-1', direction='in', minutes_ago=4000)
        await _said(girl, 'u-1', direction='out', minutes_ago=3900)
        await _card_sent(girl, 'u-1', minutes_ago=50 * 60)

        assert await initiative_mod._quiet(girl, 3600, 3600, 1) == [], \
            'сутки обнулили счёт «подряд» — молчащего зовут бесконечно'


class TestTheDailyBackstop:
    """⚠⚠ Дневной потолок на пару — предохранитель поверх обнуления.

    Обнуление ответом сверху ничем не ограничено: пара, где на той стороне
    зацикленный автоответчик, обнуляла бы счёт «подряд» каждой репликой и
    получала бы инициативы без конца. Потолок сторожит **эту** патологию, а не
    живого человека, — потому его умолчание и выше, чем «подряд».
    """

    @staticmethod
    async def _answered_twice(girl: int) -> None:
        """Две ушедшие инициативы, и на каждую он ответил. Затем снова тишина."""
        await _said(girl, 'u-1', direction='in', minutes_ago=400)
        await _card_sent(girl, 'u-1', minutes_ago=300)
        await _said(girl, 'u-1', direction='in', minutes_ago=250)
        await _card_sent(girl, 'u-1', minutes_ago=200)
        await _said(girl, 'u-1', direction='in', minutes_ago=150)
        # Мы ответили ему в чате, и разговор затих на нашей реплике.
        await _said(girl, 'u-1', direction='out', minutes_ago=100)

    async def test_it_stops_an_endless_reset(self, girl):
        """Он ответил на каждую — счёт «подряд» нулевой, а потолок держит."""
        await self._answered_twice(girl)

        assert await initiative_mod._quiet(girl, 3600, 3600, 1, 2) == [], \
            'потолок на пару не держит — обнуление стало бесконечным'

    async def test_a_high_cap_lets_the_talk_live(self, girl):
        """⚠ Тот же разговор при высоком потолке идёт дальше.

        Отличие от соседнего теста ровно одно — число потолка; значит пару
        остановил именно он, а не срок и не счёт «подряд».
        """
        await self._answered_twice(girl)

        got = await initiative_mod._quiet(girl, 3600, 3600, 1, 5)

        assert [one['uid'] for one in got] == ['u-1']
        assert [one['try_no'] for one in got] == [1], \
            'номер захода идёт от счёта «подряд», а он обнулён его ответом'

    async def test_yesterdays_cards_fall_out_of_the_day(self, girl):
        """⚠ Сутки **скользящие**: заход позавчерашний в потолок не идёт.

        Мерка та же, что у инвайтов (`invite_repeat_max_day`): полночь ничего не
        значит для того, кто читает сообщения, а 24 часа значат.
        """
        await _said(girl, 'u-1', direction='in', minutes_ago=1900)
        await _card_sent(girl, 'u-1', minutes_ago=30 * 60)
        # Он ответил уже после того захода — счёт «подряд» обнулён им.
        await _said(girl, 'u-1', direction='in', minutes_ago=29 * 60)
        await _said(girl, 'u-1', direction='out', minutes_ago=28 * 60)

        got = await initiative_mod._quiet(girl, 3600, 3600, 1, 1)

        assert [one['uid'] for one in got] == ['u-1'], \
            'заход тридцатичасовой давности держит пару вечно'


class TestTheTermIsDrawnPerPair:
    """⚠⚠ Срок тишины — интервал «от … до», и жребий внутри него устойчив.

    Одно число на сайт означало бы, что все затихшие пары оживают через
    одинаковое время после последней реплики — ряд сообщений по линейке.
    """

    def test_the_draw_stays_within_the_bounds(self):
        drawn = {initiative_mod._due_sec(7, f'u-{n}', '2026-10-03 19:00:00',
                                         3600, 7200)
                 for n in range(300)}

        assert min(drawn) >= 3600 and max(drawn) <= 7200
        assert len(drawn) > 100, 'разброса нет — жребий выдаёт одно и то же'

    def test_the_same_silence_keeps_the_same_term(self):
        """⚠⚠ Круг просыпается раз в минуту, и срок пары обязан не меняться.

        Свежий случай на каждом тике уносил бы пару в работу на первом низком
        выпадении — то есть почти сразу после «от», и «до» не значила бы ничего.
        """
        first = initiative_mod._due_sec(7, 'u-1', '2026-10-03 19:00:00',
                                        3600, 7200)
        again = initiative_mod._due_sec(7, 'u-1', '2026-10-03 19:00:00',
                                        3600, 7200)

        assert first == again

    def test_a_new_silence_gets_a_new_term(self):
        """Он ответил или ушла наша инициатива — время сдвинулось, срок другой."""
        before = initiative_mod._due_sec(7, 'u-1', '2026-10-03 19:00:00',
                                         3600, 7200)
        after = initiative_mod._due_sec(7, 'u-1', '2026-10-03 21:17:00',
                                        3600, 7200)

        assert before != after

    def test_the_upper_bound_below_the_lower_means_no_spread(self):
        """⚠ «До» ниже «от» или ноль — работаем ровно по «от», а не раньше."""
        assert initiative_mod._due_sec(7, 'u-1', 'x', 3600, 0) == 3600
        assert initiative_mod._due_sec(7, 'u-1', 'x', 3600, 1800) == 3600

    async def test_each_pair_waits_out_its_own_term(self, girl):
        """⚠⚠ Тишина прошла «от», но у каждой пары свой срок — ждёт своего.

        Восемь пар молчат **одинаково**, 61 минуту, при границах 60 и 600. Со
        старым одним числом ушли бы все восемь разом; с жребием уходят только те,
        кому он выпал ниже их тишины, — а их почти не бывает.

        ⚠ Ожидаемое считаем тем же `_due_sec`, которым считает отбор: жребий
        выводится из пары и времени её последней реплики, и обе стороны обязаны
        получить одно и то же число. Своя копия правила здесь означала бы тест,
        проверяющий сам себя.
        """
        uids = [f'u-{n}' for n in range(1, 9)]
        for uid in uids:
            await _said(girl, uid, direction='in', minutes_ago=200)
            await _said(girl, uid, direction='out', minutes_ago=61)

        due = {uid: initiative_mod._due_sec(girl, uid,
                                            await _last_at(girl, uid),
                                            3600, 36000)
               for uid in uids}
        ready = {uid for uid, term in due.items() if term <= 61 * 60}

        got = {one['uid'] for one in
               await initiative_mod._quiet(girl, 3600, 36000, 2)}

        assert got <= ready, \
            f'взяли раньше жребия: {sorted(got - ready)} при сроках {due}'
        assert len(got) == min(len(ready), initiative_mod.INITIATIVE_PER_PROFILE)

    async def test_silence_past_the_upper_bound_always_goes(self, girl):
        """Тишина длиннее «до» — берём при любом выпадении жребия."""
        await _said(girl, 'u-1', direction='in', minutes_ago=600)
        await _said(girl, 'u-1', direction='out', minutes_ago=500)

        got = await initiative_mod._quiet(girl, 3600, 7200, 2)

        assert [one['uid'] for one in got] == ['u-1']


class TestTheKindIsNotOursAnymore:
    """⚠⚠ Приём бросает сосед, и здесь его быть не должно.

    Двенадцать приёмов с весами жили тут константой и жребием, а панель слала
    готовое. Теперь их бросает шаг «Жребий» у соседа: список приёмов — часть
    задания модели, и лежать он должен рядом с промптом, который его исполняет.

    ⚠ Тест сторожит **не отсутствие кода**, а то, что копия не вернётся. Вернись
    она — разойтись с промптом у неё займёт одну правку, и узнать об этом будет
    неоткуда: приём приедет разумный, просто не тот, о котором просит промпт.
    """

    def test_the_panel_does_not_draw_it(self):
        assert not hasattr(initiative_mod, 'initiative_kind'), \
            'жребий приёма вернулся в панель — он живёт у соседа'
        assert not hasattr(initiative_mod, 'INITIATIVE_KINDS'), \
            'список приёмов вернулся в панель — он живёт у соседа'

    def test_the_question_carries_no_task(self):
        """⚠⚠ Вопрос агенту **пуст**, и задания в нём больше нет.

        `prompt_user` у соседа означает «что сказал собеседник» — так и написано
        в промпте: «=== %companion_title% пишет === %prompt_user%». Здесь он не
        сказал ничего, потому мы и пишем первыми.

        Задание («заговори сама, приёмом из жребия») целиком лежит в промпте
        ветки «Инициатива», а ветку выбирает флаг `initiative`. Положи мы задание
        в вопрос — модель прочла бы его как слова человека, а шаг записи у соседа
        сохранил бы нашу инструкцию в переписку от его имени.
        """
        import inspect

        assert not hasattr(initiative_mod, 'INITIATIVE_PROMPT'), \
            'задание вернулось в вопрос — оно живёт в промпте ветки у соседа'

        code = inspect.getsource(initiative_mod)

        assert 'initiative=True' in code, \
            'ветку больше нечем выбрать: без флага заход уйдёт в ответ на реплику'


class TestTheSiteSettingsReachTheDice:
    """⚠⚠ Доли разнообразия крутит человек на странице сайта, а бросает сосед.

    Три настройки — имя, вопрос, рассказ — уже однажды перестали работать молча:
    переезд писем на `dating_talk` унёс код, который их читал, а ручки на
    странице остались и крутились впустую. Заметить это можно было только по
    десятку одинаковых писем.

    ⚠ Проверяем **отправку**, а не жребий: жребий у соседа, и его правильность
    стережёт его же сюита. Здесь вопрос один — доехало ли число.
    """

    async def test_the_percents_are_sent(self, monkeypatch, girl):
        from src.dating import answer as answer_mod

        got = {}

        async def _chat_talk(agent_id, question, variables=None):
            got.update(variables or {})

            return {'text': 'ответ', 'message_id': 0}

        monkeypatch.setattr(answer_mod, 'rhapsody_api_chat_talk', _chat_talk)

        async with mysql_get_db_async() as db:
            await db.execute('SELECT * FROM profile WHERE id = %s', (girl,))
            profile = dict(await db.fetchone())

        await answer_mod._agent_ask(7, 'вопрос', 'en', profile)

        for _, name, share in answer_mod.ANSWER_DICE_PERCENTS:
            assert name in got, f'доля «{name}» не уехала соседу'
            # ⚠ Проценты, а не доля: у соседа жребий считает в сотнях, и `0.4`
            # вместо `40` выпадал бы раз в двести пятьдесят писем.
            assert got[name] == str(int(share * 100)), \
                f'«{name}» уехала как {got[name]!r}, а не процентами'

    async def test_a_broken_setting_falls_back_to_the_default(self, monkeypatch,
                                                              girl):
        """⚠⚠ Не прочиталось — шлём умолчание, а не ноль.

        Ноль значит «никогда», и принять такое решение молча из-за недоступной
        базы нельзя: письма пошли бы все на одно лицо, а причину искали бы в
        промпте.
        """
        from src.dating import answer as answer_mod
        from src.dating import percent as percent_mod

        async def _boom(site_id, key):
            raise RuntimeError('база недоступна')

        monkeypatch.setattr(
            'src.uvicorn.routers.dating.site_.site_seconds', _boom)

        got = await percent_mod.percent_value(343, 'favorite_mail_name_percent',
                                              0.10)

        assert got == '10', f'вместо умолчания уехало {got!r}'


class TestItReachesTheAgent:
    """Переменные вызова: повод заговорить первой."""

    async def test_the_variables_are_sent(self, monkeypatch, girl):
        from src.dating import answer as answer_mod

        got = {}

        async def _chat_talk(agent_id, question, variables=None):
            got.update(variables or {})

            return {'text': 'ответ', 'message_id': 0}

        monkeypatch.setattr(answer_mod, 'rhapsody_api_chat_talk', _chat_talk)

        async with mysql_get_db_async() as db:
            await db.execute('SELECT * FROM profile WHERE id = %s', (girl,))
            profile = dict(await db.fetchone())

        await answer_mod._agent_ask(7, 'вопрос', 'en', profile,
                                    initiative=True)

        assert got['make_initiative'] == '1'
        # ⚠ Приёма среди переменных вызова быть не должно: его бросает сосед.
        assert 'initiative_kind' not in got, \
            'панель всё ещё шлёт приём — у соседа он больше не объявлен, будет 400'

    async def test_without_initiative_the_flag_is_zero(self, monkeypatch, girl):
        """⚠ Строкой `'0'`, а не `False`: у соседа `%if%` ложно для `'0'`, а
        `'False'` было бы истиной."""
        from src.dating import answer as answer_mod

        got = {}

        async def _chat_talk(agent_id, question, variables=None):
            got.update(variables or {})

            return {'text': 'ответ', 'message_id': 0}

        monkeypatch.setattr(answer_mod, 'rhapsody_api_chat_talk', _chat_talk)

        async with mysql_get_db_async() as db:
            await db.execute('SELECT * FROM profile WHERE id = %s', (girl,))
            profile = dict(await db.fetchone())

        await answer_mod._agent_ask(7, 'вопрос', 'en', profile)

        assert got['make_initiative'] == '0'
        assert 'initiative_kind' not in got


class TestTheWholeTripSurvivesTheExchangeNumber:
    """⚠⚠ Задача 7б: живая поломка на двойной распаковке `_agent_ask`.

    `_agent_ask` когда-то отдавала пару `(текст, ошибка)`, потом переехала на
    тройку `(текст, ошибка, номер обмена)` — номер нужен, чтобы позже отметить
    доставку. Все вызывающие переписали на тройку, кроме одного: `initiative.py`
    остался на `text, error = await _agent_ask(...)`, и круг инициативы падал на
    первом же заходе `ValueError: too many values to unpack (expected 2)`.

    Починка это исправила, но её саму ни один тест не трогал: сочинение
    инициативы целиком (`_one`) в сюите не прогонялось. Следующий откат —
    например, правка `answer.py`, вернувшая старую пару, — всплыл бы снова
    только в бою, тем же `ValueError`, а не здесь.

    Тест гонит `_one` как её гонит `initiative_make`: с живым профилем, живым
    пулом и подменённым агентом. Падение на распаковке он ловит самим фактом
    прогона; а то, что номер обмена действительно доезжает до строки пула
    (`chat_initiative.talk_message_id`), проверяет прямым запросом — именно он
    нужен круга отправки, чтобы потом отметить доставку у соседа.
    """

    async def test_one_card_carries_the_exchange_number_to_the_pool(
            self, monkeypatch, girl):
        from src.dating import answer as answer_mod

        async def _chat_talk(agent_id, question, variables=None):
            return {'text': 'как прошли выходные?', 'message_id': 555111}

        monkeypatch.setattr(answer_mod, 'rhapsody_api_chat_talk', _chat_talk)

        async with mysql_get_db_async() as db:
            await db.execute('SELECT * FROM profile WHERE id = %s', (girl,))
            profile = dict(await db.fetchone())

        # Без тройки здесь уже упало бы `ValueError: too many values to
        # unpack` — ровно то, что случилось на проде.
        got = await initiative_mod._one(
            profile, 7, {'uid': 'u-1', 'name': 'Brian', 'try_no': 1})

        assert got == 'made', f'карточка не сложилась: {got}'

        async with mysql_get_db_async() as db:
            await db.execute(
                "SELECT talk_message_id FROM chat_initiative"
                " WHERE profile_id = %s AND uid = 'u-1'", (girl,))
            row = await db.fetchone()

        assert row is not None, 'карточка не легла в пул'
        assert int(row['talk_message_id'] or 0) == 555111, \
            'номер обмена потерялся по дороге от агента до строки пула'


class TestTheTwoSwitches:
    """⚠⚠ Флага два: набор и отправка."""

    async def test_both_are_off_by_default(self, auth_client):
        got = (await auth_client.get(
            f'/api/dating/site/{STAND_SITE}/chat-initiative')).json()['result']

        assert got['chat_initiative_on'] is False
        assert got['chat_initiative_send_on'] is False

    async def test_they_switch_apart(self, auth_client):
        """Набор можно поднять, не поднимая отправку, — это и есть рабочий порядок."""
        try:
            await auth_client.put(
                f'/api/dating/site/{STAND_SITE}/chat-initiative-on',
                json={'chat_initiative_on': True})

            got = (await auth_client.get(
                f'/api/dating/site/{STAND_SITE}/chat-initiative')).json()['result']

            assert got['chat_initiative_on'] is True
            assert got['chat_initiative_send_on'] is False, \
                'набор потянул за собой отправку — пул некому прочитать'
        finally:
            await auth_client.put(
                f'/api/dating/site/{STAND_SITE}/chat-initiative-on',
                json={'chat_initiative_on': False})

    async def test_a_silent_site_is_skipped(self, monkeypatch):
        """Оба флага сняты — круг про сайт не вспоминает."""
        got = await round_mod.initiative_round_run()

        assert got['made'] == 0 and got['sent'] == 0


class TestStaleCards:
    """⚠⚠ Он ответил — карточка стала вредна."""

    async def test_an_answered_pair_loses_its_card(self, girl):
        """Текст сочинялся под тишину: уйдя после его реплики, он прозвучит мимо
        разговора — он спросил о другом, а девушка будто не прочитала."""
        await chat_initiative_put(STAND_SITE, girl, 'u-1', 'Brian', 'как выходные?')
        # Он ответил уже после того, как карточка легла.
        await _said(girl, 'u-1', direction='in', minutes_ago=0)

        gone = await round_mod._stale_clean(STAND_SITE)

        assert gone >= 1
        assert await chat_initiative_waiting(girl, 'u-1') == {}

    async def test_a_silent_pair_keeps_its_card(self, girl):
        """Молчит — карточка ждёт своего часа."""
        await _said(girl, 'u-1', direction='in', minutes_ago=300)
        await chat_initiative_put(STAND_SITE, girl, 'u-1', 'Brian', 'как выходные?')

        await round_mod._stale_clean(STAND_SITE)

        assert await chat_initiative_waiting(girl, 'u-1'), 'карточку сняли зря'


class TestOfflineGirlLosesHerCards:
    """⚠⚠ Девушка ушла из сети — её карточки уходят из пула.

    То же правило, что у рассылки, инвайтов и приставаний (`pool.py`): черновик от
    анкеты не в сети отправить некому, а место в пуле он занимает.

    ⚠ Инициативе это нужнее прочих: её тексты привязаны ко времени сильнее всего —
    «расскажи про свой день», «спроси про планы на вечер», «у нас третий день
    дождь». Пролежав до завтра, такая карточка врёт не тоном, а содержанием.
    """

    def test_the_pool_is_in_the_cleaner_list(self):
        from src.dating.pool import POOL_TABLES

        assert 'chat_initiative' in POOL_TABLES, \
            'карточки инициативы переживут уход анкеты'

    def test_chat_reply_is_not(self):
        """⚠ А черновик ответа — нет, и это не забывчивость.

        Он отвечает **конкретной реплике** и ждёт собеседника, а не свежести:
        вопрос, заданный вчера, ответа не теряет.
        """
        from src.dating.pool import POOL_TABLES

        assert 'chat_reply' not in POOL_TABLES

    async def test_leaving_drops_the_card(self, girl):
        """Уход по концу смены (`pool_drop`) уносит карточку."""
        from src.dating.pool import pool_drop

        await chat_initiative_put(STAND_SITE, girl, 'u-1', 'Brian', 'как выходные?')
        got = await pool_drop(girl, 'смена кончилась')

        assert got.get('chat_initiative') == 1
        assert await chat_initiative_waiting(girl, 'u-1') == {}

    async def test_the_sweeper_catches_the_rest(self, girl):
        """⚠ Страховка на выходы, о которых мы не узнали: сайт выбросил сессию,
        вход не признали, статус сбился в `error`."""
        from src.dating.pool import pool_sweep

        await chat_initiative_put(STAND_SITE, girl, 'u-1', 'Brian', 'как выходные?')

        async with mysql_get_db_async() as db:
            await db.execute("UPDATE profile SET status = 'error' WHERE id = %s",
                             (girl,))
            await db.connection.commit()

        await pool_sweep(STAND_SITE)

        assert await chat_initiative_waiting(girl, 'u-1') == {}

    async def test_sent_cards_survive(self, girl):
        """⚠ Ушедшее — история: по ней считаются заходы. Сотри мы её, вернувшаяся
        анкета начала бы счёт заново и написала бы третий раз при пределе два."""
        from src.dating.pool import pool_drop

        async with mysql_get_db_async() as db:
            await db.execute(
                'INSERT INTO chat_initiative (site_id, profile_id, uid, name, body,'
                " try_no, status, sent_at) VALUES (%s, %s, 'u-1', 'Brian', 'ушло',"
                " 1, 'sent', NOW())", (STAND_SITE, girl))
            await db.connection.commit()

        await pool_drop(girl, 'смена кончилась')

        async with mysql_get_db_async() as db:
            await db.execute("SELECT COUNT(*) AS n FROM chat_initiative"
                             " WHERE profile_id = %s AND status = 'sent'", (girl,))
            row = await db.fetchone()

        assert int(row['n']) == 1, 'чистка унесла историю заходов'


class TestBothMustBeOnline:
    """⚠⚠ Отправка идёт, только когда в сети **оба**."""

    async def test_a_sleeping_girl_sends_nothing(self, girl):
        """Девушка не в сети — карточку даже не берут в работу.

        Проверка стоит **в запросе** (`chat_initiative_ready`), а не после: иначе
        круг набирал бы карточки спящих и каждый раз откладывал их, не доходя до
        тех, что можно отправить.
        """
        from src.dating.chat_initiative import chat_initiative_ready

        await chat_initiative_put(STAND_SITE, girl, 'u-1', 'Brian', 'текст')

        async with mysql_get_db_async() as db:
            await db.execute("UPDATE profile SET status = 'offline' WHERE id = %s",
                             (girl,))
            await db.connection.commit()

        ready = await chat_initiative_ready(STAND_SITE)

        assert not [one for one in ready if int(one['profile_id']) == girl]

    async def test_a_sleeping_companion_keeps_the_card(self, monkeypatch, girl):
        """⚠ Собеседник не в сети — карточка **ждёт**, а не гибнет.

        Он вернётся, и она уйдёт: ровно как черновик ответа. Пометки о неудаче
        тоже не ставим — неудачи не было.
        """
        round_mod._sent_at.clear()
        await chat_initiative_put(STAND_SITE, girl, 'u-1', 'Brian', 'текст')
        monkeypatch.setattr(round_mod, 'online_check',
                            lambda site_id, uid: {'online': False, 'known': True})

        sent = await round_mod._send(STAND_SITE)

        assert sent == 0
        assert await chat_initiative_waiting(girl, 'u-1'), 'карточку потеряли'

    async def test_both_online_sends(self, monkeypatch, girl):
        """Оба в сети — реплика уходит."""
        outbox = []

        async def _send(profile, presence, uid, text):
            outbox.append(text)

            return {'ok': True, 'error': ''}

        round_mod._sent_at.clear()
        await chat_initiative_put(STAND_SITE, girl, 'u-1', 'Brian', 'текст')
        monkeypatch.setattr(round_mod, 'online_check',
                            lambda site_id, uid: {'online': True, 'known': True})
        monkeypatch.setattr(round_mod, 'send_message', _send)

        sent = await round_mod._send(STAND_SITE)

        assert sent == 1 and outbox == ['текст']
        assert await chat_initiative_waiting(girl, 'u-1') == {}, 'карточка не ушла'


class TestThePauseBetweenSends:
    """⚠⚠ По одной карточке за паузу, и пауза общая на весь сайт.

    Беда, ради которой написано: пул набирается сразу **всем анкетам сайта**, а
    круг уносил по три карточки за тик, не выжидая ничего. Включённая инициатива
    выглядела залпом — десяток девушек пишет первыми в одну минуту, нередко одним
    и тем же людям, сидящим в ленте. Со стороны это не «разговоры
    возобновились», а рассылка.

    ⚠⚠ **Речь только об инициативе в чате** — продолжении затихшего разговора с
    тем, кто девушке уже отвечал. Прочие занятия меряются своими сроками, и эта
    пауза их не касается: она живёт в своём словаре и читается из своей
    настройки.
    """

    @staticmethod
    def _outbox(monkeypatch, online: bool = True) -> list:
        """Отправка и лента — заглушками; наружу сюита не ходит."""
        outbox = []

        async def _send(profile, presence, uid, text):
            outbox.append(text)

            return {'ok': True, 'error': ''}

        monkeypatch.setattr(round_mod, 'online_check',
                            lambda site_id, uid: {'online': online,
                                                  'known': True})
        monkeypatch.setattr(round_mod, 'send_message', _send)
        round_mod._sent_at.clear()

        return outbox

    async def test_only_one_card_leaves_per_pause(self, monkeypatch, girl):
        """Два готовых черновика — уходит один, второй ждёт своей паузы."""
        outbox = self._outbox(monkeypatch)

        await chat_initiative_put(STAND_SITE, girl, 'u-1', 'Brian', 'первый')
        await chat_initiative_put(STAND_SITE, girl, 'u-2', 'Colin', 'второй')

        assert await round_mod._send(STAND_SITE) == 1
        assert await round_mod._send(STAND_SITE) == 0, \
            'вторая карточка ушла сразу — паузы нет'
        assert outbox == ['первый'], 'очередь не от старой карточки к свежей'
        assert await chat_initiative_waiting(girl, 'u-2'), 'вторую потеряли'

    async def test_the_waiting_card_goes_when_the_pause_runs_out(self, monkeypatch,
                                                                 girl):
        """Пауза вышла — ждавшая карточка уходит следующим заходом."""
        import time

        outbox = self._outbox(monkeypatch)

        await chat_initiative_put(STAND_SITE, girl, 'u-1', 'Brian', 'первый')
        await chat_initiative_put(STAND_SITE, girl, 'u-2', 'Colin', 'второй')

        await round_mod._send(STAND_SITE)
        # Отодвигаем отметку на умолчание (три минуты) с запасом.
        round_mod._sent_at[STAND_SITE] = time.time() - 200

        assert await round_mod._send(STAND_SITE) == 1
        assert outbox == ['первый', 'второй']

    async def test_the_pause_covers_the_whole_site(self, monkeypatch, girl,
                                                   auth_client):
        """⚠⚠ Пауза **на сайт**, а не на анкету: залп и получается из того, что
        пишут все разом.

        Предел на каждую девушку его не разредил бы — двенадцать анкет дали бы
        двенадцать реплик в одну минуту, каждая «в рамках своей паузы».
        """
        outbox = self._outbox(monkeypatch)

        other = int((await auth_client.post('/api/dating/profile', json={
            'site_id': STAND_SITE,
            'login': f'init-pause-{uuid.uuid4().hex[:8]}@example.com',
            'password': 'x'})).json()['result'])

        try:
            async with mysql_get_db_async() as db:
                await db.execute("UPDATE profile SET status = 'online'"
                                 ' WHERE id = %s', (other,))
                await db.connection.commit()

            await chat_initiative_put(STAND_SITE, girl, 'u-1', 'Brian', 'её')
            await chat_initiative_put(STAND_SITE, other, 'u-2', 'Colin', 'соседки')

            assert await round_mod._send(STAND_SITE) == 1
            assert await round_mod._send(STAND_SITE) == 0, \
                'вторая девушка написала в ту же минуту — пауза считается по анкете'
            assert len(outbox) == 1
        finally:
            async with mysql_get_db_async() as db:
                await db.execute('DELETE FROM chat_initiative WHERE profile_id = %s',
                                 (other,))
                await db.execute('DELETE FROM profile WHERE id = %s', (other,))
                await db.connection.commit()

    async def test_nobody_online_does_not_spend_the_pause(self, monkeypatch, girl):
        """⚠⚠ Отправлять некому — пауза не тратится.

        Она мерит промежутки между **ушедшими** репликами, а не между заходами
        круга: отметь мы заход впустую, первая же тихая минута отодвинула бы
        готовую карточку на целую паузу.
        """
        self._outbox(monkeypatch, online=False)
        await chat_initiative_put(STAND_SITE, girl, 'u-1', 'Brian', 'текст')

        assert await round_mod._send(STAND_SITE) == 0
        assert STAND_SITE not in round_mod._sent_at, \
            'пустой заход съел паузу'

    async def test_a_site_refusal_ends_the_run(self, monkeypatch, girl):
        """⚠ Отказ сайта кончает заход, а не ведёт ко второй карточке.

        Причина у него почти всегда общая — сессия протухла, сайт попросил
        подождать, — и дослать вторую значило бы собрать тот же отказ, кормя
        счётчик обращений сайта.
        """
        tries = []

        async def _send(profile, presence, uid, text):
            tries.append(text)

            return {'ok': False, 'error': 'сайт отказал'}

        monkeypatch.setattr(round_mod, 'online_check',
                            lambda site_id, uid: {'online': True, 'known': True})
        monkeypatch.setattr(round_mod, 'send_message', _send)
        round_mod._sent_at.clear()

        await chat_initiative_put(STAND_SITE, girl, 'u-1', 'Brian', 'первый')
        await chat_initiative_put(STAND_SITE, girl, 'u-2', 'Colin', 'второй')

        assert await round_mod._send(STAND_SITE) == 0
        assert tries == ['первый'], 'заход пошёл за второй карточкой после отказа'
        assert (await chat_initiative_waiting(girl, 'u-1'))['error'], \
            'причина отказа не записана — круг возьмётся за ту же строку снова'

    async def test_the_pause_is_a_site_setting(self, auth_client):
        """⚠ Срок берётся из настройки сайта и считается в **минутах**.

        Умолчание — три минуты: инициатива возвращается к **начатому** разговору,
        и спешить ей некуда — человек уже знаком с девушкой и зайдёт в чат ещё.
        """
        assert await round_mod._send_every(STAND_SITE) == 3 * 60, \
            'умолчание разошлось с реестром'

        before = (await auth_client.get(
            f'/api/dating/site/{STAND_SITE}')).json()['result']['settings']
        try:
            await auth_client.put(
                f'/api/dating/site/{STAND_SITE}',
                json={'settings': {**before,
                                   'chat_initiative_send_every': '7'}})

            assert await round_mod._send_every(STAND_SITE) == 7 * 60, \
                'круг не видит паузу, поставленную на странице'
        finally:
            await auth_client.put(f'/api/dating/site/{STAND_SITE}',
                                  json={'settings': before})

    async def test_zero_means_no_pause(self, monkeypatch, auth_client, girl):
        """⚠ Ноль — законное «без паузы», а не «не отправлять».

        Выключатель у занятия свой (`chat_initiative_send_on`), и отменять
        отправку сроком значило бы два выключателя об одном. На деле ноль значит
        «по карточке в минуту»: чаще круг не просыпается.
        """
        outbox = self._outbox(monkeypatch)

        before = (await auth_client.get(
            f'/api/dating/site/{STAND_SITE}')).json()['result']['settings']
        try:
            await auth_client.put(
                f'/api/dating/site/{STAND_SITE}',
                json={'settings': {**before,
                                   'chat_initiative_send_every': '0'}})

            assert await round_mod._send_every(STAND_SITE) == 0

            await chat_initiative_put(STAND_SITE, girl, 'u-1', 'Brian', 'первый')
            await chat_initiative_put(STAND_SITE, girl, 'u-2', 'Colin', 'второй')

            assert await round_mod._send(STAND_SITE) == 1
            assert await round_mod._send(STAND_SITE) == 1, \
                'при нулевой паузе круг всё равно выжидает'
            assert outbox == ['первый', 'второй']
        finally:
            await auth_client.put(f'/api/dating/site/{STAND_SITE}',
                                  json={'settings': before})

    def test_the_old_constant_is_gone(self):
        """⚠⚠ Константа «три карточки за тик» не должна вернуться.

        Она отвечала на тот же вопрос, что настройка, — «сколько реплик уходит
        разом», — и вернись она, частота считалась бы в двух местах: круг выжидал
        бы паузу и всё равно отправлял тройку.
        """
        assert not hasattr(round_mod, 'INITIATIVE_ROUND_SEND'), \
            'частота отправки снова в коде — её задаёт настройка сайта'
