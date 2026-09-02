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

from src.dating.dating_schedule import (DATING_SCHEDULE_DEFAULT, DATING_SCHEDULE_MAX,
                                        dating_schedule_check, dating_schedule_dump,
                                        dating_schedule_load, dating_schedule_now)
from src.uvicorn.routers.dating.dating_profile import (DatingProfileSave,
                                                       dating_profile_create,
                                                       dating_profile_delete,
                                                       dating_profile_row)
from src.uvicorn.routers.dating.dating_profile_action import (DatingProfileSchedule,
                                                              dating_profile_action_schedule)
from src.uvicorn.routers.dating.dating_profile_view import dating_profile_view_card

SITE = 343
ТРИ = [{'from': '07:00', 'to': '11:00'},
       {'from': '14:00', 'to': '18:00'},
       {'from': '19:00', 'to': '23:00'}]


class TestLoad:
    """Разбор столбца: терпим к виду, строг к смыслу."""

    def test_reads_json_string_and_list(self):
        """Столбец приходит то строкой, то списком — MySQL отдаёт JSON по-разному
        в зависимости от драйвера и версии."""
        assert dating_schedule_load('[{"from": "7:0", "to": "11:00"}]') == \
            [{'from': '07:00', 'to': '11:00'}]
        assert dating_schedule_load([{'from': '07:00', 'to': '11:00'}]) == \
            [{'from': '07:00', 'to': '11:00'}]

    @pytest.mark.parametrize('value', [None, '', '[]', 'не json', '{}', 42,
                                       [{'from': '07:00'}], [{'to': '11:00'}],
                                       [{'from': '25:00', 'to': '26:00'}], ['мусор']])
    def test_junk_is_empty(self, value):
        """⚠ Мусор — пустое расписание, а не исключение: столбец правят и руками, и
        миграцией, и сорванный на этом показ карточки хуже пустого блока."""
        assert dating_schedule_load(value) == []

    def test_rows_are_sorted_by_time(self):
        """Смены правят ползунком, и заведённая последней может оказаться первой по
        часам. Порядок нужен и странице, и проверке пересечений."""
        rows = dating_schedule_load([{'from': '19:00', 'to': '23:00'},
                                     {'from': '07:00', 'to': '11:00'}])

        assert [row['from'] for row in rows] == ['07:00', '19:00']

    def test_extra_shifts_are_cut(self):
        many = [{'from': f'{h:02d}:00', 'to': f'{h:02d}:30'} for h in range(10)]

        assert len(dating_schedule_load(many)) == DATING_SCHEDULE_MAX

    def test_empty_is_not_replaced_by_default(self):
        """⚠ Пустое — законное состояние («ведут руками»), а не повод подставить
        умолчание.

        Подставь его здесь — и человек, снявший все смены, получал бы их обратно на
        каждом показе страницы.
        """
        assert dating_schedule_load(None) == []
        assert dating_schedule_load(None) != DATING_SCHEDULE_DEFAULT


class TestCheck:
    """Что считается годным расписанием."""

    def test_default_is_valid(self):
        assert dating_schedule_check(DATING_SCHEDULE_DEFAULT) == ''

    def test_empty_is_valid(self):
        """Пустое годно: это «ведём руками», а не ошибка ввода."""
        assert dating_schedule_check([]) == ''

    def test_end_before_start_is_refused(self):
        why = dating_schedule_check([{'from': '18:00', 'to': '09:00'}])

        assert 'позже' in why

    def test_overlap_is_refused(self):
        """⚠ Смены внахлёст — это вход поверх входа: анкета получит вторую сессию,
        не закрыв первую."""
        why = dating_schedule_check([{'from': '09:00', 'to': '12:00'},
                                     {'from': '11:00', 'to': '14:00'}])

        assert 'налезают' in why

    def test_touching_shifts_are_fine(self):
        """Стык минута в минуту — не пересечение: одна кончилась, другая началась."""
        assert dating_schedule_check([{'from': '09:00', 'to': '12:00'},
                                      {'from': '12:00', 'to': '15:00'}]) == ''

    def test_missing_time_is_refused(self):
        assert dating_schedule_check([{'from': '', 'to': '12:00'}]) != ''

    def test_too_many_is_refused(self):
        many = [{'from': f'{h:02d}:00', 'to': f'{h:02d}:30'} for h in range(10)]

        assert str(DATING_SCHEDULE_MAX) in dating_schedule_check(many)

    def test_reason_is_for_a_human(self):
        """Причина уезжает на страницу: она говорит, что поправить, а не какое
        правило нарушено."""
        why = dating_schedule_check([{'from': '18:00', 'to': '09:00'}])

        assert '18:00' in why and '09:00' in why


class TestNow:
    """Должна ли анкета быть в сети в эту минуту."""

    @pytest.mark.parametrize('час, в_сети', [
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
    def test_default_schedule(self, час, в_сети):
        assert dating_schedule_now(DATING_SCHEDULE_DEFAULT, час * 60) is в_сети

    def test_empty_means_not_online(self):
        """⚠ Пустое расписание — `False`, а не «всегда в сети».

        «Ведут руками» значит, что сама она не входит. Обратное толкование включило
        бы круглосуточное присутствие у всех, кто расписание не заводил.
        """
        assert dating_schedule_now([], 12 * 60) is False


class TestStorage:
    """Хранение — в живую базу, со своей анкетой."""

    @pytest.fixture
    async def profile(self, rhapsody):
        """⚠ Своя анкета: чужую трогать нельзя. Фикстура `rhapsody` нужна потому,
        что заведение сверяет сайт у соседа."""
        answer = await dating_profile_create(
            DatingProfileSave(site_id=SITE, login=f'sched-{uuid.uuid4().hex[:8]}',
                              password='x'), user=None)
        profile_id = int(answer['result'])

        yield profile_id

        try:
            await dating_profile_delete(profile_id, user=None)
        except Exception:
            pass

    async def test_saved_schedule_comes_back(self, profile):
        await dating_profile_action_schedule(
            profile, DatingProfileSchedule(schedule=ТРИ), user=None)

        card = (await dating_profile_view_card(profile, user=None))['result']

        assert card['schedule'] == ТРИ

    async def test_save_normalises_time(self, profile):
        """`9:0` → `09:00`: одна забытая ведущая ноль ломала бы сравнение строк, на
        котором держится порядок."""
        await dating_profile_action_schedule(
            profile, DatingProfileSchedule(schedule=[{'from': '9:0', 'to': '12:5'}]),
            user=None)

        card = (await dating_profile_view_card(profile, user=None))['result']

        assert card['schedule'] == [{'from': '09:00', 'to': '12:05'}]

    async def test_overlap_is_refused_by_the_endpoint(self, profile):
        """⚠ Негодное отклоняется, а не правится молча: сказать человеку полезнее,
        чем тихо сдвинуть его время."""
        with pytest.raises(HTTPException) as refusal:
            await dating_profile_action_schedule(
                profile,
                DatingProfileSchedule(schedule=[{'from': '09:00', 'to': '12:00'},
                                                {'from': '11:00', 'to': '14:00'}]),
                user=None)

        assert refusal.value.status_code == 422
        assert 'налезают' in str(refusal.value.detail)

    async def test_schedule_can_be_cleared(self, profile):
        """Снять расписание можно: это «ведём руками», законное состояние."""
        await dating_profile_action_schedule(
            profile, DatingProfileSchedule(schedule=ТРИ), user=None)
        await dating_profile_action_schedule(
            profile, DatingProfileSchedule(schedule=[]), user=None)

        card = (await dating_profile_view_card(profile, user=None))['result']

        assert card['schedule'] == []

    async def test_new_profile_gets_the_default(self, profile):
        """⚠ Заведённая анкета получает три смены, а не пустоту.

        Анкету заводят, чтобы она работала: пустое расписание при заведении означало
        бы, что каждую надо настраивать руками, прежде чем она войдёт хоть раз.
        """
        row = await dating_profile_row(profile)

        assert dating_schedule_load(row['schedule']) == DATING_SCHEDULE_DEFAULT

    async def test_dump_is_readable_back(self, profile):
        """То, что легло в столбец, читается обратно тем же."""
        assert dating_schedule_load(dating_schedule_dump(ТРИ)) == ТРИ
