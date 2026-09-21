"""Эмодзи приводятся к одному виду на входе.

## ⚠⚠ Что было сломано

Панель агентства отдаёт эмодзи номером в **пять** шестнадцатеричных цифр, а её
разборщик читает ровно четыре. Пятая остаётся в тексте буквой, и выходит пара
«знак греческого расширенного плюс цифра»:

    😉  U+1F609   →   ὠ9   (U+1F60 + '9')

Текст при этом не ломается и не подсвечивается ошибкой — он просто становится
другим. Две беды сразу: человек видит в переписке греческую букву, а наша же
реплика, вернувшаяся из журнала сайта, перестаёт совпадать с отправленной — и
отсев близнецов её не узнаёт. Так у пары Diana → Henry остались видимые дубли
после того, как все прочие исчезли.

## ⚠ Проверяется на живых байтах

Ниже не выдуманные строки, а то, что лежало в базе прода: `E1BDA039` у панели
против `F09F9889` у нас.
"""

import inspect

import pytest

from text_.text_emoji import text_emoji_fix, text_emoji_same

pytestmark = pytest.mark.chat

# Живые байты с прода: одна и та же реплика, записанная двумя дорогами.
PANEL = bytes.fromhex('74616C6B696E673F20E1BDA039').decode()
OURS = bytes.fromhex('74616C6B696E673F20F09F9889').decode()


class TestItRepairsTheMangling:
    """⚠⚠ Главное: починенное совпадает с нашим байт в байт."""

    def test_the_prod_pair_becomes_equal(self):
        assert text_emoji_fix(PANEL) == OURS

    def test_a_thumbs_up_is_repaired(self):
        """👍 U+1F44D: хвост — буква, а не цифра. Обе раскладки должны работать."""
        assert text_emoji_fix('nice ' + chr(0x1F44) + 'D') == 'nice \U0001F44D'
        assert text_emoji_fix('nice ' + chr(0x1F44) + 'd') == 'nice \U0001F44D'

    def test_several_in_one_text(self):
        given = chr(0x1F60) + '9' + ' and ' + chr(0x1F44) + 'D'
        assert text_emoji_fix(given) == '\U0001F609 and \U0001F44D'


class TestItLeavesEverythingElseAlone:
    """⚠⚠ Обратная сторона, и она важнее: починка не должна портить текст."""

    def test_plain_text_is_untouched(self):
        for one in ('hello there', 'привет, как дела?', '', 'ok'):
            assert text_emoji_fix(one) == one

    def test_a_whole_emoji_stays_whole(self):
        """Уже правильный знак трогать нельзя — иначе починка станет порчей."""
        assert text_emoji_fix('ok \U0001F609') == 'ok \U0001F609'

    def test_typographic_quotes_survive(self):
        """⚠ Их в базе три тысячи — больше всего не-ASCII."""
        assert text_emoji_fix('I’m busy “today”') == 'I’m busy “today”'

    def test_greek_without_a_digit_is_not_touched(self):
        """⚠ Пара нужна целиком: одна голова без хвоста — это просто буква."""
        given = 'ok ' + chr(0x1F60) + ' text'
        assert text_emoji_fix(given) == given

    def test_beyond_the_emoji_blocks_is_not_touched(self):
        """⚠ U+1FB0 развернулся бы в «символы старых ЭВМ» — не эмодзи, и трогать
        его нельзя. Отсюда верхняя граница U+1FAF."""
        given = chr(0x1FB0) + '5'
        assert text_emoji_fix(given) == given

    def test_it_survives_junk(self):
        for one in (None, 123, [], ''):
            assert text_emoji_fix(one) == one


class TestTheEntryPointsUseIt:
    """⚠⚠ Починка стоит на воронках, а не в одном месте.

    Текст приходит снаружи четырьмя дорогами, и каждая обязана звать общую
    функцию: чинить в одной значило бы оставить три другие с искажением.
    """

    def test_the_chat_driver_uses_it(self):
        """⚠ Смотрим **саму сборку реплики**, а не модуль целиком: через неё
        проходят и живые кадры, и накопившееся, и каждый новый драйвер сайта."""
        from src.dating.site.site_base import DatingSiteChat

        assert 'text_emoji_same' in inspect.getsource(DatingSiteChat._message), \
            'реплики чата снова без починки'

    def test_the_panel_import_uses_it(self):
        import src.dating.chat_panel as mod

        assert 'text_emoji_same' in inspect.getsource(mod._text), \
            'переписка из панели снова без починки'

    def test_names_use_it(self):
        import src.dating.people as mod

        assert 'text_emoji_same' in inspect.getsource(mod.people_name_case), \
            'имена собеседников снова без починки'

    def test_mail_bodies_use_it(self):
        import src.dating.mail_in as mod

        assert 'text_emoji_same' in inspect.getsource(mod._body), \
            'тела писем снова без починки'


class TestTheOneDoor:
    """⚠ Вызывающие зовут `text_emoji_same`: за ней сегодня одна починка, завтра
    может быть больше, а места вызова не меняются."""

    def test_same_delegates_to_fix(self):
        assert text_emoji_same(PANEL) == text_emoji_fix(PANEL)
