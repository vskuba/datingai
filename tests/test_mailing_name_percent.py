"""⚠⚠⚠ Жребий имени собеседника бросает **сосед**, а не панель.

## Как было и почему сломалось

Долю бросала панель (настройка `mailing_name_percent`): выпало — метка
`companion_name` оставалась в `mail_vars`, не выпало — вычёркивалась, и модель
про неё не знала.

Это сломалось о правила промпта. Строка «о получателе **только** через
`[[companion_name]]`» стояла у соседа **безусловно**, а метки в разрешённых не
было. Модель читала разом «разрешены только girl_name и question_to_companion» и
«обращайся через companion_name» — и выкручивалась, ставя в обращение
единственное имя, какое видела: **имя самой анкеты**.

    #524 на проде: «Olga, it's me — [[girl_name]]. I'm writing from…»

Olga — это имя анкеты. Письмо обращено к ней же и подписано меткой отправителя:
роли вывернуты наизнанку. Все пять таких писем были без метки имени; среди 42
писем с меткой — ни одного.

## Как стало

Ось `dice_mail_name` у шага «Жребий», рядом с приёмом, длиной, подписью, темой и
зачином — последними, кто уехал туда же. Веса 25/75. Один бросок правит **и
правилом, и списком разрешённых**:

    allowed variables only: [<for k,v in mail_vars>]
        [<if k != 'companion_name' or dice_mail_name == 'yes'>][[%k%]] — %v%[<endif>]
    [<endfor>]

⚠ Решение и правило живут рядом. Разнеси их, и они разойдутся молча — ровно так,
как разошлись.

## Что стережёт этот файл

Панель больше не решает: ни броска, ни настройки, ни фильтра списка. Вернись
любое — вернётся и «Olga, it's me», потому что правило о получателе у соседа
останется под жребием.
"""

import inspect
import re

import pytest

from src.dating import mail_agent
from src.dating.site_setting import SITE_KNOWN

pytestmark = pytest.mark.mail


class TestThePanelDoesNotRoll:
    """⚠⚠ Броска в панели нет: его место — у соседа, рядом с правилом."""

    def test_no_dice_in_the_writer(self):
        code = inspect.getsource(mail_agent.mail_write)

        assert 'man_name' not in code, 'бросок имени вернулся в панель'
        assert 'MAILING_NAME' not in code, 'панель снова читает долю имени'
        assert not re.search(r'percent_share\([^)]*NAME', code), \
            'панель снова спрашивает долю обращения по имени'

    def test_the_setting_is_gone(self):
        """⚠ Настройки сайта тоже нет: доля задаётся весами оси у соседа.

        Останься она в реестре — человек правил бы число, которое ни на что не
        влияет, и это хуже отсутствия настройки.
        """
        assert 'mailing_name_percent' not in SITE_KNOWN, \
            'настройка доли имени вернулась, а решает её сосед'

    def test_the_constants_are_gone(self):
        """Константы снятой настройки не должны пережить её."""
        from src.dating import mail

        for name in ('MAILING_NAME_SETTING', 'MAILING_NAME_CHANCE'):
            assert not hasattr(mail, name), f'{name} осиротела, но осталась'


class TestTheListTravelsWhole:
    """⚠⚠ Список переменных уезжает **целиком**: отбирает сосед.

    Отбери панель — появился бы второй источник правды о том, что разрешено, и
    он разошёлся бы с правилом промпта. Это и была прежняя беда.
    """

    def test_nothing_is_filtered_out(self):
        code = inspect.getsource(mail_agent.mail_write)

        assert 'allowed = known' in code, \
            'панель снова отбирает переменные — решать должен жребий у соседа'
        assert "!= INVITE_SLOT_MAN" not in code, \
            'имя собеседника снова вычёркивается панелью'

    def test_the_whole_registry_reaches_the_agent(self):
        """Все включённые переменные доезжают до `mail_vars`, включая имя."""
        import json

        from src.dating.mail_agent import _wanted_map

        got = json.loads(_wanted_map([
            {'name': 'girl_name', 'title': 'x'},
            {'name': 'companion_name', 'title': 'y'},
            {'name': 'question_to_companion', 'title': 'z'},
        ]))

        assert set(got) == {'girl_name', 'companion_name', 'question_to_companion'}, got
