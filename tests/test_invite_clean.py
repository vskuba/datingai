"""Старые инвайты уходят из базы по сроку сайта.

На проде таблица инвайтов доросла до восьми тысяч строк и продолжала расти с
каждым кругом: черновики, отправленное, неудачи — всё оставалось навсегда.
Настройка `invite_clean_hours` убирает то, что старше срока.

## ⚠⚠ Цена названа прямо, и платит её человек, а не мы

Срок короче суток **укорачивает паузу повтора и дневной потолок**: оба считаются
по этой же таблице (`invite._invited`) — пауза смотрит `MAX(sent_at)`, потолок
складывает строки за скользящие сутки. Стёртая строка поднимает их молча.

Поставив «старше 2 часов» при паузе «раз в сутки», человек получит паузу в два
часа. Это написано в самой настройке; наше дело — не решать за него тихо, а
показать выбор. Отсюда и умолчание «не удалять»: настройка, сама начинающая
стирать данные, была бы худшим из сюрпризов.

⚠ Соседняя `invite_keep` живёт по другому правилу — она **никогда** не трогает
строки за последние сутки (`test_invite_keep.py`). Правила разные потому, что
разные вопросы: та подрезает историю пары ради читаемости, эта отвечает за размер
таблицы, и человек включает её осознанно.
"""


import pytest

from mysql_.mysql_ import mysql_get_db_async

from src.dating import invite_round

pytestmark = pytest.mark.profile

STAND_SITE = 999_343


@pytest.fixture
async def hours():
    """Ставит срок чистки у сайта стенда и убирает за собой."""
    async def _set(value):
        async with mysql_get_db_async() as db:
            await db.execute(
                'UPDATE site SET settings = JSON_SET(COALESCE(settings, JSON_OBJECT()),'
                ' %s, %s) WHERE site_id = %s',
                ('$.invite_clean_hours', str(value), STAND_SITE))
            await db.connection.commit()

    yield _set

    async with mysql_get_db_async() as db:
        await db.execute('UPDATE site SET settings = JSON_REMOVE(settings, %s)'
                         ' WHERE site_id = %s',
                         ('$.invite_clean_hours', STAND_SITE))
        await db.connection.commit()


async def _row(profile_id: int, uid: str, hours_ago: int) -> int:
    """Кладёт инвайт заданной давности.

    ⚠ Время считает **база** (`NOW() - INTERVAL`), а не Python: MySQL живёт по
    UTC, и `datetime.now()` положил бы строку в будущее — на этом уже ловились.

    ⚠ `draft_uid` — столбец **вычисляемый**: его считает база из `uid` и статуса
    (уникальность черновика на пару). Подставлять его руками нельзя.
    """
    async with mysql_get_db_async() as db:
        await db.execute(
            'INSERT INTO invite (site_id, profile_id, uid, name, body,'
            '  status, created_at)'
            " VALUES (%s, %s, %s, '', 'текст', 'draft', NOW() - INTERVAL %s HOUR)",
            (STAND_SITE, int(profile_id), uid, int(hours_ago)))
        made = int(db.lastrowid)
        await db.connection.commit()

    return made


async def _alive(invite_id: int) -> bool:
    async with mysql_get_db_async() as db:
        await db.execute('SELECT id FROM invite WHERE id = %s', (int(invite_id),))

        return bool(await db.fetchone())


@pytest.fixture(autouse=True)
def now(monkeypatch):
    """Снимает отметку «чистили недавно»: она живёт в Redis и переживает тест."""
    monkeypatch.setattr(invite_round, 'round_slot_due',
                        lambda key, site_id, what, every=None: True)
    monkeypatch.setattr(invite_round, 'round_slot_mark', lambda *a: None)


class TestCleanByAge:
    """Что уходит и что остаётся."""

    async def test_older_than_the_window_is_gone(self, girl, hours):
        await hours(2)
        old = await _row(girl, 'u-old', 5)

        assert await invite_round._clean(STAND_SITE) >= 1
        assert not await _alive(old)

    async def test_younger_than_the_window_stays(self, girl, hours):
        await hours(12)
        fresh = await _row(girl, 'u-fresh', 3)

        await invite_round._clean(STAND_SITE)

        assert await _alive(fresh)

    async def test_zero_deletes_nothing(self, girl, hours):
        """⚠ Умолчание — «не удалять»: настройка, сама стирающая данные, была бы
        худшим из сюрпризов."""
        await hours(0)
        old = await _row(girl, 'u-keep', 99)

        assert await invite_round._clean(STAND_SITE) == 0
        assert await _alive(old)

    async def test_an_absent_setting_deletes_nothing(self, girl):
        """Настройки нет вовсе — поведение прежнее, ни одна строка не уходит."""
        old = await _row(girl, 'u-none', 99)

        assert await invite_round._clean(STAND_SITE) == 0
        assert await _alive(old)

    async def test_drafts_go_too(self, girl, hours):
        """⚠ Черновик старше срока — реплика тому, кто был в сети полсуток назад.

        Отправлять её уже некому, а место в пуле она держит.
        """
        await hours(2)
        old = await _row(girl, 'u-draft', 8)

        await invite_round._clean(STAND_SITE)

        assert not await _alive(old)


class TestTheRoundCleansOutsideShifts:
    """⚠ Уборка идёт **до** проверки смен, и это не оплошность.

    Строки копятся круглые сутки, а смены говорят только о том, когда набирать:
    чисти мы внутри смены, у сайта с ночным расписанием таблица росла бы весь
    день.
    """

    def test_clean_comes_before_the_shift_check(self):
        import inspect

        code = inspect.getsource(invite_round._gate)
        clean = code.index('await _clean(site_id)')
        shift = code.index('schedule_now(shifts, minutes)')

        assert clean < shift, 'уборка уехала внутрь смены'


class TestItHasItsOwnPace:
    """Свой срок, а не срок набора: таблица растёт и когда набор молчит."""

    def test_the_mark_is_set_even_when_switched_off(self):
        """⚠ Отметку ставим и при выключенной настройке.

        Иначе чтение настройки повторялось бы каждый тик — шестьдесят раз за те
        же десять минут, и всё впустую.
        """
        import inspect

        code = inspect.getsource(invite_round._clean)
        mark = code.index('round_slot_mark(INVITE_ROUND_CLEAN_KEY')
        off = code.index('if hours <= 0:')

        assert mark < off, 'отметка ставится после выхода по выключенной настройке'
