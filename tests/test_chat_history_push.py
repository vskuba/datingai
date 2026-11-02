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
from src.dating.chat_history import chat_history_said
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


@pytest.fixture
def put(monkeypatch):
    """Ручка соседа «положить обмен»: запоминает, что ей отдали.

    ⚠ Стороны запоминаются **частями**, как и уезжают: склейку делает сосед
    своим правилом, и проверять здесь готовый ключ значило бы повторить это
    правило третьим местом.
    """
    seen = []

    async def _put(owner_id, owner_name, companion_id, companion_name,
                   question='', answer='', kind='chat', source_uid='',
                   delivered=False, said_at=''):
        seen.append({'owner': (owner_id, owner_name),
                     'companion': (companion_id, companion_name),
                     'question': question, 'answer': answer, 'kind': kind,
                     'source_uid': source_uid, 'delivered': delivered,
                     'said_at': said_at})

        return 4242

    monkeypatch.setattr('src.rhapsody.rhapsody_api.rhapsody_api_chat_put', _put)

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


class TestWhatTheOperatorWroteInThePanel:
    """⚠⚠⚠ Текст, набранный оператором руками в панели сайта.

    Его не сочинял никто из наших дорог: человек написал в панели агентства,
    сайт отправил, собеседник прочёл — а у соседа этого нет, и агент
    продолжает разговор, будто сказанного не было.

    Отбор здесь **не** «докладываем то, что легло»: исходящая строка из панели
    ложится и когда она наша (инвайт переносится в переписку позже; наша
    отправка бывает в полёте в момент захода). Пара появится, но **после**
    вставки — поэтому три признака: выдержка, отсутствие близнеца и наличие
    разговора.
    """

    @staticmethod
    async def _panel_out(girl: int, text: str, ago_min: int = 60,
                         uid: str = UID) -> int:
        """Кладёт исходящую строку из панели — как её кладёт импорт.

        ⚠ С именем собеседника: разбор панели его заполняет (`chat_panel_rows`),
        и без него ключ выродился бы в «номер_номер» — беда, на которую здесь
        уже стоит отдельный класс сторожей.
        """
        async with mysql_get_db_async() as db:
            await db.execute(
                "INSERT INTO message (profile_id, sender_uid, sender_name, text,"
                " message_uid, direction, is_read, source, created_at)"
                " VALUES (%s, %s, 'Brian Ritter', %s, %s, 'out', 1, 'panel',"
                "         NOW() - INTERVAL %s MINUTE)",
                (girl, uid, text, f'panel:{uuid.uuid4().hex[:10]}', ago_min))
            made = db.lastrowid
            await db.connection.commit()

        return int(made)

    @staticmethod
    async def _his_reply(girl: int, uid: str = UID) -> None:
        """Его реплика — без неё разговора нет, и докладывать нечего."""
        async with mysql_get_db_async() as db:
            await db.execute(
                "INSERT INTO message (profile_id, sender_uid, sender_name, text,"
                " message_uid, direction, is_read, source, created_at)"
                " VALUES (%s, %s, 'Brian', 'hi there', %s, 'in', 1, 'panel',"
                "         NOW() - INTERVAL 90 MINUTE)",
                (girl, uid, f'panel:{uuid.uuid4().hex[:10]}'))
            await db.connection.commit()

    @staticmethod
    async def _ours(girl: int, text: str, ago_min: int) -> None:
        """Наша собственная запись той же реплики — та самая пара-близнец."""
        async with mysql_get_db_async() as db:
            await db.execute(
                "INSERT INTO message (profile_id, sender_uid, sender_name, text,"
                " message_uid, direction, is_read, source, created_at)"
                " VALUES (%s, %s, '', %s, %s, 'out', 1, 'http',"
                "         NOW() - INTERVAL %s MINUTE)",
                (girl, UID, text, uuid.uuid4().hex[:20], ago_min))
            await db.connection.commit()

    @staticmethod
    async def _marked(row_id: int):
        async with mysql_get_db_async() as db:
            await db.execute('SELECT history_at FROM message WHERE id = %s',
                             (row_id,))

            return (await db.fetchone())['history_at']

    async def test_it_reaches_the_neighbour(self, girl, pushed, put):
        """Ради этого всё: операторская реплика уехала в историю."""
        await self._his_reply(girl)
        row = await self._panel_out(girl, 'это написал оператор руками')

        assert await chat_history_said(SITE) == 1
        assert len(put) == 1

        got = put[0]

        assert got['answer'] == 'это написал оператор руками'
        assert got['question'] == '', 'оператор писал, а не собеседник'
        assert got['owner'] == ('599939', 'elena'), 'ключ анкеты собран не так'
        assert got['companion'] == ('329110', 'brian_ritter')
        assert got['delivered'] is True, 'строка из журнала сайта — сайт её принял'
        assert await self._marked(row) is not None, 'доложенное не отмечено'

    async def test_the_moment_is_the_replys_own(self, put, girl):
        """⚠⚠ Едет время реплики, а не время доклада.

        Без него вся привезённая переписка легла бы у соседа одним числом.
        """
        await self._his_reply(girl)
        await self._panel_out(girl, 'написано час назад', ago_min=60)

        await chat_history_said(SITE)

        assert put[0]['said_at'], 'время не уехало вовсе'
        assert ':' in put[0]['said_at'] and put[0]['said_at'][:2] == '20'

    async def test_told_once_and_never_again(self, put, girl):
        """Отметка закрывает строку: второй заход её не возьмёт."""
        await self._his_reply(girl)
        await self._panel_out(girl, 'однажды сказанное')

        assert await chat_history_said(SITE) == 1
        assert await chat_history_said(SITE) == 0
        assert len(put) == 1

    async def test_a_failure_leaves_it_in_the_queue(self, girl, monkeypatch):
        """⚠ Не ушло — отметки нет: реплика уедет следующим заходом.

        Пометь мы её заранее, молчание соседа стоило бы реплики навсегда.
        """
        async def _nope(*a, **kw):
            return 0

        monkeypatch.setattr('src.rhapsody.rhapsody_api.rhapsody_api_chat_put',
                            _nope)

        await self._his_reply(girl)
        row = await self._panel_out(girl, 'соседа нет дома')

        assert await chat_history_said(SITE) == 0
        assert await self._marked(row) is None, 'непривезённое отмечено доложенным'

    async def test_our_own_reply_is_not_told_twice(self, put, girl):
        """⚠ У нашей отправки есть своя запись — её пишет та дорога, что
        сочинила. Доклад завёл бы у соседа второй экземпляр сказанного."""
        await self._his_reply(girl)
        await self._ours(girl, 'это сказали мы сами', ago_min=60)
        await self._panel_out(girl, 'это сказали мы сами', ago_min=60)

        assert await chat_history_said(SITE) == 0
        assert put == []

    async def test_a_fresh_line_waits_out_the_race(self, put, girl):
        """⚠⚠ Выдержка — это **окно гонки**, а не вежливость.

        Между «сайт принял кадр» и нашей записью проходят секунды, и заход
        импорта в этот промежуток помещается. Молодую строку не трогаем.

        ⚠ Возраст строки берётся **заметно меньше выдержки**, а не нулевым:
        с нулём обе отметки времени попадают в одну секунду, `<` отвечает
        ложью сам по себе, и сторож проходил бы даже со снятой выдержкой —
        проверено порчей кода.
        """
        from src.dating import chat_history

        young = max(1, chat_history.CHAT_HISTORY_SAID_WAIT_SEC // 60 // 2)
        await self._his_reply(girl)
        await self._panel_out(girl, 'отправлено только что', ago_min=young)

        assert await chat_history_said(SITE) == 0
        assert put == []

    async def test_without_a_conversation_nothing_is_told(self, put, girl):
        """⚠⚠ Инвайт, на который не ответили, разговором не считается.

        Лента из одних наших слов — монолог, а не история; у соседа ему места
        нет. Это же правило отсекает приглашения, которые импорт привозит
        исходящими строками раньше, чем их переносит первый ответ.
        """
        await self._panel_out(girl, 'I might be bad for your health')

        assert await chat_history_said(SITE) == 0
        assert put == []

    async def test_the_batch_has_a_ceiling(self, put, girl, monkeypatch):
        """Потолок на заход — предохранитель от неожиданности."""
        from src.dating import chat_history

        monkeypatch.setattr(chat_history, 'CHAT_HISTORY_SAID_MAX', 2)
        await self._his_reply(girl)
        for n in range(4):
            await self._panel_out(girl, f'реплика оператора {n}')

        assert await chat_history_said(SITE) == 2
