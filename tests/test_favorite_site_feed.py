"""Избранные всего сайта — сумма по анкетам, а не отбор из ленты онлайна.

## ⚠⚠ Зачем

Без выбранной анкеты на «Приставании» стояла лента «Избранные онлайн»: та же
лента «Все онлайн», из которой выброшены неотмеченные. Пока никто из избранных
не в сети — она пуста, и на вопрос «кто у нас вообще в избранных» панель не
отвечала вовсе. Живой случай 08.11.2026: у Step2Love три звёздочки на три
анкеты, лента показывает ноль, человек видит пустоту и чинит несломанное.

Теперь без анкеты лента показывает **всех избранных сайта** — сумму по его
анкетам. Берётся она из нашей базы (`favorite` плюс карточки `companion`), то
есть не стоит ни входа, ни похода на сайт, и «ни одна анкета не в сети» ей не
помеха.

⚠⚠ **Источник «кто в сети» при этом остался один.** Прежнее правило было про
него, и оно цело: принадлежность ленте больше не значит «в сети», но точку
по-прежнему ставит снимок «Все онлайн» (`_online_now`) — второго списка
онлайна не завелось. Разойдись они, человек видел бы в одном виджете зелёного,
а в другом того же серым.
"""

import uuid

import pytest

from mysql_.mysql_ import mysql_get_db_async

from src.uvicorn.routers.dating.people import people_site_list

pytestmark = pytest.mark.mail

STAND_SITE = 999_343


@pytest.fixture
async def stars(girl_make):
    """Две анкеты сайта, звёздочки у обеих и одна сорвавшаяся."""
    first = await girl_make('favsite')
    second = await girl_make('favsite')

    uids = {'her': f'fs-{uuid.uuid4().hex[:8]}',    # избранный первой
            'his': f'fs-{uuid.uuid4().hex[:8]}',    # избранный второй
            'both': f'fs-{uuid.uuid4().hex[:8]}',   # избранный у обеих
            'bad': f'fs-{uuid.uuid4().hex[:8]}'}    # звёздочку нажать не вышло

    async with mysql_get_db_async() as db:
        await db.execute(
            'INSERT INTO favorite (site_id, profile_id, uid, state)'
            ' VALUES (%s, %s, %s, %s), (%s, %s, %s, %s),'
            '        (%s, %s, %s, %s), (%s, %s, %s, %s), (%s, %s, %s, %s)',
            (STAND_SITE, first, uids['her'], 'done',
             STAND_SITE, second, uids['his'], 'already',
             STAND_SITE, first, uids['both'], 'done',
             STAND_SITE, second, uids['both'], 'done',
             STAND_SITE, first, uids['bad'], 'fail'))

        for uid, name in ((uids['her'], 'Lance'), (uids['his'], 'Semen'),
                          (uids['both'], 'Bruce')):
            await db.execute(
                'INSERT INTO companion (site_id, uid, name) VALUES (%s, %s, %s)',
                (STAND_SITE, uid, name))
        await db.connection.commit()

    yield uids

    async with mysql_get_db_async() as db:
        await db.execute("DELETE FROM favorite WHERE site_id = %s AND uid LIKE 'fs-%%'",
                         (STAND_SITE,))
        await db.execute("DELETE FROM companion WHERE site_id = %s AND uid LIKE 'fs-%%'",
                         (STAND_SITE,))
        await db.connection.commit()


async def _feed(site_id: int = STAND_SITE) -> dict:
    return (await people_site_list(site_id, 'favorite'))['result']


class TestItSumsUpTheProfiles:
    """Главное: лента показывает избранных **всех** анкет сайта."""

    async def test_every_girls_favorite_is_there(self, stars):
        got = {one['uid'] for one in (await _feed())['people']}

        assert stars['her'] in got, 'избранный первой анкеты потерялся'
        assert stars['his'] in got, 'избранный второй анкеты потерялся'

    async def test_one_man_two_girls_is_one_row(self, stars):
        """⚠ Человек в ленте один, хоть его отметили обе.

        Лента отвечает «кто у нас в избранных», а не «сколько звёздочек
        нажато»: две одинаковые карточки читались бы как два разных человека.
        """
        rows = [one for one in (await _feed())['people']
                if one['uid'] == stars['both']]

        assert len(rows) == 1, f'двойная карточка: {rows}'

    async def test_a_failed_star_is_not_in_the_feed(self, stars):
        """⚠ `fail` значит «нажать не вышло» — избранным человек не стал."""
        got = {one['uid'] for one in (await _feed())['people']}

        assert stars['bad'] not in got

    async def test_another_site_does_not_leak(self, stars):
        got = {one['uid'] for one in (await _feed(STAND_SITE + 1))['people']}

        assert stars['her'] not in got


class TestItCostsNothingOutside:
    """⚠⚠ Лента из нашей базы: ни входа, ни похода на сайт."""

    async def test_it_answers_without_a_girl_online(self, stars):
        """⚠⚠⚠ Ровно то, чего не умела прежняя дорога.

        Снимок по сайту берётся **под анкетой**, и без единой в сети ручка
        отвечала `422` «ни одна анкета не в сети — под кем заходить?». Для
        суммы заходить не под кем и не надо: она уже у нас.
        """
        got = await _feed()

        assert got['error'] == '', f'лента отказала: {got["error"]}'
        assert len(got['people']) == 3

    async def test_it_is_not_a_snapshot(self, stars):
        """⚠ Ответ свежий всегда: это чтение базы, а не снимок в Redis.

        `cached: true` у такой ленты означало бы возраст, которого у неё нет, —
        страница показала бы «кеш 40 мин назад» под строкой, прочитанной
        секунду назад.
        """
        got = await _feed()

        assert got['cached'] is False
        assert got['age_sec'] == 0
        assert got['refreshing'] is False

    async def test_the_rows_carry_names_from_cards(self, stars):
        """Имя и лицо — из карточек: сайт отдаёт одни номера."""
        row = next(one for one in (await _feed())['people']
                   if one['uid'] == stars['her'])

        assert row['name'] == 'Lance'


class TestTheOnlineAnswerStaysOne:
    """⚠⚠ Второго источника «кто в сети» не завелось — правило прежнее."""

    def test_the_dot_comes_from_the_online_snapshot(self):
        import inspect

        import src.uvicorn.routers.dating.people as mod

        code = inspect.getsource(mod._favorite_site_feed)

        assert '_online_now(' in code, \
            'точка «в сети» считается не снимком ленты'

    async def test_online_men_come_first(self, stars, monkeypatch):
        """⚠ Кто в сети — в начало: лента прокручивается, и работать человек
        будет с теми, кому можно написать прямо сейчас.
        """
        import src.uvicorn.routers.dating.people as mod

        def _lit(rows, site_id, kind):
            for one in rows:
                one['online'] = one['uid'] == stars['his']

            return rows

        monkeypatch.setattr(mod, '_online_now', _lit)

        got = (await _feed())['people']

        assert got[0]['uid'] == stars['his'], \
            f'в сети не первым: {[one["uid"] for one in got]}'
