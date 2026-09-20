"""Статистика считает по своему счётчику, а не по рабочим таблицам.

## ⚠⚠ Что было сломано

Страница считала «сколько инвайтов ушло» прямо по таблице `invite`:
`SUM(status = 'sent')`. Пока та хранила всё, это работало. Как только у неё
появилась чистка по сроку (шесть часов), счёт обвалился — страница показывала
38 889 ушедших там, где за трое суток ушло 280 тысяч.

## ⚠ Правило общее, а не про инвайты

**Рабочая таблица** отвечает на «что сейчас в работе» и вправе забывать старое —
иначе растёт без предела. **Статистика** отвечает на «сколько сделано» и забывать
не вправе. Считать второе по первой значит привязать историю к сроку уборки.

⚠ «В пуле» при этом по-прежнему считается по таблице, и это верно: текущее
состояние — не накопленное.
"""

import uuid

import pytest

from mysql_.mysql_ import mysql_get_db_async

from src.dating.stat import (STAT_INVITE_SENT, STAT_KINDS, stat_add, stat_reset,
                             stat_sum)

pytestmark = pytest.mark.core

STAND_SITE = 999_343


@pytest.fixture
async def clean():
    """Свой сайт на тест: счётчик общий, и соседи мешали бы счёту."""
    site = 900_000 + int(uuid.uuid4().int % 90_000)

    yield site

    async with mysql_get_db_async() as db:
        await db.execute('DELETE FROM stat_day WHERE site_id = %s', (site,))
        await db.connection.commit()


class TestItCounts:
    """Сложение событий."""

    async def test_one_event(self, clean):
        await stat_add(clean, 1, STAT_INVITE_SENT)

        assert await stat_sum(clean, STAT_INVITE_SENT) == 1

    async def test_events_add_up(self, clean):
        """⚠⚠ Складываются, а не плодят строки: на этом держится вся затея."""
        for _ in range(5):
            await stat_add(clean, 1, STAT_INVITE_SENT)

        assert await stat_sum(clean, STAT_INVITE_SENT) == 5

        async with mysql_get_db_async() as db:
            await db.execute('SELECT COUNT(*) AS n FROM stat_day'
                             ' WHERE site_id = %s', (clean,))
            row = await db.fetchone()

        assert int(row['n']) == 1, 'счётчик плодит строки вместо сложения'

    async def test_a_batch_adds_at_once(self, clean):
        await stat_add(clean, 1, STAT_INVITE_SENT, n=7)

        assert await stat_sum(clean, STAT_INVITE_SENT) == 7

    async def test_profiles_are_apart(self, clean):
        await stat_add(clean, 1, STAT_INVITE_SENT, n=3)
        await stat_add(clean, 2, STAT_INVITE_SENT, n=4)

        assert await stat_sum(clean, STAT_INVITE_SENT, profile_id=1) == 3
        assert await stat_sum(clean, STAT_INVITE_SENT, profile_id=2) == 4
        assert await stat_sum(clean, STAT_INVITE_SENT) == 7, 'по сайту — сумма'

    async def test_site_wide_events_do_not_multiply(self, clean):
        """⚠⚠ Ноль вместо `NULL`, и вот зачем.

        В ключе `NULL` не равен `NULL`, поэтому `ON DUPLICATE KEY UPDATE` не
        сработал бы для строк «по сайту целиком» — счётчик плодил бы по строке на
        событие вместо того, чтобы складывать.
        """
        for _ in range(4):
            await stat_add(clean, 0, STAT_INVITE_SENT)

        async with mysql_get_db_async() as db:
            await db.execute('SELECT COUNT(*) AS rows_, SUM(n) AS total'
                             ' FROM stat_day WHERE site_id = %s', (clean,))
            row = await db.fetchone()

        assert int(row['rows_']) == 1
        assert int(row['total']) == 4

    async def test_kinds_are_apart(self, clean):
        from src.dating.stat import STAT_CHAT_OUT

        await stat_add(clean, 1, STAT_INVITE_SENT, n=2)
        await stat_add(clean, 1, STAT_CHAT_OUT, n=9)

        assert await stat_sum(clean, STAT_INVITE_SENT) == 2
        assert await stat_sum(clean, STAT_CHAT_OUT) == 9


class TestItRefusesNonsense:
    """⚠ Счётчик не роняет работу и не принимает мусор."""

    async def test_an_unknown_kind_is_ignored(self, clean):
        """Опечатка не должна заводить третий вид того же события."""
        await stat_add(clean, 1, 'выдуманное_событие')

        async with mysql_get_db_async() as db:
            await db.execute('SELECT COUNT(*) AS n FROM stat_day'
                             ' WHERE site_id = %s', (clean,))
            row = await db.fetchone()

        assert int(row['n']) == 0

    async def test_no_site_no_row(self, clean):
        await stat_add(0, 1, STAT_INVITE_SENT)

        assert await stat_sum(0, STAT_INVITE_SENT) == 0

    async def test_zero_count_is_ignored(self, clean):
        await stat_add(clean, 1, STAT_INVITE_SENT, n=0)

        assert await stat_sum(clean, STAT_INVITE_SENT) == 0


class TestReset:
    """⚠ Обнуление — действием человека, а не само."""

    async def test_it_clears_the_site(self, clean):
        await stat_add(clean, 1, STAT_INVITE_SENT, n=5)

        gone = await stat_reset(clean)

        assert gone == 1
        assert await stat_sum(clean, STAT_INVITE_SENT) == 0

    async def test_it_can_clear_one_kind(self, clean):
        from src.dating.stat import STAT_CHAT_OUT

        await stat_add(clean, 1, STAT_INVITE_SENT, n=5)
        await stat_add(clean, 1, STAT_CHAT_OUT, n=3)

        await stat_reset(clean, STAT_INVITE_SENT)

        assert await stat_sum(clean, STAT_INVITE_SENT) == 0
        assert await stat_sum(clean, STAT_CHAT_OUT) == 3, 'снесли лишнее'

    async def test_rows_are_deleted_not_zeroed(self, clean):
        """⚠ Нулевая строка означала бы «в этот день ничего не было», и отчёт за
        месяц честно показал бы её в ряду суток. Отсутствие строки означает «не
        считали», и это правда."""
        await stat_add(clean, 1, STAT_INVITE_SENT, n=5)
        await stat_reset(clean)

        async with mysql_get_db_async() as db:
            await db.execute('SELECT COUNT(*) AS n FROM stat_day'
                             ' WHERE site_id = %s', (clean,))
            row = await db.fetchone()

        assert int(row['n']) == 0


def _body(name: str) -> str:
    """Код функции без докстринга.

    ⚠ Проверять по всему тексту нельзя: объяснение «почему ушли от `SUM(status =
    'sent')`» содержит ровно ту строку, которую ищем как признак старого кода.
    """
    import ast
    import inspect

    import src.uvicorn.routers.dating.stats as mod

    tree = ast.parse(inspect.getsource(mod))
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) \
                and node.name == name:
            body = node.body[1:] if ast.get_docstring(node) else node.body

            return '\n'.join(ast.unparse(one) for one in body)

    return ''


class TestTheStatsPageUsesIt:
    """⚠⚠ Страница читает счётчик, а не таблицу инвайтов."""

    def test_the_section_reads_the_counter(self):
        code = _body('_invite')

        assert 'stat_sum' in code, 'ушедшее снова считается по таблице'
        # ⚠ Смотрим **код без докстринга**: там та же строка стоит в объяснении,
        # почему от неё ушли, и поиск по всему тексту нашёл бы именно её.
        assert "SUM(status = 'sent'" not in code

    def test_the_pool_is_still_from_the_table(self):
        """⚠ «Сколько сейчас ждёт отправки» — вопрос о текущем состоянии, а не о
        сделанном: счётчик тут соврал бы."""
        assert "status = 'draft'" in _body('_invite')

    async def test_the_reset_endpoint_works(self, auth_client, clean):
        await stat_add(clean, 1, STAT_INVITE_SENT, n=5)

        got = await auth_client.post(f'/api/dating/stats/reset?site_id={clean}')

        assert got.status_code == 200
        assert got.json()['result']['deleted'] == 1
        assert await stat_sum(clean, STAT_INVITE_SENT) == 0

    async def test_the_reset_needs_a_site(self, auth_client):
        """⚠ Без сайта — отказ, а не обнуление всего подряд."""
        got = await auth_client.post('/api/dating/stats/reset')

        assert got.status_code == 422


class TestSendingCounts:
    """⚠ Инвайт при отправке прибавляет счётчик."""

    def test_the_sender_counts(self):
        import inspect

        from src.dating.invite_send import invite_send_one

        code = inspect.getsource(invite_send_one)

        assert 'stat_add' in code, 'отправка больше не считается'
        assert 'STAT_INVITE_SENT' in code


class TestKindsAreClosed:
    """⚠ Список видов закрытый: опечатка не заведёт четвёртый вид того же."""

    def test_all_kinds_are_listed(self):
        assert STAT_INVITE_SENT in STAT_KINDS
        assert len(STAT_KINDS) == len(set(STAT_KINDS))
