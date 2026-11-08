"""Лента «Избранные сайта»: сумма звёздочек всех анкет, без похода наружу.

## ⚠⚠ Что здесь поменялось и почему

Лента была отбором из «Все онлайн» по отметке `favorite` — то есть показывала
избранных, **которые сейчас в сети**. Пока никто из них не в сети, она пуста, и
на вопрос «кто у нас вообще в избранных» панель не отвечала вовсе: 08.11.2026 у
Step2Love три звёздочки на три анкеты, в ленте ноль, и это читается как
поломка.

Теперь лента показывает всех избранных сайта — сумму по его анкетам, из нашей
базы (`favorite` плюс карточки `companion`). Ни входа, ни снимка, ни «ни одна
анкета не в сети».

## ⚠⚠ Прежнее правило цело: источник «кто в сети» один

Отбор заводили ровно затем, чтобы не завести **второй список онлайна**: два
снимка разъезжаются во времени, и человек видел бы в одном виджете зелёного, а
в другом того же серым. Этого не случилось и теперь — точку ставит тот же
снимок ленты (`people._online_now`), изменилось лишь то, что принадлежность
ленте больше не значит «в сети». Теперь это говорит точка, и потому лента
**ушла** из `ONLINE_STRIPS`: её карточки обязан красить `stripDotsPaint`.

## ⚠ Знание «кто избранный» — в одном месте

`favorite.favorite_site_uids`. Спрашивают его из разных углов, и своя копия
запроса в каждом расходится с первой правкой — так уже случилось с долями
процентов, с правилом близнеца и с датой в журнале доставки.

## ⚠ Избранный у сайта, а не у открытой девушки

Лента отвечает на «с кем вообще работаем», а работать может любая анкета
сайта: человек, избранный у Дианы, для Елены такой же собеседник. Своя лента
«My favorites» отвечает на другой вопрос и встаёт на то же место, когда анкета
выбрана.
"""

import inspect
import uuid

import pytest

from mysql_.mysql_ import mysql_get_db_async

from src.dating.favorite import favorite_site_uids

pytestmark = pytest.mark.page

STAND_SITE = 999_343


@pytest.fixture
async def stars(auth_client):
    """Две анкеты сайта и звёздочки у них: удавшиеся и сорвавшаяся."""
    made = []
    for tag in ('fav-a', 'fav-b'):
        one = (await auth_client.post('/api/dating/profile', json={
            'site_id': STAND_SITE,
            'login': f'{tag}-{uuid.uuid4().hex[:8]}@example.com',
            'password': 'x'})).json()['result']
        made.append(int(one))

    uids = {'her': f'u{uuid.uuid4().hex[:8]}',     # избранный первой
            'his': f'u{uuid.uuid4().hex[:8]}',     # избранный второй
            'bad': f'u{uuid.uuid4().hex[:8]}',     # звёздочку нажать не вышло
            'none': f'u{uuid.uuid4().hex[:8]}'}    # просто прохожий

    async with mysql_get_db_async() as db:
        await db.execute(
            'INSERT INTO favorite (site_id, profile_id, uid, state)'
            ' VALUES (%s, %s, %s, %s), (%s, %s, %s, %s), (%s, %s, %s, %s)',
            (STAND_SITE, made[0], uids['her'], 'done',
             STAND_SITE, made[1], uids['his'], 'already',
             STAND_SITE, made[0], uids['bad'], 'fail'))
        await db.connection.commit()

    yield made, uids

    async with mysql_get_db_async() as db:
        for one in made:
            await db.execute('DELETE FROM favorite WHERE profile_id = %s', (one,))
        await db.connection.commit()

    for one in made:
        await auth_client.delete(f'/api/dating/profile/{one}')


class TestWhoCountsAsAFavorite:
    """⚠ Кто попадает в набор, а кто нет."""

    async def test_favorites_of_every_girl_are_there(self, stars):
        """⚠⚠ По сайту, а не по паре: избранный у одной девушки для ленты такой
        же избранный, как избранный у другой."""
        _, uids = stars

        got = await favorite_site_uids(STAND_SITE)

        assert uids['her'] in got, 'избранный первой анкеты потерялся'
        assert uids['his'] in got, 'избранный второй анкеты потерялся'

    async def test_a_failed_star_is_not_a_favorite(self, stars):
        """⚠ `fail` означает, что нажать звёздочку не вышло — человек избранным
        не стал. Тем же набором состояний отбирает письма `favorite_mail._quiet`;
        разойдись перечисления, лента показывала бы тех, кому пул писать
        отказывается."""
        _, uids = stars

        assert uids['bad'] not in await favorite_site_uids(STAND_SITE)

    async def test_a_stranger_is_not_a_favorite(self, stars):
        _, uids = stars

        assert uids['none'] not in await favorite_site_uids(STAND_SITE)

    async def test_another_site_does_not_leak(self, stars):
        """⚠ Набор по **своему** сайту: чужие избранные в ленту не попадают."""
        _, uids = stars

        assert uids['her'] not in await favorite_site_uids(STAND_SITE + 1)

    async def test_no_site_gives_nothing(self):
        assert await favorite_site_uids(0) == set()


class TestThePageTakesItsOwnFeed:
    """⚠⚠ У ленты теперь свой список — но не свой онлайн."""

    def _page(self):
        return open('data/uvicorn/template/admin/dating/profile.html',
                    encoding='utf-8').read()

    def test_it_asks_the_site_wide_favorites(self):
        page = self._page()

        assert 'people/site/${site}/favorite' in page, \
            'лента больше не спрашивает избранных сайта'
        assert "stripPaint('favsite', FAVSITE_KEY" in page, \
            'лента рисуется не своим снимком'

    def test_it_left_the_online_strips(self):
        """⚠⚠ Главное отличие от прежнего устройства.

        Пока лента была отбором из онлайна, принадлежность ей значила «в сети»,
        и точки у её карточек красить было нечем и незачем. Теперь в ней есть
        и те, кого сейчас нет, — останься она в `ONLINE_STRIPS`, у всех
        стояла бы серая точка, и лента врала бы молча.
        """
        page = self._page()

        assert "ONLINE_STRIPS = ['online']" in page, \
            'лента снова считается частью онлайна — точки красить перестанут'
        assert 'ONLINE_STRIPS.includes(row.dataset.strip)' in page, \
            'пропал пропуск самой ленты онлайна у точек'

    def test_the_dot_still_comes_from_one_snapshot(self):
        """⚠ Второго источника «кто в сети» не завелось: сервер ставит точку
        тем же снимком ленты, что и везде."""
        import src.uvicorn.routers.dating.people as mod

        code = inspect.getsource(mod._favorite_site_feed)

        assert '_online_now(' in code

    def test_the_knowledge_is_asked_once(self):
        """⚠ Кто избранный — знает одна функция, и зовут её одним запросом на
        ответ, а не на карточку: избранных бывает полторы сотни."""
        import src.uvicorn.routers.dating.people as mod

        code = inspect.getsource(mod._favorite_site_feed)

        assert code.count('favorite_site_uids(') == 1

    def test_the_profile_loader_skips_it(self):
        """⚠⚠⚠ Общий перебор видов обязан пропускать ленты про сайт.

        Он грузит списки **выбранной анкеты** (`/people/{id}/{вид}`), а такого
        списка по анкете не бывает: сервер отвечает `422`, отказ ложится в
        состояние ленты, которую никто не рисует, и в консоли остаётся одна
        красная строка на каждую отрисовку. Ровно так и жила прежняя лента до
        08.11.2026 — `/people/45/favonline` отказывал на проде при исправной
        панели, и нашлось это кадром, а не тестом.

        ⚠ Список, а не `delete` по месту: третий забытый вид прошёл бы так же
        тихо, как второй.
        """
        page = self._page()

        assert "SITE_STRIPS = ['online', 'guests', 'favsite']" in page, \
            'лента про сайт выпала из списка пропускаемых'
        assert 'SITE_STRIPS.forEach(kind => kinds.delete(kind))' in page, \
            'перебор видов больше не пропускает ленты про сайт'

    def test_the_empty_text_is_its_own(self):
        """⚠ Пусто здесь значит «звёздочек не нажато ни одной», а не «никого
        нет в сети»: общий текст отправил бы человека проверять ленту."""
        page = self._page()

        assert "favsite: 'ни у одной анкеты сайта нет избранных'" in page

    def test_the_markup_uses_the_macro(self):
        """⚠ Тем же макросом, что и прочие ленты: копия разъехалась бы с ними в
        день первой правки высоты или стрелок."""
        page = open('data/uvicorn/template/admin/dating/profile/site/_base.html',
                    encoding='utf-8').read()

        assert "strip_row('favsite'" in page


class TestTheTwoStripsTakeTurns:
    """⚠⚠ Две ленты избранных стоят по очереди, а не рядом.

    «My favorites» — про открытую девушку, «Избранные сайта» — про весь сайт, и
    вместе они отвечают на один вопрос дважды. Выбрана анкета — говорит её
    список; не выбрана — общий.

    Показав оба разом, мы заставили бы человека гадать, чем они отличаются, и
    отдали бы две с половиной сотни пикселей высоты за ответ, который нужен один.
    """

    def _page(self):
        return open('data/uvicorn/template/admin/dating/profile.html',
                    encoding='utf-8').read()

    def test_the_general_strip_hides_when_a_girl_is_open(self):
        """⚠ Условие **обратное** тому, что у своей ленты: `!noGirl` против
        `noGirl`. Поставь одинаковое — ленты либо обе горели бы, либо обе гасли."""
        page = self._page()

        assert ("workEl.querySelectorAll('.strip-row[data-strip=\"favsite\"]')"
                ".forEach(\n            row => row.classList.toggle('off', !noGirl))"
                in page), 'общая лента больше не уступает место своей'

    def test_the_own_strip_hides_without_a_girl(self):
        page = self._page()

        assert ("workEl.querySelectorAll('.strip-row[data-strip=\"favorite\"]')"
                ".forEach(\n            row => row.classList.toggle('off', noGirl))"
                in page), 'своя лента больше не прячется без выбранной анкеты'

    def test_the_splitter_stays(self):
        """⚠ Разделитель один на обе ленты и виден всегда: какая бы ни стояла,
        под ней начинается переписка, и черта между занятиями нужна.

        Прежде он прятался вместе со своей лентой — теперь под ним всегда что-то
        есть, и пропадать ему незачем.
        """
        page = self._page()

        assert "split.classList.remove('off')" in page, \
            'разделитель снова прячется, хотя лента под ним осталась'

    def test_the_switch_runs_on_profile_change(self):
        """⚠ Переключение обязано случаться при **смене анкеты**, а не только при
        открытии вкладки: человек выбирает девушку, не уходя с «Приставания»."""
        page = self._page()

        at_paint = page.index('async function workPaint()')
        at_sub = page.index('favSubShow();', at_paint)

        assert at_sub > at_paint, 'смена анкеты не перерисовывает вкладку'


class TestTheActivationCheckboxBehavesLikeEverywhere:
    """⚠⚠ Галочка «генерация писем» — это и есть «активировать» расписание.

    Стоит она там же, где у рассылки и инвайтов, — в правом краю полосы
    подвкладок, с тем же классом `sched-on-box`. Своей второй галочки у смен
    нет: работает занятие или нет, отвечается один раз.

    Но вела она себя **не как везде**, и это чинится здесь. Переключение
    перерисовывало пул и не трогало ленту расписания: включённые смены
    оставались серыми до переоткрытия вкладки. У рассылки и инвайтов лента
    перерисовывалась — их переключатель писали позже и не забыли. Ровно та
    разница, которую в девяти копиях одного кода не видно.
    """

    def _page(self):
        return open('data/uvicorn/template/admin/dating/profile.html',
                    encoding='utf-8').read()

    def test_the_checkbox_repaints_the_schedule_band(self):
        """⚠ Оттенок зелёного у идущей смены зависит от флага: без перерисовки
        панель молчит о работе, которая уже началась."""
        page = self._page()
        start = page.index('async function favMailFlagSet(')
        block = page[start:start + 900]

        assert 'favMailSchedule.paint()' in block, \
            'галочка снова не перерисовывает ленту расписания'

    def test_every_switch_goes_through_one_door(self):
        """⚠⚠ Сторож от возврата девяти копий.

        Каждая делала одно: `PUT` флага, запись в своё хранилище, перерисовка,
        уведомление, откат галочки при отказе. Отличались они тем, **что
        перерисовать**, — и именно в этом разошлись.

        Признак копии — свой откат галочки (`box.checked = !on`). Он обязан быть
        ровно один, в общей функции.
        """
        page = self._page()

        assert page.count('box.checked = !on') == 1, \
            'переключатель сайта снова написан своими руками'
        assert page.count('async function siteFlagToggle(') == 1, \
            'общая дорога переключателя пропала или раздвоилась'

    def test_the_schedule_block_uses_it_too(self):
        """⚠ И расписания: их `onSet` был десятой копией того же тела."""
        page = self._page()
        start = page.index('        async function onSet(box) {')
        block = page[start:start + 700]

        assert 'siteFlagToggle(box' in block, \
            'расписание снова переключает флаг само'
