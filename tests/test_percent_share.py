"""Доля из настройки сайта: одна функция на инвайты, рассылку и избранных.

## ⚠⚠ Что было сломано

Функция жила **тремя копиями** — в `invite.py`, `mail_agent.py` и
`favorite_mail.py`. Копии разошлись, и разошлись молча: две клампили значение
сами (`min(max(…), 100) / 100`), третья читала через `site_seconds` — то есть
соблюдала границы из реестра настроек.

Правило проекта — «границы числа проверяет и **чтение**, форма не единственный
вход» — выполняла одна копия из трёх. Узнать об этом можно было, лишь сравнив их
глазами.

## ⚠ Почему это не придирка к красоте

Доля решает, появится ли имя в письме, вопрос, рассказ. Разойдись две копии на
границе — одно занятие слушается реестра, другое нет, и человек, поправивший
настройку, видит разное поведение у соседних вкладок.
"""

import pytest

from mysql_.mysql_ import mysql_get_db_async

from src.dating.percent import percent_hit, percent_share

pytestmark = pytest.mark.mail

STAND_SITE = 999_343
KEY = 'mailing_story_percent'


@pytest.fixture
async def site_set():
    """Ставит настройку у сайта стенда и убирает за собой.

    ⚠ `None` означает «ключа нет вовсе» — это отдельный случай от нуля, и
    проверяется он отдельно.
    """
    keys = []

    async def _set(key, value):
        keys.append(key)

        async with mysql_get_db_async() as db:
            if value is None:
                await db.execute(
                    'UPDATE site SET settings = JSON_REMOVE('
                    ' COALESCE(settings, JSON_OBJECT()), %s) WHERE site_id = %s',
                    (f'$.{key}', STAND_SITE))
            else:
                await db.execute(
                    'UPDATE site SET settings = JSON_SET('
                    ' COALESCE(settings, JSON_OBJECT()), %s, %s)'
                    ' WHERE site_id = %s',
                    (f'$.{key}', str(value), STAND_SITE))
            await db.connection.commit()

    yield _set

    async with mysql_get_db_async() as db:
        for key in keys:
            await db.execute(
                'UPDATE site SET settings = JSON_REMOVE(settings, %s)'
                ' WHERE site_id = %s', (f'$.{key}', STAND_SITE))
        await db.connection.commit()


class TestItReadsTheSetting:
    """Перевод процентов в долю и умолчание."""

    async def test_an_unset_key_gives_the_fallback(self, site_set):
        await site_set(KEY, None)

        assert await percent_share(STAND_SITE, KEY, 0.4) == pytest.approx(0.4)

    async def test_a_number_becomes_a_share(self, site_set):
        await site_set(KEY, 25)

        assert await percent_share(STAND_SITE, KEY, 0.4) == pytest.approx(0.25)

    async def test_zero_means_never(self, site_set):
        """⚠⚠ Ноль — законное значение, а не «настройки нет».

        Человек выбрал в форме «никогда». Подмени мы его умолчанием — рассказ
        появлялся бы там, где его запретили, и молча.
        """
        await site_set(KEY, 0)

        assert await percent_share(STAND_SITE, KEY, 0.4) == 0.0

    async def test_a_hundred_means_always(self, site_set):
        await site_set(KEY, 100)

        assert await percent_share(STAND_SITE, KEY, 0.4) == pytest.approx(1.0)


class TestTheBoundsComeFromTheRegistry:
    """⚠⚠ То самое различие, что копии скрывали.

    Две копии клампили сами, третья спрашивала `site_seconds` — а он применяет
    `min`/`max` из реестра. Теперь спрашивает одна функция, и правило действует
    везде одинаково.
    """

    async def test_it_reads_through_site_seconds(self):
        """Проверяем **устройство**, а не только исход: клампинг руками дал бы
        тот же ответ на этих числах, но разошёлся бы с реестром на других."""
        import inspect

        code = inspect.getsource(percent_share)

        assert 'site_seconds' in code, 'границы реестра снова не применяются'

    async def test_an_over_hundred_value_is_capped(self, site_set):
        """⚠ 101% — не «много», а не существует: `random.random() < 1.5` это
        просто «всегда», то есть тихо выключенный жребий вместо отказа."""
        await site_set(KEY, 150)

        assert await percent_share(STAND_SITE, KEY, 0.4) <= 1.0


class TestNobodyKeepsAPrivateCopy:
    """⚠ Сторож от возврата: копия вернётся — разойдётся снова."""

    def test_no_module_defines_its_own(self):
        import pathlib

        for name in ('invite', 'mail_agent', 'favorite_mail'):
            text = pathlib.Path(f'src/dating/{name}.py').read_text(encoding='utf-8')

            assert 'async def _percent(' not in text, \
                f'{name}.py снова держит свою копию доли'

    def test_all_three_use_the_shared_one(self):
        import pathlib

        for name in ('invite', 'mail_agent', 'favorite_mail'):
            text = pathlib.Path(f'src/dating/{name}.py').read_text(encoding='utf-8')

            assert 'percent_share' in text, f'{name}.py не зовёт общую долю'


class TestTheDiceLivesHereToo:
    """⚠ Жребий рядом с чтением: порознь их и путали — `random.random() <`
    писалось руками в семи местах, и каждое было поводом ошибиться знаком."""

    async def test_zero_never_hits(self, site_set):
        await site_set(KEY, 0)

        assert not any([await percent_hit(STAND_SITE, KEY, 0.5)
                        for _ in range(20)])

    async def test_a_hundred_always_hits(self, site_set):
        await site_set(KEY, 100)

        assert all([await percent_hit(STAND_SITE, KEY, 0.5) for _ in range(20)])
