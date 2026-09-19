"""Ответ в чате пишется заранее и ждёт собеседника в сети.

## ⚠⚠ Зачем

Ответ сочинялся и уходил одним движением: нет собеседника в сети — круг уходил, не
спросив агента. По деньгам верно, но у этого две цены.

Первая: в панели пусто. «Ответ готовится» ничем не отличалось от «ничего не
происходит» — с этого вопроса всё и началось.

Вторая: собеседник вернулся и ждёт ещё круг плюс десяток секунд на LLM.

Теперь текст пишется сразу, ложится строкой в `chat_reply` и уходит первым же
кругом, который застал человека в сети. Механизм тот же, что у писем
(`mail_reply`): строка со `status = 'draft'`, показ последней репликой разговора,
отправка отдельно от сочинения.

⚠ **Наружу сюита не ходит** (`testing.md`): агент и отправка подменяются.
"""

import uuid

import pytest

from mysql_.mysql_ import mysql_get_db_async

from src.dating import answer as answer_mod
from src.dating.chat_reply import (chat_reply_failed, chat_reply_pending,
                                   chat_reply_put, chat_reply_sent,
                                   chat_reply_waiting)

pytestmark = pytest.mark.chat

STAND_SITE = 999_343


@pytest.fixture
async def girl(auth_client):
    profile_id = int((await auth_client.post('/api/dating/profile', json={
        'site_id': STAND_SITE,
        'login': f'draft-{uuid.uuid4().hex[:8]}@example.com',
        'password': 'x'})).json()['result'])

    yield profile_id

    async with mysql_get_db_async() as db:
        # ⚠ Черновики уходят каскадом за репликами, но анкету чистим явно:
        # порядок удаления тут важнее краткости.
        await db.execute('DELETE FROM chat_reply WHERE profile_id = %s', (profile_id,))
        await db.execute('DELETE FROM message WHERE profile_id = %s', (profile_id,))
        await db.execute('DELETE FROM profile WHERE id = %s', (profile_id,))
        await db.connection.commit()


async def _said(profile_id: int, uid: str, text: str = 'привет',
                direction: str = 'in') -> int:
    """Реплика в переписке. Возвращает её номер — он же повод написать."""
    async with mysql_get_db_async() as db:
        await db.execute(
            'INSERT INTO message (profile_id, sender_uid, sender_name, text,'
            '  message_uid, direction) VALUES (%s, %s, %s, %s, %s, %s)',
            (int(profile_id), uid, 'Someone', text,
             f'zz-{uuid.uuid4().hex[:12]}', direction))
        await db.connection.commit()

        return int(db.lastrowid)


class TestTheDraftLives:
    """Черновик кладётся, находится и снимается."""

    async def test_it_is_found_by_its_reason(self, girl):
        message_id = await _said(girl, 'u-1')
        await chat_reply_put(STAND_SITE, girl, message_id, 'u-1', 'Brian', 'ответ')

        got = await chat_reply_waiting(girl, message_id)

        assert got['body'] == 'ответ'

    async def test_a_new_reason_has_no_draft(self, girl):
        """⚠⚠ Протухание даром: написал он снова — повод другой.

        Ключ стоит на реплике, а не на паре. Иначе пришлось бы сверять времена
        руками и однажды отправить ответ на позавчерашнее.
        """
        first = await _said(girl, 'u-1', 'первое')
        await chat_reply_put(STAND_SITE, girl, first, 'u-1', 'Brian', 'ответ')
        second = await _said(girl, 'u-1', 'второе')

        assert await chat_reply_waiting(girl, second) == {}, \
            'к новой реплике подошёл старый черновик'

    async def test_twice_is_one_draft(self, girl):
        """⚠ Круг ходит раз в минуту и запросто зайдёт дважды на ту же реплику."""
        message_id = await _said(girl, 'u-1')
        await chat_reply_put(STAND_SITE, girl, message_id, 'u-1', 'Brian', 'первый')
        await chat_reply_put(STAND_SITE, girl, message_id, 'u-1', 'Brian', 'второй')

        async with mysql_get_db_async() as db:
            await db.execute('SELECT COUNT(*) AS n, MIN(body) AS body'
                             ' FROM chat_reply WHERE message_id = %s', (message_id,))
            row = await db.fetchone()

        assert int(row['n']) == 1, 'второй заход завёл второй ответ тому же человеку'
        assert row['body'] == 'первый', 'повтор перетёр уже написанное'

    async def test_sent_leaves_the_pool(self, girl):
        message_id = await _said(girl, 'u-1')
        await chat_reply_put(STAND_SITE, girl, message_id, 'u-1', 'Brian', 'ответ')
        got = await chat_reply_waiting(girl, message_id)
        await chat_reply_sent(int(got['id']))

        assert await chat_reply_waiting(girl, message_id) == {}

    async def test_failure_keeps_it_waiting(self, girl):
        """⚠ Неудача оставляет черновик в пуле с причиной, а не хоронит его.

        `failed` похоронил бы ответ на живую реплику, и оживить его можно было бы
        только правкой базы руками.
        """
        message_id = await _said(girl, 'u-1')
        await chat_reply_put(STAND_SITE, girl, message_id, 'u-1', 'Brian', 'ответ')
        got = await chat_reply_waiting(girl, message_id)
        await chat_reply_failed(int(got['id']), 'сайт отказал')

        again = await chat_reply_waiting(girl, message_id)

        assert again, 'черновик пропал после неудачи'
        assert again['error'] == 'сайт отказал'


class TestItShowsInTheTalk:
    """Черновик виден в разговоре — иначе всё это незачем."""

    async def test_the_pending_reply_is_returned(self, girl):
        message_id = await _said(girl, 'u-1')
        await chat_reply_put(STAND_SITE, girl, message_id, 'u-1', 'Brian', 'ответ')

        got = await chat_reply_pending(girl, 'u-1')

        assert [one['body'] for one in got] == ['ответ']

    async def test_a_stale_draft_is_hidden(self, girl):
        """⚠⚠ Показываем только тот, чей повод всё ещё последний.

        Иначе рядом со свежей репликой висел бы ответ на предыдущую — человек
        прочитал бы его как наш следующий ход, хотя круг его уже не отправит.
        """
        first = await _said(girl, 'u-1', 'первое')
        await chat_reply_put(STAND_SITE, girl, first, 'u-1', 'Brian', 'старый ответ')
        await _said(girl, 'u-1', 'второе')

        assert await chat_reply_pending(girl, 'u-1') == []

    async def test_the_endpoint_puts_it_last(self, auth_client, girl):
        """⚠ В ленте он **последний** и **без времени**.

        Поставь мы `created_at`, строка встала бы между репликами и читалась бы
        как отправленная: будто ответ ушёл, а собеседник продолжил, не заметив.
        """
        message_id = await _said(girl, 'u-1', 'вопрос')
        await chat_reply_put(STAND_SITE, girl, message_id, 'u-1', 'Brian', 'ответ')

        got = (await auth_client.get(
            f'/api/dating/profile/{girl}/message?uid=u-1')).json()['result']

        # Ручка отдаёт свежими сверху — ждущее идёт первым.
        assert got[0]['pending'] is True
        assert got[0]['text'] == 'ответ'
        assert got[0]['created_at'] is None, 'у черновика появилось время'
        assert got[0]['direction'] == 'out'

    async def test_the_list_preview_stays_clean(self, auth_client, girl):
        """⚠ В предпросмотр списка черновик **не** идёт.

        Страница берёт оттуда одну последнюю реплику в строку: черновик в ней
        читался бы как «уже ответили» про разговор, который ждёт отправки.
        """
        message_id = await _said(girl, 'u-1', 'вопрос')
        await chat_reply_put(STAND_SITE, girl, message_id, 'u-1', 'Brian', 'ответ')

        got = (await auth_client.get(
            f'/api/dating/chat/dialogs?site_id={STAND_SITE}&profile_id={girl}')
        ).json()['result']
        row = next(one for one in got['contacts'] if one['uid'] == 'u-1')

        assert [one['text'] for one in row['messages_last']] == ['вопрос']


class TestTheRoundWritesAhead:
    """⚠⚠ Главное: офлайн больше не значит «не спрашиваем агента»."""

    @pytest.fixture
    def quiet(self, monkeypatch):
        """Агент отвечает, отправка считается; «в сети» задаётся тестом."""
        sent = []

        # ⚠ Подпись повторяет живую целиком, включая `anyway`: заглушка без него
        # ловила бы `TypeError` вместо проверки правила.
        async def _ask(agent_id, question, language, profile, history='', uid='',
                       anyway=False):
            return 'готовый ответ', ''

        async def _send(profile, presence, uid, text):
            sent.append(text)

            return {'ok': True, 'error': ''}

        monkeypatch.setattr(answer_mod, '_agent_ask', _ask)
        monkeypatch.setattr(answer_mod, 'send_message', _send)

        return sent

    def _offline(self, monkeypatch):
        monkeypatch.setattr(answer_mod, 'online_check',
                            lambda site_id, uid: {'online': False, 'known': True})

    def _online(self, monkeypatch):
        monkeypatch.setattr(answer_mod, 'online_check',
                            lambda site_id, uid: {'online': True, 'known': True})

    async def _run(self, girl):
        async with mysql_get_db_async() as db:
            await db.execute('SELECT * FROM profile WHERE id = %s', (girl,))
            profile = dict(await db.fetchone())

        return await answer_mod.answer_run(profile, {}, 7)

    async def test_offline_still_writes_the_answer(self, monkeypatch, quiet, girl):
        message_id = await _said(girl, 'u-1', 'вопрос')
        self._offline(monkeypatch)

        got = await self._run(girl)

        assert got['asked'] == 1, 'агента не спросили — черновика не будет'
        assert got['drafted'] == 1
        assert quiet == [], 'ответ ушёл человеку, которого нет в сети'
        assert (await chat_reply_waiting(girl, message_id))['body'] == 'готовый ответ'

    async def test_the_waiting_draft_goes_out(self, monkeypatch, quiet, girl):
        """Собеседник вернулся — ответ уходит, и агента не спрашивают снова."""
        message_id = await _said(girl, 'u-1', 'вопрос')
        self._offline(monkeypatch)
        await self._run(girl)

        self._online(monkeypatch)
        got = await self._run(girl)

        assert quiet == ['готовый ответ']
        assert got['asked'] == 0, 'за готовый текст заплатили второй раз'
        assert got['sent'] == 1
        assert await chat_reply_waiting(girl, message_id) == {}

    async def test_online_writes_and_sends_at_once(self, monkeypatch, quiet, girl):
        """Человек в сети — ничего не ждём: пишем и тут же отправляем."""
        await _said(girl, 'u-1', 'вопрос')
        self._online(monkeypatch)

        got = await self._run(girl)

        assert got['drafted'] == 1 and got['sent'] == 1
        assert quiet == ['готовый ответ']

    async def test_a_failed_send_keeps_the_draft(self, monkeypatch, quiet, girl):
        """Отказ сайта не стоит нового вызова LLM: текст остаётся."""
        message_id = await _said(girl, 'u-1', 'вопрос')

        async def _send(profile, presence, uid, text):
            return {'ok': False, 'error': 'сайт отказал'}

        monkeypatch.setattr(answer_mod, 'send_message', _send)
        self._online(monkeypatch)
        await self._run(girl)

        again = await chat_reply_waiting(girl, message_id)

        assert again['body'] == 'готовый ответ'
        assert again['error'] == 'сайт отказал'

    async def test_the_retry_pause_does_not_hold_a_draft(self, monkeypatch, quiet, girl):
        """⚠⚠ Выдержка повтора сторожит вызовы LLM, а не отправку.

        После сочинения ставится `answer_tried_at`, и пара выпадала бы из выборки
        на `ANSWER_RETRY_SEC`. Готовый текст пролежал бы эти пять минут при
        собеседнике в сети — ровно та задержка, ради которой всё делалось.
        """
        await _said(girl, 'u-1', 'вопрос')
        self._offline(monkeypatch)
        await self._run(girl)

        # Выдержку не двигаем: попытка отмечена только что, и без послабления
        # пара в выборку не попала бы.
        self._online(monkeypatch)
        got = await self._run(girl)

        assert got['sent'] == 1, 'готовый ответ ждал выдержки повтора'
