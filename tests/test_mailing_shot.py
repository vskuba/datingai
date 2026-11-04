"""Снимок у письма рассылки: кладётся при сборке, виден в карточке.

## ⚠⚠⚠ Что здесь стережётся

Рассылка была единственным пулом писем без фотографий. Решением владельца
снимок прикладывается к **каждому** её письму, если у анкеты есть
неотправленный этой паре кадр; нет свободного — письмо уходит текстом.

⚠⚠ **Жребия здесь нет**, и это отличие от ответов с приставанием. Там долю
бросает шаг `dice` у соседа и кладёт `img_attach` в `extra` прогона — письмо
сочиняется на пару, и вызов агента всё равно идёт. Рассылка собирается одним
шаблоном на всех, агента на письмо не зовёт, и второе место с долей
разошлось бы с первым молча.

⚠⚠ **Прежний довод против снимков у рассылки записан в коде и отпал.** Он
звучал так: «её письмо уходит полусотне людей одним текстом, и снимок к нему —
один и тот же кадр, разосланный веером». Верно ровно тогда, когда снимок
выбирается к **шаблону**. Здесь он выбирается **на пару**, той же памятью, что
у прочих пулов, — веера не выходит.
"""

from pathlib import Path

import pytest

pytestmark = pytest.mark.mail

PAGE_FILE = Path('data/uvicorn/template/admin/dating/profile.html')


@pytest.fixture(scope='module')
def page() -> str:
    return PAGE_FILE.read_text(encoding='utf-8')


class TestTheLetterCanCarryAShot:
    """⚠ Рассылка — полноправный пул со снимком, как три соседних."""

    def test_the_pool_is_in_the_list(self):
        from src.dating.mail_send import MAIL_SEND_SHOT_TABLES

        assert 'mailing' in MAIL_SEND_SHOT_TABLES, (
            'рассылка не считается пулом со снимком — круг отправки не '
            'выберет колонку, и кадр не доедет до письма молча')

    async def test_the_column_exists(self):
        from mysql_.mysql_ import mysql_get_db_async

        async with mysql_get_db_async() as db:
            await db.execute(
                'SELECT COUNT(*) AS n FROM information_schema.COLUMNS'
                ' WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s'
                '   AND COLUMN_NAME = %s', ('mailing', 'gallery_file_id'))
            got = int(((await db.fetchone()) or {}).get('n') or 0)

        assert got == 1, 'колонки снимка у рассылки нет'


class TestTheShotIsPickedPerPair:
    """⚠⚠⚠ Снимок выбирается **получателю**, а не шаблону.

    На этом и держится отмена прежнего довода («веер»): память общая со всеми
    прочими письмами — пара «сайт + человек», — и отправленный кадр больше не
    уйдёт ему ни от этой анкеты, ни другим занятием.
    """

    def test_it_asks_the_shared_memory(self):
        import inspect

        from src.dating import mailing

        body = inspect.getsource(mailing._shot)

        assert 'companion_gallery_fresh' in body, (
            'рассылка завела свой счёт показанного — один кадр уйдёт '
            'человеку дважды, разными занятиями')
        assert 'uid' in body, 'снимок выбирается не на получателя'

    def test_it_takes_the_public_folder(self):
        """⚠ Интимные уходят только по обещанию агента, где о них просили."""
        import inspect

        from src.dating import mailing
        from src.dating.mail_shot import MAIL_SHOT_GROUP

        assert MAIL_SHOT_GROUP == 'public'
        assert 'MAIL_SHOT_GROUP' in inspect.getsource(mailing._shot), (
            'папка задана своей строкой — разойдётся с прочими письмами')

    def test_no_dice_is_thrown(self):
        """⚠⚠ Доли здесь нет: снимок кладётся каждому письму, у кого он есть."""
        from tests_.tests_guard import tests_guard_code

        from src.dating import mailing

        # ⚠ Смотрим **код**, а не исходник: в докстрингах жребий упомянут
        # нарочно — там объяснено, почему его здесь нет. Чистит общий сторож
        # слоя, а не свой разбор: `ast` сам по себе докстринг не отбрасывает —
        # он такая же строковая константа.
        text = (tests_guard_code(mailing._shot)
                + tests_guard_code(mailing._put)).lower()

        for word in ('dice', 'percent', 'random', 'img_attach'):
            assert word not in text, (
                f'у рассылки завёлся жребий ({word}) — это второе место с '
                f'долей, оно разойдётся с первым')

    async def test_no_free_shot_is_not_an_error(self, girl):
        """⚠⚠ Нет галереи — письмо уходит текстом, а не падает.

        Снимка может не быть по трём причинам: галереи нет вовсе, всё уже
        показано, Диск не обошли. Ни одна не повод отказать письму.
        """
        from src.dating.mailing import _shot

        assert await _shot(int(girl), 999_343, 'nobody-here') == 0


class TestTheCardShowsIt:
    """⚠ Человек видит, что именно уйдёт, не открывая письмо."""

    def test_the_pool_query_selects_the_shot(self):
        import inspect

        import src.uvicorn.routers.dating.mailing as mod

        body = inspect.getsource(mod)

        assert 'mail_shot_join' in body, (
            'ручка пула не отдаёт кадр — карточка покажет письмо без '
            'фотографии, хотя она уйдёт')

    def test_the_card_draws_the_thumb(self, page):
        assert 'mailShotThumb(row)' in page, 'карточка не рисует снимок'
        assert 'function mailShotThumb' in page, 'выкройки нет'

    def test_no_shot_means_no_node(self, page):
        """⚠⚠ Нет кадра — узла нет вовсе, а не пустой квадрат.

        Письма без фотографии законны, и у большинства карточек рамка
        читалась бы как «кадр не загрузился».
        """
        box = page[page.index('function mailShotThumb'):]
        box = box[:box.index('// ⚠ Снимок **готового письма**')]

        assert "if (!file) return '';" in box, (
            'у письма без снимка рисуется пустая рамка')

    def test_it_asks_the_file_id_not_the_row(self, page):
        """⚠⚠ Превью отдаётся по номеру файла **на Диске**, а не строки.

        Спутать легко — оба числа, — и ошибка видна лишь пустым квадратом.
        """
        box = page[page.index('function mailShotThumb'):]
        box = box[:box.index('// ⚠ Снимок **готового письма**')]

        assert 'gallery_file' in box
        assert 'gallery_file_id' not in box, (
            'в адрес превью уехал номер строки индекса вместо номера файла')

    def test_the_thumb_has_its_look(self, page):
        assert '.mail-card-thumb {' in page, 'у снимка нет своего вида'
