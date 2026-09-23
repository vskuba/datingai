"""Пул инициативы: третья колонка «Диалогов» и её ручки.

## Зачем пул

Разговор затихает не потому, что кончился, а потому, что последнее слово осталось
за нами и человек не ответил. Круг отвечает только на входящее, а сам не пишет —
такая пара лежит мёртвой.

Пул собирает, **чем** заговорить снова: вопрос или короткий текст под конкретную
пару. Человек читает и правит до отправки, как в трёх соседних пулах.

## ⚠⚠ Работы за пулом пока нет

Таблица, ручки и колонка заведены, а набирать карточки некому: круга инициативы не
существует. Пул пуст, и пустоту объясняет **сервер** — иначе молчащая колонка
читается как поломка.
"""

import uuid

import pytest

from mysql_.mysql_ import mysql_get_db_async

pytestmark = pytest.mark.chat

STAND_SITE = 999_343


@pytest.fixture
async def girl(auth_client):
    profile_id = int((await auth_client.post('/api/dating/profile', json={
        'site_id': STAND_SITE,
        'login': f'init-{uuid.uuid4().hex[:8]}@example.com',
        'password': 'x'})).json()['result'])

    yield profile_id

    async with mysql_get_db_async() as db:
        await db.execute('DELETE FROM chat_initiative WHERE profile_id = %s',
                         (profile_id,))
        await db.execute('DELETE FROM profile WHERE id = %s', (profile_id,))
        await db.connection.commit()


async def _card(profile_id: int, uid: str, body: str = 'как прошли выходные?',
                status: str = 'draft', try_no: int = 1) -> int:
    async with mysql_get_db_async() as db:
        await db.execute(
            'INSERT INTO chat_initiative (site_id, profile_id, uid, name, body,'
            '  try_no, status, sent_at)'
            " VALUES (%s, %s, %s, %s, %s, %s, %s, IF(%s = 'sent', NOW(), NULL))",
            (STAND_SITE, int(profile_id), uid, 'Brian', body, int(try_no),
             status, status))
        await db.connection.commit()

        return int(db.lastrowid)


class TestThePool:
    """Чтение пула."""

    async def test_a_card_is_returned(self, auth_client, girl):
        await _card(girl, 'u-1')

        got = (await auth_client.get(
            f'/api/dating/chat-initiative?site_id={STAND_SITE}&profile_id={girl}')
        ).json()

        assert [one['body'] for one in got['result']] == ['как прошли выходные?']

    async def test_sent_cards_are_apart(self, auth_client, girl):
        """Ушедшее лежит в своём списке: пул — это то, что ещё не сказано."""
        await _card(girl, 'u-1', 'ждёт')
        await _card(girl, 'u-2', 'ушло', status='sent')

        pool = (await auth_client.get(
            f'/api/dating/chat-initiative?site_id={STAND_SITE}&profile_id={girl}')
        ).json()['result']
        sent = (await auth_client.get(
            f'/api/dating/chat-initiative?site_id={STAND_SITE}'
            f'&profile_id={girl}&status_=sent')).json()['result']

        assert [one['body'] for one in pool] == ['ждёт']
        assert [one['body'] for one in sent] == ['ушло']

    async def test_no_site_no_pool(self, auth_client):
        """⚠ Без сайта — пусто, а не пул всех сайтов сразу."""
        got = (await auth_client.get('/api/dating/chat-initiative')).json()

        assert got['result'] == []

    async def test_the_card_tells_online(self, auth_client, girl):
        """⚠ В сети ли адресат — тем же `online_show`, что у трёх соседних пулов."""
        await _card(girl, 'u-1')

        got = (await auth_client.get(
            f'/api/dating/chat-initiative?site_id={STAND_SITE}&profile_id={girl}')
        ).json()['result']

        assert 'online' in got[0] and 'online_known' in got[0]


class TestEmptinessExplainsItself:
    """⚠⚠ Пустое место не отличается от поломки."""

    async def test_the_reason_comes_with_the_answer(self, auth_client, girl):
        got = (await auth_client.get(
            f'/api/dating/chat-initiative?site_id={STAND_SITE}&profile_id={girl}')
        ).json()

        assert got['result'] == []
        assert 'круг инициативы ещё не сделан' in got['answer']['why'], \
            'колонка молчит без объяснения — читается как поломка'

    async def test_a_full_pool_says_nothing(self, auth_client, girl):
        """Есть карточки — объяснять нечего."""
        await _card(girl, 'u-1')

        got = (await auth_client.get(
            f'/api/dating/chat-initiative?site_id={STAND_SITE}&profile_id={girl}')
        ).json()

        assert got['answer']['why'] == ''


class TestOneDraftPerPair:
    """⚠ Одна ждущая карточка на пару, но история не запрещена."""

    async def test_a_second_draft_is_refused(self, girl):
        import pymysql

        await _card(girl, 'u-1')

        with pytest.raises(pymysql.err.IntegrityError):
            await _card(girl, 'u-1', 'вторая')

    async def test_history_is_allowed(self, girl):
        """⚠⚠ Ушедшие ключ не держат: иначе второй инициативы тому же человеку не
        было бы **никогда**, а весь смысл настройки «сколько раз повторить» — в
        том, что заходов несколько."""
        await _card(girl, 'u-1', 'первая', status='sent')
        await _card(girl, 'u-1', 'вторая', status='sent', try_no=2)
        await _card(girl, 'u-1', 'третья, ждёт')

        async with mysql_get_db_async() as db:
            await db.execute('SELECT COUNT(*) AS n FROM chat_initiative'
                             ' WHERE profile_id = %s AND uid = %s', (girl, 'u-1'))
            row = await db.fetchone()

        assert int(row['n']) == 3

    async def test_another_pair_is_free(self, girl):
        await _card(girl, 'u-1')
        await _card(girl, 'u-2')

        async with mysql_get_db_async() as db:
            await db.execute("SELECT COUNT(*) AS n FROM chat_initiative"
                             " WHERE profile_id = %s AND status = 'draft'", (girl,))
            row = await db.fetchone()

        assert int(row['n']) == 2


class TestEditAndDrop:
    """Правка и удаление — только пока карточка в пуле."""

    async def test_the_text_is_edited(self, auth_client, girl):
        card_id = await _card(girl, 'u-1')

        got = await auth_client.put(f'/api/dating/chat-initiative/{card_id}',
                                    json={'body': 'а чем занят в субботу?'})

        assert got.status_code == 200
        assert got.json()['result']['body'] == 'а чем занят в субботу?'

    async def test_sent_is_not_edited(self, auth_client, girl):
        """⚠ Ушедшее — уже сказанное: подменять его значило бы держать переписку,
        которой не было."""
        card_id = await _card(girl, 'u-1', status='sent')

        got = await auth_client.put(f'/api/dating/chat-initiative/{card_id}',
                                    json={'body': 'поздно'})

        assert got.status_code == 409

    async def test_a_card_is_dropped(self, auth_client, girl):
        card_id = await _card(girl, 'u-1')

        assert (await auth_client.delete(
            f'/api/dating/chat-initiative/{card_id}')).status_code == 200

    async def test_clearing_keeps_the_sent(self, auth_client, girl):
        await _card(girl, 'u-1', 'ждёт')
        await _card(girl, 'u-2', 'ушло', status='sent')

        await auth_client.delete(
            f'/api/dating/chat-initiative?site_id={STAND_SITE}&profile_id={girl}')

        async with mysql_get_db_async() as db:
            await db.execute('SELECT status FROM chat_initiative'
                             ' WHERE profile_id = %s', (girl,))
            rows = await db.fetchall()

        assert [row['status'] for row in rows] == ['sent'], \
            'чистка пула унесла и ушедшее — а это часть переписки'


class TestTheSettings:
    """Настройки инициативы — пара, их читают вместе."""

    def test_both_are_registered(self):
        from src.uvicorn.routers.dating.site_ import SITE_KNOWN

        after = SITE_KNOWN['chat_initiative_after']
        times = SITE_KNOWN['chat_initiative_repeat']

        assert after['title'] == 'Инициатива, проявить через минут'
        assert times['title'] == 'Инициатива, сколько раз повторить'
        assert times['default'] == '2'
        assert times['kind'] == 'number' and times['min'] == 0
        assert after['group'] == times['group'] == 'invite', \
            'настройки читают вместе — и стоять им рядом'

    def test_both_admit_they_do_nothing_yet(self):
        """⚠⚠ Работы нет — об этом должно быть написано у обеих."""
        from src.uvicorn.routers.dating.site_ import SITE_KNOWN

        for key in ('chat_initiative_after', 'chat_initiative_repeat'):
            assert 'работа не сделана' in SITE_KNOWN[key]['note'], key


class TestTheColumn:
    """Третья колонка на вкладке «Диалоги»."""

    def test_the_markup_has_three_columns(self):
        import pathlib

        root = pathlib.Path(__file__).resolve().parents[1]
        page = (root / 'data/uvicorn/template/admin/dating/profile/site/_base.html'
                ).read_text(encoding='utf-8')

        assert 'in-cols-3' in page, 'раскладка на три колонки не заведена'
        assert 'data-slot="initiative-list"' in page
        assert 'data-slot="initiative-count"' in page

    def test_the_layout_is_its_own(self):
        """⚠ Своя раскладка, а не правка общей: у соседних вкладок колонки две, и
        расширь мы `.in-cols`, у них появилась бы пустая третья."""
        import pathlib

        root = pathlib.Path(__file__).resolve().parents[1]
        page = (root / 'data/uvicorn/template/admin/dating/profile.html'
                ).read_text(encoding='utf-8')

        assert '.in-cols-3 {' in page
        # У общей раскладки колонок по-прежнему три поля сетки (колонка, полоса,
        # колонка), а не пять.
        shared = page[page.index('    .in-cols {'):]
        shared = shared[:shared.index('}')]

        assert shared.count('minmax') == 2, 'общую раскладку тронули — сломает соседей'


class TestTheColumnKeepsUp:
    """⚠⚠ Пул живёт своей жизнью, и колонка обязана за ним поспевать.

    Карточку уносит отправка или чистка пулов, когда у анкеты кончается смена
    (`pool.py`). Человек в этот момент смотрит на список, набранный минуту назад.

    Поймано на проде: у Oksana кончилась смена в 21:01, чистка убрала 105
    черновиков вместе с карточкой #3, а колонка её показывала — и «×» отвечал
    «карточки #3 нет» про то, что человек видит на экране.
    """

    def _page(self) -> str:
        import pathlib

        root = pathlib.Path(__file__).resolve().parents[1]

        return (root / 'data/uvicorn/template/admin/dating/profile.html'
                ).read_text(encoding='utf-8')

    def test_the_pool_is_reread_on_a_timer(self):
        """⚠ Тем же кругом, что пулы инвайтов и писем: их наполняет воркер."""
        page = self._page()
        loop = page[page.index('const POOL_IDLE_MS'):]
        loop = loop[:loop.index('}, POOL_IDLE_MS);')]

        assert 'initiativeLoad()' in loop, 'колонка не перечитывается сама'

    def test_the_chat_timer_is_not_enough(self):
        """⚠ Круг чата сюда не годится: он стоит при невыбранной анкете, а диалоги
        сайта смотрят как раз без выбора."""
        page = self._page()
        chat = page[page.index("if (workTab === 'chat') chatLoad();") - 200:]
        chat = chat[:chat.index('}, LOG_MS);')]

        assert '!openId' in chat, 'условие круга чата изменилось — проверьте вывод'

    def test_a_vanished_card_is_not_an_error(self):
        """⚠⚠ «Карточки нет» — рассинхрон, а не отказ: цель человека достигнута."""
        page = self._page()
        # ⚠ Ищем **обработчик пула инициативы**, а не первое вхождение `mail-card-drop`:
        # тем же классом подписана кнопка у трёх соседних пулов, и первое совпадение
        # приходится на общее правило CSS.
        block = page[page.index('function initiativeBind('):]
        block = block[:block.index('\n    }')]

        assert 'err.status === 404' in block, \
            'исчезнувшая карточка всё ещё показывается красной ошибкой'
        assert 'initiativeLoad();' in block, 'список после отказа не перечитывается'

    def test_the_status_code_reaches_the_page(self):
        """⚠ Код ответа — полем ошибки, а не только в тексте.

        Разбирать текст вместо кода — примета, которая рассыплется от первой же
        правки формулировки в роутере.
        """
        import pathlib

        root = pathlib.Path(__file__).resolve().parents[1]
        api = (root / 'data/uvicorn/public/js/api.js').read_text(encoding='utf-8')

        assert 'error.status = response.status' in api
