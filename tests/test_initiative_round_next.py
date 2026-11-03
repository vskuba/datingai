"""Срок круга в шапке пула инициативы: когда он вернётся к сайту.

## ⚠⚠⚠ Почему этот ответ может дать только сервер

Круг крутится в **воркере**, а страницу обслуживает uvicorn — другой процесс.
Темп кругов живёт в памяти того, кто их гоняет (`round_mem`), и панель его не
видит вовсе: своей арифметикой она бы врала.

У инициативы вдобавок **нет слота** — её тик и есть её срок, — поэтому
отметка заведена отдельно и только для показа. Имя у неё `seen`, а не
`round`: увидев слово «слот», следующий читатель решил бы, что круг чем-то
придержан, и пошёл бы искать настройку.

## ⚠⚠ Замерший счётчик — признак умершего круга

Отметка ставится **первой строкой** захода, до всяких проверок и флагов.
Значит «счётчик не двигается» читается однозначно: круг не приходил. Прежде
упавший круг выглядел живым, и это уже стоило часов простоя (`round.md`).
"""

from pathlib import Path

import pytest

pytestmark = pytest.mark.page

PAGE_FILE = Path('data/uvicorn/template/admin/dating/profile.html')
BASE_FILE = Path('data/uvicorn/template/admin/dating/profile/site/_base.html')


@pytest.fixture(scope='module')
def page() -> str:
    return PAGE_FILE.read_text(encoding='utf-8')


@pytest.fixture(scope='module')
def base() -> str:
    return BASE_FILE.read_text(encoding='utf-8')


class TestTheServerCountsIt:
    """⚠⚠ Считает сервер — у страницы нет и не может быть этих данных."""

    def test_the_door_exists(self):
        from src.dating.initiative_round import initiative_round_next

        assert callable(initiative_round_next)

    def test_it_is_asked_through_our_layer_door(self):
        """⚠ К слою — только через `src/dating/round.py`: прямой импорт уводит
        строки журнала в безымянный `[round]`."""
        import inspect

        from src.dating import initiative_round

        body = inspect.getsource(initiative_round)

        assert 'from src.dating.round import' in body
        assert 'from round_.round_slot import' not in body, (
            'круг пошёл к слою мимо нашей двери')

    def test_the_mark_is_set_before_anything_else(self):
        """⚠⚠⚠ Отметка — первой строкой захода, до флагов и проверок.

        Поставь её по делу — и у сайта с выключенным набором счётчик замирал
        бы при живом круге, то есть врал бы ровно в ту сторону, ради которой
        заведён.
        """
        import inspect

        from src.dating import initiative_round

        body = inspect.getsource(initiative_round._work)
        head = body[:body.index('stale = await _stale_clean')]

        assert 'round_slot_mark' in head, (
            'отметка захода ставится после работы — упавший круг будет '
            'выглядеть живым')

    def test_the_mark_is_not_a_slot(self):
        """⚠⚠ Круг ничем не придержан, и отметку он **не спрашивает**.

        Спроси он её — тик перестал бы быть сроком, и занятие молча сменило
        бы темп.
        """
        import inspect

        from src.dating import initiative_round

        body = inspect.getsource(initiative_round)

        assert 'round_slot_due' not in body, (
            'круг стал спрашивать отметку — он больше не ходит каждым тиком')
        assert 'seen' in initiative_round.INITIATIVE_SEEN_KEY, (
            'ключ назван слотом — следующий читатель пойдёт искать настройку')


class TestItRidesWithTheList:
    """⚠ Срок приезжает **вместе со списком**: два запроса дали бы два момента
    времени, и счёт спорил бы со сроком в одном взгляде."""

    def test_the_handle_answers_with_it(self):
        import inspect

        import src.uvicorn.routers.dating.chat_initiative as mod

        body = inspect.getsource(mod.chat_initiative_list)

        assert "'next_sec'" in body, 'ручка списка не отдаёт срок круга'
        assert 'initiative_round_next' in body

    def test_the_page_reads_it_from_the_same_answer(self, page):
        assert 'next_sec' in page, 'страница не читает срок из ответа списка'
        assert 'initiative-pool-next' in page, 'сроку негде показаться'

    def test_the_markup_has_a_place_for_it(self, base):
        assert 'data-slot="initiative-pool-next"' in base


class TestTheTextIsOnePattern:
    """⚠⚠ Слова у всех пулов общие, и это не косметика.

    «через 90 минут» у одного и «через 1 ч 30 мин» у другого читались бы как
    разные механизмы. Выкройка уже есть — своя копия разошлась бы с ней.
    """

    def test_it_uses_the_shared_one(self, page):
        box = page[page.index("fillSlot('initiative-pool-next'"):]
        box = box[:box.index(');') + 2]

        assert 'roundNextText(' in box, 'срок рисуется своей копией выкройки'

    def test_the_pattern_is_still_single(self, page):
        assert page.count('Math.floor(mins / 60)') == 1, (
            'выкройка срока размножилась копиями')

    def test_the_verb_fits_this_round(self, page):
        """⚠ «Наберёт» — круг инициативы набирает пул сам.

        У намерений стоит «соберёт», потому что их пул наполняет прогон
        агента, а круг лишь собирает письма по готовому. Поставь здесь чужой
        глагол — он обещал бы чужую работу.
        """
        box = page[page.index("fillSlot('initiative-pool-next'"):]
        box = box[:box.index(');') + 2]

        assert 'соберёт' not in box, 'взят глагол круга намерений'


class TestTheCountdownIsNotASecondHeading:
    """⚠ Шапка колонки задаёт `uppercase` и жирность всему внутри.

    Без сброса срок выходил бы вторым заголовком рядом с «В ПУЛЕ: 0».
    """

    def test_it_sheds_the_heading_look(self, page):
        spot = page.find('[data-slot="initiative-pool-next"]')

        assert spot > 0, 'у срока нет своего вида — возьмёт вид заголовка'

        rule = page[spot:page.find('}', spot)]

        assert 'text-transform: none' in rule and 'font-weight: 400' in rule, (
            f'срок остался заголовком: {rule.strip()}')

    def test_it_shares_the_look_with_the_neighbour(self, page):
        """⚠ Один вид на оба пула: два правила разошлись бы при первой правке."""
        spot = page.find('[data-slot="initiative-pool-next"]')
        head = page[max(0, spot - 60):spot]

        assert 'intention-pool-next' in head, (
            'у соседнего пула завёлся свой вид срока')
