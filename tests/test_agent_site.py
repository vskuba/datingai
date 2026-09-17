"""
Метка сайта, с которой заводится агент анкеты у соседа.

У RhapsodyAi поле `site` в `POST /agent/create` обязательное и проверяется форматом
`^[a-z0-9]+$` — не подойдёт, и заведение отвечает `422`. Отсюда две обязанности здесь:
добыть имя сайта из справочника соседа и привести его к этому формату.

Ведёт список по-прежнему сосед — сайты заводит человек на той стороне, — но имя
берётся из **нашего снимка** этого списка (`site_`), а не запросом его
карточки. Снимок затем и заведён, чтобы раздел не зависел от соседа в мелочах
(`api.md`, §3.5); сверка идёт в фоне. Имя там человеческое — `Goldenbride`,
`Ladadate`, — а уехать должно `goldenbride`, `ladadate`.

⚠ **Прежде имя спрашивалось карточкой у соседа**, и метка не собиралась, пока он
не ответит. Заведение агента вставало от чужого перезапуска — при том что имя
сайта у нас лежало всё это время.

Сосед здесь поддельный (`rhapsody_fake`): сюита не ходит в чужой проект.
"""

import pytest
from mysql_.mysql_ import mysql_get_db_async

from src.dating.agent import agent_site

pytestmark = pytest.mark.profile


async def _snapshot(site_id: int, name: str) -> None:
    """Кладёт сайт в снимок — то, что в работе делает фоновая сверка с соседом."""
    async with mysql_get_db_async() as db:
        await db.execute(
            'INSERT INTO site (site_id, name, url, tags, is_gone)'
            " VALUES (%s, %s, '', 'dating', 0)"
            ' ON DUPLICATE KEY UPDATE name = VALUES(name), is_gone = 0',
            (int(site_id), name))
        await db.connection.commit()


async def _snapshot_drop(site_id: int) -> None:
    async with mysql_get_db_async() as db:
        await db.execute('DELETE FROM site WHERE site_id = %s', (int(site_id),))
        await db.connection.commit()
class TestSiteSlug:
    """Имя из справочника превращается в метку, пригодную для поля `site`."""

    async def test_human_name_becomes_slug(self):
        await _snapshot(19, 'Goldenbride')

        assert await agent_site(19) == 'goldenbride'

    async def test_second_site_keeps_its_own_name(self):
        """Метка — имя своего сайта, а не общая для дейтинга: агенты разных сайтов различимы."""
        await _snapshot(19, 'Goldenbride')
        await _snapshot(343, 'Ladadate')

        assert await agent_site(343) == 'ladadate'
        assert await agent_site(19) == 'goldenbride'

    async def test_spaces_and_signs_are_dropped(self):
        """У соседа формат без пробелов и знаков — чистим сами, а не ловим потом `422`."""
        await _snapshot(7, 'Golden Bride-2!')
        try:
            assert await agent_site(7) == 'goldenbride2'
        finally:
            await _snapshot_drop(7)

    async def test_unknown_site_gives_empty(self):
        """Сайта нет — пустая строка, и решает вызывающий. Выдумывать метку нельзя."""
        assert await agent_site(99999) == ''

    async def test_a_silent_neighbour_does_not_stop_the_slug(self, rhapsody):
        """⚠⚠ Лежащий сосед метку больше не отнимает.

        Прежде имя спрашивалось его карточкой, и заведение агента вставало от
        чужого перезапуска — при том что имя сайта лежало у нас всё это время.
        Здесь сосед молчит, а метка собирается.
        """
        await _snapshot(19, 'Goldenbride')
        rhapsody.offline()

        assert await agent_site(19) == 'goldenbride'

    async def test_name_without_latin_gives_empty(self):
        """
        Из кириллицы метки не выйдет, и это честнее подстановки.

        Транслитерация тут была бы догадкой: `Знакомства` → `znakomstva` — имя,
        которого нет ни в одном справочнике, и по нему потом не понять, чей агент.
        """
        await _snapshot(5, 'Знакомства')
        try:
            assert await agent_site(5) == ''
        finally:
            await _snapshot_drop(5)


class TestSiteReachesTheNeighbour:
    """Метка доезжает до заведения агента — тем полем, которое сосед и требует."""

    async def test_create_sends_site(self, rhapsody):
        from src.rhapsody.rhapsody_api import rhapsody_api_agent_create

        await rhapsody_api_agent_create(node='Dating', workflow='dating_girl_create',
                                        name='natalia_1430119', title='Наталья',
                                        variables={'girl_bio': 'био'}, site='goldenbride')

        body = rhapsody.bodies.get('/agent/create') or {}
        assert body.get('site') == 'goldenbride', f'сосед получил не ту метку: {body!r}'
        # Остальные поля не должны потеряться при добавлении нового: заведение без
        # workflow или без биографии у соседа сорвётся уже своей ошибкой.
        assert body.get('node') == 'Dating'
        assert body.get('workflow') == 'dating_girl_create'
        assert body.get('variables') == {'girl_bio': 'био'}
