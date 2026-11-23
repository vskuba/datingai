"""Месячные активные — второй круг адресатов рассылки после «Все онлайн».

Владелец 23.11.2026. ⚠ Наружу сюита не ходит, Redis подменяется.
"""

import json

import pytest

from src.dating import people_offline as po
from tests.conftest import TEST_STAND_SITE

pytestmark = pytest.mark.mail


class _Redis:
    def __init__(self):
        self.data = {}

    def get(self, key):
        return self.data.get(key)

    def set(self, key, value, ex=None):
        self.data[key] = value.encode()


@pytest.fixture
def redis(monkeypatch):
    fake = _Redis()
    monkeypatch.setattr(po, 'redis_conn_get', lambda: fake)
    monkeypatch.setattr(po, '_synced_at', {})
    return fake


def _hours(monkeypatch, value):
    async def _every(site_id):
        return value * 3600

    monkeypatch.setattr(po, '_every', _every)


def _site(monkeypatch, offline):
    async def _ready(site_id):
        return True

    async def _setting(site_id, key):
        return 'https://api'

    monkeypatch.setattr('src.dating.people_api.people_api_offline', offline)
    monkeypatch.setattr('src.uvicorn.routers.dating.people._api_ready', _ready)
    monkeypatch.setattr('src.dating.site_setting.site_setting', _setting)


class TestTheList:
    async def test_one_girl_fills_the_list_for_the_site(self, redis, monkeypatch, girl_make):
        await girl_make(site_id=TEST_STAND_SITE, online=True)
        _hours(monkeypatch, 6)
        calls = []

        async def _offline(profile, base, password):
            calls.append(profile['id'])
            return [{'uid': '1', 'name': 'A'}, {'uid': '2', 'name': 'B'}]

        _site(monkeypatch, _offline)

        assert await po.people_offline_sync(TEST_STAND_SITE) == 2
        assert len(calls) == 1, 'список общий на сайт — одной анкеты хватает'
        assert [one['uid'] for one in await po.people_offline_rows(TEST_STAND_SITE)] == ['1', '2']

    async def test_zero_turns_it_off(self, redis, monkeypatch):
        _hours(monkeypatch, 0)
        redis.data[po.PEOPLE_OFFLINE_KEY.format(site_id=1)] = json.dumps([{'uid': '1'}]).encode()

        assert not await po.people_offline_due(1)
        assert await po.people_offline_rows(1) == [], 'выключенный список всё равно идёт в рассылку'

    async def test_a_dead_site_keeps_the_old_list(self, redis, monkeypatch, girl_make):
        await girl_make(site_id=TEST_STAND_SITE, online=True)
        _hours(monkeypatch, 6)
        key = po.PEOPLE_OFFLINE_KEY.format(site_id=TEST_STAND_SITE)
        redis.data[key] = json.dumps([{'uid': 'old'}]).encode()

        async def _offline(profile, base, password):
            raise RuntimeError('сайт не отвечает')

        _site(monkeypatch, _offline)

        assert await po.people_offline_sync(TEST_STAND_SITE) == 0
        assert [one['uid'] for one in await po.people_offline_rows(TEST_STAND_SITE)] == ['old']


class TestTheAudience:
    async def test_online_first_then_the_month_without_repeats(self, monkeypatch):
        from src.dating import mailing

        async def _people(site_id):
            return [{'uid': 'o1'}, {'uid': 'o2'}]

        async def _offline(site_id):
            return [{'uid': 'o2'}, {'uid': 'm1'}, {'uid': 'pay'}]

        async def _marked(site_id):
            return set()

        monkeypatch.setattr(mailing, '_people', _people)
        monkeypatch.setattr(po, 'people_offline_rows', _offline)
        monkeypatch.setattr(mailing, '_marked', _marked)
        monkeypatch.setattr('src.dating.payers.payers_uids', lambda site_id: {'pay'})

        got = [one['uid'] for one in await mailing._audience(1)]

        assert got == ['pay', 'o1', 'o2', 'm1'], \
            'платящий не впереди, онлайн не первыми или повтор из второго круга'

    def test_the_setting_is_hours_and_can_be_off(self):
        from src.dating.site_setting import SITE_KNOWN

        known = SITE_KNOWN['mailing_offline_every']
        assert known['kind'] == 'number' and int(known['min']) == 0
