"""Окно «что с инициативой у пары»: считает ли оно то же, что решает круг.

## ⚠⚠ Главное здесь — сторож согласия, а не проверка полей

У занятия восемь правил, и семь невидимы: человек видит пустую колонку либо
красный значок и не знает, упёрлась пара в потолок, не вышел ли срок или она не
годится вовсе. Окно отвечает на это — но ровно до тех пор, пока отвечает **теми
же правилами**, что и круг.

Отбор круга (`_quiet`) идёт по всем парам анкеты разом и выражен одним запросом;
окно (`initiative_explain`) спрашивает про одну пару и выражено иначе. Свести их
в один запрос не выходит, и значит остаётся риск: правка в одном месте, молчание
в другом — и окно объясняет занятие, которого нет.

Поэтому `TestTheyAgree` гоняет **обе дороги по одним данным** и сверяет вердикт.
Это не вкусовщина: та же грабля уже случалась дважды — с `initiative_why`
(колонка уверяла, что круга нет, а он работал две недели) и со значком потолка
(своя копия счёта в ручке).

⚠ База настоящая, сайт — стенд сюиты. Ручки зовутся в процессе: тому же правилу
следуют соседние `test_chat_initiative.py` и `test_profile_mailing.py`.
"""

import uuid

import pytest
from fastapi import HTTPException

from mysql_.mysql_ import mysql_get_db_async
from src.dating.initiative import _quiet, initiative_explain
from src.uvicorn.routers.dating.chat_initiative import chat_initiative_explain

pytestmark = pytest.mark.chat

STAND_SITE = 999_343

# Сроки занятия для проверок — в тех же единицах, в каких их берёт круг:
# секунды у тишины, штуки у потолков.
AFTER = 60 * 60
AFTER_MAX = 2 * 60 * 60
REPEAT = 2
MAX_DAY = 5


@pytest.fixture
async def pair(girl):
    """Затихшая пара: он написал, мы ответили, и с тех пор тишина.

    ⚠⚠ Реплик **две, и порядок важен**. Его входящая обязательна: без неё пара —
    неотвеченный инвайт, и это работа инвайтов. Наша исходящая обязана быть
    **последней**: пока последним говорил он, реплику ждёт автоответ, и
    инициатива туда не лезет — иначе вышли бы два наших сообщения подряд.

    ⚠ Первая версия фикстуры клала только входящую, и половина сторожей
    «согласия» проверяла не то: круг пару законно не брал, а тест считал это
    расхождением.
    """
    uid = f'ex-{uuid.uuid4().hex[:10]}'
    await _said_in(girl, uid, minutes_ago=240)
    await _said_out(girl, uid, minutes_ago=180)

    return girl, uid


@pytest.fixture
async def day_cap():
    """Ставит дневной предел у сайта стенда и убирает за собой.

    ⚠ Нужен ровно одному случаю. Умолчания (5 против «подряд» 2) хватает всем
    прочим, а упереться в дневной предел при нём нельзя вовсе: счёт за сутки —
    часть счёта «подряд», и первым упрётся тот.
    """
    async def _set(value):
        async with mysql_get_db_async() as db:
            await db.execute(
                'UPDATE site SET settings = JSON_SET(COALESCE(settings,'
                ' JSON_OBJECT()), %s, %s) WHERE site_id = %s',
                ('$.chat_initiative_max_day', str(value), STAND_SITE))
            await db.connection.commit()

    yield _set

    async with mysql_get_db_async() as db:
        await db.execute('UPDATE site SET settings = JSON_REMOVE(settings, %s)'
                         ' WHERE site_id = %s',
                         ('$.chat_initiative_max_day', STAND_SITE))
        await db.connection.commit()


class TestTheyAgree:
    """⚠⚠ Окно и круг отвечают одинаково — на одних и тех же данных.

    Сверяется **вердикт**, а не слова: у окна они человеческие и меняются, у
    круга их нет вовсе. Общее у них одно — «берём пару или нет», и ровно это
    обязано совпадать.
    """

    async def test_a_ripe_pair_is_seen_by_both(self, pair):
        """Пара созрела: круг её берёт, окно не находит помех."""
        profile_id, uid = pair

        got = await initiative_explain(profile_id, uid)
        mine = {one['uid'] for one in await _quiet(profile_id, AFTER, AFTER_MAX,
                                                   REPEAT, MAX_DAY)}

        assert uid in mine, 'круг пару не взял — проверять согласие не на чем'
        # ⚠ Выключатель сайта окно считает помехой, а `_quiet` про него не знает
        # вовсе: его спрашивает круг уровнем выше. Поэтому сверяем помехи
        # **кроме** него — иначе тест ловил бы разницу охвата, а не расхождение.
        assert _pair_blockers(got) == [], \
            f'круг пару взял, а окно нашло помехи: {_pair_blockers(got)}'

    async def test_a_capped_pair_is_skipped_by_both(self, pair):
        """Потолок заходов подряд: круг пару не берёт, окно говорит почему."""
        profile_id, uid = pair
        for _ in range(REPEAT):
            await _card(profile_id, uid, status='sent')

        got = await initiative_explain(profile_id, uid)
        mine = {one['uid'] for one in await _quiet(profile_id, AFTER, AFTER_MAX,
                                                   REPEAT, MAX_DAY)}

        assert uid not in mine, 'круг взял пару, упёршуюся в потолок'
        assert got['capped'] is True
        assert got['in_row'] == REPEAT
        assert any('потолок заходов подряд' in one for one in got['blockers'])

    async def test_a_fresh_silence_is_skipped_by_both(self, girl):
        """Срок тишины не вышел: обоим рано, и окно называет сроки.

        ⚠ Реплика минуту назад — тишина короче любого срока занятия.
        """
        uid = f'ex-{uuid.uuid4().hex[:10]}'
        await _said_in(girl, uid, minutes_ago=1)

        got = await initiative_explain(girl, uid)
        mine = {one['uid'] for one in await _quiet(girl, AFTER, AFTER_MAX,
                                                   REPEAT, MAX_DAY)}

        assert uid not in mine, 'круг взял пару, которая только что говорила'
        assert any('срок тишины не вышел' in one for one in got['blockers'])
        assert got['wait_sec'] > 0, 'ждать нечего, хотя срок не вышел'

    async def test_an_unanswered_invite_is_skipped_by_both(self, girl):
        """Он не сказал ни слова: это работа инвайтов, а не инициативы.

        ⚠ Пара из одного нашего исходящего — ровно тот случай, ради которого в
        отборе стоит `his_last IS NOT NULL`.
        """
        uid = f'ex-{uuid.uuid4().hex[:10]}'
        await _said_out(girl, uid, minutes_ago=180)

        got = await initiative_explain(girl, uid)
        mine = {one['uid'] for one in await _quiet(girl, AFTER, AFTER_MAX,
                                                   REPEAT, MAX_DAY)}

        assert uid not in mine, 'круг взял неотвеченный инвайт'
        assert any('не написал ни разу' in one for one in got['blockers'])

    async def test_his_word_awaiting_an_answer_is_skipped_by_both(self, girl):
        """Последним говорил он, решения молчать нет — реплику ждёт автоответ.

        ⚠ Лезть туда инициативе нельзя: вышли бы два наших сообщения подряд на
        одну реплику.
        """
        uid = f'ex-{uuid.uuid4().hex[:10]}'
        await _said_in(girl, uid, minutes_ago=180)

        got = await initiative_explain(girl, uid)
        mine = {one['uid'] for one in await _quiet(girl, AFTER, AFTER_MAX,
                                                   REPEAT, MAX_DAY)}

        assert uid not in mine, 'круг взял реплику, которую ждёт автоответ'
        assert got['last_side'] == 'in'
        assert any('ответ ещё не решён' in one for one in got['blockers'])


class TestTheCounts:
    """Счета и сроки: что окно говорит человеку."""

    async def test_an_untouched_pair_has_room(self, pair):
        profile_id, uid = pair

        got = await initiative_explain(profile_id, uid)

        assert got['known'] is True
        assert got['in_row'] == 0 and got['in_day'] == 0
        assert got['capped'] is False
        assert got['left'] == got['repeat'], 'запас заходов посчитан не от потолка'

    async def test_sent_cards_are_counted(self, pair):
        """Ушедшая карточка — заход, черновик — нет.

        ⚠ Считаются только `status = 'sent'`: лежащий черновик человеку ещё не
        уходил, и числить его заходом значило бы пугать потолком раньше срока.
        """
        profile_id, uid = pair
        await _card(profile_id, uid, status='sent')
        await _card(profile_id, uid, status='draft')

        got = await initiative_explain(profile_id, uid)

        assert got['in_row'] == 1, 'черновик посчитан заходом'
        assert got['in_day'] == 1
        assert got['left'] == got['repeat'] - 1

    async def test_his_answer_resets_both_counts(self, pair):
        """⚠⚠ Его ответ обнуляет **оба** счёта — разговор жив.

        Граница у них одна (`_since_sql`). Прежде дневной мерился от
        «сутки назад» и ответа не замечал: Diana ↔ Bruce — заход в 08:02, его
        ответ в 11:02, а окно отвечало «подряд 0 из 7» и рядом «за сутки 1 из
        1». Живой разговор замирал до завтра.
        """
        profile_id, uid = pair
        await _card(profile_id, uid, status='sent', minutes_ago=120)
        await _said_in(profile_id, uid, minutes_ago=1)

        got = await initiative_explain(profile_id, uid)

        assert got['in_row'] == 0, 'его ответ не обнулил счёт подряд'
        assert got['in_day'] == 0, 'его ответ не обнулил дневной счёт'

    async def test_a_manual_reset_frees_the_pair(self, pair):
        """Сброс руками двигает нижнюю границу счёта — пара отпущена.

        ⚠⚠ Граница одна на оба предела, значит отметка снимает **и дневной**.
        Иначе кнопка «отпустить пару» пару не отпускает: счёт «подряд»
        обнулялся, а окно по-прежнему отвечало «за сутки 1 из 1».
        """
        profile_id, uid = pair
        for _ in range(REPEAT):
            await _card(profile_id, uid, status='sent')

        before = await initiative_explain(profile_id, uid)
        assert before['capped'] is True

        await _reset(profile_id, uid)
        after = await initiative_explain(profile_id, uid)

        assert after['capped'] is False, 'сброс не отпустил пару'
        assert after['in_row'] == 0
        assert after['in_day'] == 0, 'сброс не обнулил дневной счёт'

    async def test_the_day_cap_speaks_for_itself(self, pair, day_cap):
        """Дневной предел исчерпан — окно называет и его.

        ⚠⚠ Предел ставим **ниже «подряд»**, иначе проверять нечего: счёт за
        сутки — часть счёта «подряд», и при умолчании (5 против 2) упереться в
        него нельзя. В этом и смысл настройки — растянуть заходы по дням.

        ⚠ Он молчит с самого начала: ответь он, обнулились бы оба счёта, и
        случай вышел бы про обнуление, а не про предел.
        """
        profile_id, uid = pair
        await day_cap(1)
        await _card(profile_id, uid, status='sent', minutes_ago=120)

        got = await initiative_explain(profile_id, uid)
        mine = {one['uid'] for one in await _quiet(profile_id, AFTER, AFTER_MAX,
                                                   REPEAT, 1)}

        assert uid not in mine, 'круг взял пару, упёршуюся в дневной предел'
        assert got['in_row'] == 1 and got['in_day'] == 1
        assert got['capped'] is False, 'пару остановил «подряд», а не сутки'
        assert got['day_capped'] is True
        assert any('дневной потолок' in one for one in got['blockers'])

    async def test_the_sent_log_is_shown(self, pair):
        """Последние заходы видны — вместе с теми, что не ушли.

        ⚠ Без них «заходов 0» читается как «занятие не работало», хотя карточки
        могли лечь в ошибки.
        """
        profile_id, uid = pair
        await _card(profile_id, uid, status='sent')
        await _card(profile_id, uid, status='draft')

        got = await initiative_explain(profile_id, uid)

        assert len(got['sent']) == 2, 'показаны не все заходы пары'


class TestTheEndpoint:
    """Ручка: что она отвечает и кого пускает."""

    async def test_it_answers_for_a_pair(self, pair):
        profile_id, uid = pair

        got = (await chat_initiative_explain(profile_id=profile_id, uid=uid,
                                             user=None))['result']

        assert got['known'] is True
        assert got['site_id'] == STAND_SITE

    async def test_an_unknown_pair_is_not_an_error(self, girl):
        """⚠ Разговора нет — обычный исход, а не 404.

        Человек открывает окно у пары, которой нет в `message`; красная ошибка
        читалась бы как поломка панели.
        """
        got = (await chat_initiative_explain(profile_id=girl, uid='nobody-000',
                                             user=None))['result']

        assert got['known'] is False
        assert got['blockers'], 'окно промолчало о том, что пары нет'

    async def test_without_arguments_it_refuses(self):
        """422 со словами: пара — это два довода, и половины не бывает."""
        with pytest.raises(HTTPException) as refusal:
            await chat_initiative_explain(profile_id=0, uid='', user=None)

        assert refusal.value.status_code == 422

    async def test_closed_without_login(self, client):
        """То, что решается до входа в обработчик, — по HTTP, как и вся сюита."""
        answer = await client.get('/api/dating/chat-initiative/explain'
                                  '?profile_id=1&uid=x')

        assert answer.status_code == 401

    async def test_the_word_path_is_not_eaten_by_the_number_one(self, auth_client):
        """⚠⚠ `explain` — путь-слово рядом с путём-числом `/{initiative_id}`.

        Объяви его ниже, и FastAPI отдал бы запрос преобразователю номера: ручка
        отвечала бы 422 на любой вызов. Соседний роутер на этом уже ловился,
        поэтому сторож стоит здесь, а не в чьей-то памяти.
        """
        answer = await auth_client.get('/api/dating/chat-initiative/explain'
                                       '?profile_id=1&uid=x')

        assert answer.status_code == 200, answer.text


class TestThePageCarriesTheIcon:
    """Значок «что с инициативой» и его обвязка — в шапке разговора.

    ⚠ Сторожим **и разметку, и привязку**: нарисованный мёртвый значок хуже
    пропавшего — пропажу видно сразу, а мёртвый узнаётся только щелчком. Та же
    пара проверок, что у значка сброса рядом.
    """

    def _page(self, name: str) -> str:
        import pathlib

        root = pathlib.Path(__file__).resolve().parents[1]

        return (root / name).read_text(encoding='utf-8')

    def test_the_markup_has_the_icon(self):
        page = self._page(
            'data/uvicorn/template/admin/dating/profile/site/_base.html')

        assert page.count('data-slot="initiative-info"') == 1, \
            'значок либо не заведён, либо уехал не в одну шапку'

    def test_it_stands_before_the_reset(self):
        """⚠ Порядок — решение, а не случайность: сперва смотрят, что с парой,
        и только потом решают отпускать."""
        page = self._page(
            'data/uvicorn/template/admin/dating/profile/site/_base.html')

        assert (page.index('data-slot="initiative-info"')
                < page.index('data-slot="initiative-free"')), \
            'значок информации встал после сброса'

    def test_the_two_icons_stay_together(self):
        """⚠⚠ Оба значка — внутри одной обёртки.

        Шапка переносит по словам (`.in-head` c `flex-wrap: wrap`), а колонку
        разговора человек сужает сам. Без обёртки два значка вставали столбиком
        один под другим — поймано замером в браузере, а не на глаз.
        """
        page = self._page(
            'data/uvicorn/template/admin/dating/profile/site/_base.html')
        head, _, tail = page.partition('class="talk-acts"')

        assert tail, 'обёртки значков больше нет — они разъедутся в узкой колонке'
        assert tail.index('data-slot="initiative-info"') < tail.index('</span>')
        assert tail.index('data-slot="initiative-free"') < tail.index('</span>')

    def test_the_glyph_is_the_circled_one(self):
        """⚠⚠ Кружок `ⓘ`, а не `ℹ` — и это не вкусовщина.

        `ℹ` (U+2139) в шрифте панели рисуется **голой буквой «i»** без кружка:
        рядом со значком сброса она читается как случайный символ, а не кнопка.
        Видно это только кадром — в DOM оба состояния выглядят одинаково
        («значок на месте»), и первая версия так и ушла бы.
        """
        page = self._page(
            'data/uvicorn/template/admin/dating/profile/site/_base.html')
        mark = page.split('data-slot="initiative-info"')[1].split('</button>')[0]

        assert 'ⓘ' in mark, 'значок информации не кружковый'
        assert 'ℹ' not in mark, 'вернулся глиф, который рисуется голой буквой'

    def test_the_handler_is_bound(self):
        page = self._page('data/uvicorn/template/admin/dating/profile.html')

        assert 'initiative-info' in page, 'страница не ищет значок'
        assert 'initiativeInfoShow' in page, 'значок нарисован, но мёртв'

    def test_the_page_asks_the_server(self):
        """⚠⚠ Своей арифметики на странице нет — и не должно появиться.

        Правила считает сервер тем же кодом, что и круг; копия на странице
        разошлась бы с ним на первой правке. Сторожим это по вызову ручки.
        """
        page = self._page('data/uvicorn/template/admin/dating/profile.html')

        assert 'chat-initiative/explain' in page, \
            'окно не спрашивает сервер — похоже, считает само'


def _pair_blockers(got: dict) -> list:
    """Помехи, о которых знает и круг: без выключателя сайта.

    ⚠ `_quiet` про выключатель не знает вовсе — его спрашивает круг уровнем выше
    (`initiative_make`). Сверять их вместе значило бы ловить разницу охвата, а не
    расхождение правил.
    """
    return [one for one in (got.get('blockers') or [])
            if 'выключен' not in one and 'обнулён' not in one]


async def _said_in(profile_id: int, uid: str, minutes_ago: int = 60) -> None:
    """Его входящая реплика.

    ⚠ Время считает **база**: MySQL живёт по UTC, и `datetime.now()` положил бы
    строку в будущее. То же правило, что в `test_chat_initiative.py`.
    """
    await _said(profile_id, uid, 'in', minutes_ago)


async def _said_out(profile_id: int, uid: str, minutes_ago: int = 60) -> None:
    """Наша исходящая реплика — пара без единого его слова."""
    await _said(profile_id, uid, 'out', minutes_ago)


async def _said(profile_id: int, uid: str, side: str, minutes_ago: int) -> None:
    async with mysql_get_db_async() as db:
        await db.execute(
            'INSERT INTO message (profile_id, sender_uid, sender_name, text,'
            '  message_uid, direction, created_at)'
            " VALUES (%s, %s, 'Brian', 'привет', %s, %s,"
            '  NOW() - INTERVAL %s MINUTE)',
            (int(profile_id), uid, f'ex-{uuid.uuid4().hex[:12]}', side,
             int(minutes_ago)))
        await db.connection.commit()


async def _card(profile_id: int, uid: str, status: str = 'draft',
                minutes_ago: int = 60) -> int:
    """Карточка инициативы. Ушедшей ставится `sent_at` — по ней и счёт.

    ⚠⚠ `minutes_ago` обязателен по существу, а не для красоты: счёт «подряд»
    считает карточки **новее его последнего слова**, и обнуление проверяется
    репликой, пришедшей после них. Ставь карточкам `NOW()` — и его ответ
    пришлось бы класть в будущее, чего база не позволит. Первая версия теста так
    и падала: «его ответ не обнулил счёт», а виноват был порядок времён.
    """
    async with mysql_get_db_async() as db:
        await db.execute(
            'INSERT INTO chat_initiative (site_id, profile_id, uid, name, body,'
            '  try_no, status, sent_at)'
            " VALUES (%s, %s, %s, 'Brian', 'как выходные?', 1, %s,"
            "  IF(%s = 'sent', NOW() - INTERVAL %s MINUTE, NULL))",
            (STAND_SITE, int(profile_id), uid, status, status, int(minutes_ago)))
        await db.connection.commit()

        return int(db.lastrowid)


async def _reset(profile_id: int, uid: str) -> None:
    """Сброс счёта руками — тем же способом, что и кнопка в шапке разговора."""
    async with mysql_get_db_async() as db:
        await db.execute(
            'UPDATE chat_initiative SET reset_at = NOW()'
            ' WHERE profile_id = %s AND uid = %s',
            (int(profile_id), uid))
        await db.connection.commit()
