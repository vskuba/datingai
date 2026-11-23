"""Платившие нашим девушкам — первыми в наборе, но без флага «Приоритет».

Владелец 23.11.2026: приоритет плативших (`GET /users/payers`). ⚠ Наружу сюита
не ходит, Redis подменяется: живого Redis у сюиты с хоста нет.
"""

import pytest

from src.dating import payers as payers_mod
from tests.conftest import TEST_STAND_SITE

pytestmark = pytest.mark.mail

SITE = 999_778

# ⚠ Сверке нужны живые анкеты в базе — только стендовый сайт: заведение
# проверяет номер сайта.
STAND = TEST_STAND_SITE


class _Redis:
    """Ровно то, что зовёт `payers.py`: множество, срок и конвейер."""

    def __init__(self):
        self.sets = {}

    def smembers(self, key):
        return {one.encode() for one in self.sets.get(key, set())}

    def pipeline(self):
        return self

    def delete(self, key):
        self.sets.pop(key, None)

    def sadd(self, key, *values):
        self.sets.setdefault(key, set()).update(values)

    def expire(self, key, sec):
        pass

    def execute(self):
        pass


@pytest.fixture
def redis(monkeypatch):
    fake = _Redis()
    monkeypatch.setattr(payers_mod, 'redis_conn_get', lambda: fake)
    monkeypatch.setattr(payers_mod, '_synced_at', {})
    return fake


class TestTheOrder:
    async def test_payers_go_after_the_marked_and_before_the_rest(self, monkeypatch):
        from src.dating import mailing

        async def _marked(site_id):
            return {'m'}

        monkeypatch.setattr(mailing, '_marked', _marked)
        monkeypatch.setattr(payers_mod, 'payers_uids', lambda site_id: {'p'})
        people = [{'uid': 'a'}, {'uid': 'p'}, {'uid': 'b'}, {'uid': 'm'}]

        got = await mailing._by_priority(SITE, people)

        assert [one['uid'] for one in got] == ['m', 'p', 'a', 'b']
        assert [one['uid'] for one in got if one['priority']] == ['m'], \
            'платившему поставлен флаг «Приоритет» — инвайты позвали бы его всеми анкетами'

    async def test_no_marks_no_payers_keeps_the_feed(self, monkeypatch):
        from src.dating import mailing

        async def _marked(site_id):
            return set()

        monkeypatch.setattr(mailing, '_marked', _marked)
        monkeypatch.setattr(payers_mod, 'payers_uids', lambda site_id: set())
        people = [{'uid': 'a'}, {'uid': 'b'}]

        assert await mailing._by_priority(SITE, people) is people


class TestTheSync:
    async def test_the_answers_of_all_girls_are_joined(self, redis, monkeypatch, girl_make):
        one = await girl_make(site_id=STAND, online=True)
        two = await girl_make(site_id=STAND, online=True)
        # ⚠ По номеру анкеты: на стенде бывают и чужие анкеты в сети.
        lists = {one: [{'uid': '1'}, {'uid': '2'}], two: [{'uid': '2'}, {'uid': '3'}]}

        async def _payers(profile, base, password):
            return lists.get(int(profile['id']), [])

        async def _setting(site_id, key):
            return 'https://api' if key == 'api_base' else 'pw'

        monkeypatch.setattr('src.dating.people_api.people_api_payers', _payers)
        monkeypatch.setattr('src.dating.site_setting.site_setting', _setting)

        assert await payers_mod.payers_sync(STAND) == 3
        assert payers_mod.payers_uids(STAND) == {'1', '2', '3'}

    async def test_a_dead_site_keeps_the_old_order(self, redis, monkeypatch, girl_make):
        await girl_make(site_id=STAND, online=True)
        redis.sets[payers_mod.PAYERS_KEY.format(site_id=STAND)] = {'old'}

        async def _payers(profile, base, password):
            raise RuntimeError('сайт не отвечает')

        async def _setting(site_id, key):
            return 'https://api'

        monkeypatch.setattr('src.dating.people_api.people_api_payers', _payers)
        monkeypatch.setattr('src.dating.site_setting.site_setting', _setting)

        assert await payers_mod.payers_sync(STAND) == 0
        assert payers_mod.payers_uids(STAND) == {'old'}, \
            'отказ сайта стёр порядок, верный час назад'

    async def test_a_sync_waits_an_hour(self, redis, monkeypatch):
        async def _setting(site_id, key):
            return ''

        monkeypatch.setattr('src.dating.site_setting.site_setting', _setting)

        assert await payers_mod.payers_due(STAND)
        await payers_mod.payers_sync(STAND)
        assert not await payers_mod.payers_due(STAND)
