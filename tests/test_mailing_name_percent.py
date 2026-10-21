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

1. **столбец `name`** переменной `mail_vars` — из него сосед выводит список
   обязательных меток (`text_marks_missing`): останься там
   `companion_name`, судья у соседа завернёт безымянный шаблон как неполный;
2. **она же целиком** — то, что видит модель: покажи ей имя, и
   она впишет его, даже когда не просили.

Тест проверяет обе: сломай одну — покраснеет.

⚠ Третьей был **зачин**: списки делились по наличию имени, чтобы приём не просил
имени там, где метки нет. С 2026-10-20 зачин бросает сосед
(`dice_mail_greeting`), и панель его не шлёт вовсе — согласовывать больше
нечего.
"""

import random

import json

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



# ⚠⚠ Прежде `mail_var_must` и `mail_var_names` уезжали отдельными переменными
# и собирались побайтово одинаково; с 2026-10-21 сосед выводит обоих из
# `mail_vars` циклом по ключам. Поведение то же, носитель другой — и сторожа
# смотрят туда же, куда посмотрит он.
def _names(caught) -> str:
    """Ключи `mail_vars` — прежние `mail_var_must`/`mail_var_names`.

    ⚠ Ровно то, что выведет сосед циклом `[<for k,v in mail_vars>]%k%,[<endfor>]`.
    """
    return ','.join(json.loads(caught['mail_vars']))


class TestTheDiceDrivesEverything:
    """⚠⚠ Главное: выпавший жребий меняет все три вещи разом."""

    async def test_the_name_is_required_when_it_wins(self, caught, monkeypatch):
        _fix_dice(monkeypatch, name=True)

        await mail_agent.mail_write(STAND_SITE, VARS)

        assert 'companion_name' in _names(caught)
        assert 'companion_name' in _names(caught)

    async def test_the_name_is_dropped_when_it_loses(self, caught, monkeypatch):
        """⚠⚠ Тот самый случай, ради которого всё и делалось.

        Останься имя в обязательных — судья завернул бы безымянный шаблон, и
        доля не работала бы вовсе: панель просит имя, судья требует имя, жребий
        ни при чём.
        """
        _fix_dice(monkeypatch, name=False)

        await mail_agent.mail_write(STAND_SITE, VARS)

        assert 'companion_name' not in _names(caught), \
            'имя осталось обязательным — судья завернёт шаблон без него'
        assert 'companion_name' not in _names(caught), \
            'имя показано модели — она впишет его, хотя не просили'

    async def test_other_variables_survive_either_way(self, caught, monkeypatch):
        """⚠ Убираем **только имя**: прочие метки нужны всегда."""
        _fix_dice(monkeypatch, name=False)

        await mail_agent.mail_write(STAND_SITE, VARS)

        assert 'girl_name' in _names(caught)
        assert 'question_to_companion' in _names(caught)

    async def test_no_service_key_travels(self, caught, monkeypatch):
        """⚠⚠ Служебного в списке нет: жребий правит **переменными**, и ничего,
        кроме них, соседу не уезжает.

        Был ключ `@live` со списком тех, чьё значение — целое предложение. Снят
        20.10.2026 вместе с предметным инструментом: признак переехал в шаг
        workflow константой.
        """
        import json

        _fix_dice(monkeypatch, name=False)

        await mail_agent.mail_write(STAND_SITE, VARS)

        keys = json.loads(caught['mail_vars'])

        assert all(not key.startswith('@') for key in keys), \
            f'в список вернулся служебный ключ: {sorted(keys)}'
