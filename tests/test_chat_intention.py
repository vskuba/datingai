"""Намерения разговора: что агент пообещал и исполнено ли это.

Исполнителя ещё нет — круга, который берёт открытые намерения и делает дело.
Поэтому здесь проверяется **дверь**: ляжет ли метка, не ляжет ли вторая, пока
первая открыта, отпустит ли ключ выполненная, и не отметят ли одну строку
дважды два исполнителя.
"""
import uuid

import pytest

from mysql_.mysql_ import mysql_get_db_async

from src.dating import answer as answer_module
from src.dating import chat_intention as intention_module
from src.dating.answer import _agent_ask
from src.dating.json_column import json_column_load
from src.dating.chat_intention import (chat_intention_do_allowed,
                                       chat_intention_done, chat_intention_pairs,
                                       chat_intention_put, chat_intention_rows)
from tests.conftest import TEST_STAND_SITE

pytestmark = pytest.mark.chat

PROMISE = 'she_promised_send_photo'


def _uid() -> str:
    """Свой собеседник каждому тесту: ключ таблицы — на пару, а не на анкету."""
    return f'ci-{uuid.uuid4().hex[:8]}'


async def _rows(profile_id: int) -> list:
    """Строки намерений этой анкеты, старые первыми."""
    async with mysql_get_db_async() as db:
        await db.execute(
            # ⚠ Колонки перечислены явно, и новую надо дописывать сюда же:
            # не выбрал запрос — тест читает `KeyError`, а не «поля нет».
            'SELECT id, uid, intention, context, status, done_at'
            '  FROM chat_intention WHERE profile_id = %s ORDER BY id',
            (int(profile_id),))

        return [dict(row) for row in (await db.fetchall() or [])]


async def _open(profile_id: int) -> list:
    """Открытые намерения анкеты — тем же чтением, что у вкладки.

    ⚠ Своей двери «дай открытые» у домена больше нет: она повторяла этот отбор
    и не имела ни одного вызывающего в бою.
    """
    got = await chat_intention_rows(TEST_STAND_SITE, profile_id=int(profile_id),
                                    status='open', per=1000)

    return got['rows']


async def _profile_row(profile_id: int) -> dict:
    """Настоящая строка `profile` — её и получает `_agent_ask` в бою."""
    async with mysql_get_db_async() as db:
        await db.execute('SELECT * FROM profile WHERE id = %s',
                         (int(profile_id),))

        return dict(await db.fetchone())


class TestThePut:
    """Запись намерения."""

    async def test_it_lands(self, girl):
        uid = _uid()

        assert await chat_intention_put(girl, TEST_STAND_SITE, uid,
                                        {PROMISE: {}}) == 1

        rows = await _rows(girl)
        assert len(rows) == 1
        assert rows[0]['intention'] == PROMISE
        assert rows[0]['status'] == 'open'
        assert rows[0]['done_at'] is None

    async def test_twice_while_open_does_not_land(self, girl):
        """⚠ Одна открытая на пару и метку: повтор новой строки не заводит."""
        uid = _uid()
        await chat_intention_put(girl, TEST_STAND_SITE, uid, {PROMISE: {}})

        assert await chat_intention_put(girl, TEST_STAND_SITE, uid,
                                        {PROMISE: {}}) == 0
        assert len(await _rows(girl)) == 1

    async def test_the_same_label_twice_in_one_batch_lands_once(self, girl):
        """Повторы внутри пачки схлопывает ключ базы, а не разбор."""
        uid = _uid()

        assert await chat_intention_put(girl, TEST_STAND_SITE, uid,
                                        {PROMISE: {}, PROMISE: {}}) == 1

    async def test_another_label_lands_beside_it(self, girl):
        """Ключ — на метку: другое обещание той же паре законно."""
        uid = _uid()
        await chat_intention_put(girl, TEST_STAND_SITE, uid, {PROMISE: {}})

        assert await chat_intention_put(girl, TEST_STAND_SITE, uid,
                                        {'he_asked_not_to_write': {}}) == 1
        assert len(await _rows(girl)) == 2

    async def test_another_companion_lands_beside_it(self, girl):
        """Ключ — на пару: та же метка другому человеку законна."""
        await chat_intention_put(girl, TEST_STAND_SITE, _uid(), {PROMISE: {}})

        assert await chat_intention_put(girl, TEST_STAND_SITE, _uid(),
                                        {PROMISE: {}}) == 1
        assert len(await _rows(girl)) == 2


class TestWhatIsNotWritten:
    """Чего в таблицу не попадает."""

    async def test_a_long_label_is_dropped(self, girl):
        """⚠ Длиннее 100 знаков — пересказ разговора, а не метка."""
        assert await chat_intention_put(girl, TEST_STAND_SITE, _uid(),
                                        ['x' * 101]) == 0
        assert await _rows(girl) == []

    async def test_a_label_of_exactly_the_limit_lands(self, girl):
        """Граница включительная: сто знаков — ещё метка."""
        assert await chat_intention_put(girl, TEST_STAND_SITE, _uid(),
                                        {'x' * 100: {}}) == 1

    async def test_blank_labels_are_dropped(self, girl):
        assert await chat_intention_put(girl, TEST_STAND_SITE, _uid(),
                                        {'': {}, '   ': {}}) == 0
        assert await _rows(girl) == []

    async def test_an_empty_list_writes_nothing(self, girl):
        assert await chat_intention_put(girl, TEST_STAND_SITE, _uid(), {}) == 0

    async def test_without_a_companion_writes_nothing(self, girl):
        """⚠ Пустой `uid` — намерению не к кому принадлежать."""
        assert await chat_intention_put(girl, TEST_STAND_SITE, '',
                                        {PROMISE: {}}) == 0
        assert await _rows(girl) == []

    async def test_without_a_profile_writes_nothing(self, girl):
        assert await chat_intention_put(0, TEST_STAND_SITE, _uid(),
                                        {PROMISE: {}}) == 0


class TestTheDoneMark:
    """Отметка «исполнено»."""

    async def test_it_lands(self, girl):
        await chat_intention_put(girl, TEST_STAND_SITE, _uid(), {PROMISE: {}})
        one = (await _open(girl))[0]

        assert await chat_intention_done(one['id']) is True

        rows = await _rows(girl)
        assert rows[0]['status'] == 'done'
        assert rows[0]['done_at'] is not None

    async def test_the_second_mark_is_not_ours(self, girl):
        """⚠ Условный UPDATE: два исполнителя не отметят одну строку дважды."""
        await chat_intention_put(girl, TEST_STAND_SITE, _uid(), {PROMISE: {}})
        one = (await _open(girl))[0]
        await chat_intention_done(one['id'])

        assert await chat_intention_done(one['id']) is False

    async def test_a_done_one_releases_the_key(self, girl):
        """⚠⚠ Второе обещание фото законно — но только после первого."""
        uid = _uid()
        await chat_intention_put(girl, TEST_STAND_SITE, uid, {PROMISE: {}})
        one = (await _open(girl))[0]
        await chat_intention_done(one['id'])

        assert await chat_intention_put(girl, TEST_STAND_SITE, uid,
                                        {PROMISE: {}}) == 1
        assert [row['status'] for row in await _rows(girl)] == ['done', 'open']

    async def test_a_missing_row_is_not_ours(self):
        assert await chat_intention_done(0) is False


class TestTheWritingPoint:
    """`_agent_ask` — одна точка записи на чат, инициативу и оба письма."""

    async def test_it_writes_what_the_run_returned(self, girl, monkeypatch):
        uid = _uid()

        async def _talk(*args, **kwargs):
            return {'text': 'ок', 'message_id': 7, 'intentions': {PROMISE: {}}}

        monkeypatch.setattr(answer_module, 'rhapsody_api_chat_talk', _talk)

        text, error, message_id = await _agent_ask(
            1, 'вопрос', 'en', await _profile_row(girl), uid=uid, name='Bob')

        assert (text, error, message_id) == ('ок', '', 7)
        assert [one['intention'] for one
                in await _open(girl)] == [PROMISE]

    async def test_an_empty_list_writes_nothing(self, girl, monkeypatch):
        async def _talk(*args, **kwargs):
            return {'text': 'ок', 'message_id': 7, 'intentions': {}}

        monkeypatch.setattr(answer_module, 'rhapsody_api_chat_talk', _talk)

        await _agent_ask(1, 'вопрос', 'en', await _profile_row(girl),
                         uid=_uid(), name='Bob')

        assert await _rows(girl) == []

    async def test_a_broken_write_does_not_break_the_answer(self, girl,
                                                            monkeypatch):
        """⚠⚠ Реплика уже сочинена и сейчас уйдёт человеку: уронить её из-за
        ненаписавшейся отметки значит отменить дорогую работу ради дешёвой."""
        async def _talk(*args, **kwargs):
            return {'text': 'ок', 'message_id': 7, 'intentions': {PROMISE: {}}}

        async def _boom(*args, **kwargs):
            raise RuntimeError('база легла')

        monkeypatch.setattr(answer_module, 'rhapsody_api_chat_talk', _talk)
        monkeypatch.setattr(answer_module, 'chat_intention_put', _boom)

        text, error, message_id = await _agent_ask(
            1, 'вопрос', 'en', await _profile_row(girl), uid=_uid(),
            name='Bob')

        assert (text, error, message_id) == ('ок', '', 7)


async def _note(intention_id: int) -> str:
    """Заметка об исполнении у этой строки."""
    async with mysql_get_db_async() as db:
        await db.execute('SELECT done_note FROM chat_intention WHERE id = %s',
                         (int(intention_id),))

        return str(dict(await db.fetchone())['done_note'])


class TestTheDoneNote:
    """Чем намерение исполнили — словами, в той же строке."""

    async def test_the_note_lands(self, girl):
        await chat_intention_put(girl, TEST_STAND_SITE, _uid(), {PROMISE: {}})
        one = (await _open(girl))[0]

        assert await chat_intention_done(
            one['id'], note='отправлено фото public/a.png') is True
        assert await _note(one['id']) == 'отправлено фото public/a.png'

    async def test_without_a_note_the_old_contract_holds(self, girl):
        """⚠ Довод необязательный: прежние вызовы и их тесты целы."""
        await chat_intention_put(girl, TEST_STAND_SITE, _uid(), {PROMISE: {}})
        one = (await _open(girl))[0]

        assert await chat_intention_done(one['id']) is True
        assert await _note(one['id']) == ''

    async def test_a_long_note_is_cut_to_the_column(self, girl):
        """⚠ Пятьсот знаков — предел столбца. Обрезаем **мы**, а не база: в
        строгом режиме MySQL такая вставка падает ошибкой, и закрытие намерения
        сорвалось бы из-за длинного текста исполнителя."""
        await chat_intention_put(girl, TEST_STAND_SITE, _uid(), {PROMISE: {}})
        one = (await _open(girl))[0]

        assert await chat_intention_done(one['id'], note='x' * 600) is True
        assert len(await _note(one['id'])) == 500

    async def test_the_second_mark_leaves_the_first_note(self, girl):
        """Закрыли дважды — вторая отметка не наша, и заметку она не трогает."""
        await chat_intention_put(girl, TEST_STAND_SITE, _uid(), {PROMISE: {}})
        one = (await _open(girl))[0]
        await chat_intention_done(one['id'], note='первое')

        assert await chat_intention_done(one['id'], note='второе') is False
        assert await _note(one['id']) == 'первое'


async def _companion(uid: str, name: str, photo: str = '') -> None:
    """Карточка собеседника на стендовом сайте: её заводят по требованию."""
    async with mysql_get_db_async() as db:
        await db.execute(
            'INSERT INTO companion (site_id, uid, name, photo)'
            ' VALUES (%s, %s, %s, %s)',
            (TEST_STAND_SITE, uid, name, photo))
        await db.connection.commit()


class TestTheRows:
    """Строки для показа: с именами, страницами и счётом."""

    async def test_it_gives_the_row_with_names(self, girl):
        uid = _uid()
        await _companion(uid, 'Bruce', 'http://x/b.jpg')
        await chat_intention_put(girl, TEST_STAND_SITE, uid, {PROMISE: {}})

        got = await chat_intention_rows(TEST_STAND_SITE, profile_id=girl)

        assert got['total'] == 1
        assert got['rows'][0]['name'] == 'Bruce'
        assert got['rows'][0]['photo'] == 'http://x/b.jpg'
        assert got['rows'][0]['intention'] == PROMISE

    async def test_a_companion_without_a_card_is_not_empty(self, girl):
        """⚠ Карточка заводится по требованию — её может не быть. Тогда в строке
        остаётся номер, а не пустое место: по пустому собеседника не узнать."""
        uid = _uid()
        await chat_intention_put(girl, TEST_STAND_SITE, uid, {PROMISE: {}})

        got = await chat_intention_rows(TEST_STAND_SITE, profile_id=girl)

        assert got['rows'][0]['name'] == uid

    async def test_it_filters_by_status(self, girl):
        uid = _uid()
        await chat_intention_put(girl, TEST_STAND_SITE, uid,
                                 {PROMISE: {}, 'he_asked_not_to_write': {}})
        one = (await _open(girl))[0]
        await chat_intention_done(one['id'], note='сделано')

        done = await chat_intention_rows(TEST_STAND_SITE, profile_id=girl,
                                         status='done')
        waiting = await chat_intention_rows(TEST_STAND_SITE, profile_id=girl,
                                            status='open')

        assert [row['done_note'] for row in done['rows']] == ['сделано']
        assert done['total'] == 1 and waiting['total'] == 1

    async def test_it_filters_by_companion(self, girl):
        mine = _uid()
        await chat_intention_put(girl, TEST_STAND_SITE, mine, {PROMISE: {}})
        await chat_intention_put(girl, TEST_STAND_SITE, _uid(), {PROMISE: {}})

        got = await chat_intention_rows(TEST_STAND_SITE, profile_id=girl,
                                        uid=mine)

        assert got['total'] == 1
        assert got['rows'][0]['uid'] == mine

    async def test_a_profile_of_zero_means_all_of_them(self, girl, girl_make):
        """⚠ Анкета — фильтр, а не условие показа. Общее правило раздела."""
        other = await girl_make()
        await chat_intention_put(girl, TEST_STAND_SITE, _uid(), {PROMISE: {}})
        await chat_intention_put(other, TEST_STAND_SITE, _uid(), {PROMISE: {}})

        got = await chat_intention_rows(TEST_STAND_SITE, per=1000)

        assert {row['profile_id'] for row in got['rows']} >= {girl, other}

    async def test_pages_are_counted_by_the_server(self, girl):
        uid = _uid()
        await chat_intention_put(girl, TEST_STAND_SITE, uid,
                                 {'a_one': {}, 'a_two': {}, 'a_three': {}})

        got = await chat_intention_rows(TEST_STAND_SITE, profile_id=girl, per=2)

        assert got['total'] == 3
        assert got['pages'] == 2
        assert len(got['rows']) == 2

    async def test_a_page_past_the_end_is_fixed_not_refused(self, girl):
        """⚠ Строку могли закрыть с последней страницы: отказ вместо предыдущей
        читался бы как поломка. То же правило, что у журнала доставки."""
        await chat_intention_put(girl, TEST_STAND_SITE, _uid(), {PROMISE: {}})

        got = await chat_intention_rows(TEST_STAND_SITE, profile_id=girl,
                                        page=99)

        assert got['page'] == 1
        assert len(got['rows']) == 1

    async def test_without_a_site_it_gives_nothing(self):
        got = await chat_intention_rows(0)

        assert got == {'rows': [], 'page': 1, 'pages': 1, 'total': 0}

    async def test_dates_come_as_strings(self, girl):
        """Страница сдвигает время сама (`time.js`), и ей нужна строка, а не
        объект, который JSON не переживёт."""
        await chat_intention_put(girl, TEST_STAND_SITE, _uid(), {PROMISE: {}})

        got = await chat_intention_rows(TEST_STAND_SITE, profile_id=girl)

        assert isinstance(got['rows'][0]['created_at'], str)
        assert got['rows'][0]['created_at']
        assert got['rows'][0]['done_at'] == ''


class TestThePairs:
    """Колонка пар: по строке на пару, со счётчиками."""

    async def test_one_row_per_pair_with_counts(self, girl):
        uid = _uid()
        await chat_intention_put(girl, TEST_STAND_SITE, uid,
                                 {PROMISE: {}, 'he_asked_not_to_write': {}})
        one = (await _open(girl))[0]
        await chat_intention_done(one['id'], note='сделано')

        got = await chat_intention_pairs(TEST_STAND_SITE, profile_id=girl)

        assert got['total'] == 1
        assert got['rows'][0]['uid'] == uid
        assert got['rows'][0]['open_count'] == 1
        assert got['rows'][0]['done_count'] == 1

    async def test_counts_are_numbers_not_decimals(self, girl):
        """⚠ `SUM()` в MySQL отдаёт `Decimal`, а JSON его не переживёт: ручка
        упала бы на сериализации, а не на запросе."""
        await chat_intention_put(girl, TEST_STAND_SITE, _uid(), {PROMISE: {}})

        got = await chat_intention_pairs(TEST_STAND_SITE, profile_id=girl)

        assert isinstance(got['rows'][0]['open_count'], int)
        assert isinstance(got['rows'][0]['done_count'], int)

    async def test_two_companions_are_two_rows(self, girl):
        await chat_intention_put(girl, TEST_STAND_SITE, _uid(), {PROMISE: {}})
        await chat_intention_put(girl, TEST_STAND_SITE, _uid(), {PROMISE: {}})

        got = await chat_intention_pairs(TEST_STAND_SITE, profile_id=girl)

        assert got['total'] == 2

    async def test_a_pair_without_intentions_is_not_listed(self, girl):
        """Вкладка отвечает на «что агент наобещал». Пары без намерений — шум."""
        got = await chat_intention_pairs(TEST_STAND_SITE, profile_id=girl)

        assert got['rows'] == []
        assert got['total'] == 0

    async def test_a_profile_of_zero_means_all_of_them(self, girl, girl_make):
        other = await girl_make()
        await chat_intention_put(girl, TEST_STAND_SITE, _uid(), {PROMISE: {}})
        await chat_intention_put(other, TEST_STAND_SITE, _uid(), {PROMISE: {}})

        got = await chat_intention_pairs(TEST_STAND_SITE, per=1000)

        assert {row['profile_id'] for row in got['rows']} >= {girl, other}

    async def test_the_companion_name_comes_through(self, girl):
        uid = _uid()
        await _companion(uid, 'Mike')
        await chat_intention_put(girl, TEST_STAND_SITE, uid, {PROMISE: {}})

        got = await chat_intention_pairs(TEST_STAND_SITE, profile_id=girl)

        assert got['rows'][0]['name'] == 'Mike'

    async def test_pages_are_counted_by_the_server(self, girl):
        for _ in range(3):
            await chat_intention_put(girl, TEST_STAND_SITE, _uid(), {PROMISE: {}})

        got = await chat_intention_pairs(TEST_STAND_SITE, profile_id=girl, per=2)

        assert got['total'] == 3
        assert got['pages'] == 2
        assert len(got['rows']) == 2

    async def test_without_a_site_it_gives_nothing(self):
        got = await chat_intention_pairs(0)

        assert got == {'rows': [], 'page': 1, 'pages': 1, 'total': 0}


async def _do_flag(site_id: int, on: bool) -> None:
    """Поднять или снять выключатель сайта напрямую — минуя ручку."""
    async with mysql_get_db_async() as db:
        await db.execute(
            'INSERT INTO site (site_id, chat_intention_do_on) VALUES (%s, %s)'
            ' ON DUPLICATE KEY UPDATE chat_intention_do_on = VALUES(chat_intention_do_on)',
            (int(site_id), 1 if on else 0))
        await db.connection.commit()


class TestTheDoFlag:
    """Разрешение исполнять намерения — дверь для будущего исполнителя."""

    async def test_it_is_off_by_default(self):
        """⚠ Умолчание снято: поднятая галочка у работы, которой нет, — ложь."""
        await _do_flag(TEST_STAND_SITE, False)

        assert await chat_intention_do_allowed(TEST_STAND_SITE) is False

    async def test_it_is_on_when_raised(self):
        await _do_flag(TEST_STAND_SITE, True)
        try:
            assert await chat_intention_do_allowed(TEST_STAND_SITE) is True
        finally:
            await _do_flag(TEST_STAND_SITE, False)

    async def test_a_site_without_a_row_is_not_allowed(self):
        """Строки сайта может не быть вовсе — это обычное состояние, не поломка."""
        assert await chat_intention_do_allowed(999_111) is False

    async def test_a_zero_site_is_not_allowed(self):
        assert await chat_intention_do_allowed(0) is False

    async def test_a_broken_read_is_not_allowed(self, monkeypatch):
        """⚠⚠ Сбой чтения даёт `False`, а не исключение и не `True`.

        Исполнение уходит живому человеку, и «на всякий случай сделал» отменить
        нельзя. Тем же правилом живёт `chat_answer_on` в `auto.py`.
        """
        def _boom(*args, **kwargs):
            raise RuntimeError('база легла')

        monkeypatch.setattr(intention_module, 'mysql_get_db_async', _boom)

        assert await chat_intention_do_allowed(TEST_STAND_SITE) is False


class TestTheContext:
    """Контекст намерения: с чем его исполнять.

    ## ⚠⚠ Что здесь стережётся

    Контекст собирает **тот же прогон**, что нашёл намерение, — пока история
    разговора под рукой. Потеряйся он при записи, исполнителю пришлось бы
    добывать текст письма заново: по пересказу вместо первоисточника и уже без
    разговора. Беда тихая — намерение на месте, исполнять просто нечем.
    """

    async def test_the_context_lands_with_the_label(self, girl):
        uid = _uid()
        await chat_intention_put(girl, TEST_STAND_SITE, uid,
                                 {PROMISE: {'letter': 'Hi!', 'tags': ['dress']}})

        rows = await _rows(girl)

        assert len(rows) == 1
        got = json_column_load(rows[0]['context'])
        assert got['letter'] == 'Hi!'
        assert got['tags'] == ['dress']

    async def test_a_label_without_context_is_still_written(self, girl):
        """⚠ Метка важнее контекста: помечено — записано, даже если собрать
        контекст было нечем."""
        await chat_intention_put(girl, TEST_STAND_SITE, _uid(), {PROMISE: {}})

        rows = await _rows(girl)

        assert len(rows) == 1
        assert rows[0]['context'] is None, 'пустой контекст записан как объект'

    async def test_a_fresher_context_wins(self, girl):
        """⚠⚠ Повтор метки ключ отвергает, но контекст догоняет.

        Свежий собран по более полной истории — он точнее. Остановись мы на
        `INSERT IGNORE`, у открытой строки навсегда остался бы контекст первого
        прогона.
        """
        uid = _uid()
        await chat_intention_put(girl, TEST_STAND_SITE, uid, {PROMISE: {'letter': 'первый'}})
        await chat_intention_put(girl, TEST_STAND_SITE, uid, {PROMISE: {'letter': 'второй'}})

        rows = await _rows(girl)

        assert len(rows) == 1, 'метка задвоилась — ключ перестал держать'
        assert json_column_load(rows[0]['context'])['letter'] == 'второй'

    async def test_an_empty_context_does_not_erase_the_old_one(self, girl):
        """⚠⚠ Прогон, пометивший намерение без контекста, не обнуляет работу
        прошлого: иначе один «пустой» ответ агента стирал бы собранное письмо.
        """
        uid = _uid()
        await chat_intention_put(girl, TEST_STAND_SITE, uid, {PROMISE: {'letter': 'цел'}})
        await chat_intention_put(girl, TEST_STAND_SITE, uid, {PROMISE: {}})

        rows = await _rows(girl)

        assert json_column_load(rows[0]['context'])['letter'] == 'цел'

    async def test_a_stranger_context_does_not_lose_the_label(self, girl):
        """⚠ Контекст не той формы становится пустым, а метка остаётся.

        Форму кладёт сосед, и сменить её он может раньше, чем мы узнаем;
        терять из-за этого само намерение нельзя.
        """
        for чужое in ('строка', 42, ['список']):
            await chat_intention_put(girl, TEST_STAND_SITE, _uid(), {PROMISE: чужое})

        assert len(await _rows(girl)) == 3
