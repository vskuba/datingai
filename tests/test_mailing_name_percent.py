"""Доля шаблонов с обращением по имени — настройкой сайта.

## ⚠⚠ Зачем настройка

Прежде долей не управляли вовсе. Имя появлялось **случайно** — через зачин: из
шести приёмов два были с именем, да и те лишь когда приветствие вообще выпало.
Выходило около трети, и подвинуть её было нечем.

Имя в каждом письме читается как рассылка; совсем без имени письмо безадресно.
Поэтому долю сделали настройкой, а умолчанием взяли ту самую треть.

## ⚠⚠ Что здесь легко сломать

Жребий имени управляет **двумя** вещами разом, и разойдись они — настройка
перестанет работать молча:

1. **`mail_var_must`** — список обязательных меток: останься там
   `companion_name`, судья у соседа завернёт безымянный шаблон как неполный;
2. **`mail_vars` и `mail_var_names`** — то, что видит модель: покажи ей имя, и
   она впишет его, даже когда не просили.

Тест проверяет обе: сломай одну — покраснеет.

⚠ Третьей был **зачин**: списки делились по наличию имени, чтобы приём не просил
имени там, где метки нет. С 2026-10-20 зачин бросает сосед
(`dice_mail_greeting`), и панель его не шлёт вовсе — согласовывать больше
нечего.
"""

import random

import pytest

from src.dating import mail_agent
from src.dating.mail import MAILING_NAME_CHANCE, MAILING_NAME_SETTING
from src.dating.site_setting import SITE_KNOWN

pytestmark = pytest.mark.mail

STAND_SITE = 999_343

# Реестр сайта в том виде, в каком его получает `mail_write`.
VARS = [
    {'name': 'companion_name', 'source': 'companion', 'title': 'Имя получателя'},
    {'name': 'girl_name', 'source': 'profile', 'title': 'Имя девушки'},
    {'name': 'question_to_companion', 'source': 'live', 'title': 'Вопрос'},
]


@pytest.fixture
def caught(monkeypatch):
    """Перехватывает переменные, ушедшие агенту, — к соседу не ходим."""
    seen = {}

    async def _ask(site_id, agent_id=0, question='', variables=None, **kw):
        seen.update(variables or {})

        # ответ workflow — только текст письма, без названия (2026-10-17).
        return 'Hi there [[companion_name]]'

    async def _agent(site_id, name, workflow):
        return 1

    async def _language(site_id):
        return 'английском'

    monkeypatch.setattr(mail_agent, '_chat_ask', _ask)
    monkeypatch.setattr(mail_agent, '_agent_id', _agent)
    monkeypatch.setattr(mail_agent, 'mail_agent_language', _language)
    # ⚠ Отбор по занятию — не предмет этой проверки: берём реестр целиком.
    monkeypatch.setattr('src.uvicorn.routers.dating.mail_var.mail_var_used',
                        lambda row, use: True)

    return seen


def _fix_dice(monkeypatch, *, name: bool):
    """Заставляет выпасть (или не выпасть) именно жребий имени.

    ⚠ Подменяем **настройку**, а не `random`: так проверяется вся цепочка —
    чтение доли, сравнение, ветвление, — а не одна её середина.
    """
    async def _percent(site_id, key, fallback):
        if key == MAILING_NAME_SETTING:
            return 1.0 if name else 0.0

        return 0.0

    monkeypatch.setattr(mail_agent, 'percent_share', _percent)


class TestTheSettingIsWiredUp:
    """⚠ Настройка есть в реестре и согласована с кодом."""

    def test_the_registry_knows_it(self):
        one = SITE_KNOWN[MAILING_NAME_SETTING]

        assert one['group'] == 'mail', 'настройка не на вкладке рассылки'
        assert one['kind'] == 'number'
        assert (one['min'], one['max']) == (0, 100), 'доля без границ 0—100'

    def test_the_default_matches_the_code(self):
        """⚠⚠ Умолчание живёт **в двух местах**: реестр показывает его человеку,
        код берёт, когда настройка не сохранена. Разойдись они — страница врала
        бы, и никакой ошибки при этом не случилось бы."""
        assert (float(SITE_KNOWN[MAILING_NAME_SETTING]['default']) / 100
                == MAILING_NAME_CHANCE)


class TestTheDiceDrivesEverything:
    """⚠⚠ Главное: выпавший жребий меняет все три вещи разом."""

    async def test_the_name_is_required_when_it_wins(self, caught, monkeypatch):
        _fix_dice(monkeypatch, name=True)

        await mail_agent.mail_write(STAND_SITE, VARS)

        assert 'companion_name' in caught['mail_var_must']
        assert 'companion_name' in caught['mail_var_names']

    async def test_the_name_is_dropped_when_it_loses(self, caught, monkeypatch):
        """⚠⚠ Тот самый случай, ради которого всё и делалось.

        Останься имя в обязательных — судья завернул бы безымянный шаблон, и
        доля не работала бы вовсе: панель просит имя, судья требует имя, жребий
        ни при чём.
        """
        _fix_dice(monkeypatch, name=False)

        await mail_agent.mail_write(STAND_SITE, VARS)

        assert 'companion_name' not in caught['mail_var_must'], \
            'имя осталось обязательным — судья завернёт шаблон без него'
        assert 'companion_name' not in caught['mail_var_names'], \
            'имя показано модели — она впишет его, хотя не просили'

    async def test_other_variables_survive_either_way(self, caught, monkeypatch):
        """⚠ Убираем **только имя**: прочие метки нужны всегда."""
        _fix_dice(monkeypatch, name=False)

        await mail_agent.mail_write(STAND_SITE, VARS)

        assert 'girl_name' in caught['mail_var_must']
        assert 'question_to_companion' in caught['mail_var_must']

    async def test_the_live_list_follows_too(self, caught, monkeypatch):
        """⚠ `mail_var_live` собирается из того же списка: разойдись он — метка
        вопроса потерялась бы вместе с именем."""
        _fix_dice(monkeypatch, name=False)

        await mail_agent.mail_write(STAND_SITE, VARS)

        assert 'question_to_companion' in caught['mail_var_live']
