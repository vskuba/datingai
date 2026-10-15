"""Драйвер панели Luxee: карта имён формы и нормализация её меры.

Зеркало `test_site_panel.py`, и про то же другое: Luxee пишет дату рождения
**одним полем** и сразу ISO, а меру — **в скобках** (`5'7.4&quot; (175 cm)`).
Содержать то, чего у сайта нет (волосы, глаза, окно возраста), драйвер не
обязан; обязан не выдумывать то, чего в реестре нет.
"""

from pathlib import Path

import pytest

from src.dating.panel_fetch import panel_fetch_form
from src.dating.profile_param import PROFILE_PARAM_KEYS
from src.dating.site.site_panel import DatingSitePanelLuxee
from src.dating.site.site_registry import site_registry_panel

pytestmark = pytest.mark.profile

SAMPLE = (Path(__file__).parent / 'frames' / 'luxee_lady_edit.html'
          ).read_text(encoding='utf-8')


@pytest.fixture
def luxee():
    return DatingSitePanelLuxee({'driver': 'luxee'})


class TestTheyAgree:
    """⚠⚠ Сторож согласия: карта драйвера и реестр — про одно и то же.

    У Ladadate сторож требовал покрытия всех ключей; здесь планка другая: карта
    знает ровно то, что есть в форме Luxee, и ни одного ключа вне реестра.
    """

    def test_the_map_lands_inside_the_registry(self, luxee):
        extra = set(luxee.FIELDS.values()) - set(PROFILE_PARAM_KEYS)

        assert not extra, f'драйвер кладёт ключи вне реестра: {sorted(extra)}'

    def test_the_map_covers_the_form_it_reads(self, luxee):
        """Форма знает 13 полей реестра — столько же обязана знать и карта."""
        assert len(luxee.FIELDS) == 13


class TestTheSampleIsParsed:
    def test_simple_fields_land_as_is(self, luxee):
        got = luxee.params(panel_fetch_form(SAMPLE))

        assert got['country'] == 'Ukraine'
        assert got['occupation'] == 'businesswoman'
        assert got['education'] == 'University degree'
        assert got['religion'] == 'Christianity'
        assert got['marital_state'] == 'Divorced'
        assert got['children'] == '2'
        assert got['body'] == 'Big & beautiful'
        assert got['smoke'] == 'No, never'
        assert got['drink'] == 'Socially'

    def test_the_birthday_is_a_single_field(self, luxee):
        """⚠ Не тройка Ladadate: поле уже ISO, собирать нечего."""
        got = luxee.params(panel_fetch_form(SAMPLE))

        assert got['birthday'] == '1971-06-08'

    def test_a_multiple_select_lands_whole(self, luxee):
        """⚠ Выбранных строк четыре — потерялась бы любая, пропала бы часть
        интересов, и никто бы не заметил: поле-то непустое."""
        got = luxee.params(panel_fetch_form(SAMPLE))

        assert got['hobby'] == 'Food, Fitness, Sports, Movies'

    def test_a_field_the_site_has_no_is_empty_not_invented(self, luxee):
        got = luxee.params(panel_fetch_form(SAMPLE))

        assert (got['hair'], got['eyes'], got['english'],
                got['age_from'], got['age_to']) == ('', '', '', '', '')

    def test_the_frame_is_whole(self, luxee):
        """Каркас приезжает целиком: 21 ключ реестра, неизвестное — пустой
        строкой, а не отсутствием."""
        got = luxee.params(panel_fetch_form(SAMPLE))

        assert list(got) == list(PROFILE_PARAM_KEYS)


class TestTheNormalisation:
    def test_height_keeps_the_metric_half(self, luxee):
        """Панель пишет обе системы: имперская до скобок, метрическая — в них."""
        assert luxee.params(
            {'FakeProfileModel[height]': '5\'7.4" (175 cm)'})['height'] == '175 cm'

    def test_weight_keeps_the_metric_half(self, luxee):
        assert luxee.params(
            {'FakeProfileModel[weight]': '169 lbs (77 kg)'})['weight'] == '77 kg'

    def test_a_value_without_brackets_is_kept_whole(self, luxee):
        """Сайт вправе писать иначе — тогда берём как есть, а не теряем."""
        assert luxee.params(
            {'FakeProfileModel[height]': '175 cm'})['height'] == '175 cm'


class TestTheMarkupGuard:
    """⚠⚠ Без этого счётчика смена разметки обнулила бы параметры у всех анкет."""

    def test_the_sample_is_recognised(self, luxee):
        assert luxee.known(panel_fetch_form(SAMPLE)) >= len(luxee.FIELDS)

    def test_a_stranger_page_is_recognised_as_nothing(self, luxee):
        assert luxee.known({'Login': '', 'Password': ''}) == 0


class TestTheRegistry:
    def test_luxee_gets_its_driver(self):
        assert isinstance(site_registry_panel({'driver': 'luxee'}),
                          DatingSitePanelLuxee)

    def test_the_path_reaches_the_edit_page(self):
        """Карточка анкеты живёт по `profile/update/{номер}/` — с чертой: на
        неё сайт отвечает прямо, без `301`. """
        assert site_registry_panel({'driver': 'luxee'}).path == \
            'profile/update/{uid}/'
