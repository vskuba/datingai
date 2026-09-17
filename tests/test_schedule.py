"""Расписание анкеты: когда ей быть в сети.

⚠ Прежде это был **только вид**: смены жили в памяти страницы до перезагрузки, ни
таблицы, ни ручки. В коде страницы так и стояло — «чтобы никто не решил, будто
расписание уже работает». Теперь оно хранится, и правила надо держать.

Разбор и проверка — без базы: это чистые функции. Хранение — по-настоящему, в живую
базу со своей одноразовой анкетой.
"""

import uuid

import pytest
from fastapi import HTTPException

# ⚠ `_hhmm` и `_minutes` приватные, и берём их намеренно: разбор времени — то
# самое место, где живёт `24:00`, и проверять его через ручку значило бы проверять
# заодно права, базу и HTTP.
from src.dating.schedule import (_hhmm, _minutes,
                                        SCHEDULE_DEFAULT, SCHEDULE_MAX,
                                        schedule_check, schedule_dump,
                                        schedule_load, schedule_now,
                                        schedule_profile_now,
                                        schedule_why_idle)
from src.uvicorn.routers.dating.profile_ import (DatingProfileSave,
                                                       profile_create,
                                                       profile_delete,
                                                       profile_row)
from src.uvicorn.routers.dating.profile_action import (DatingProfileSchedule,
                                                              profile_action_schedule)
from src.uvicorn.routers.dating.profile_view import profile_view_card

pytestmark = pytest.mark.profile
SITE = 343
THREE = [{'from': '07:00', 'to': '11:00'},
         {'from': '14:00', 'to': '18:00'},
         {'from': '19:00', 'to': '23:00'}]


class TestLoad:
    """Разбор столбца: терпим к виду, строг к смыслу."""

    def test_reads_json_string_and_list(self):
        """Столбец приходит то строкой, то списком — MySQL отдаёт JSON по-разному
        в зависимости от драйвера и версии."""
        assert schedule_load('[{"from": "7:0", "to": "11:00"}]') == \
            [{'from': '07:00', 'to': '11:00'}]
        assert schedule_load([{'from': '07:00', 'to': '11:00'}]) == \
            [{'from': '07:00', 'to': '11:00'}]

    @pytest.mark.parametrize('value', [None, '', '[]', 'не json', '{}', 42,
                                       [{'from': '07:00'}], [{'to': '11:00'}],
                                       [{'from': '25:00', 'to': '26:00'}], ['мусор']])
    def test_junk_is_empty(self, value):
        """⚠ Мусор — пустое расписание, а не исключение: столбец правят и руками, и
        миграцией, и сорванный на этом показ карточки хуже пустого блока."""
        assert schedule_load(value) == []

    def test_rows_are_sorted_by_time(self):
        """Смены правят ползунком, и заведённая последней может оказаться первой по
        часам. Порядок нужен и странице, и проверке пересечений."""
        rows = schedule_load([{'from': '19:00', 'to': '23:00'},
                                     {'from': '07:00', 'to': '11:00'}])

        assert [row['from'] for row in rows] == ['07:00', '19:00']

    def test_extra_shifts_are_cut(self):
        many = [{'from': f'{h:02d}:00', 'to': f'{h:02d}:30'} for h in range(10)]

        assert len(schedule_load(many)) == SCHEDULE_MAX

    def test_empty_is_not_replaced_by_default(self):
        """⚠ Пустое — законное состояние («ведут руками»), а не повод подставить
        умолчание.

        Подставь его здесь — и человек, снявший все смены, получал бы их обратно на
        каждом показе страницы.
        """
        assert schedule_load(None) == []
        assert schedule_load(None) != SCHEDULE_DEFAULT


class TestCheck:
    """Что считается годным расписанием."""

    def test_default_is_valid(self):
        assert schedule_check(SCHEDULE_DEFAULT) == ''

    def test_empty_is_valid(self):
        """Пустое годно: это «ведём руками», а не ошибка ввода."""
        assert schedule_check([]) == ''

    def test_end_before_start_is_refused(self):
        why = schedule_check([{'from': '18:00', 'to': '09:00'}])

        assert 'позже' in why

    def test_overlap_is_refused(self):
        """⚠ Смены внахлёст — это вход поверх входа: анкета получит вторую сессию,
        не закрыв первую."""
        why = schedule_check([{'from': '09:00', 'to': '12:00'},
                                     {'from': '11:00', 'to': '14:00'}])

        assert 'налезают' in why

    def test_touching_shifts_are_fine(self):
        """Стык минута в минуту — не пересечение: одна кончилась, другая началась."""
        assert schedule_check([{'from': '09:00', 'to': '12:00'},
                                      {'from': '12:00', 'to': '15:00'}]) == ''

    def test_missing_time_is_refused(self):
        assert schedule_check([{'from': '', 'to': '12:00'}]) != ''

    def test_too_many_is_refused(self):
        many = [{'from': f'{h:02d}:00', 'to': f'{h:02d}:30'} for h in range(10)]

        assert str(SCHEDULE_MAX) in schedule_check(many)

    def test_reason_is_for_a_human(self):
        """Причина уезжает на страницу: она говорит, что поправить, а не какое
        правило нарушено."""
        why = schedule_check([{'from': '18:00', 'to': '09:00'}])

        assert '18:00' in why and '09:00' in why


class TestMidnight:
    """⚠ Конец суток — это `24:00`, и только он.

    Ползунок ходит до 1440 минут, и его правый край обязан во что-то
    превращаться. Свёрнутый по модулю суток, он давал `00:00` — начало тех же
    суток: смена «19:00 → 00:00» становилась отрицательной длины, уезжала в начало
    ленты и пропадала с экрана. Человек это и увидел: «ползунок доходит до конца,
    исчезает и появляется в начале».
    """

    def test_midnight_is_accepted_as_the_end(self):
        assert schedule_check([{'from': '19:00', 'to': '24:00'}]) == ''

    def test_midnight_is_the_last_minute_of_the_day(self):
        assert _minutes(_hhmm('24:00')) == 24 * 60

    def test_midnight_as_a_start_is_refused(self):
        """Началом `24:00` быть не может: позже полуночи в сутках ничего нет.

        Отдельного правила для этого не нужно — смена отклоняется на общих
        основаниях, потому что конец обязан быть позже начала.
        """
        why = schedule_check([{'from': '24:00', 'to': '24:00'}])

        assert 'позже начала' in why

    @pytest.mark.parametrize('moment', ['24:15', '24:01', '25:00', '30:00'])
    def test_beyond_midnight_is_refused(self, moment):
        """⚠ Послабление ровно одно. `24:15` — это уже не «конец суток», а мусор,
        и принять его значило бы завести смену, которой некуда деться."""
        assert _hhmm(moment) == ''


class TestNow:
    """Должна ли анкета быть в сети в эту минуту."""

    @pytest.mark.parametrize('hour, online', [
        (6, False),    # до первой смены
        (7, True),     # начало ровно
        (10, True),
        (11, False),   # конец не включаем: смена «до 11:00»
        (13, False),   # перерыв
        (15, True),
        (22, True),
        (23, False),   # ночь
        (3, False),
    ])
    def test_default_schedule(self, hour, online):
        assert schedule_now(SCHEDULE_DEFAULT, hour * 60) is online

    def test_midnight_end_covers_the_last_minute(self):
        """⚠ Смена «до 24:00» действует до последней минуты суток.

        Полночь — законный конец смены, и выразить его иначе нечем: `00:00`
        означает начало тех же суток, и смена схлопнулась бы в ноль длины.
        """
        all_day = [{'from': '19:00', 'to': '24:00'}]

        assert schedule_now(all_day, 19 * 60) is True
        assert schedule_now(all_day, 23 * 60 + 59) is True
        assert schedule_now(all_day, 18 * 60 + 59) is False

    def test_empty_means_not_online(self):
        """⚠ Пустое расписание — `False`, а не «всегда в сети».

        «Ведут руками» значит, что сама она не входит. Обратное толкование включило
        бы круглосуточное присутствие у всех, кто расписание не заводил.
        """
        assert schedule_now([], 12 * 60) is False


class TestOnlyForEnabled:
    """⚠ Расписание действует только у включённой анкеты.

    Выключенную не ведут вовсе: она не отвечает и ничего не отправляет. Входить по
    расписанию ей тем более незачем — вход это действие наружу, самое заметное из
    всех.
    """

    def test_disabled_is_never_online(self):
        """Смены на месте, но анкета выключена — значит не входит."""
        profile = {'enabled': 0, 'schedule': SCHEDULE_DEFAULT}

        assert schedule_profile_now(profile, 9 * 60) is False

    def test_enabled_follows_the_schedule(self):
        """Обратная сторона: включённая живёт по сменам.

        Без этой проверки запрет мог бы оказаться безусловным, и расписание не
        работало бы ни у кого.
        """
        profile = {'enabled': 1, 'schedule': SCHEDULE_DEFAULT}

        assert schedule_profile_now(profile, 9 * 60) is True
        assert schedule_profile_now(profile, 13 * 60) is False

    def test_missing_flag_means_enabled(self):
        """Строка без `enabled` читается как «ведём» — то же умолчание, что везде:
        колонка появилась при работающих анкетах."""
        assert schedule_profile_now(
            {'schedule': SCHEDULE_DEFAULT}, 9 * 60) is True

    @pytest.mark.parametrize('profile, part', [
        ({'enabled': 0, 'schedule': SCHEDULE_DEFAULT}, 'выключена'),
        ({'enabled': 1, 'schedule': []}, 'руками'),
        ({'enabled': 1, 'schedule': SCHEDULE_DEFAULT}, ''),
    ])
    def test_reason_explains_itself(self, profile, part):
        """Почему не действует — словами: блок расписания у выключенной выглядит
        рабочим, и человек ждёт, что анкета войдёт в семь утра."""
        why = schedule_why_idle(profile)

        assert (part in why) if part else (why == '')


class TestStorage:
    """Хранение — в живую базу, со своей анкетой."""

    @pytest.fixture
    async def profile(self, rhapsody):
        """⚠ Своя анкета: чужую трогать нельзя. Фикстура `rhapsody` нужна потому,
        что заведение сверяет сайт у соседа."""
        answer = await profile_create(
            DatingProfileSave(site_id=SITE, login=f'sched-{uuid.uuid4().hex[:8]}',
                              password='x'), user=None)
        profile_id = int(answer['result'])

        yield profile_id

        try:
            await profile_delete(profile_id, user=None)
        except Exception:
            pass

    async def test_saved_schedule_comes_back(self, profile):
        await profile_action_schedule(
            profile, DatingProfileSchedule(schedule=THREE), user=None)

        card = (await profile_view_card(profile, user=None))['result']

        assert card['schedule'] == THREE

    async def test_save_normalises_time(self, profile):
        """`9:0` → `09:00`: одна забытая ведущая ноль ломала бы сравнение строк, на
        котором держится порядок."""
        await profile_action_schedule(
            profile, DatingProfileSchedule(schedule=[{'from': '9:0', 'to': '12:5'}]),
            user=None)

        card = (await profile_view_card(profile, user=None))['result']

        assert card['schedule'] == [{'from': '09:00', 'to': '12:05'}]

    async def test_overlap_is_refused_by_the_endpoint(self, profile):
        """⚠ Негодное отклоняется, а не правится молча: сказать человеку полезнее,
        чем тихо сдвинуть его время."""
        with pytest.raises(HTTPException) as refusal:
            await profile_action_schedule(
                profile,
                DatingProfileSchedule(schedule=[{'from': '09:00', 'to': '12:00'},
                                                {'from': '11:00', 'to': '14:00'}]),
                user=None)

        assert refusal.value.status_code == 422
        assert 'налезают' in str(refusal.value.detail)

    async def test_schedule_can_be_cleared(self, profile):
        """Снять расписание можно: это «ведём руками», законное состояние."""
        await profile_action_schedule(
            profile, DatingProfileSchedule(schedule=THREE), user=None)
        await profile_action_schedule(
            profile, DatingProfileSchedule(schedule=[]), user=None)

        card = (await profile_view_card(profile, user=None))['result']

        assert card['schedule'] == []

    async def test_new_profile_gets_the_default(self, profile):
        """⚠ Заведённая анкета получает три смены, а не пустоту.

        Анкету заводят, чтобы она работала: пустое расписание при заведении означало
        бы, что каждую надо настраивать руками, прежде чем она войдёт хоть раз.
        """
        row = await profile_row(profile)

        assert schedule_load(row['schedule']) == SCHEDULE_DEFAULT

    async def test_dump_is_readable_back(self, profile):
        """То, что легло в столбец, читается обратно тем же."""
        assert schedule_load(schedule_dump(THREE)) == THREE


class TestSwitchingOffStopsTheWatch:
    """⚠ Снятая галочка расписания снимает и присмотр автомата.

    Поймано живьём: галочку сняли в 16:33, сессия стёрлась («Расписание выключено,
    сокет снят»), а через минуту круг восстановления вошёл заново — «Вхожу заново
    (попытка 1)», «Вход восстановлен», — и анкета снова оказалась в сети. Он берёт
    всех с `keep_online = 1`, а анкету **без расписания** считает дежурящей
    круглосуточно (`revive_db._on_duty`).

    То есть две наши же части дрались: одна гасила, другая поднимала. Смысл
    галочки — «перестань водить её сама», и присмотр, переживший снятие, отменял
    это решение молча.
    """

    @pytest.fixture
    async def kept(self, rhapsody):
        """Анкета под присмотром автомата — как после удачного ручного входа."""
        from mysql_.mysql_ import mysql_get_db_async

        answer = await profile_create(
            DatingProfileSave(site_id=SITE, login=f'keep-{uuid.uuid4().hex[:8]}',
                              password='x'), user=None)
        profile_id = int(answer['result'])

        async with mysql_get_db_async() as db:
            await db.execute("UPDATE profile SET keep_online = 1,"
                             "       schedule_on = 1, status = 'online'"
                             ' WHERE id = %s', (profile_id,))
            await db.connection.commit()

        yield profile_id

        try:
            await profile_delete(profile_id, user=None)
        except Exception:
            pass

    async def test_switching_off_clears_the_watch(self, kept):
        from mysql_.mysql_ import mysql_get_db_async
        from src.uvicorn.routers.dating.profile_action import (
            DatingProfileScheduleOn, profile_action_schedule_on)

        await profile_action_schedule_on(
            kept, DatingProfileScheduleOn(schedule_on=False), user=None)

        async with mysql_get_db_async() as db:
            await db.execute('SELECT keep_online FROM profile WHERE id = %s',
                             (kept,))
            row = await db.fetchone()

        assert not row['keep_online'], (
            'присмотр пережил снятие расписания — круг восстановления вернёт '
            'анкету в сеть через минуту'
        )

    async def test_switching_on_does_not_touch_the_watch(self, kept):
        """⚠ Включение галочки присмотра **не трогает**, и это не симметрия ради неё.

        Поднять `keep_online` здесь значило бы объявить вход существующим там, где
        его нет: ставит этот флаг только удачный вход (`login.py`). Круг
        расписания сам войдёт, когда начнётся смена.
        """
        from mysql_.mysql_ import mysql_get_db_async
        from src.uvicorn.routers.dating.profile_action import (
            DatingProfileScheduleOn, profile_action_schedule_on)

        await profile_action_schedule_on(
            kept, DatingProfileScheduleOn(schedule_on=True), user=None)

        async with mysql_get_db_async() as db:
            await db.execute('SELECT keep_online FROM profile WHERE id = %s',
                             (kept,))
            row = await db.fetchone()

        assert row['keep_online'], 'включение расписания сняло присмотр'
