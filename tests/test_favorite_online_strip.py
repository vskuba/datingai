"""Лента «Избранные онлайн»: та же лента, из которой выброшены чужие.

## ⚠⚠ Почему не отдельный список

Два списка «кто сейчас в сети» разъезжаются во времени: сняты они в разные
минуты, и человек видел бы в одном виджете зелёного, а в другом того же серым.
Этим уже болели «My favorites» и «Все онлайн» — и лечили сведе́нием к одному
источнику (`people._online_now`).

Поэтому у новой ленты нет ни своей ручки, ни своего кэша, ни своего снимка:
сервер помечает карточки ленты полем `favorite`, страница берёт тот же снимок и
оставляет отмеченных.

## ⚠ Знание «кто избранный» — в одном месте

`favorite.favorite_site_uids`. Спрашивают его из разных углов, и своя копия
запроса в каждом расходится с первой правкой — так уже случилось с долями
процентов, с правилом близнеца и с датой в журнале доставки.

## ⚠ Избранный у сайта, а не у открытой девушки

Лента отвечает на «с кем сейчас можно поработать», а работать может любая анкета
сайта: человек, избранный у Дианы, для Елены такой же собеседник.
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


class TestTheFeedIsMarked:
    """⚠⚠ Сервер помечает карточки ленты, а не отдаёт второй список."""

    def test_only_the_online_feed_is_marked(self):
        """⚠ У списков по анкете вопрос другой — «кто у **этой** девушки», — и
        отвечает на него `favorite_settled`."""
        import src.uvicorn.routers.dating.people as mod

        code = inspect.getsource(mod._with_favorites)

        assert "str(kind) != 'online'" in code, \
            'отметка поехала в списки по анкете'

    def test_it_asks_the_base_once(self):
        """⚠ Один запрос на ответ, а не на карточку: лента бывает в полторы сотни
        человек."""
        import src.uvicorn.routers.dating.people as mod

        code = inspect.getsource(mod._with_favorites)

        assert code.count('favorite_site_uids(') == 1

    def test_the_site_feed_uses_it(self):
        import src.uvicorn.routers.dating.people as mod

        code = inspect.getsource(mod.people_site_list)

        assert code.count('_with_favorites(') == 3, \
            'какой-то из возвратов ленты остался без отметки'


class TestThePageReusesOneFeed:
    """⚠⚠ У ленты нет своего запроса, кэша и снимка."""

    def test_it_paints_from_the_online_cache(self):
        page = open('data/uvicorn/template/admin/dating/profile.html',
                    encoding='utf-8').read()

        assert "stripPaint('favonline', ONLINE_KEY, _favOnly(feed)" in page, \
            'лента избранных больше не берёт снимок онлайна'

    def test_reload_goes_to_the_online_feed(self):
        """⚠ Списка `favonline` у сервера нет вовсе — это отбор по отметке.
        Отправь кнопку в `peopleLoad`, она звала бы `/people/{id}/favonline` и
        получала 422."""
        page = open('data/uvicorn/template/admin/dating/profile.html',
                    encoding='utf-8').read()

        assert "ONLINE_STRIPS = ['online', 'favonline']" in page
        assert 'ONLINE_STRIPS.includes(kind)' in page

    def test_dots_skip_it(self):
        """⚠ Точку «в сети» у этих карточек не трогаем: принадлежность ленте и
        означает «в сети»."""
        page = open('data/uvicorn/template/admin/dating/profile.html',
                    encoding='utf-8').read()

        assert 'ONLINE_STRIPS.includes(row.dataset.strip)' in page

    def test_the_empty_text_is_its_own(self):
        """⚠ Пусто здесь означает другое: лента снята и полна, просто избранных
        среди них нет. Общий текст отправил бы проверять, снимается ли лента."""
        page = open('data/uvicorn/template/admin/dating/profile.html',
                    encoding='utf-8').read()

        assert "favonline: 'избранных нет в сети'" in page

    def test_the_markup_uses_the_macro(self):
        """⚠ Тем же макросом, что и прочие ленты: копия разъехалась бы с ними в
        день первой правки высоты или стрелок."""
        page = open('data/uvicorn/template/admin/dating/profile/site/_base.html',
                    encoding='utf-8').read()

        assert "strip_row('favonline'" in page


class TestTheTwoStripsTakeTurns:
    """⚠⚠ Две ленты избранных стоят по очереди, а не рядом.

    «My favorites» — про открытую девушку, «Избранные онлайн» — про весь сайт, и
    вместе они отвечают на один вопрос дважды. Выбрана анкета — говорит её
    список; не выбрана — общий.

    Показав оба разом, мы заставили бы человека гадать, чем они отличаются, и
    отдали бы две с половиной сотни пикселей высоты за ответ, который нужен один.
    """

    def _page(self):
        return open('data/uvicorn/template/admin/dating/profile.html',
                    encoding='utf-8').read()

    def test_the_general_strip_hides_when_a_girl_is_open(self):
        """⚠ Условие **обратное** тому, что у своей ленты: `!пусто` против
        `пусто`. Поставь одинаковое — ленты либо обе горели бы, либо обе гасли."""
        page = self._page()

        assert ("workEl.querySelectorAll('.strip-row[data-strip=\"favonline\"]')"
                ".forEach(\n            row => row.classList.toggle('off', !пусто))"
                in page), 'общая лента больше не уступает место своей'

    def test_the_own_strip_hides_without_a_girl(self):
        page = self._page()

        assert ("workEl.querySelectorAll('.strip-row[data-strip=\"favorite\"]')"
                ".forEach(\n            row => row.classList.toggle('off', пусто))"
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
