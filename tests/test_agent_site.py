"""
Метка сайта, с которой заводится агент анкеты у соседа.

У RhapsodyAi поле `site` в `POST /agent/create` обязательное и проверяется форматом
`^[a-z0-9]+$` — не подойдёт, и заведение отвечает `422`. Отсюда две обязанности здесь:
добыть имя сайта из справочника соседа и привести его к этому формату.

Справочник чужой, своего списка сайтов у нас нет: сайты заводит человек на той стороне,
и вторая копия имён разошлась бы с первой на первом же новом сайте. Имя там
человеческое — `Goldenbride`, `Ladadate`, — а уехать должно `goldenbride`, `ladadate`.

Сосед здесь поддельный (`rhapsody_fake`): сюита не ходит в чужой проект.
"""

import pytest

from src.dating.dating_agent import dating_agent_site

pytestmark = pytest.mark.profile
class TestSiteSlug:
    """Имя из справочника превращается в метку, пригодную для поля `site`."""

    async def test_human_name_becomes_slug(self, rhapsody):
        rhapsody.sites = [{'id': 19, 'name': 'Goldenbride', 'url': 'http://goldenbride.net/'}]

        assert await dating_agent_site(19) == 'goldenbride'

    async def test_second_site_keeps_its_own_name(self, rhapsody):
        """Метка — имя своего сайта, а не общая для дейтинга: агенты разных сайтов различимы."""
        rhapsody.sites = [{'id': 19, 'name': 'Goldenbride'},
                          {'id': 343, 'name': 'Ladadate'}]

        assert await dating_agent_site(343) == 'ladadate'
        assert await dating_agent_site(19) == 'goldenbride'

    async def test_spaces_and_signs_are_dropped(self, rhapsody):
        """У соседа формат без пробелов и знаков — чистим сами, а не ловим потом `422`."""
        rhapsody.sites = [{'id': 7, 'name': 'Golden Bride-2!'}]

        assert await dating_agent_site(7) == 'goldenbride2'

    async def test_unknown_site_gives_empty(self, rhapsody):
        """Сайта нет — пустая строка, и решает вызывающий. Выдумывать метку нельзя."""
        rhapsody.sites = []

        assert await dating_agent_site(99999) == ''

    async def test_name_without_latin_gives_empty(self, rhapsody):
        """
        Из кириллицы метки не выйдет, и это честнее подстановки.

        Транслитерация тут была бы догадкой: `Знакомства` → `znakomstva` — имя,
        которого нет ни в одном справочнике, и по нему потом не понять, чей агент.
        """
        rhapsody.sites = [{'id': 5, 'name': 'Знакомства'}]

        assert await dating_agent_site(5) == ''


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
