"""Драйвер панели: карта имён и нормализация значений.

## ⚠ Почему нормализация здесь, а не в сборке био

`5'8" - 172 cm` — то, как пишет рост **Ladadate**. В общем коде это знание стало бы
миной для второго сайта: его панель напишет иначе, и чужая обрезка молча испортила
бы значение. Та же причина, по которой заведён `site_about.py`.
"""

from pathlib import Path

import pytest

from src.dating.panel_fetch import panel_fetch_form
from src.dating.profile_param import PROFILE_PARAM_KEYS
from src.dating.site.site_panel import DatingSitePanel, DatingSitePanelLadadate
from src.dating.site.site_registry import site_registry_panel

pytestmark = pytest.mark.profile

SAMPLE = (Path(__file__).parent / 'frames' / 'ladadate_lady_edit.html'
          ).read_text(encoding='utf-8')


@pytest.fixture
def ladadate():
    return DatingSitePanelLadadate({'driver': 'ladadate'})


class TestTheyAgree:
    """⚠⚠ Сторож согласия: карта драйвера и реестр — про одно и то же.

    Два списка про одно и то же расходятся **молча**: добавил ключ в реестр, забыл
    в карте — окно показывает пустую строку вечно; наоборот — драйвер кладёт ключ,
    которого не знает ни показ, ни био.
    """

    def test_every_registry_key_is_covered(self, ladadate):
        covered = set(ladadate.FIELDS.values()) | set(ladadate.BUILT)
        missed = set(PROFILE_PARAM_KEYS) - covered

        assert not missed, f'драйвер не знает ключей реестра: {sorted(missed)}'

    def test_the_driver_invents_nothing(self, ladadate):
        covered = set(ladadate.FIELDS.values()) | set(ladadate.BUILT)
        extra = covered - set(PROFILE_PARAM_KEYS)

        assert not extra, f'драйвер кладёт ключи вне реестра: {sorted(extra)}'

    def test_sources_feed_the_built_keys(self, ladadate):
        """⚠ `SOURCES` — поля формы, из которых собрано `BUILT`; на них же стоит
        проверка «разметка сменилась»."""
        assert ladadate.SOURCES == ('BirthdayDay', 'BirthdayMonth', 'BirthdayYear')


class TestTheSampleIsParsed:
    def test_simple_fields_land_as_is(self, ladadate):
        got = ladadate.params(panel_fetch_form(SAMPLE))

        assert got['country'] == 'Ukraine'
        assert got['occupation'] == 'teacher'
        assert got['eyes'] == 'Hazel'
        assert got['hobby'] == 'swimming and dancing'
        assert got['about_partner'] == 'kind and honest man'
        assert got['age_from'] == '35'

    def test_the_frame_is_whole(self, ladadate):
        got = ladadate.params(panel_fetch_form(SAMPLE))

        assert list(got) == list(PROFILE_PARAM_KEYS)


class TestTheNormalisation:
    def test_height_keeps_the_metric_half(self, ladadate):
        assert ladadate.params({'HeightId': '5\'8" - 172 cm'})['height'] == '172 cm'

    def test_weight_keeps_the_metric_half(self, ladadate):
        assert ladadate.params({'WeightId': '112 lb - 51 kg'})['weight'] == '51 kg'

    def test_a_value_without_a_dash_is_kept_whole(self, ladadate):
        """Сайт вправе писать иначе — тогда берём как есть, а не теряем."""
        assert ladadate.params({'HeightId': '172 cm'})['height'] == '172 cm'

    def test_birthday_is_built_from_three_fields(self, ladadate):
        got = ladadate.params({'BirthdayDay': '21', 'BirthdayMonth': 'Jan',
                               'BirthdayYear': '1990'})

        assert got['birthday'] == '1990-01-21'

    def test_a_single_digit_day_is_padded(self, ladadate):
        got = ladadate.params({'BirthdayDay': '5', 'BirthdayMonth': 'Mar',
                               'BirthdayYear': '1991'})

        assert got['birthday'] == '1991-03-05'

    def test_an_unknown_month_gives_nothing(self, ladadate):
        """⚠ Половина даты хуже её отсутствия: «1990-00-21» уехало бы в био."""
        got = ladadate.params({'BirthdayDay': '21', 'BirthdayMonth': '',
                               'BirthdayYear': '1990'})

        assert got['birthday'] == ''

    def test_an_incomplete_triple_gives_nothing(self, ladadate):
        got = ladadate.params({'BirthdayDay': '21', 'BirthdayMonth': 'Jan'})

        assert got['birthday'] == ''


class TestTheMarkupGuard:
    """⚠⚠ Без этого счётчика смена разметки обнулила бы параметры у всех анкет."""

    def test_the_sample_is_recognised(self, ladadate):
        assert ladadate.known(panel_fetch_form(SAMPLE)) >= 20

    def test_a_stranger_page_is_recognised_as_nothing(self, ladadate):
        assert ladadate.known({'Login': '', 'Password': ''}) == 0


class TestTheRegistry:
    def test_ladadate_gets_its_driver(self):
        assert isinstance(site_registry_panel({'driver': 'ladadate'}),
                          DatingSitePanelLadadate)

    def test_an_unknown_site_gets_the_base_one(self):
        got = site_registry_panel({'driver': 'goldenbride'})

        assert type(got) is DatingSitePanel

    def test_the_base_driver_has_no_path(self):
        """⚠ Пустой путь и значит «панель этого сайта читать нечем»."""
        assert site_registry_panel(None).path == ''

    def test_the_base_driver_gives_an_empty_frame(self):
        got = site_registry_panel(None).params({'HeightId': '172 cm'})

        assert set(got.values()) == {''}
