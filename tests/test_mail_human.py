"""Письма, написанные живым человеком на сайте, а не нами (26.11.2026).

## ⚠ **Страница истории — живой кадр панели, а не выдумка**

Разбор проверяется на сохранённом кадре `tests/frames/`
(`ladadate_usermessagehistory.html`, пара Ekaterina 622715 → Mark 549126,
замер 26.11.2026): пять писем, её красные, его серая, снимок во вложении.
Живого похода к панели нет нигде: `panel_fetch_html` подменяется кадром —
тот же порядок, что у импорта чата (`test_chat_panel.py`).

## Что здесь ловится

**Разбор: её письма краснеют, его — сереют, время — часами страницы.** Сторона
разметкой, номер письма — ключом, вложение — miniature-ссылкой: ошибка в любом
из четырёх молча перекладывает чужое письмо на нашу сторону или теряет его.

**Ответ человека — верхнее её письмо, новее его.** Правило владельца
от 27.11.2026: страница первая, письмо одно; старше его письма — не ответ.

**Близнец нашей отправки не заводится второй строкой.** Наш текст, ушедший
через сайт, возвращается из истории тем же письмом; без сверки с пулами
отправки (`message_twin`) оператор увидел бы в ленте собственное письмо дважды.

**Письмо, отвеченное на сайте, агент не отвечает вовсе.** Тот случай, из-за
которого всё затевалось: две реплики подряд — мужчина получил ответ живым
стилем, второй — машиной.

**Доклад — один раз, неудача повторяется.** Ноль соседа — отказ, а не «уже
было» (тот же смысл, что у `chat_history_said`): отметка встанет только по
ненулевому номеру, иначе молчание соседа стоило бы реплики навсегда.
"""

import pathlib
import uuid
from datetime import datetime

import pytest

from src.dating import mail_human as mod
from src.dating.mail_reply import _to_answer, mail_reply_make, mail_reply_thread
from src.dating.site import site_panel

pytestmark = pytest.mark.mail

SITE = 999_777

# Анкета заводится на живом номере: заведение сверяет сайт у соседа, а ключевые
# FK смотрят в `profile`. Панель при этом не трогается — `panel` ниже.
LIVE_SITE = 343

FRAME = (pathlib.Path(__file__).resolve().parents[1]
         / 'tests' / 'frames' / 'ladadate_usermessagehistory.html')

# Его номер в кадре — тот, что на странице. Её номер на сайте берём **свой**
# (уникальный на запуск): в базе стенда живёт настоящая анкета 622715, а на
# паре `(site_id, profile_id)` — UNIQUE.
HIM = '549126'

# Номер её свежего письма в кадре (со снимком); остальные три глубже границы
# «старше его неотвеченного» и в забор не должны попадать.
HER_FRESH = 853653413


@pytest.fixture
def agent(monkeypatch):
    """Подменяет агента писем: помнит вызовы вместо похода к LLM."""
    from src.dating import mail_reply as mail_mod

    calls = []

    async def fake(agent_id, profile, uid, language, name='', letter=''):
        calls.append({'uid': str(uid)})

        return f'Ответ №{len(calls)}', '', 0, {}

    async def fake_agent(site_id):
        return 1

    monkeypatch.setattr(mail_mod, 'answer_mail_reply', fake)
    monkeypatch.setattr(mail_mod, '_agent', fake_agent)

    return calls


@pytest.fixture
async def profile(auth_client):
    """Анкета с включённой генерацией и номером на сайте, как у кадра."""
    from mysql_.mysql_ import mysql_get_db_async

    her_on_site = str(uuid.uuid4().int % 9_000_000 + 1_000_000)
    made = (await auth_client.post('/api/dating/profile', json={
        'site_id': LIVE_SITE, 'login': f'hum-{uuid.uuid4().hex[:8]}@example.com',
        'password': 'x'})).json()['result']

    await auth_client.put(f'/api/dating/site/{SITE}/reply-make-on',
                          json={'reply_make_on': True})

    async with mysql_get_db_async() as db:
        # ⚠ Номер на сайте вставляем руками: `agent_site_uid` собирает им путь
        # страницы истории, а заведение анкеты его не знает — на проде его
        # привозит импорт.
        await db.execute('UPDATE profile SET profile_id = %s WHERE id = %s',
                         (her_on_site, made))
        await db.connection.commit()
        await db.execute('SELECT id, site_id, login, profile, profile_id'
                         ' FROM profile WHERE id = %s', (made,))
        row = dict(await db.fetchone())

    yield row

    await auth_client.delete(f'/api/dating/profile/{made}')
    await auth_client.put(f'/api/dating/site/{SITE}/reply-make-on',
                          json={'reply_make_on': False})


@pytest.fixture
def panel(monkeypatch):
    """Панель — кадр вместо живого похода: драйвер Ladadate, страница — файл.

    ⚠ Подменяются `site_presence` и `panel_fetch_html` **в модуле** — тот же
    порядок, что у подмены агента в `test_mail_reply`: `site_registry_panel`
    остаётся настоящим и проверяет, что `driver` из присутствия вправду ведёт
    на драйвер с `HISTORY_PATH`.
    """
    calls = []
    page = FRAME.read_text(encoding='utf-8')

    async def presence(site_id):
        return {'driver': site_panel.DatingSitePanelLadadate.name}

    async def fetch(site_id, path, params=None, **kwargs):
        calls.append({'path': str(path), 'id2': str((params or {}).get('id2')),
                      'page': str((params or {}).get('page'))})

        # ⚠ Кадр — первая страница; дальше пустая: за ним страниц нет, и это
        # проверяется тоже — пустая страница должна **остановить** поход.
        return page if calls[-1]['page'] == '1' else ''

    monkeypatch.setattr(mod, 'site_presence', presence)
    monkeypatch.setattr(mod, 'panel_fetch_html', fetch)

    return calls


@pytest.fixture
def said(monkeypatch):
    """Сосед вместо истории: помнит вызовы, отвечает по уговору."""
    calls = {'list': [], 'answer': 0}

    async def add(owner_id, owner_name, companion_id, companion_name, **kw):
        calls['list'].append({'owner_id': owner_id,
                              'companion_id': companion_id, **kw})

        return calls['answer']

    import src.rhapsody.rhapsody_api as api

    monkeypatch.setattr(api, 'rhapsody_api_chat_add', add)

    return calls


async def _pair(profile: dict, uid: str = HIM, at: str = '',
                checked=False, replied=1, msg_id: int = 0) -> int:
    """Кладёт его письмо: по умолчанию **отвеченное на сайте** — завязка
    всей проверки. `replied=0` — обычное живое письмо, агенту вход.

    `at` — время письма строкой (кадр помечен датами, и сверка границами
    должна быть детерминированной, не от `NOW()`).
    """
    from mysql_.mysql_ import mysql_get_db_async

    async with mysql_get_db_async() as db:
        await db.execute(
            'INSERT INTO mail_in'
            ' (site_id, profile_id, message_id, uid, name, body, date_post,'
            '  is_replied, human_checked_at)'
            ' VALUES (%s, %s, %s, %s, %s, %s,'
            "         COALESCE(NULLIF(%s, ''), NOW()), %s,"
            '         IF(%s, NOW(), NULL))',
            (SITE, int(profile['id']),
             int(msg_id or uuid.uuid4().int % 10**9), uid,
             f'Man {uid}', 'Привет, как ты?', at, int(replied),
             1 if checked else 0))
        await db.connection.commit()
        await db.execute('SELECT LAST_INSERT_ID() AS id')

        return int((await db.fetchone())['id'])


async def _human(profile: dict, uid: str, body: str, at: str = '',
                 message_id: int = 0) -> int:
    """Кладёт её письмо человека прямо в `mail_human` (минуя забор)."""
    from mysql_.mysql_ import mysql_get_db_async

    async with mysql_get_db_async() as db:
        await db.execute(
            'INSERT INTO mail_human'
            ' (site_id, profile_id, uid, message_id, body, date_post)'
            ' VALUES (%s, %s, %s, %s, %s, COALESCE(NULLIF(%s, %s), NOW()))',
            (SITE, int(profile['id']), uid,
             int(message_id or uuid.uuid4().int % 10**9), body, at, ''))
        await db.connection.commit()
        await db.execute('SELECT LAST_INSERT_ID() AS id')

        return int((await db.fetchone())['id'])


async def _draft(profile: dict, in_id: int, uid: str = HIM,
                 body: str = 'Ответ агента') -> int:
    """Кладёт **наш** черновик ответа на его письмо — то, что не должно уйти."""
    from mysql_.mysql_ import mysql_get_db_async

    async with mysql_get_db_async() as db:
        await db.execute(
            ' INSERT INTO mail_reply'
            ' (site_id, profile_id, mail_in_id, uid, name, body, status)'
            " VALUES (%s, %s, %s, %s, %s, %s, 'draft')",
            (SITE, int(profile['id']), in_id, uid, f'Man {uid}', body))
        await db.connection.commit()
        await db.execute('SELECT LAST_INSERT_ID() AS id')

        return int((await db.fetchone())['id'])


def _letter(profile: dict, draft_id: int, uid: str = HIM) -> dict:
    """Строка пула ответов в том виде, в каком её приносит круг."""
    return {'id': draft_id, 'uid': uid, 'name': f'Man {uid}',
            'body': 'Ответ агента', 'status': 'draft', 'reply_to': 0,
            'site_id': SITE, 'profile_id': int(profile['id']),
            'message_id': 0}


async def _one(sql: str, args: tuple = ()):
    """Одна строка одна колонка — проверки глядят в базу напрямую."""
    from mysql_.mysql_ import mysql_get_db_async

    async with mysql_get_db_async() as db:
        await db.execute(sql, args)

        row = await db.fetchone()

    return row


async def _rows(sql: str, args: tuple = ()):
    from mysql_.mysql_ import mysql_get_db_async

    async with mysql_get_db_async() as db:
        await db.execute(sql, args)

        return [dict(one) for one in await db.fetchall() or []]


class TestHistoryPage:
    """Разбор кадра: стороны, номера, время, текст, вложение."""

    def test_frame(self):
        letters = site_panel.ladadate_history_page(
            FRAME.read_text(encoding='utf-8'), her='Ekaterina', him='Mark')

        assert [int(one['message_id']) for one in letters] == \
               [853653413, 853634242, 852239294, 851427921, 842732053]
        assert [one['side'] for one in letters] == \
               ['her', 'his', 'her', 'her', 'her']

        fresh, his = letters[0], letters[1]

        assert fresh['at'] == '2026-11-26 17:06:00'
        assert his['at'] == '2026-11-26 16:19:00'
        assert fresh['name'] == 'Ekaterina' and his['name'] == 'Mark'

        # ⚠ Голый текст: теги страницы в базу не едут, и проверка это держит.
        assert '<' not in fresh['body'] and '<' not in his['body']
        assert fresh['body'] and his['body']

        # ⚠ Вложение — miniature того же снимка; у его письма — никакого.
        assert '-s.' in fresh['attachment']
        assert 'attachments/622715' in fresh['attachment']
        assert his['attachment'] == ''

    def test_anonymous_head_does_not_count(self):
        """Карточки шапки страницы — не письма: без `ID: … | имя` они мимо."""
        letters = site_panel.ladadate_history_page(
            FRAME.read_text(encoding='utf-8'))

        assert all(one['side'] in ('her', 'his') for one in letters)
        assert len(letters) == 5


class TestPull:
    """Забор страницы: её письма остаются, наши уходят, пара отмечается."""

    async def test_keeps_hers_fresh_and_marks(self, panel, profile):
        await _pair(profile, at='2026-11-26 16:19:00')

        made = await mod.mail_human_pull(SITE, profile['id'], HIM)

        assert made == 1
        rows = await _rows('SELECT message_id, body, date_post, attachment'
                           ' FROM mail_human WHERE profile_id = %s',
                           (int(profile['id']),))
        assert [int(one['message_id']) for one in rows] == [HER_FRESH]
        assert rows[0]['date_post'] == datetime(2026, 11, 26, 17, 6)
        assert rows[0]['attachment']

        # ⚠ По правилу владельца страница **одна**, и письмо одно — верхнее
        # её, новее его неотвеченного; глубже истории не листаем.
        assert len(panel) == 1
        assert panel[0]['path'] == f"usermessagehistory/{profile['profile_id']}"
        assert panel[0]['id2'] == HIM

        got = await _one('SELECT human_checked_at FROM mail_in'
                         ' WHERE profile_id = %s AND uid = %s',
                         (int(profile['id']), HIM))
        assert got['human_checked_at'] is not None

    async def test_drops_twin_of_ours(self, panel, profile):
        """Наше письмо, вернувшееся из истории, — не письмо человека."""
        from mysql_.mysql_ import mysql_get_db_async

        letters = site_panel.ladadate_history_page(
            FRAME.read_text(encoding='utf-8'))
        mine = next(one for one in letters
                    if int(one['message_id']) == HER_FRESH)

        await _pair(profile, at='2026-11-26 16:19:00')

        async with mysql_get_db_async() as db:
            # ⚠ Пул рассылки: та же дорога «мы отправили», что у mail_reply,
            # и тот же смысл близнеца (`message_twin`).
            await db.execute(
                'INSERT INTO mailing'
                ' (site_id, profile_id, mail_id, uid, name, body, status,'
                '  sent_at)'
                " VALUES (%s, %s, NULL, %s, 'girl', %s, 'sent',"
                "         '2026-11-26 17:05:30')",
                (SITE, int(profile['id']), HIM, mine['body']))
            await db.connection.commit()

        made = await mod.mail_human_pull(SITE, profile['id'], HIM)

        assert made == 0
        rows = await _rows('SELECT id FROM mail_human WHERE profile_id = %s',
                           (int(profile['id']),))
        assert rows == []

    async def test_top_letter_older_than_his_not_taken(self, panel, profile):
        """Верхнее её письмо **старее** его письма — это не ответ ему."""
        # Его письмо позднее всех её писем кадра: живым ответом на кадровое
        # письмо прикидывать нечего — брать не из чего.
        await _pair(profile, at='2026-12-10 09:00:00')

        assert await mod.mail_human_pull(SITE, profile['id'], HIM) == 0

        got = await _one('SELECT human_checked_at FROM mail_in'
                         ' WHERE profile_id = %s AND uid = %s',
                         (int(profile['id']), HIM))
        assert got['human_checked_at'] is not None, 'пара проверена, просто пусто'

    async def test_silent_without_history_page(self, monkeypatch, profile):
        """Сайт без страницы истории: ноль **молча** и пара не отмечена."""

        async def presence(site_id):
            return {}

        async def fetch(site_id, path, params=None, **kwargs):
            raise AssertionError('ходить к панели не за чем')

        monkeypatch.setattr(mod, 'site_presence', presence)
        monkeypatch.setattr(mod, 'panel_fetch_html', fetch)

        await _pair(profile)

        assert await mod.mail_human_pull(SITE, profile['id'], HIM) == 0

        got = await _one('SELECT human_checked_at FROM mail_in'
                         ' WHERE profile_id = %s AND uid = %s',
                         (int(profile['id']), HIM))
        assert got['human_checked_at'] is None


class TestTrigger:
    """Письмо, отвеченное на сайте, — не вход агенту."""

    async def test_to_answer_skips_it(self, profile):
        await _pair(profile)
        await _pair(profile, uid='777001', replied=0)  # обычное, живое

        rows = await _to_answer(SITE)
        uids = {str(row['uid']) for row in rows}

        assert HIM not in uids
        assert '777001' in uids

    async def test_makes_no_draft(self, agent, profile):
        """Круг ответов проходит мимо: ни звонка агенту, ни строки в пуле."""
        # Обе пары **отмеченные**: круг не ходит к панели, а только отвечает.
        await _pair(profile, checked=True)
        await _pair(profile, uid='777003', replied=0)

        await mail_reply_make(SITE)

        assert [one['uid'] for one in agent] == ['777003']

        rows = await _rows('SELECT uid FROM mail_reply WHERE profile_id = %s',
                           (int(profile['id']),))
        assert {str(one['uid']) for one in rows} == {'777003'}


class TestReport:
    """Доклад соседу: один раз, по успешному ответу, именами из карточек."""

    async def test_once_and_retry(self, said, profile):
        from mysql_.mysql_ import mysql_get_db_async

        async with mysql_get_db_async() as db:
            await db.execute('INSERT INTO contact (profile_id, uid, name)'
                             " VALUES (%s, %s, 'Mark')",
                             (int(profile['id']), HIM))
            await db.connection.commit()

        row = await _human(profile, HIM, 'Written by a human on the site',
                           at='2026-11-26 12:00:00')

        said['answer'] = 0
        assert await mod.mail_human_report(SITE) == 0

        got = await _one('SELECT history_at FROM mail_human WHERE id = %s',
                         (row,))
        assert got['history_at'] is None, 'отказ соседа не права на отметку'

        said['answer'] = 77
        assert await mod.mail_human_report(SITE) == 1

        got = await _one('SELECT history_at FROM mail_human WHERE id = %s',
                         (row,))
        assert got['history_at'] is not None

        assert await mod.mail_human_report(SITE) == 0, 'второго доклада нет'

        one = said['list'][-1]
        assert one['answer'] == 'Written by a human on the site'
        assert one['question'] == ''
        assert one['kind'] == 'mail'
        assert one['delivered'] is True
        assert one['source_uid'] == f'mail_human:{row}'
        assert str(one['said_at']).startswith('2026-11-26 12:00')
        # Ключ владеля — её номер на сайте, тем же порядком, что у чата
        # (`agent_vars` зовёт эти же ключи).
        assert one['owner_id'] == str(profile['profile_id'])
        assert one['companion_id'] == HIM


class TestThread:
    """Показ: её письма человека в нити, с пометкой рода."""

    async def test_shows_human_letters(self, profile):
        await _pair(profile)
        await _human(profile, HIM, 'Hello from the site',
                     at='2026-11-26 12:00:00')

        rows = await mail_reply_thread(int(profile['id']), HIM)

        kinds = [(str(one['from']), str(one['kind'])) for one in rows]

        assert ('her', 'human') in kinds
        assert ('him', 'in') in kinds

        human = next(one for one in rows if one['kind'] == 'human')
        assert human['text'] == 'Hello from the site'
        # Пометка рода — на странице (`threadPaint` по `kind`), здесь — сам род.
        assert human['status'].strip() == 'sent'


class TestSend:
    """Отправка ответа **спрашивает сайт** в последнюю секунду (27.11.2026)."""

    @staticmethod
    def _api(monkeypatch, answered, fail=False):
        """Сайт вместо живого: список — с `IsReplied`, шаги отправки — ОК."""
        from src.dating.site_api import DatingSiteApiError

        async def call(profile, base, password, path, payload=None,
                       method='', door=''):
            if path.startswith('/messages?'):
                if fail:
                    raise DatingSiteApiError('сайт молчит')

                return {'Messages': [{'MessageId': 700001,
                                      'IsReplied': bool(answered)}]}

            if path.startswith('/messages/check-send/'):
                return {'CheckId': 'check-' + 'x' * 20}

            return {'Status': 'OK', 'MessageId': 900001}

        monkeypatch.setattr(mod, 'site_api_call', call)

        import src.dating.mail_send as ms

        # Шаги самой отправки (`check-send`, `send`) ходят через **его**
        # неймспейс — правка одной двери не чинит другую половину дороги.
        monkeypatch.setattr(ms, 'site_api_call', call)

        async def silence(*args, **kwargs):
            return None

        # Сосед в этих тестах — молчание: доставка проверяется ниже по смыслу,
        # а не глазами соседа (её держит `test_mail_send`).
        monkeypatch.setattr(ms, 'rhapsody_api_chat_delivered', silence)
        monkeypatch.setattr(ms, 'chat_intention_delivered', silence)

        return ms

    async def test_answered_at_send_blocks(self, monkeypatch, panel, profile):
        """Человек ответил, пока черновик зрел: письмо не уходит, след убран."""
        ms = self._api(monkeypatch, answered=True)

        in_id = await _pair(profile, at='2026-11-26 16:19:00', replied=0,
                            msg_id=700001)
        draft_id = await _draft(profile, in_id)

        with pytest.raises(ms.DatingMailSendError):
            await ms.mail_send_one(_letter(profile, draft_id), profile,
                                   'http://site.test', 'pw',
                                   table='mail_reply')

        assert await _one('SELECT id FROM mail_reply WHERE id = %s',
                         (draft_id,)) is None, 'черновик снят с полки'

        row = await _one('SELECT is_replied, human_checked_at FROM mail_in'
                         ' WHERE id = %s', (in_id,))
        assert int(row['is_replied']) == 1
        assert row['human_checked_at'] is not None, 'пара проверена сразу'

        got = await _one('SELECT message_id FROM mail_human'
                         ' WHERE profile_id = %s', (int(profile['id']),))
        assert got and int(got['message_id']) == HER_FRESH, 'забор вызван'

        line = await _one("SELECT text FROM log WHERE profile_id = %s"
                          "   AND text LIKE '%%ответ не ушёл%%'"
                          ' ORDER BY id DESC LIMIT 1', (int(profile['id']),))
        assert line and 'на письмо уже ответил человек на сайте' in line['text']

    async def test_not_answered_sends(self, monkeypatch, panel, profile):
        """Сайт говорит «не отвечено»: обычная дорога, черновик уходит."""
        ms = self._api(monkeypatch, answered=False)

        in_id = await _pair(profile, replied=0, msg_id=700001)
        draft_id = await _draft(profile, in_id)

        assert await ms.mail_send_one(_letter(profile, draft_id), profile,
                                      'http://site.test', 'pw',
                                      table='mail_reply') == {'sent': True}

        row = await _one('SELECT status FROM mail_reply WHERE id = %s',
                         (draft_id,))
        assert row['status'] == 'sent'
        assert await _one('SELECT id FROM mail_human WHERE profile_id = %s',
                          (int(profile['id']),)) is None, 'забор не звался'

    async def test_site_error_sends(self, monkeypatch, panel, profile):
        """Свой сбой не запирает письмо: сайт молчит — уходит как уходило."""
        ms = self._api(monkeypatch, answered=True, fail=True)

        in_id = await _pair(profile, replied=0, msg_id=700001)
        draft_id = await _draft(profile, in_id)

        assert await ms.mail_send_one(_letter(profile, draft_id), profile,
                                      'http://site.test', 'pw',
                                      table='mail_reply') == {'sent': True}

        row = await _one('SELECT status FROM mail_reply WHERE id = %s',
                         (draft_id,))
        assert row['status'] == 'sent'
