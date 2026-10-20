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

from src.dating.percent import percent_share
from src.dating.site_setting import SITE_KNOWN

pytestmark = pytest.mark.mail

STAND_SITE = 999_343
KEY = 'mailing_question_percent'


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
        """⚠ Ключ в реестре — молчит реестр, а не fallback звонящего.

        Ключ выехал из реестра (`mailing_story_percent`) — вот тогда и только
        тогда заговорит довод звонящего: реестру сказать нечего.
        """
        await site_set(KEY, None)

        assert await percent_share(STAND_SITE, KEY, 0.4) == pytest.approx(
            int(SITE_KNOWN[KEY]['default']) / 100)

        await site_set('mailing_story_percent', None)

        assert await percent_share(STAND_SITE, 'mailing_story_percent',
                                   0.4) == pytest.approx(0.4)

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
        """⚠ Список шире, чем у соседней проверки, и намеренно.

        Там — кто долю читает сегодня; здесь — кому **нельзя заводить свою**,
        и `favorite_mail` остаётся в списке, хотя сам больше не читает: копия
        вернётся именно туда, откуда уехала.
        """
        import pathlib

        for name in ('invite', 'mail_agent', 'answer', 'favorite_mail'):
            text = pathlib.Path(f'src/dating/{name}.py').read_text(encoding='utf-8')

            assert 'async def _percent(' not in text, \
                f'{name}.py снова держит свою копию доли'

    def test_all_three_use_the_shared_one(self):
        """⚠ Проверяются те, кто долю **читает сегодня**, и обе её выдачи.

        Список был `invite`, `mail_agent`, `favorite_mail`, и третий в нём
        устарел: жребий приставания переехал к соседу, а доли ему отдаёт
        `answer._dice_percents` — через `percent_value`, вторую выдачу того же
        общего модуля. В `favorite_mail.py` от этого остался **импорт без
        единого вызова**, и сторож держал его живым: убери импорт — краснеет,
        хотя правило («своей копии доли нет») соблюдено.

        ⚠ Поэтому проверяется не имя `percent_share`, а обращение к общему
        модулю любой из двух дверей. Само правило от этого не слабеет: копию
        ловит соседний `test_no_module_defines_its_own`, и он смотрит ровно на
        то, что запрещено.
        """
        import pathlib

        for name in ('invite', 'mail_agent', 'answer'):
            text = pathlib.Path(f'src/dating/{name}.py').read_text(encoding='utf-8')

            assert 'percent_share' in text or 'percent_value' in text, \
                f'{name}.py не зовёт общую долю'


class TestTheEdgesMeanWhatTheySay:
    """⚠⚠ Края доли значат «никогда» и «всегда» — и это проверяется прямо.

    Прежде здесь стояла пара тестов на `percent_hit`: двадцать бросков при доле 0 и
    при доле 100. Функции больше нет — жребий переехал к соседу (шаг `dice`), и её
    не позвал никто ни разу. Но **проверяемое ею правило осталось**, и оно про
    чтение: ноль обязан дойти нулём, а сотня — единицей.

    ⚠ Без этих двух проверок незамеченным прошло бы, например, `max(0.01, …)` —
    и «никогда» превратилось бы в «иногда». На жребии это ловилось двадцатью
    бросками; на доле ловится одним сравнением, и точнее.
    """

    async def test_zero_reads_as_never(self, site_set):
        await site_set(KEY, 0)

        assert await percent_share(STAND_SITE, KEY, 0.5) == 0.0

    async def test_a_hundred_reads_as_always(self, site_set):
        await site_set(KEY, 100)

        assert await percent_share(STAND_SITE, KEY, 0.5) == 1.0
