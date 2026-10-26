"""Драйвер панели Step2Love: карта имён формы и нормализация её меры.

Зеркало `test_site_panel_luxee.py`, и про то же другое: Step2Love прячет
параметры в **группах `radio`** с числовыми `value` — подписи стоят текстом за
отмеченными тегами, и без их сбора анкета уехала бы в био номерами. Анкета при
этом разложена по трём вкладкам, и образец — сшитая форма всех трёх.
"""

from pathlib import Path

import pytest

from src.dating.panel_fetch import panel_fetch_form
from src.dating.profile_param import PROFILE_PARAM_KEYS
from src.dating.site.site_panel import DatingSitePanelStep2Love
from src.dating.site.site_registry import site_registry_panel

pytestmark = pytest.mark.profile

SAMPLE = (Path(__file__).parent / 'frames' / 'step2love_edit_user.html'
          ).read_text(encoding='utf-8')


@pytest.fixture
def step2love():
    return DatingSitePanelStep2Love({'driver': 'step2love'})


class TestTheyAgree:
    """⚠⚠ Сторож согласия: карта драйвера и реестр — про одно и то же.

    Планка та же, что у Luxee: карта знает ровно то, что есть в форме, и ни
    одного ключа вне реестра. Не знает она одного ключа — религии: на сайте её
    нет вовсе, и выдумывать её драйверу нечем.
    """

    def test_the_map_lands_inside_the_registry(self, step2love):
        extra = set(step2love.FIELDS.values()) - set(PROFILE_PARAM_KEYS)

        assert not extra, f'драйвер кладёт ключи вне реестра: {sorted(extra)}'

    def test_the_map_covers_the_form_it_reads(self, step2love):
        """Форма знает 18 полей реестра — столько же обязана знать и карта."""
        assert len(step2love.FIELDS) == 18

    def test_religion_is_the_only_key_the_panel_lacks(self, step2love):
        gap = set(PROFILE_PARAM_KEYS) - set(step2love.FIELDS.values()) \
            - {'birthday', 'english'}

        assert gap == {'religion'}


class TestTheSampleIsParsed:
    def test_radio_groups_land_as_their_labels(self, step2love):
        """⚠ В `value` групп — только нумерация сайта: взяли бы её вместо
        текста — в био уехало бы `156` там, где «Athletic», и поле-то было бы
        непустое, молча."""
        got = step2love.params(panel_fetch_form(SAMPLE))

        assert got['body'] == 'Athletic'
        assert got['hair'] == 'Light blonde'
        assert got['hair_style'] == 'Long'
        assert got['eyes'] == 'Blue'
        assert got['ethnicity'] == 'European'
        assert got['marital_state'] == 'Single'
        assert got['children'] == '1 child'
        assert got['smoke'] == 'No'
        assert got['drink'] == 'Only light alcohol'

    def test_the_hidden_country_lands_as_its_name(self, step2love):
        """Город и страна — скрытые поля с номером; название — текст за тегом."""
        got = step2love.params(panel_fetch_form(SAMPLE))

        assert got['country'] == 'Ukraine'

    def test_the_birthday_is_three_hidden_fields(self, step2love):
        """⚠ Месяц здесь цифрой, а не `Jan`, как у Ladadate."""
        got = step2love.params(panel_fetch_form(SAMPLE))

        assert got['birthday'] == '1990-05-04'

    def test_the_english_comes_from_the_language_boxes(self, step2love):
        """Отмечены English, Russian, Ukrainian — английский значит «Yes»."""
        got = step2love.params(panel_fetch_form(SAMPLE))

        assert got['english'] == 'Yes'

    def test_the_texts_land_whole(self, step2love):
        got = step2love.params(panel_fetch_form(SAMPLE))

        assert got['occupation'] == 'Film and Television Director'
        assert got['about_partner'].startswith('I am looking for a kind')
        assert got['hobby'].startswith('Dancing is one of my main hobbies')

    def test_religion_is_empty_not_invented(self, step2love):
        assert step2love.params(panel_fetch_form(SAMPLE))['religion'] == ''

    def test_the_frame_is_whole(self, step2love):
        """Каркас приезжает целиком: 21 ключ реестра, неизвестное — пустой
        строкой, а не отсутствием."""
        got = step2love.params(panel_fetch_form(SAMPLE))

        assert list(got) == list(PROFILE_PARAM_KEYS)


class TestTheNormalisation:
    def test_height_meters_become_centimeters(self, step2love):
        """Скобка Luxee несёт сантиметры, скобка Step2Love — метры; био
        и агенту нужен единый `165 cm`."""
        assert step2love.params({'id_height': '5\'5" (1.65m)'})['height'] \
            == '165 cm'

    def test_weight_kilos_land_clean(self, step2love):
        assert step2love.params({'id_weight': '120 lbs. (54kg.)'})['weight'] \
            == '54 kg'

    def test_a_value_without_the_metric_half_is_kept_whole(self, step2love):
        """Сайт вправе писать иначе — тогда берём как есть, а не теряем."""
        assert step2love.params({'id_height': '170 cm'})['height'] == '170 cm'
        assert step2love.params({'id_weight': '60 kg'})['weight'] == '60 kg'

    def test_the_education_falls_back_to_the_kind(self, step2love):
        """Степень не выбрана — остаётся хотя бы «Graduated from»."""
        got = step2love.params({'id_education_type': 'Graduated from'})

        assert got['education'] == 'Graduated from'


class TestTheCardFields:
    def test_the_city_lands_from_the_hidden_text(self, step2love):
        assert step2love.card(panel_fetch_form(SAMPLE))['city'] == 'Kyiv'

    def test_the_about_me_text_lands(self, step2love):
        got = step2love.card(panel_fetch_form(SAMPLE))

        assert got['about'].startswith('I am a creative and feminine woman')

    def test_the_age_is_counted_from_the_birthday(self, step2love):
        """Возраст считает панель по дате; год рождения 1990-й — либо 35,
        либо 36 в зависимости от сегодня, и это оба верно."""
        assert step2love.card(panel_fetch_form(SAMPLE))['age'] in (35, 36)

    def test_a_missing_birthday_leaves_no_age(self, step2love):
        """Возраста без даты не бывает: нуль в карточку уезжать не должен."""
        assert 'age' not in step2love.card({'city': 'Kyiv'})


class TestTheMarkupGuard:
    """⚠⚠ Без этого счётчика смена разметки обнулила бы параметры у всех анкет."""

    def test_the_sample_is_recognised(self, step2love):
        assert step2love.known(panel_fetch_form(SAMPLE)) >= len(step2love.FIELDS)

    def test_a_stranger_page_is_recognised_as_nothing(self, step2love):
        assert step2love.known({'Login': '', 'Password': ''}) == 0


class TestTheRegistry:
    def test_step2love_gets_its_driver(self):
        assert isinstance(site_registry_panel({'driver': 'step2love'}),
                          DatingSitePanelStep2Love)

    def test_the_path_reaches_the_edit_page(self):
        assert site_registry_panel({'driver': 'step2love'}).path == \
            'my_profiles.php?com=edit_user&id_user={uid}'

    def test_the_tabs_are_addressed_by_suffixes(self, step2love):
        """Путь уже с запросом — вкладки дописываются амперсандом, а не
        вопросом: `?cmd=3?cmd=5` сайт прочитал бы как битую ссылку."""
        assert step2love.PAGES == ('&cmd=3', '&cmd=5')
