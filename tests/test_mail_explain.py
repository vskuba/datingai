"""Окно «почему этой паре не пишут письма»: считает ли оно то же, что круги.

## ⚠⚠ Главное здесь — сторож согласия, а не проверка полей

Занятий два, правил у них шестнадцать, и почти все невидимы: человек видит
молчащую переписку и не знает, упёрлась ли пара в дневной предел получателя, не
вышла ли пауза отправительницы, не закрылся ли человек или просто не идёт смена.
Окно отвечает на это — но ровно до тех пор, пока отвечает **теми же правилами**,
что и круги.

Отбор кругов (`favorite_mail._quiet`, `mail_reply._to_answer`) идёт по всем парам
сайта одним запросом; окно спрашивает про одну пару и выражено иначе. Свести их в
один запрос не выходит, и значит остаётся риск: правка в одном месте, молчание в
другом — и окно объясняет занятие, которого нет.

Поэтому `TestTheyAgree` гоняет **обе дороги по одним данным**. Та же грабля в
проекте уже случалась дважды — с `initiative_why` и со значком потолка.

⚠ База настоящая, сайт — стенд сюиты, ручки зовутся в процессе: тому же правилу
следуют `test_initiative_explain.py` и `test_favorite_mail.py`.

⚠⚠ **Redis подменяется, а не используется живым.** С хоста он недоступен вовсе
(`REDIS_URL` — имя сети compose, порт наружу не опубликован), а весь код глотает
его сбой молча: на живом Redis проверки «он в сети» и «остуда» зеленели бы, ничего
не проверяя.
"""

import time
import uuid

import pytest

from mysql_.mysql_ import mysql_get_db_async

from src.dating import mail_explain as explain
from src.dating import online as online_module
from src.dating.mail_explain import mail_explain

pytestmark = pytest.mark.mail

STAND_SITE = 999_343

# Смена на целые сутки: без неё первой помехой всегда ловилось бы «не смена», и
# каждая проверка проверяла бы расписание вместо своего правила.
ALL_DAY = '[{"from": "00:00", "to": "23:59"}]'


# ── Подмены ──────────────────────────────────────────────────────────────────

@pytest.fixture
def online_feed(monkeypatch):
    """Кладёт людей в снимок «кто в сети» сайта стенда.

    ⚠⚠ Подменяется `online._snapshot`, а не Redis: с хоста Redis недоступен
    вовсе, и `_snapshot` при сбое отвечает `None` — то есть «не знаем». На живом
    Redis проверка «он в сети» зеленела бы, ничего не проверяя.

    ⚠ Форма снимка — та, которую ждёт `online._check`: `at` (метка времени) и
    `people` со списком `uid`.
    """
    def _set(uids: list) -> None:
        monkeypatch.setattr(
            online_module, '_snapshot',
            lambda site_id: {'at': time.time(),
                             'people': [{'uid': str(one)} for one in uids]})

    return _set


@pytest.fixture
def cooling(monkeypatch):
    """Ставит остуду получателю: сайт просит подождать.

    ⚠ Подменяется имя **в `mail_explain`**, а не в `mail_wait`: модуль
    импортировал функцию к себе, и подмена по прежнему месту его ссылку не
    трогает — тест зеленел бы молча.
    """
    def _set(seconds: int) -> None:
        monkeypatch.setattr(explain, 'mail_wait_left',
                            lambda site_id, uid: int(seconds))

    return _set


# ── Посев ────────────────────────────────────────────────────────────────────

async def _said_in(profile_id: int, uid: str, *, minutes_ago: int = 60,
                   body: str = 'his letter') -> int:
    """Его входящее письмо. Возвращает номер строки `mail_in`."""
    async with mysql_get_db_async() as db:
        await db.execute(
            'INSERT INTO mail_in'
            ' (site_id, profile_id, message_id, uid, name, body, date_post)'
            ' VALUES (%s, %s, %s, %s, %s, %s, NOW() - INTERVAL %s MINUTE)',
            (STAND_SITE, int(profile_id), int(uuid.uuid4().int % 10 ** 9),
             str(uid), 'He', body, int(minutes_ago)))
        await db.connection.commit()
        await db.execute('SELECT LAST_INSERT_ID() AS id')

        return int((await db.fetchone())['id'])


async def _star(profile_id: int, uid: str, state: str = 'done') -> None:
    """Звёздочка: пара в избранных."""
    async with mysql_get_db_async() as db:
        await db.execute(
            'INSERT INTO favorite (site_id, profile_id, uid, state)'
            ' VALUES (%s, %s, %s, %s)'
            ' ON DUPLICATE KEY UPDATE state = VALUES(state)',
            (STAND_SITE, int(profile_id), str(uid), str(state)))
        await db.connection.commit()


async def _block(profile_id: int, uid: str, *, channel: str = 'all',
                 reason: str = 'он закрылся от вас') -> None:
    """Запрет «не пишем» на пару."""
    async with mysql_get_db_async() as db:
        await db.execute(
            'INSERT INTO block'
            ' (site_id, profile_id, uid, name, reason, source, channel)'
            ' VALUES (%s, %s, %s, %s, %s, %s, %s)',
            (STAND_SITE, int(profile_id), str(uid), 'He', reason, 'hand',
             str(channel)))
        await db.connection.commit()


async def _fav_sent(profile_id: int, uid: str, *, minutes_ago: int = 10) -> None:
    """Ушедшее приставание — им считаются оба предела и последняя реплика."""
    async with mysql_get_db_async() as db:
        await db.execute(
            'INSERT INTO favorite_mail'
            ' (site_id, profile_id, uid, name, body, status, sent_at)'
            " VALUES (%s, %s, %s, 'He', 'hi', 'sent',"
            '         NOW() - INTERVAL %s MINUTE)',
            (STAND_SITE, int(profile_id), str(uid), int(minutes_ago)))
        await db.connection.commit()


async def _fav_draft(profile_id: int, uid: str) -> None:
    """Черновик приставания в пуле."""
    async with mysql_get_db_async() as db:
        await db.execute(
            'INSERT INTO favorite_mail'
            ' (site_id, profile_id, uid, name, body, status)'
            " VALUES (%s, %s, %s, 'He', 'hi', 'draft')",
            (STAND_SITE, int(profile_id), str(uid)))
        await db.connection.commit()


async def _site_flag(name: str, value: int) -> None:
    """Выключатель занятия у сайта стенда.

    ⚠ Имя столбца уезжает текстом — параметром его не передать; приходит оно из
    самих тестов, снаружи взяться неоткуда.
    """
    async with mysql_get_db_async() as db:
        await db.execute(f'UPDATE site SET {name} = %s WHERE site_id = %s',
                         (int(value), STAND_SITE))
        await db.connection.commit()


@pytest.fixture
async def fav_on():
    """Приставание включено, смена сочинения идёт круглые сутки.

    Убирает за собой: сайт стенда общий на всю сюиту, и оставленный поднятым
    выключатель менял бы ответы соседних файлов.
    """
    async with mysql_get_db_async() as db:
        await db.execute(
            'UPDATE site SET favorite_mail_make_on = 1,'
            ' favorite_mail_send_on = 1, favorite_mail_schedule = %s'
            ' WHERE site_id = %s', (ALL_DAY, STAND_SITE))
        await db.connection.commit()

    yield

    async with mysql_get_db_async() as db:
        await db.execute(
            'UPDATE site SET favorite_mail_make_on = 0,'
            ' favorite_mail_send_on = 0, favorite_mail_schedule = NULL'
            ' WHERE site_id = %s', (STAND_SITE,))
        await db.connection.commit()


def _has(got: dict, part: str, needle: str) -> bool:
    """Есть ли среди помех занятия строка с этими словами."""
    return any(needle in one for one in got[part]['blockers'])


# ── Проверки ─────────────────────────────────────────────────────────────────

class TestKnown:
    """Знает ли окно эту пару вообще."""

    async def test_unknown_profile_is_not_an_error(self):
        """Анкеты нет — пустой ответ, а не исключение.

        ⚠ Человек открывает окно у разговора, которого в наших таблицах может не
        быть вовсе. Падение здесь нарисовалось бы красным там, где всё в порядке;
        в `known: false` пустоту превращает ручка.
        """
        assert await mail_explain(0, 'nobody') == {}
        assert await mail_explain(999_999_999, 'nobody') == {}

    async def test_pair_without_letters_is_known(self, girl):
        """Анкета есть, писем нет — пара известна, просто пустая.

        ⚠ Отсутствие переписки **не** делает пару неизвестной: приставание ей
        законно (звёздочка сама означает, что разговор был), и окно обязано это
        показать, а не отговориться незнанием.
        """
        got = await mail_explain(girl, f'ex-{uuid.uuid4().hex[:10]}')

        assert got['known'] is True
        assert got['site_id'] == STAND_SITE
        assert got['last']['at'] is None
        assert got['last']['side'] == ''


class TestCommon:
    """Общее для обоих занятий: последняя реплика, запрет, остуда."""

    async def test_last_word_is_his(self, girl):
        """Его письмо — последняя реплика, и сторона названа."""
        uid = f'ex-{uuid.uuid4().hex[:10]}'
        await _said_in(girl, uid, minutes_ago=30)

        got = await mail_explain(girl, uid)

        assert got['last']['side'] == 'him'
        assert got['last']['at'] is not None

    async def test_our_letter_is_the_last_word(self, girl):
        """Наше ушедшее письмо позже его — последнее слово наше.

        ⚠⚠ Считается **по всей почте**, а не по входящим: смотри окно только
        `mail_in`, затихшим выглядел бы всякий, кому мы писали последними.
        """
        uid = f'ex-{uuid.uuid4().hex[:10]}'
        await _said_in(girl, uid, minutes_ago=120)
        await _fav_sent(girl, uid, minutes_ago=10)

        got = await mail_explain(girl, uid)

        assert got['last']['side'] == 'her'

    async def test_block_is_seen_with_mark_and_channel(self, girl):
        """Запрет виден приметой и каналом — по каналу решается охват."""
        uid = f'ex-{uuid.uuid4().hex[:10]}'
        await _block(girl, uid, channel='mail',
                     reason='вы не подходите ему по возрасту')

        got = await mail_explain(girl, uid)

        assert got['block']['is'] is True
        assert got['block']['channel'] == 'mail'
        assert 'возраст' in got['block']['mark']

    async def test_no_block_is_silent(self, girl):
        """Запрета нет — поле пустое, а не отсутствующее."""
        got = await mail_explain(girl, f'ex-{uuid.uuid4().hex[:10]}')

        assert got['block'] == {'is': False, 'mark': '', 'channel': ''}


class TestFavoriteBlockers:
    """Одиннадцать помех приставания — в порядке, в каком их проверяет круг."""

    async def test_make_off(self, girl, fav_on):
        uid = f'ex-{uuid.uuid4().hex[:10]}'
        await _star(girl, uid)
        await _site_flag('favorite_mail_make_on', 0)

        got = await mail_explain(girl, uid)

        assert _has(got, 'favorite', 'набор писем избранным выключен')

    async def test_not_a_shift(self, girl, fav_on):
        """Смена в прошлом — круг не наберёт ничего, сколько бы пар ни ждало."""
        uid = f'ex-{uuid.uuid4().hex[:10]}'
        await _star(girl, uid)
        async with mysql_get_db_async() as db:
            await db.execute(
                'UPDATE site SET favorite_mail_schedule = %s WHERE site_id = %s',
                ('[{"from": "00:00", "to": "00:01"}]', STAND_SITE))
            await db.connection.commit()

        got = await mail_explain(girl, uid)

        assert _has(got, 'favorite', 'не смена')

    async def test_no_star(self, girl, fav_on):
        """Звёздочки нет — приставание не про эту пару."""
        got = await mail_explain(girl, f'ex-{uuid.uuid4().hex[:10]}')

        assert got['favorite']['star'] is False
        assert _has(got, 'favorite', 'нет в избранных')

    async def test_girl_offline(self, girl, fav_on):
        """Анкета не в сети: письмо уходит от её имени, и отправка её ждёт."""
        uid = f'ex-{uuid.uuid4().hex[:10]}'
        await _star(girl, uid)

        got = await mail_explain(girl, uid)

        assert _has(got, 'favorite', 'анкета не в сети')

    async def test_he_spoke_last(self, girl_make, fav_on):
        """Последним говорил он — отвечать должен круг ответов, не этот."""
        girl = await girl_make(online=True)
        uid = f'ex-{uuid.uuid4().hex[:10]}'
        await _star(girl, uid)
        await _said_in(girl, uid, minutes_ago=10)

        got = await mail_explain(girl, uid)

        assert _has(got, 'favorite', 'последним говорил он')

    async def test_draft_already_waits(self, girl_make, fav_on):
        """У анкеты уже лежит черновик — второй был бы вторым письмом подряд.

        ⚠ Условие **по анкете**, а не по паре: черновик занимает ближайшую
        очередь девушки целиком, кому бы он ни был написан.
        """
        girl = await girl_make(online=True)
        uid = f'ex-{uuid.uuid4().hex[:10]}'
        await _star(girl, uid)
        await _fav_draft(girl, f'other-{uuid.uuid4().hex[:8]}')

        got = await mail_explain(girl, uid)

        assert _has(got, 'favorite', 'уже лежит черновик')

    async def test_block_stops_favorite(self, girl_make, fav_on):
        """Запрет «не пишем» гасит приставание и называет примету."""
        girl = await girl_make(online=True)
        uid = f'ex-{uuid.uuid4().hex[:10]}'
        await _star(girl, uid)
        await _block(girl, uid, channel='all')

        got = await mail_explain(girl, uid)

        assert _has(got, 'favorite', 'запрещено')
        assert _has(got, 'favorite', 'он закрылся от вас')

    async def test_he_is_offline(self, girl_make, fav_on):
        """Его нет в сети — и оговорка, что ленту могли не снимать.

        ⚠ «Не знаем» здесь читается как «не сейчас», в отличие от чата: письмо
        стоит вызова модели и одного из двух писем в сутки.
        """
        girl = await girl_make(online=True)
        uid = f'ex-{uuid.uuid4().hex[:10]}'
        await _star(girl, uid)

        got = await mail_explain(girl, uid)

        assert got['favorite']['him_online'] is False
        assert got['favorite']['him_known'] is False
        assert _has(got, 'favorite', 'нет в сети')
        assert _has(got, 'favorite', 'ленту ещё не снимали')

    async def test_cooling(self, girl_make, fav_on, online_feed, cooling):
        """Сайт сам просит подождать — выбран предел входящих у получателя."""
        girl = await girl_make(online=True)
        uid = f'ex-{uuid.uuid4().hex[:10]}'
        await _star(girl, uid)
        online_feed([uid])
        cooling(1800)

        got = await mail_explain(girl, uid)

        assert got['cooling_sec'] == 1800
        assert _has(got, 'favorite', 'предел входящих')

    async def test_person_day_cap(self, girl_make, fav_on, online_feed):
        """Дневной предел считает **получатель**, а не пара.

        ⚠ Письма шлёт другая анкета — и всё равно считаются: пока ключом был
        `profile_id`, тринадцать девушек давали одному человеку до 26 писем.
        """
        girl = await girl_make(online=True)
        other = await girl_make(online=True)
        uid = f'ex-{uuid.uuid4().hex[:10]}'
        await _star(girl, uid)
        online_feed([uid])
        for _ in range(2):
            await _fav_sent(other, uid, minutes_ago=600)

        got = await mail_explain(girl, uid)

        assert got['favorite']['day_sent'] >= 2
        assert _has(got, 'favorite', 'за сутки')

    async def test_girl_gap(self, girl_make, fav_on, online_feed):
        """Пауза считает **отправительницу**: кому она писала — неважно."""
        girl = await girl_make(online=True)
        uid = f'ex-{uuid.uuid4().hex[:10]}'
        await _star(girl, uid)
        online_feed([uid])
        await _fav_sent(girl, f'other-{uuid.uuid4().hex[:8]}', minutes_ago=5)

        got = await mail_explain(girl, uid)

        assert _has(got, 'favorite', 'пауза')


class TestFavoriteNotBlockers:
    """Чего в помехах быть не должно."""

    async def test_round_pass_is_not_a_blocker(self, girl_make, fav_on,
                                               online_feed):
        """Срок круга — поле `next_sec`, а не помеха.

        ⚠ Помеха — то, что мешает **паре** быть годной. Срок круга мешает не
        паре, а всем сразу, и назови мы его помехой — человек стал бы искать,
        что эта пара нарушила.
        """
        girl = await girl_make(online=True)
        uid = f'ex-{uuid.uuid4().hex[:10]}'
        await _star(girl, uid)
        online_feed([uid])

        got = await mail_explain(girl, uid)

        assert not _has(got, 'favorite', 'вернётся через')
        assert isinstance(got['favorite']['next_sec'], int)

    async def test_batch_rules_are_absent(self, girl_make, fav_on, online_feed):
        """Внутрисходовых правил в окне нет вовсе.

        ⚠⚠ «Ему уже пишет другая анкета этим заходом» и «эта анкета уже написала
        этим заходом» — про **заход**, а не про пару: к следующему заходу их нет.
        Назови их окно помехой, человек ждал бы снятия того, что уже снято.
        """
        girl = await girl_make(online=True)
        uid = f'ex-{uuid.uuid4().hex[:10]}'
        await _star(girl, uid)
        online_feed([uid])

        got = await mail_explain(girl, uid)

        assert not _has(got, 'favorite', 'этим заходом')


class TestOrder:
    """Помехи идут в том же порядке, в каком их проверяет круг.

    ⚠⚠ **Порядок — не украшение.** Человек читает первую строку и по ней решает,
    ждать ему или править настройку. Встань преходящая помеха («он не в сети»,
    пройдёт за минуту) выше решённой человеком («набор выключен»), и он пошёл бы
    ждать вместо того, чтобы поднять выключатель.
    """

    async def test_switch_stands_above_online(self, girl, fav_on):
        """Выключатель выше «его нет в сети»: первое решает человек, второе — время."""
        uid = f'ex-{uuid.uuid4().hex[:10]}'
        await _star(girl, uid)
        await _site_flag('favorite_mail_make_on', 0)

        rows = (await mail_explain(girl, uid))['favorite']['blockers']
        switch = next(i for i, one in enumerate(rows) if 'выключен' in one)
        online = next(i for i, one in enumerate(rows) if 'нет в сети' in one)

        assert switch < online

    async def test_star_stands_above_limits(self, girl_make, fav_on, online_feed):
        """Звёздочка выше пределов: без неё занятие к паре не относится вовсе."""
        girl = await girl_make(online=True)
        uid = f'ex-{uuid.uuid4().hex[:10]}'
        online_feed([uid])
        # Звёздочки нет — и при этом выбран дневной предел получателя.
        for _ in range(2):
            await _fav_sent(girl, uid, minutes_ago=600)

        rows = (await mail_explain(girl, uid))['favorite']['blockers']
        star = next(i for i, one in enumerate(rows) if 'в избранных' in one)
        day = next(i for i, one in enumerate(rows) if 'за сутки' in one)

        assert star < day


@pytest.fixture
async def reply_on():
    """Генерация и отправка ответов включены — убирается за собой."""
    await _site_flag('reply_make_on', 1)
    await _site_flag('reply_send_on', 1)

    yield

    await _site_flag('reply_make_on', 0)
    await _site_flag('reply_send_on', 0)


async def _reply_draft(profile_id: int, uid: str, mail_in_id: int, *,
                       error: str = '') -> int:
    """Черновик ответа в пуле."""
    async with mysql_get_db_async() as db:
        await db.execute(
            'INSERT INTO mail_reply (site_id, profile_id, mail_in_id, uid,'
            " name, body, status, error)"
            " VALUES (%s, %s, %s, %s, 'He', 'reply body', 'draft', %s)",
            (STAND_SITE, int(profile_id), int(mail_in_id), str(uid),
             str(error)))
        await db.connection.commit()
        await db.execute('SELECT LAST_INSERT_ID() AS id')

        return int((await db.fetchone())['id'])


class TestReplyBlockers:
    """Пять помех ответа — в порядке `mail_reply._to_answer`."""

    async def test_make_off(self, girl, reply_on):
        uid = f'ex-{uuid.uuid4().hex[:10]}'
        await _said_in(girl, uid)
        await _site_flag('reply_make_on', 0)

        got = await mail_explain(girl, uid)

        assert _has(got, 'reply', 'генерация ответов выключена')

    async def test_profile_disabled(self, girl, reply_on):
        uid = f'ex-{uuid.uuid4().hex[:10]}'
        await _said_in(girl, uid)
        async with mysql_get_db_async() as db:
            await db.execute('UPDATE profile SET enabled = 0 WHERE id = %s',
                             (int(girl),))
            await db.connection.commit()

        got = await mail_explain(girl, uid)

        assert _has(got, 'reply', 'анкета выключена')

    async def test_he_never_wrote(self, girl, reply_on):
        """Писем от него нет — отвечать не на что, и это не поломка."""
        got = await mail_explain(girl, f'ex-{uuid.uuid4().hex[:10]}')

        assert got['reply']['his_last'] is None
        assert _has(got, 'reply', 'не писал ни разу')

    async def test_answer_already_written(self, girl, reply_on):
        """На последнее письмо ответ уже есть — второй был бы двумя подряд."""
        uid = f'ex-{uuid.uuid4().hex[:10]}'
        letter = await _said_in(girl, uid)
        await _reply_draft(girl, uid, letter)

        got = await mail_explain(girl, uid)

        assert _has(got, 'reply', 'ответ уже написан')

    async def test_old_answer_does_not_block_a_new_letter(self, girl, reply_on):
        """⚠ Ответ на **позапрошлое** письмо новому не мешает.

        Ключ — номер письма, а не пара: спроси окно «есть ли ответ этой паре»,
        оно запрещало бы отвечать на каждое второе письмо подряд.
        """
        uid = f'ex-{uuid.uuid4().hex[:10]}'
        old = await _said_in(girl, uid, minutes_ago=120)
        await _reply_draft(girl, uid, old)
        await _said_in(girl, uid, minutes_ago=5)

        got = await mail_explain(girl, uid)

        assert not _has(got, 'reply', 'ответ уже написан')

    async def test_empty_body(self, girl, reply_on):
        """Тело письма ещё не забрали — отвечать нечем."""
        uid = f'ex-{uuid.uuid4().hex[:10]}'
        await _said_in(girl, uid, body='')

        got = await mail_explain(girl, uid)

        assert got['reply']['his_last']['empty'] is True
        assert _has(got, 'reply', 'нет текста')

    async def test_ripe_pair_has_no_blockers(self, girl, reply_on):
        """Письмо есть, текст есть, ответа нет — помех быть не должно."""
        uid = f'ex-{uuid.uuid4().hex[:10]}'
        await _said_in(girl, uid, body='his letter')

        got = await mail_explain(girl, uid)

        assert got['reply']['blockers'] == []


@pytest.fixture
async def day_long():
    """Смена **отправки** — круглые сутки (`mailing_schedule`, одна на почту).

    Без неё «не смена» ловилось бы первой помехой отправки в каждой проверке.
    """
    async with mysql_get_db_async() as db:
        await db.execute(
            'UPDATE site SET mailing_schedule = %s WHERE site_id = %s',
            (ALL_DAY, STAND_SITE))
        await db.connection.commit()

    yield

    async with mysql_get_db_async() as db:
        await db.execute('UPDATE site SET mailing_schedule = NULL'
                         ' WHERE site_id = %s', (STAND_SITE,))
        await db.connection.commit()


def _sent_has(got: dict, part: str, needle: str) -> bool:
    """Есть ли среди помех **отправки** строка с этими словами."""
    return any(needle in one for one in got[part]['send_blockers'])


class TestSendBlockers:
    """Четыре помехи отправки — у каждого занятия своя часть."""

    async def test_send_off(self, girl, reply_on, day_long):
        uid = f'ex-{uuid.uuid4().hex[:10]}'
        letter = await _said_in(girl, uid)
        await _reply_draft(girl, uid, letter)
        await _site_flag('reply_send_on', 0)

        got = await mail_explain(girl, uid)

        assert _sent_has(got, 'reply', 'отправка ответов выключена')

    async def test_not_a_send_shift(self, girl, reply_on):
        """Смены отправки нет вовсе — письмо лежит до её часов."""
        uid = f'ex-{uuid.uuid4().hex[:10]}'
        letter = await _said_in(girl, uid)
        await _reply_draft(girl, uid, letter)

        got = await mail_explain(girl, uid)

        assert _sent_has(got, 'reply', 'не смена')

    async def test_draft_with_error_is_not_taken(self, girl, reply_on, day_long):
        """Письмо с прежней ошибкой круг не берёт — его досылают кнопкой."""
        uid = f'ex-{uuid.uuid4().hex[:10]}'
        letter = await _said_in(girl, uid)
        await _reply_draft(girl, uid, letter, error='сайт отказал')

        got = await mail_explain(girl, uid)

        assert _sent_has(got, 'reply', 'прежняя ошибка')

    async def test_cooling_stops_sending(self, girl, reply_on, day_long,
                                         cooling):
        uid = f'ex-{uuid.uuid4().hex[:10]}'
        letter = await _said_in(girl, uid)
        await _reply_draft(girl, uid, letter)
        cooling(900)

        got = await mail_explain(girl, uid)

        assert _sent_has(got, 'reply', 'предел входящих')

    async def test_clean_draft_has_no_send_blockers(self, girl, reply_on,
                                                    day_long):
        """Выключатель поднят, смена идёт, ошибки нет — помех отправки нет."""
        uid = f'ex-{uuid.uuid4().hex[:10]}'
        letter = await _said_in(girl, uid)
        await _reply_draft(girl, uid, letter)

        got = await mail_explain(girl, uid)

        assert got['reply']['send_blockers'] == []


class TestWhatIsNext:
    """Что уйдёт паре следующим — считает сервер, а не страница."""

    async def test_reply_draft_wins(self, girl, reply_on, day_long):
        """Черновик ответа лежит и уйти может — следующим уйдёт он."""
        uid = f'ex-{uuid.uuid4().hex[:10]}'
        letter = await _said_in(girl, uid)
        await _reply_draft(girl, uid, letter)

        got = await mail_explain(girl, uid)

        assert got['next'] == 'reply'

    async def test_blocked_draft_does_not_win(self, girl, reply_on):
        """Черновик есть, но отправка стоит — следующим он не считается.

        ⚠ Иначе окно обещало бы письмо, которое никуда не уйдёт: смены
        отправки нет, и круг его не возьмёт.
        """
        uid = f'ex-{uuid.uuid4().hex[:10]}'
        letter = await _said_in(girl, uid)
        await _reply_draft(girl, uid, letter)

        got = await mail_explain(girl, uid)

        assert got['next'] != 'reply'

    async def test_ripe_reply_is_next(self, girl, reply_on, day_long):
        """Письмо без ответа — следующим будет ответ, его сочинит круг."""
        uid = f'ex-{uuid.uuid4().hex[:10]}'
        await _said_in(girl, uid, body='his letter')

        got = await mail_explain(girl, uid)

        assert got['next'] == 'reply'

    async def test_nothing_when_both_blocked(self, girl):
        """Оба занятия упёрлись — честное «нечего», а не выдуманное занятие."""
        got = await mail_explain(girl, f'ex-{uuid.uuid4().hex[:10]}')

        assert got['next'] == ''
        assert got['wait_sec'] == 0


# ── Сторож согласия ──────────────────────────────────────────────────────────
#
# ⚠⚠ **Отбор круга приставания знает не про все помехи окна.** `_quiet` — это
# один запрос по всем парам сайта, и в нём живут пять правил: звёздочка, анкета
# в сети, чьё последнее слово, черновик анкеты, запрет. Выключатель и смену
# спрашивает круг **уровнем выше**, а «он в сети», остуду и оба предела — цикл
# набора **после** запроса.
#
# Сверять надо ровно переписанное. Прочее в окне не переписано, а **взято теми
# же функциями** (`favorite_mail_person_soon`, `favorite_mail_girl_soon`,
# `online_show`, `mail_wait_left`) — разойтись им не с чем.
_QUIET_BLIND = (
    'набор писем избранным выключен',   # спрашивает круг выше отбора
    'сейчас не смена',                  # шлюз круга
    'его нет в сети',                   # цикл набора, после запроса
    'предел входящих',                  # остуда, цикл набора
    'за сутки уже ушло',                # дневной предел получателя, цикл
    'она писала',                       # пауза отправительницы, цикл
)


def _quiet_blockers(got: dict) -> list:
    """Помехи приставания, про которые отбор круга и правда знает."""
    return [one for one in got['favorite']['blockers']
            if not any(word in one for word in _QUIET_BLIND)]


class TestTheyAgree:
    """⚠⚠ Окно и круги отвечают одинаково — на одних и тех же данных.

    Сверяется **вердикт**, а не слова: у окна они человеческие и меняются, у
    отбора их нет вовсе. Общее у них одно — «берём пару или нет», и ровно это
    обязано совпадать.

    Без сторожа окно разойдётся с кругами на первой правке и начнёт объяснять
    занятие, которого нет. В проекте это случалось дважды — с `initiative_why`
    и со значком потолка.
    """

    async def test_ripe_favorite_pair_is_seen_by_both(self, girl_make, fav_on,
                                                      online_feed):
        """Пара созрела для приставания: круг берёт, окно не находит помех."""
        from src.dating.favorite_mail import _quiet

        girl = await girl_make(online=True)
        uid = f'ex-{uuid.uuid4().hex[:10]}'
        await _star(girl, uid)
        online_feed([uid])

        got = await mail_explain(girl, uid)
        mine = {str(one['uid']) for one in await _quiet(STAND_SITE, girl)}

        assert uid in mine, 'круг пару не взял — проверять согласие не на чем'
        assert _quiet_blockers(got) == [], \
            f'круг взял, а окно нашло помехи: {_quiet_blockers(got)}'

    async def test_blocked_favorite_pair_is_skipped_by_both(self, girl_make,
                                                            fav_on,
                                                            online_feed):
        """Запрет «не пишем»: круг пару не берёт, окно говорит почему."""
        from src.dating.favorite_mail import _quiet

        girl = await girl_make(online=True)
        uid = f'ex-{uuid.uuid4().hex[:10]}'
        await _star(girl, uid)
        await _block(girl, uid)
        online_feed([uid])

        got = await mail_explain(girl, uid)
        mine = {str(one['uid']) for one in await _quiet(STAND_SITE, girl)}

        assert uid not in mine, 'круг взял запрещённую пару — это другая беда'
        assert _quiet_blockers(got), 'круг пару не взял, а окно помех не нашло'

    async def test_he_spoke_last_is_skipped_by_both(self, girl_make, fav_on,
                                                    online_feed):
        """Последним говорил он: приставания нет ни у круга, ни у окна."""
        from src.dating.favorite_mail import _quiet

        girl = await girl_make(online=True)
        uid = f'ex-{uuid.uuid4().hex[:10]}'
        await _star(girl, uid)
        await _said_in(girl, uid, minutes_ago=5)
        online_feed([uid])

        got = await mail_explain(girl, uid)
        mine = {str(one['uid']) for one in await _quiet(STAND_SITE, girl)}

        assert uid not in mine
        assert _quiet_blockers(got)

    async def test_girl_draft_is_skipped_by_both(self, girl_make, fav_on,
                                                 online_feed):
        """Черновик анкеты занимает её очередь: не берут оба."""
        from src.dating.favorite_mail import _quiet

        girl = await girl_make(online=True)
        uid = f'ex-{uuid.uuid4().hex[:10]}'
        await _star(girl, uid)
        await _fav_draft(girl, f'other-{uuid.uuid4().hex[:8]}')
        online_feed([uid])

        got = await mail_explain(girl, uid)
        mine = {str(one['uid']) for one in await _quiet(STAND_SITE, girl)}

        assert uid not in mine
        assert _quiet_blockers(got)

    async def test_ripe_reply_is_seen_by_both(self, girl, reply_on):
        """Письмо без ответа: круг берёт его, окно не находит помех.

        ⚠ У ответов сверяется **весь** список: `_to_answer` знает и про
        выключатель, и про включённость анкеты — слепых правил тут нет.
        """
        from src.dating.mail_reply import _to_answer

        uid = f'ex-{uuid.uuid4().hex[:10]}'
        await _said_in(girl, uid, body='his letter')

        got = await mail_explain(girl, uid)
        mine = {str(one['uid']) for one in await _to_answer(STAND_SITE)}

        assert uid in mine, 'круг письмо не взял — проверять согласие не на чем'
        assert got['reply']['blockers'] == [], \
            f"круг взял, а окно нашло помехи: {got['reply']['blockers']}"

    async def test_answered_reply_is_skipped_by_both(self, girl, reply_on):
        """Ответ уже написан: круг письмо не берёт, окно говорит почему."""
        from src.dating.mail_reply import _to_answer

        uid = f'ex-{uuid.uuid4().hex[:10]}'
        letter = await _said_in(girl, uid, body='his letter')
        await _reply_draft(girl, uid, letter)

        got = await mail_explain(girl, uid)
        mine = {str(one['uid']) for one in await _to_answer(STAND_SITE)}

        assert uid not in mine
        assert got['reply']['blockers']

    async def test_empty_body_is_skipped_by_both(self, girl, reply_on):
        """Тело письма не забрали: не берут оба."""
        from src.dating.mail_reply import _to_answer

        uid = f'ex-{uuid.uuid4().hex[:10]}'
        await _said_in(girl, uid, body='')

        got = await mail_explain(girl, uid)
        mine = {str(one['uid']) for one in await _to_answer(STAND_SITE)}

        assert uid not in mine
        assert got['reply']['blockers']
