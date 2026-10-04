"""Журнал анкет чистится по сроку сайта.

## ⚠⚠ Зачем

Журнал растёт **вслед за инвайтами**: каждая отправка пишет строку, и при
девяноста тысячах инвайтов в сутки выходит около ста двенадцати тысяч строк —
три миллиона в месяц, под три гигабайта в год.

Ротации у него не было вовсе. Единственное удаление в модуле схлопывает подряд
идущие повторы, а по возрасту не убиралось ничего: на проде лежали записи
месячной давности.

⚠ **Срок у сайта, а таблица общая.** В `log` сайта нет, есть анкета; сайт узнаётся
через неё. Поэтому чистим посайтно — у каждого свой срок.
"""

import uuid

import pytest

from mysql_.mysql_ import mysql_get_db_async

from src.dating.log import LOG_KEEP_SETTING, log_sweep
from src.dating.site_setting import SITE_KNOWN

pytestmark = pytest.mark.core

STAND_SITE = 999_343

# Умолчание срока, часами. ⚠⚠ Это **сверка с реестром, а не второй источник**:
# настоящее умолчание лежит у настройки сайта (`SITE_KNOWN['log_keep_hours']`), и
# `test_the_default_is_three_days` ниже сличает это число с ним. Разойдутся — падёт
# именно та проверка, а не «край срока» с непонятной причиной.
#
# ⚠ Прежде число держала своя константа в `log.py` — `LOG_KEEP_DEFAULT = 72`, — но
# чистка её не читала: срок она берёт из настройки. Копия умолчания в коде
# читается как действующая и расходится с реестром молча.
KEEP_HOURS = 72


@pytest.fixture
async def girl(auth_client):
    profile_id = int((await auth_client.post('/api/dating/profile', json={
        'site_id': STAND_SITE,
        'login': f'sweep-{uuid.uuid4().hex[:8]}@example.com',
        'password': 'x'})).json()['result'])

    yield profile_id

    async with mysql_get_db_async() as db:
        await db.execute('DELETE FROM log WHERE profile_id = %s', (profile_id,))
        await db.execute('DELETE FROM profile WHERE id = %s', (profile_id,))
        await db.connection.commit()


async def _line(profile_id: int, text: str, hours_ago: int = 0) -> int:
    """Строка журнала. ⚠ Время считает база: MySQL живёт по UTC."""
    async with mysql_get_db_async() as db:
        await db.execute(
            'INSERT INTO log (profile_id, level, text, created_at)'
            " VALUES (%s, 'info', %s, NOW() - INTERVAL %s HOUR)",
            (int(profile_id), text, int(hours_ago)))
        await db.connection.commit()

        return int(db.lastrowid)


async def _count(profile_id: int) -> int:
    async with mysql_get_db_async() as db:
        await db.execute('SELECT COUNT(*) AS n FROM log WHERE profile_id = %s',
                         (int(profile_id),))

        return int(((await db.fetchone()) or {}).get('n') or 0)


class TestItSweepsByAge:
    """Старое уходит, свежее остаётся."""

    async def test_old_lines_go(self, girl):
        await _line(girl, 'позавчерашняя', hours_ago=100)

        await log_sweep()

        assert await _count(girl) == 0

    async def test_fresh_lines_stay(self, girl):
        """⚠ Разбирают журнал задним числом: «что было в пятницу» — живой вопрос
        в понедельник."""
        await _line(girl, 'вчерашняя', hours_ago=20)

        await log_sweep()

        assert await _count(girl) == 1

    async def test_the_edge_is_the_setting(self, girl):
        """Умолчание — трое суток; час до границы живёт, час после уходит."""
        await _line(girl, 'внутри срока', hours_ago=KEEP_HOURS - 1)
        await _line(girl, 'за сроком', hours_ago=KEEP_HOURS + 1)

        await log_sweep()

        async with mysql_get_db_async() as db:
            await db.execute('SELECT text FROM log WHERE profile_id = %s', (girl,))
            rows = await db.fetchall() or []

        assert [row['text'] for row in rows] == ['внутри срока']


class TestZeroMeansKeep:
    """⚠⚠ Ноль — «не чистить», а не «стереть всё»."""

    async def test_zero_keeps_everything(self, girl, monkeypatch):
        """Подставь мы ноль в `INTERVAL`, условие стало бы «создано в будущем», и
        уборка снесла бы **весь** журнал — ровно наоборот написанному в подсказке.
        """
        import src.dating.log as log_mod

        async def _zero(site_id, key):
            return 0 if key == LOG_KEEP_SETTING else 0

        monkeypatch.setattr('src.dating.log.site_seconds', _zero)

        await _line(girl, 'древняя', hours_ago=5000)
        await log_sweep()

        assert await _count(girl) == 1, 'ноль стёр журнал вместо того, чтобы беречь'


class TestTheSetting:
    """Настройка сайта."""

    async def test_it_is_registered(self, auth_client):
        got = (await auth_client.get('/api/dating/site')).json()['result']
        known = {row['key']: row for row in got['known']}
        one = known['log_keep_hours']

        assert one['title'] == 'Логи в базе данных хранить, в часах'
        assert one['default'] == str(KEEP_HOURS)
        assert one['kind'] == 'number'
        assert one['min'] == 0, 'ноль обязан быть допустим — это «не чистить»'

    def test_the_default_is_three_days(self):
        """⚠ Умолчание спрашиваем у **реестра настроек** — единственного места, где
        оно есть. Сверка держит и `KEEP_HOURS` выше: поменяй реестр, не поправив
        число, — упадёт здесь, с прямой причиной."""
        assert SITE_KNOWN['log_keep_hours']['default'] == '72'
        assert KEEP_HOURS == 72


class TestTheRoundRunsIt:
    """⚠ Уборка висит на существующем круге, а не на новом."""

    def test_the_round_sweeps_both_journals(self):
        import inspect

        from src.dating.request_round import request_round_run

        code = inspect.getsource(request_round_run)

        assert 'log_sweep()' in code, 'журнал анкет никто не чистит'
        assert 'request_sweep()' in code, 'журнал запросов перестал чиститься'

    async def test_it_reports_both(self, girl):
        """Ответ круга различает журналы: по нему видно, что именно убрано."""
        import time

        import src.dating.request_round as round_mod

        # ⚠ **Просто снимаем отметку, а не подставляем «час назад».**
        #
        # Темп круга держит `round_mem_*` общего слоя, и «не ходили ещё ни
        # разу» он читает как отсутствие ключа — это и есть пора. Прежде отметка
        # была числом по `time.monotonic()`, и приходилось считать «час назад»:
        # ноль там означал не «никогда», а **момент загрузки машины**, то есть
        # на свежей машине тест краснел, а на рабочей зеленел. Зависимость от
        # аптайма — худший вид плавающего теста.
        round_mod._swept_at.pop(round_mod.REQUEST_ROUND_MEM_KEY, None)
        await _line(girl, 'древняя', hours_ago=5000)

        got = await round_mod.request_round_run()

        assert got['ran'] is True
        assert got['logs'] >= 1
        assert 'requests' in got


class TestItIsIndexed:
    """⚠ Без индекса по времени удаление шло бы полным сканом."""

    async def test_the_index_exists(self):
        async with mysql_get_db_async() as db:
            await db.execute("SHOW INDEX FROM log WHERE Key_name = 'idx_log_when'")
            rows = await db.fetchall() or []

        assert rows, 'индекс по времени пропал — уборка ляжет на таблицу'
        assert rows[0]['Column_name'] == 'created_at'
