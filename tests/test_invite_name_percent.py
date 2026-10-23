"""⚠⚠⚠ Жребий имени в инвайтах бросает **сосед**, и панель к нему не причём.

## Как было и что уехало 23.10.2026

«Вставляй имя изредка» LLM понимает как «вставляй всегда»: вероятность у неё не
считается, она стилистическая — выбирать должен кто-то снаружи. С 26.09.2026 это
шаг `dice` в workflow `dating_invite_one`, но доли ему ещё присылала панель:
настройка сайта `invite_name_percent` читалась здесь (`invite._man_percent`) и
уезжала переменными вызова `invite_name_percent` / `invite_girl_name_percent`.

Тем же поездом, каким 22.10 уехали почтовые доли, 23.10.2026 уехали последние
две: веса лежат **числами в самом `spec` жребия** — `dice_invite_man_name` 10,
`dice_invite_girl_name` 0. Переменные у соседа больше не объявлены, и прислать их
значит поймать отказ «Переменные … не объявлены» на весь вызов.

## Что стережёт этот файл

Ни настройки, ни констант, ни чтения, ни переменных в пути. Возвращаются они
**только вместе** (объявление у соседа, ссылка в `spec`, запись в реестре, чтение
в коде) — поодиночке любое место бесполезно или ломает заход.

## ⚠ Имя — первое слово

В чате зовут по имени, а не по паспорту: «John» вместо «John Smith». Полное имя в
первой реплике выглядит выпиской из документов.

⚠ **Одно слово — тоже имя.** Здесь была живая беда: `people_name_parts` отвечает
пустотой на имя без пробела (а таких на сайте большинство), и в двадцати трёх
черновиках из тридцати шести у людей осталось сырое `[[companion_name]]`.
"""

import pytest

from src.dating.site_setting import SITE_KNOWN

pytestmark = pytest.mark.chat


class TestThePanelDoesNotRoll:
    """⚠⚠ Броска и долей в панели нет: их место — в `spec` жребия у соседа."""

    def test_the_settings_are_gone(self):
        """⚠ Настроек сайта нет: доля задаётся весами оси у соседа.

        Останься они в реестре — человек правил бы число, которое ни на что не
        влияет, и это хуже отсутствия настройки.
        """
        for key in ('invite_name_percent', 'invite_girl_name_percent'):
            assert key not in SITE_KNOWN, \
                f'настройка {key} вернулась, а решает её сосед'

    def test_the_constants_are_gone(self):
        """Константы снятой настройки не должны пережить её."""
        from src.dating import invite

        for name in ('INVITE_MAN_NAME_SETTING', 'INVITE_MAN_NAME_PERCENT',
                     'INVITE_GIRL_NAME_PERCENT'):
            assert not hasattr(invite, name), f'{name} осиротела, но осталась'

    def test_the_reader_is_gone(self):
        """Чтения доли тоже нет: переменной, которой оно кормилось, больше нет."""
        from src.dating import invite

        assert not hasattr(invite, '_man_percent'), \
            'чтение вернулось — переменной у соседа больше не объявляют'


class TestNothingTravels:
    """⚠⚠ Ни одна дорога не везёт прежних переменных.

    Вызов с необъявленной переменной — отказ на весь заход, а не молчаливая
    потеря довода: уедет обратно то, что переехало к соседу.
    """

    def test_the_template_sends_no_name_percents(self):
        import inspect

        from src.dating import mail_agent

        code = inspect.getsource(mail_agent.invite_template)

        # ⚠ Ключ словаря, а не голое имя: в этом же коде стоит памятный
        # комментарий, какие переменные присылать нельзя, — он не passing.
        for key in ("'invite_name_percent'", "'invite_girl_name_percent'"):
            assert key not in code, \
                f'панель снова шлёт {key} — у соседа она не объявлена'

    def test_the_round_passes_no_name_args(self):
        import inspect

        from src.dating import invite

        code = inspect.getsource(invite._templates)

        assert 'name_percent' not in code, \
            'заход снова передаёт доли — их некому принимать'


class TestTheName:
    """Что именно подставляется вместо метки — это по-прежнему наша работа."""

    def test_only_the_first_word(self):
        from src.dating.invite import _first_word

        assert _first_word('John Smith') == 'John'

    def test_one_word_is_a_name_too(self):
        """⚠⚠ Живая беда: 23 черновика из 36 ушли с сырым `[[companion_name]]`,
        потому что разбор имени отвечал пустотой на имя без пробела."""
        from src.dating.invite import _first_word

        assert _first_word('Frank') == 'Frank'

    def test_the_site_field_wins_over_parsing(self):
        """Сайт отдаёт `firstname` — его и берём, не разбирая полное имя."""
        from src.dating.invite import _fill

        got = _fill('Привет, [[companion_name]]!', {'profile': '{}'},
                    {'firstname': 'Vincent', 'name': 'Vincent Vega'})

        assert got == 'Привет, Vincent!'

    def test_a_raw_mark_never_leaves(self):
        """⚠ Не подставилось — метку вырезаем: в скобках она уйдёт человеку."""
        from src.dating.invite import _fill

        got = _fill('Привет, [[companion_name]]!', {'profile': '{}'}, {'name': ''})

        assert '[[' not in got and ']]' not in got


class TestTimeIsGone:
    """⚠⚠ Доля времени убрана вместе со временем — 26.09.2026.

    Прежде здесь был класс `TestTimeShare`: доля `invite_time_percent`, флаг
    `with_time`, проверка, что он доезжает до агента. Всего этого больше нет — ни
    настройки, ни секции в промпте, ни шага `datetime_current_get` у соседа.

    ⚠ Довод, по которому время заводили, стоит знать: «доброе утро» в девять вечера
    выдаёт робота вернее всего остального, и в пуле прода лежал шаблон «My thoughts
    have turned sideways tonight» — модель писала про вечер, не зная, который час.
    Мера была в доле, потому что при показе всегда она поминала время в **94
    инвайтах из 202 за сутки**.

    Класс оставлен сторожем обратного: вернуть время «наполовину» нельзя — нужны
    четыре вещи разом, и любая одна без остальных ломает заход.
    """

    async def test_the_setting_is_gone(self):
        """⚠ Настройки в реестре нет: она обещала бы влияние, которого не будет."""
        assert 'invite_time_percent' not in SITE_KNOWN

    async def test_the_panel_sends_nothing_about_time(self):
        """⚠⚠ И в присылке ничего про время: у соседа эти переменные не объявлены,
        а ручка отвергает вызов целиком — «Переменные … не объявлены». То есть
        лишняя переменная не потеряется молча, заход упадёт."""
        import inspect

        from src.dating import mail_agent

        code = inspect.getsource(mail_agent.invite_template)
        for gone in ('invite_time', 'datetime_current_get_zone',
                     'invite_country'):
            assert f"'{gone}'" not in code, \
                f'панель снова шлёт {gone} — вызов упадёт у соседа'
