"""Пул инициативы: третья колонка «Диалогов» и её ручки.

## Зачем пул

Разговор затихает не потому, что кончился, а потому, что последнее слово осталось
за нами и человек не ответил. Круг отвечает только на входящее, а сам не пишет —
такая пара лежит мёртвой.

Пул собирает, **чем** заговорить снова: вопрос или короткий текст под конкретную
пару. Человек читает его до отправки, как в трёх соседних пулах; правки текста
здесь нет намеренно.

## ⚠⚠ Пустоту объясняет сервер, и повод считает круг

Набирает пул круг инициативы, он же уносит готовое в чат. Пустая колонка без
причины читается как поломка — поэтому ответ несёт `answer.why`, а считает его
`initiative_why`: тот же код, что решает за круг.

⚠ Прежде здесь стояла одна фраза на все случаи — «круга инициативы ещё не
сделан», — и она **пережила сам круг** на две недели: занятие работало, а колонка
уверяла, что его нет. Поэтому проверки ниже спрашивают **повод**, а не текст.
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


async def _switch(make: bool) -> None:
    """Выключатель набора у сайта сюиты — им и решается, пуст ли пул законно."""
    async with mysql_get_db_async() as db:
        await db.execute('UPDATE site SET chat_initiative_on = %s'
                         ' WHERE site_id = %s',
                         (1 if make else 0, STAND_SITE))
        await db.connection.commit()


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
    """⚠⚠ Пустое место не отличается от поломки.

    Повод считает круг (`initiative_why`), и проверяем мы **повод**, а не
    заученный текст: прежняя проверка стерегла фразу «круга инициативы ещё не
    сделан» и честно её сторожила, пока круг две недели работал.
    """

    @pytest.fixture(autouse=True)
    async def _keep_the_switch(self):
        """⚠ Выключатель общий на сайт, а сайт сюиты один на весь прогон.

        Оставь мы его поднятым — соседние файлы увидели бы включённое занятие
        там, где его никто не включал.
        """
        async with mysql_get_db_async() as db:
            await db.execute('SELECT chat_initiative_on AS on_ FROM site'
                             ' WHERE site_id = %s', (STAND_SITE,))
            before = int(((await db.fetchone()) or {}).get('on_') or 0)

        yield

        await _switch(make=bool(before))

    async def test_a_lowered_switch_is_named(self, auth_client, girl):
        """Набор снят — так и сказано: это человек сделал сам."""
        await _switch(make=False)

        got = (await auth_client.get(
            f'/api/dating/chat-initiative?site_id={STAND_SITE}&profile_id={girl}')
        ).json()

        assert got['result'] == []
        assert 'набор инициативы выключен' in got['answer']['why'], \
            got['answer']['why']

    async def test_a_sleeping_site_is_named(self, auth_client, girl):
        """⚠ Набор поднят, но ни одной анкеты в сети — инициатива уходит в чат."""
        await _switch(make=True)

        got = (await auth_client.get(
            f'/api/dating/chat-initiative?site_id={STAND_SITE}&profile_id={girl}')
        ).json()

        assert 'не в сети' in got['answer']['why'], got['answer']['why']

    async def test_nothing_in_the_way_is_said_out_loud(self, auth_client, girl):
        """⚠⚠ Мешать некому — это **утверждение**, а не пустая строка.

        Молчание здесь читалось бы как поломка ровно так же, как пустая колонка.
        """
        await _switch(make=True)
        async with mysql_get_db_async() as db:
            await db.execute("UPDATE profile SET status = 'online' WHERE id = %s",
                             (girl,))
            await db.connection.commit()

        got = (await auth_client.get(
            f'/api/dating/chat-initiative?site_id={STAND_SITE}&profile_id={girl}')
        ).json()

        assert 'затихших разговоров сейчас нет' in got['answer']['why'], \
            got['answer']['why']

    async def test_the_reason_is_not_a_second_copy_of_the_rules(self):
        """⚠⚠ Ручка не считает повод сама — зовёт круг.

        Своя копия правил разошлась бы с кругом на первой же правке отбора, и
        колонка объясняла бы простой, которого нет.
        """
        from pytest_.pytest_guard import pytest_guard_calls

        from src.uvicorn.routers.dating import chat_initiative as handler

        assert 'initiative_why' in pytest_guard_calls(handler)

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


class TestDropNotEdit:
    """Карточку убирают, а не переписывают."""

    async def test_the_text_is_not_edited(self, auth_client, girl):
        """⚠⚠ Правки текста нет вовсе, и это стережётся.

        Карточку сочиняет агент `dating_talk`, а сосед записывает сказанное к
        себе **внутри прогона** — до того, как мы постучимся к сайту. Исправь
        текст здесь, и уйдёт одно, а в памяти агента останется другое: в
        следующем разговоре он продолжит от слов, которых собеседник не читал.
        Расхождение молчаливое, в журналах его не видно.

        ⚠ Поэтому ручки нет совсем, а не «нельзя у ушедшего»: половинчатый
        запрет выглядел бы недоделкой и его бы сняли.
        """
        card_id = await _card(girl, 'u-1')

        got = await auth_client.put(f'/api/dating/chat-initiative/{card_id}',
                                    json={'body': 'а чем занят в субботу?'})

        assert got.status_code == 405, 'правка обязана быть недоступна'

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


# Настройки занятия: их читают вместе, и стоять им рядом — одним подразделом.
INITIATIVE_SETTINGS = ('chat_initiative_after', 'chat_initiative_after_max',
                       'chat_initiative_repeat', 'chat_initiative_send_every')


class TestTheSettings:
    """Настройки инициативы — четвёрка, их читают вместе."""

    def test_all_four_are_registered(self):
        from src.uvicorn.routers.dating.site_ import SITE_KNOWN

        after = SITE_KNOWN['chat_initiative_after']
        upto = SITE_KNOWN['chat_initiative_after_max']
        times = SITE_KNOWN['chat_initiative_repeat']
        pause = SITE_KNOWN['chat_initiative_send_every']

        assert after['title'] == 'Инициатива, проявить через минут (от)'
        assert upto['title'] == 'Инициатива, проявить через минут (до)'
        assert times['title'] == 'Инициатива, сколько раз повторить, в сутки'
        assert pause['title'] == 'Инициатива, пауза между отправками, в мин'
        assert times['default'] == '2'
        assert times['kind'] == 'number' and times['min'] == 0
        assert upto['kind'] == 'number' and upto['min'] == 0, \
            'ноль обязан быть допустим — это «без разброса»'
        assert pause['kind'] == 'number' and pause['min'] == 0, \
            'ноль обязан быть допустим — это «без паузы»'
        assert all(SITE_KNOWN[key]['group'] == 'invite'
                   for key in INITIATIVE_SETTINGS), \
            'настройки читают вместе — и стоять им рядом'

    def test_they_stand_in_one_subsection(self):
        """⚠⚠ Все четыре — подразделом «Инициатива», а не вразброс по вкладке.

        Вкладка «Инвайты и чат» собрала настройки трёх занятий, и плоская
        таблица заставляет читать все четырнадцать строк, чтобы найти одну.
        Подраздел отвечает на вопрос «где настраивается инициатива» глазами.

        ⚠ Подписи и порядок подразделов знает **сервер** (`SITE_SUBGROUPS`), а не
        страница: иначе новый подраздел требовал бы правки в двух местах.
        """
        from src.uvicorn.routers.dating.site_ import (SITE_GROUPS,
                                                      SITE_KNOWN,
                                                      SITE_SUBGROUPS)

        subs = {key: (group, title) for key, group, title in SITE_SUBGROUPS}

        assert subs.get('initiative') == ('invite', 'Инициатива'), \
            'подраздел инициативы не объявлен — настройки уедут в общую таблицу'
        assert all(SITE_KNOWN[key].get('sub') == 'initiative'
                   for key in INITIATIVE_SETTINGS), \
            'настройка инициативы осталась вне подраздела'

        groups = {key for key, _ in SITE_GROUPS}
        for key, group, _ in SITE_SUBGROUPS:
            assert group in groups, \
                f'подраздел «{key}» висит на несуществующей вкладке «{group}»'

    def test_the_page_draws_subsections_from_the_answer(self):
        """⚠ Страница рисует подразделы по ответу сервера, а не по своему списку.

        Своя копия разошлась бы с реестром на первой правке: настройка получила
        бы `sub`, которого страница не знает, и легла бы в общую таблицу молча.
        """
        import pathlib

        root = pathlib.Path(__file__).resolve().parents[1]
        page = (root / 'data/uvicorn/template/admin/dating/site.html'
                ).read_text(encoding='utf-8')

        assert 'answer.subs' in page, 'подразделы с сервера не читаются'
        assert 'set-box-head sub' in page, 'шапка подраздела не рисуется'

    def test_none_of_them_pretends_to_be_unfinished(self):
        """⚠⚠ Работа сделана — и оговорки «пока ни на что не влияет» быть не должно.

        Оговорка в подписи переживает работу легко: её пишут при заведении
        настройки и забывают снять, а человек потом читает, что рабочее число ни
        на что не влияет, и не ставит его вовсе.
        """
        from src.uvicorn.routers.dating.site_ import SITE_KNOWN

        for key in INITIATIVE_SETTINGS:
            assert 'работа не сделана' not in SITE_KNOWN[key]['note'], key

    def test_the_day_is_spelled_out_in_the_note(self):
        """⚠ Скользящие сутки и то, что ответ не обнуляет счёт, — в подписи.

        Оба правила человек не выведет из названия: «сколько раз повторить, в
        сутки» не говорит ни про 24 часа вместо полуночи, ни про ответ.
        """
        from src.uvicorn.routers.dating.site_ import SITE_KNOWN

        note = SITE_KNOWN['chat_initiative_repeat']['note']

        assert 'скольз' in note and 'не обнуля' in note


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
        """⚠ Отдельная раскладка, а не правка общей `.in-cols`.

        ⚠⚠ Довод изменился, и это стоит знать: `in-cols-3` теперь **общая на две
        вкладки** — «Чат → Диалоги» (пул инициативы) и «Фавориты → Диалоги» (пул
        приставания). Двумя колонками остались «Письма → Диалоги», и расширь мы
        `.in-cols`, пустая третья появилась бы у них.
        """
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
