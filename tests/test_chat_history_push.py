"""⚠⚠ Входящая реплика едет к соседу сразу, а не когда агент решит ответить.

Путь в `companion_session` был один — прогон workflow из `answer.py`, — а он
проходим только при **ответе**. Сказанное, на что мы смолчали, у соседа не
появлялось вовсе: ни ошибки, ни следа. Замерено на проде 02.10.2026 —
двенадцать таких реплик за восемь дней, среди потерянного «how did you
sleep???» и «sent you photo».

⚠⚠ **Дорог две, и правило у них одно.** Прежде доклад жил приватной функцией
внутри `chat.py`, и импорт из панели не докладывал **ничего** — а у сайта без
сокета это единственная дорога реплики. У Step2Love агент видел только ту
реплику, на которую отвечал прямо сейчас: ни своего зачина, ни прежних его
слов. Поймано живьём на паре Anastasiya ↔ Semen 02.11.2026 — агент ответил
«I'm glad you wrote» человеку, которому написали первыми мы.

Поэтому доклад теперь общая дверь `chat_history_incoming`, и здесь проверяются
**обе** дороги: сокет кладёт реплику сразу, импорт — со своим потолком на
заход.

⚠⚠ Своей бедой приём сообщения не роняется: оно к этому моменту уже лежит в
`message`, и исключение отменило бы запись. Это здесь и проверяется отдельно.

Наружу не ходит ничего: ручка соседа подменена.
"""

import json
import uuid

import pytest

from mysql_.mysql_ import mysql_get_db_async
from src.dating import chat
from src.dating.chat import _message_save
from src.uvicorn.routers.dating.profile_ import (DatingProfileSave,
                                                 profile_create,
                                                 profile_delete)

pytestmark = pytest.mark.chat

SITE = 999_343
UID = '329110'


@pytest.fixture
async def girl(rhapsody):
    """Анкета с именем и номером на сайте — из них собирается ключ."""
    made = await profile_create(
        DatingProfileSave(site_id=SITE, login=f'push-{uuid.uuid4().hex[:8]}',
                          password='x'), user=None)
    profile_id = int(made['result'])

    async with mysql_get_db_async() as db:
        await db.execute('UPDATE profile SET profile = %s WHERE id = %s',
                         (json.dumps({'name': 'Elena', 'id': '599939'}),
                          profile_id))
        await db.connection.commit()

    yield profile_id

    try:
        await profile_delete(profile_id, user=None)
    except Exception:
        pass


@pytest.fixture
def pushed(monkeypatch):
    """Ручка соседа: запоминает, что ей отдали."""
    seen = []

    async def _push(owner, companion, text, kind='chat', source_uid='',
                    said_at=''):
        # ⚠ `said_at` запоминается наравне с прочим: без него лента у соседа
        # встаёт временем доклада, а не временем реплики, и привезённая
        # переписка выглядит случившейся в одну минуту.
        seen.append({'owner': owner, 'companion': companion, 'text': text,
                     'kind': kind, 'source_uid': source_uid,
                     'said_at': said_at})

        return 777

    monkeypatch.setattr('src.rhapsody.rhapsody_api.rhapsody_api_chat_incoming',
                        _push)

    return seen


def _message(text: str = 'how did you sleep???') -> dict:
    return {'sender_uid': UID, 'sender_name': 'Brian Ritter', 'text': text,
            'message_uid': f'push-{uuid.uuid4().hex[:12]}'}


class TestIncomingGoesToHistory:
    async def test_reply_is_pushed_with_keys(self, girl, pushed):
        """⚠⚠ Ради этого всё: реплика уехала, ключи собраны верно."""
        assert await _message_save(girl, _message())
        assert len(pushed) == 1

        got = pushed[0]

        assert got['owner'] == '599939_elena', 'ключ анкеты собран не так'
        assert got['companion'] == '329110_brian_ritter'
        assert got['text'] == 'how did you sleep???'
        assert got['kind'] == 'chat'
        assert got['source_uid'], 'без ключа источника повтор задвоит строку'

    async def test_duplicate_message_is_not_pushed(self, girl, pushed):
        """Повтор того же `message_uid` не записался — и не уехал."""
        one = _message()

        assert await _message_save(girl, one)
        assert not await _message_save(girl, one), 'повтор не должен записаться'
        assert len(pushed) == 1, 'уехало дважды то, что записалось один раз'

    async def test_push_failure_does_not_break_saving(self, girl, monkeypatch):
        """⚠⚠ Сосед недоступен — сообщение всё равно записано.

        Оно к этому моменту уже лежит в `message`: исключение отсюда отменило
        бы запись живого сообщения ради строки в чужой истории.
        """
        async def _boom(*a, **kw):
            raise RuntimeError('сосед недоступен')

        monkeypatch.setattr(
            'src.rhapsody.rhapsody_api.rhapsody_api_chat_incoming', _boom)

        assert await _message_save(girl, _message()), \
            'приём сообщения упал из-за чужой истории'

    async def test_key_falls_back_like_the_agent_call(self, girl, pushed):
        """⚠⚠ У анкеты без номера сайта ключ тот же, что при вызове агента.

        `agent_site_uid` пусто не возвращает никогда — последней ступенью
        берётся наш внутренний номер. Это не недосмотр: ключ у приёма и у
        вызова агента обязан быть ОДИН, иначе реплика ляжет под одну запись
        собеседника, а ответ — под другую, и разговор разъедется надвое.
        """
        from src.dating.agent import agent_vars

        async with mysql_get_db_async() as db:
            await db.execute('UPDATE profile SET profile = %s WHERE id = %s',
                             (json.dumps({'name': 'Elena'}), girl))
            await db.connection.commit()
            await db.execute('SELECT * FROM profile WHERE id = %s', (girl,))
            profile = await db.fetchone()

        assert await _message_save(girl, _message())

        want = agent_vars(profile, {'name': 'Elena'})

        assert pushed[0]['owner'] == f"{want['girl_id']}_{want['girl_name']}"

    async def test_companion_without_number_is_skipped(self, girl, pushed):
        """Номера собеседника нет — класть реплику некуда, соседа не зовём."""
        assert await _message_save(girl, {**_message(), 'sender_uid': ''})
        assert pushed == [], 'уехал ключ без номера собеседника'


class TestThePanelRoadTellsToo:
    """⚠⚠⚠ Вторая дорога: импорт из панели. У сайта без сокета — единственная.

    Пока доклад жил внутри `chat.py`, привезённое из панели к соседу не
    попадало вовсе, и агент вёл разговор, не зная ни одной прежней реплики.
    """

    async def test_an_imported_reply_reaches_the_neighbour(self, girl, pushed):
        """Реплика, привезённая импортом, едет в историю той же дверью."""
        from src.dating.chat_panel import chat_panel_save

        out = await chat_panel_save([{
            'profile_id': girl, 'sender_uid': UID, 'sender_name': 'Brian Ritter',
            'text': 'я писал тебе вчера', 'direction': 'in',
            'message_uid': f'panel:{uuid.uuid4().hex[:10]}',
            'sent_at': '2026-11-02 11:56:56'}])

        assert out['added'] == 1
        assert len(pushed) == 1 and pushed[0]['text'] == 'я писал тебе вчера'

    async def test_the_reply_keeps_its_own_time(self, girl, pushed):
        """⚠⚠ Едет **время реплики**, а не время доклада.

        Без него сосед ставит своё «сейчас», и привезённая переписка легла бы в
        ленту одним числом: агент увидел бы недельный разговор случившимся в
        одну минуту.
        """
        from src.dating.chat_panel import chat_panel_save

        await chat_panel_save([{
            'profile_id': girl, 'sender_uid': UID, 'sender_name': 'Brian Ritter',
            'text': 'когда-то давно', 'direction': 'in',
            'message_uid': f'panel:{uuid.uuid4().hex[:10]}',
            'sent_at': '2026-10-20 08:15:00'}])

        assert pushed[0]['said_at'] == '2026-10-20 08:15:00'

    async def test_our_own_lines_are_not_told(self, girl, pushed):
        """⚠ Исходящее не докладываем: у соседа его пишет та дорога, что сочинила.

        Привезённая из панели копия — близнец, и доклад завёл бы второй
        экземпляр сказанного.
        """
        from src.dating.chat_panel import chat_panel_save

        out = await chat_panel_save([{
            'profile_id': girl, 'sender_uid': UID, 'sender_name': 'Brian Ritter',
            'text': 'это сказали мы', 'direction': 'out',
            'message_uid': f'panel:{uuid.uuid4().hex[:10]}',
            'sent_at': '2026-11-02 12:00:00'}])

        assert out['added'] == 1, 'в переписку исходящее лечь обязано'
        assert pushed == [], 'а соседу его докладывать нельзя'

    async def test_what_did_not_land_is_not_told(self, girl, pushed):
        """Строка уже была — не легла, значит и у соседа она есть."""
        from src.dating.chat_panel import chat_panel_save

        row = {'profile_id': girl, 'sender_uid': UID, 'sender_name': 'Brian Ritter',
               'text': 'дважды привезённое', 'direction': 'in',
               'message_uid': f'panel:{uuid.uuid4().hex[:10]}',
               'sent_at': '2026-11-02 13:00:00'}

        await chat_panel_save([row])
        await chat_panel_save([row])

        assert len(pushed) == 1, 'повтор импорта задвоил историю у соседа'

    async def test_the_batch_has_a_ceiling(self, girl, pushed, monkeypatch):
        """⚠ Ручной импорт берёт до шестидесяти дней — доклад идёт с потолком.

        Берутся **самые свежие**: лента агента и так показывает последние
        реплики, и месячный хвост в неё не попадёт, сколько его ни вози.
        """
        from src.dating import chat_panel

        monkeypatch.setattr(chat_panel, 'CHAT_PANEL_HISTORY_MAX', 2)

        await chat_panel.chat_panel_save([
            {'profile_id': girl, 'sender_uid': UID, 'sender_name': 'Brian Ritter',
             'text': f'реплика {n}', 'direction': 'in',
             'message_uid': f'panel:{uuid.uuid4().hex[:10]}',
             'sent_at': f'2026-11-02 1{n}:00:00'} for n in range(5)])

        assert len(pushed) == 2
        assert [one['text'] for one in pushed] == ['реплика 3', 'реплика 4'], \
            'докладывать надо свежие и по порядку времени'
