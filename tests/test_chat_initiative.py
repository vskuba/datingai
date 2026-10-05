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


import pytest

from mysql_.mysql_ import mysql_get_db_async
from src.dating import initiative_round
from src.dating.initiative_round import initiative_why
from src.dating.site_setting import SITE_GROUPS, SITE_KNOWN, SITE_SUBGROUPS

pytestmark = pytest.mark.chat

STAND_SITE = 999_343


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


async def _said_in(profile_id: int, uid: str, minutes_ago: int = 60) -> None:
    """Входящая реплика пары: без неё диалога нет вовсе (`chat_dialogs`).

    ⚠ По умолчанию **час назад**, а не «сейчас»: счёт заходов сравнивает
    `sent_at > his_last` строго, а `_card` ставит карточке `NOW()`. Положи мы
    реплику в ту же секунду — карточка не посчиталась бы, и проверка потолка
    мерила бы не то, что думает. В жизни инициатива уходит заметно позже его
    слов, и время в тесте обязано это повторять.

    ⚠ Время считает **база** (`NOW() - INTERVAL`): MySQL живёт по UTC, и
    `datetime.now()` положил бы строку в будущее.
    """
    import uuid

    async with mysql_get_db_async() as db:
        await db.execute(
            'INSERT INTO message (profile_id, sender_uid, sender_name, text,'
            '  message_uid, direction, created_at)'
            " VALUES (%s, %s, 'Brian', 'привет', %s, 'in',"
            '  NOW() - INTERVAL %s MINUTE)',
            (int(profile_id), uid, f'zz-{uuid.uuid4().hex[:12]}',
             int(minutes_ago)))
        await db.connection.commit()


def _awake(monkeypatch) -> None:
    """Показывает кругу стенд с поднятым набором — подменой отбора, а не базой.

    ⚠⚠ **Поднимать флаг в базе здесь нельзя, и это не предосторожность.** Строка
    стенда лежит `is_gone = 1` (`conftest.py`) именно затем, чтобы круги её не
    видели: ту же базу читает **живой воркер в соседнем контейнере**. Подними мы
    `chat_initiative_on` у видимой кругу строки — он взял бы стенд в работу и
    пошёл писать первым живым людям, а его отправку никакой `monkeypatch` не
    подменяет: это другой процесс. Правило общее, и у отправки инвайтов оно
    записано теми же словами (`test_invite_send_round.py`, фикстура `site`).

    ⚠ Прежде эти два теста поднимали флаг в базе и ходили в ручку по HTTP.
    Работало это лишь потому, что отбор круга инициативы **не спрашивал
    `is_gone`** — единственный из десяти, кто его не спрашивал. Дыра закрыта
    (`round_sites`), и тесты переведены на прямой вызов: проверяют они повод
    словами, а он считается здесь же, в процессе.
    """
    async def ours():
        return [{'site_id': STAND_SITE, 'chat_initiative_on': 1,
                 'chat_initiative_send_on': 0}]

    monkeypatch.setattr(initiative_round, '_sites', ours)


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

    async def test_a_sleeping_site_is_named(self, girl, monkeypatch):
        """⚠ Набор поднят, но ни одной анкеты в сети — инициатива уходит в чат."""
        _awake(monkeypatch)

        assert 'не в сети' in await initiative_why(STAND_SITE)

    async def test_nothing_in_the_way_is_said_out_loud(self, girl, monkeypatch):
        """⚠⚠ Мешать некому — это **утверждение**, а не пустая строка.

        Молчание здесь читалось бы как поломка ровно так же, как пустая колонка.
        """
        _awake(monkeypatch)
        async with mysql_get_db_async() as db:
            await db.execute("UPDATE profile SET status = 'online' WHERE id = %s",
                             (girl,))
            await db.connection.commit()

        assert 'затихших разговоров сейчас нет' in await initiative_why(STAND_SITE)

    async def test_the_reason_is_not_a_second_copy_of_the_rules(self):
        """⚠⚠ Ручка не считает повод сама — зовёт круг.

        Своя копия правил разошлась бы с кругом на первой же правке отбора, и
        колонка объясняла бы простой, которого нет.
        """
        from tests_.tests_guard import tests_guard_calls

        from src.uvicorn.routers.dating import chat_initiative as handler

        assert 'initiative_why' in tests_guard_calls(handler)

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
        было бы **никогда**, а весь смысл настройки «сколько раз подряд звать
        молчащего» — в том, что заходов несколько."""
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


class TestTheCappedMarkInTheRow:
    """⚠⚠ Строка диалога говорит, упёрлась ли пара в потолок заходов."""

    async def test_a_free_pair_is_not_marked(self, auth_client, girl):
        await _said_in(girl, 'u-1')

        rows = (await auth_client.get(
            f'/api/dating/chat/dialogs?site_id={STAND_SITE}'
            f'&profile_id={girl}')).json()['result']['contacts']

        assert [one['initiative_blocked'] for one in rows] == [False]

    async def test_a_capped_pair_is_marked(self, auth_client, girl):
        """⚠ Потолок у стендового сайта умолчанием два — значит две карточки."""
        await _said_in(girl, 'u-1')
        await _card(girl, 'u-1', status='sent')
        await _card(girl, 'u-1', status='sent')

        rows = (await auth_client.get(
            f'/api/dating/chat/dialogs?site_id={STAND_SITE}'
            f'&profile_id={girl}')).json()['result']['contacts']

        assert [one['initiative_blocked'] for one in rows] == [True]

    def test_the_rule_is_the_one_the_round_uses(self):
        """⚠⚠ Правило одно, и проверяется это чтением кода, а не повторением.

        Своя копия счёта в ручке разошлась бы с кругом на первой правке, и иконка
        горела бы не тогда, когда круг молчит. Ровно так уже устарела подсказка
        `initiative_why`, пережившая появление самого круга на две недели.
        """
        import inspect

        from src.dating import initiative

        assert 'initiative_in_row_sql' in inspect.getsource(
            initiative.initiative_blocked), \
            'ручка считает заходы своим выражением, а не общим'
        assert 'initiative_in_row_sql' in inspect.getsource(initiative._quiet), \
            'отбор круга перестал звать общее выражение'


class TestTheReleaseIcon:
    """⚠ Иконка сброса — в шапке разговора на «Чат → Диалоги», и только там."""

    def _page(self, name: str) -> str:
        import pathlib

        root = pathlib.Path(__file__).resolve().parents[1]

        return (root / name).read_text(encoding='utf-8')

    def test_the_markup_has_the_icon(self):
        page = self._page(
            'data/uvicorn/template/admin/dating/profile/site/_base.html')

        assert page.count('data-slot="initiative-free"') == 1, \
            'иконка либо не заведена, либо уехала не в одну шапку'

    def test_it_stands_in_the_chat_tab_only(self):
        """⚠⚠ Шапок «Разговор» в шаблоне три, и две чужие.

        Вторая — «Фавориты → Диалоги», третья — «Письма»: у них своё занятие, и
        инициатива к ним отношения не имеет. Копия иконки там предлагала бы
        сбросить то, чего нет.
        """
        page = self._page(
            'data/uvicorn/template/admin/dating/profile/site/_base.html')
        before = page.split('data-slot="initiative-free"')[0]

        assert before.count('{% block work_talk %}') == 1, \
            'иконка стоит до блока «Чат → Диалоги»'
        assert '{% block work_favorite_talks %}' not in before, \
            'иконка уехала в шапку «Фаворитов»'
        assert '{% block work_mail_sent %}' not in before, \
            'иконка уехала в шапку «Писем»'

    def test_the_page_reads_the_server_field(self):
        page = self._page('data/uvicorn/template/admin/dating/profile.html')

        assert 'initiative_blocked' in page, \
            'страница не читает признак потолка с сервера'
        assert 'initiative-reset' in page, \
            'щелчок по иконке не зовёт ручку сброса'


class TestTheResetHandle:
    """Ручка сброса: ею страница отпускает упёршуюся пару."""

    async def test_it_resets(self, auth_client, girl):
        await _card(girl, 'u-1', status='sent')

        got = (await auth_client.post(
            f'/api/dating/chat/dialogs/{girl}/u-1/initiative-reset')).json()

        assert got['result'] == {'reset': True}

        async with mysql_get_db_async() as db:
            await db.execute(
                'SELECT COUNT(*) AS n FROM chat_initiative'
                ' WHERE profile_id = %s AND uid = %s AND reset_at IS NOT NULL',
                (girl, 'u-1'))

            assert int(((await db.fetchone()) or {})['n']) == 1, \
                'отметка не легла'

    async def test_nothing_to_reset_is_not_an_error(self, auth_client, girl):
        """⚠ Инициатива этой паре не уходила — ответ «нечего», а не 404.

        Кнопку видно у любого разговора, и нажатие на том, куда мы не писали, —
        обычное дело, а не поломка.
        """
        got = await auth_client.post(
            f'/api/dating/chat/dialogs/{girl}/u-none/initiative-reset')

        assert got.status_code == 200
        assert got.json()['result'] == {'reset': False}

    async def test_a_stranger_is_turned_away(self, client, girl):
        """⚠ Ручка за входом: сброс меняет работу круга, а не вид страницы."""
        got = await client.post(
            f'/api/dating/chat/dialogs/{girl}/u-1/initiative-reset')

        assert got.status_code in (401, 403)


# Настройки занятия: их читают вместе, и стоять им рядом — одним подразделом.
INITIATIVE_SETTINGS = ('chat_initiative_after', 'chat_initiative_after_max',
                       'chat_initiative_reset_after',
                       'chat_initiative_repeat', 'chat_initiative_max_day',
                       'chat_initiative_send_every')


class TestTheSettings:
    """Настройки инициативы — шестёрка, их читают вместе."""

    def test_all_six_are_registered(self):
        after = SITE_KNOWN['chat_initiative_after']
        upto = SITE_KNOWN['chat_initiative_after_max']
        again = SITE_KNOWN['chat_initiative_reset_after']
        times = SITE_KNOWN['chat_initiative_repeat']
        cap = SITE_KNOWN['chat_initiative_max_day']
        pause = SITE_KNOWN['chat_initiative_send_every']

        assert after['title'] == 'Инициатива, проявить впервые через минут (от)'
        assert upto['title'] == 'Инициатива, проявить впервые через минут (до)'
        assert again['title'] == 'Инициатива, сброс через часов'
        assert times['title'] == 'Инициатива, сколько раз подряд звать молчащего'
        assert cap['title'] == 'Инициатива, не больше этого в сутки на пару'
        assert pause['title'] == 'Инициатива, пауза между отправками, в мин'
        assert times['default'] == '2'
        assert times['kind'] == 'number' and times['min'] == 0
        assert upto['kind'] == 'number' and upto['min'] == 0, \
            'ноль обязан быть допустим — это «без разброса»'
        assert pause['kind'] == 'number' and pause['min'] == 0, \
            'ноль обязан быть допустим — это «без паузы»'
        assert again['kind'] == 'number' and again['min'] == 0, \
            'ноль обязан быть допустим — это «сброса нет»'
        assert all(SITE_KNOWN[key]['group'] == 'invite'
                   for key in INITIATIVE_SETTINGS), \
            'настройки читают вместе — и стоять им рядом'

    def test_the_reset_is_off_by_default(self):
        """⚠⚠ Умолчание — ноль, и это не осторожность, а совместимость.

        Настройка меняет **поведение** занятия, а не добавляет новое. Поставь
        умолчанием сутки, и выкладка молча включила бы повторные заходы на всех
        сайтах разом, в том числе там, где человек об этом не просил.
        """
        from src.dating.initiative import INITIATIVE_RESET_AFTER_DEFAULT

        assert SITE_KNOWN['chat_initiative_reset_after']['default'] == '0'
        assert INITIATIVE_RESET_AFTER_DEFAULT == 0, \
            'умолчание кода разошлось с умолчанием реестра'

    def test_the_note_says_what_zero_means(self):
        """⚠ Ноль здесь значит «сброса нет», а не «занятие выключено».

        Без этой строки человек, увидев ноль, читает его как выключатель и
        начинает искать, почему инициатива «выключена», хотя она работает.
        """
        note = SITE_KNOWN['chat_initiative_reset_after']['note']

        assert 'сброса нет' in note
        assert 'Выключателем занятия она не является' in note

    def test_the_backstop_cannot_be_zeroed(self):
        """⚠⚠ Ноль у дневного потолка **запрещён**, и это не придирка.

        Занятие выключают галочкой либо нулём в «сколько раз подряд»; третий
        способ, да ещё неявный, означал бы, что человек, поставивший ноль в
        потолок, молча выключил инициативу целиком и ищет причину в сроках.
        """
        from src.dating.initiative import INITIATIVE_MAX_DAY_DEFAULT
        cap = SITE_KNOWN['chat_initiative_max_day']

        assert cap['kind'] == 'number' and cap['min'] == 1
        assert cap['default'] == str(INITIATIVE_MAX_DAY_DEFAULT), \
            'умолчание реестра разошлось с умолчанием кода'

    def test_the_backstop_is_above_the_row_limit(self):
        """⚠ Потолок не должен мешать живому разговору.

        Он сторожит зацикленный автоответчик, а не человека, — и умолчание у
        него поэтому выше, чем у счёта «подряд». Сравняй их, и предохранитель
        начал бы отвечать на тот же вопрос, что «подряд», то есть стал бы вторым
        пределом об одном.
        """
        from src.dating.initiative import (INITIATIVE_MAX_DAY_DEFAULT,
                                           INITIATIVE_REPEAT_DEFAULT)

        assert INITIATIVE_MAX_DAY_DEFAULT > INITIATIVE_REPEAT_DEFAULT

    def test_they_stand_in_one_subsection(self):
        """⚠⚠ Все четыре — подразделом «Инициатива», а не вразброс по вкладке.

        Вкладка «Инвайты и чат» собрала настройки трёх занятий, и плоская
        таблица заставляет читать все четырнадцать строк, чтобы найти одну.
        Подраздел отвечает на вопрос «где настраивается инициатива» глазами.

        ⚠ Подписи и порядок подразделов знает **сервер** (`SITE_SUBGROUPS`), а не
        страница: иначе новый подраздел требовал бы правки в двух местах.
        """
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
        for key in INITIATIVE_SETTINGS:
            assert 'работа не сделана' not in SITE_KNOWN[key]['note'], key

    def test_the_reset_is_spelled_out_in_the_note(self):
        """⚠⚠ Обнуление ответом — в подписи, иначе его не вывести из названия.

        «Сколько раз подряд звать молчащего» не говорит, что считается именно
        промежуток тишины и что его реплика счёт сбрасывает. А это главное в
        настройке: без обнуления число читалось бы как дневная норма.
        """
        note = SITE_KNOWN['chat_initiative_repeat']['note']

        assert 'обнуля' in note, 'про обнуление ответом в подписи не сказано'
        assert 'не обнуля' not in note, \
            'подпись осталась от дневного предела — она теперь врёт'

    def test_the_sliding_day_is_spelled_out_in_the_backstop(self):
        """⚠ Скользящие сутки — в подписи потолка: полночь человек подразумевает сам."""
        assert 'скольз' in SITE_KNOWN['chat_initiative_max_day']['note']


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
        """⚠⚠ «Карточки нет» — рассинхрон, а не отказ: цель человека достигнута.

        ⚠ Примета сменилась вместе с устройством, а правило — нет. Прежде она
        искала `err.status === 404` **внутри** `initiativeBind`: правило знал
        один обработчик из пяти, и стеречь его можно было только по месту.
        Теперь оно живёт в `poolDrop` (`js/dating/pool.js`) — одно на все пулы,
        — и проверка стала строже: стережёт не это место, а то, что правило
        вообще где-то есть и что пул инициативы им пользуется.

        Трое соседей, кстати, его не знали и показывали красную ошибку там, где
        у человека всё получилось.
        """
        import pathlib

        root = pathlib.Path(__file__).resolve().parents[1]
        mod = (root / 'data/uvicorn/public/js/dating/pool.js').read_text(
            encoding='utf-8')

        assert 'e.status === 404' in mod, \
            'исчезнувшая карточка всё ещё показывается красной ошибкой'
        assert 'POOL_DROP_GONE' in mod, 'исход «её уже не было» неотличим от удачи'

        page = self._page()
        block = page[page.index('function initiativeBind('):]
        block = block[:block.index('\n    }')]

        assert 'poolDrop(' in block, 'пул инициативы снова удаляет своей рукой'
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
