"""Смена сайта уносит снимки **всех** лент про сайт, а не трёх знакомых.

## ⚠⚠ Зачем

Ленты про сайт живут в памяти страницы под ключами без номера анкеты, и при
смене сайта их надо забыть: иначе новый сайт до прихода ответа показывает
людей прежнего, поданных как своих.

Чистка стояла перечислением: `ONLINE_KEY` строкой и ключи `|guests` обходом.
Лента «Избранные сайта», заведённая 08.11.2026, в перечень не попала — и
08.11.2026 это увидел владелец: переключаешь Ladadate → Step2Love, лента та
же, обновишь страницу — правильная. Беда тихая ровно потому, что данные
настоящие, просто чужие.

Теперь признак общий: **ключ, начинающийся с `site|`, и значит «про сайт»** —
по нему чистятся все ленты разом, включая те, которых ещё нет. Ключи по анкете
(`42|favorite`) начинаются с номера и под правило не попадают.
"""

import pathlib
import re

import pytest

pytestmark = pytest.mark.page

PAGE = (pathlib.Path(__file__).resolve().parents[1]
        / 'data/uvicorn/template/admin/dating/profile.html')


class TestEverySiteFeedIsForgotten:
    """Главное: правило одно на все ленты про сайт."""

    def test_the_cleanup_goes_by_prefix(self):
        page = _page()

        assert "key.startsWith('site|')" in page, \
            'чистка снимков снова идёт перечислением знакомых лент'
        assert 'function peopleSiteForget(' in page, \
            'у чистки нет своего имени — значит она размазана по месту вызова'

    def test_the_site_change_calls_it(self):
        """⚠ Зовётся на смене сайта: там и рождается чужой снимок."""
        body = _body('siteEl.addEventListener')

        assert 'peopleSiteForget()' in body, \
            'смена сайта больше не забывает снимки лент'

    def test_the_old_enumeration_is_gone(self):
        """⚠⚠ Перечисление убрано, а не оставлено рядом.

        Две чистки на одно дело — это два списка лент, и расходиться они будут
        молча: вторая лента попадёт в один и не попадёт в другой.
        """
        body = _body('siteEl.addEventListener')

        assert 'peopleCache.delete(ONLINE_KEY)' not in body, \
            'рядом с общей чисткой осталась штучная'
        assert "endsWith('|guests')" not in body, \
            'рядом с общей чисткой остался обход гостей'


class TestTheKeysAgreeWithTheRule:
    """⚠⚠ Правило держится на виде ключей — за ним и следим."""

    def test_site_feeds_are_keyed_by_site(self):
        page = _page()

        assert "const ONLINE_KEY = 'site|online'" in page
        assert "const FAVSITE_KEY = 'site|favorite'" in page
        assert "`site|${openId || 0}|guests`" in page, \
            'ключ гостей перестал начинаться с «site|» — чистка его не найдёт'

    def test_profile_feeds_are_keyed_by_profile(self):
        """⚠ Списки по анкете чистить незачем: номер уникален и чужим не
        станет. Начинайся они с `site|` — смена сайта уносила бы и их."""
        page = _page()

        assert '`${openId}|${kind}`' in page
        assert '`${requestId}|${kind}`' in page


def _page() -> str:
    return PAGE.read_text(encoding='utf-8')


def _body(mark: str) -> str:
    """Кусок страницы от метки до конца её блока — грубо, но по месту."""
    page = _page()
    at = page.index(mark)
    got = page[at:]
    end = got.index('\n    });')

    return got[:end]
