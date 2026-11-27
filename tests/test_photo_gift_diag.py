"""Диагностика фото-подарка: почему «нет» — слышно, а не молча.

Девять причин у `photo_gift_due` прятались за одним `False`; теперь каждая
 ложится строкой в журнал и JSON в список Redis
`dating:photo_gift:why:{site}:{profile}:{uid}`. Проверяем ровно то, за что
диагностика отвечает, и только то:

* коды причин не врут — `day_limit`, `few_messages`, `media_closed` (и его
  кэш-близнец `media_closed_cached`), `ok`;
* `bool`-ответ не поменялся — он тот же, что до диагностики;
* список читается обратно свежими вперёд (`photo_gift_why_rows`);
* Redis лёг — ответ не сломан, чтение отдаёт пустоту.

⚠ Redis — поддельный, тот же приём, что `_GirlsRedis` в
`test_fav_card_girl_pause.py`: живого хранилища в процессе сюиты нет.
Решение считается в процессе на настоящей базе; наружу сюита не ходит.
"""

import uuid

import pytest

from mysql_.mysql_ import mysql_get_db_async

from src.dating import photo_gift as gift_mod

pytestmark = pytest.mark.chat

LUXEE = {'driver': 'luxee'}


class _WhyRedis:
    """Списки причин: `lpush`/`ltrim`/`lrange`/`expire` — подделка того же ряда,
    что `_GirlsRedis` в `test_fav_card_girl_pause.py`."""

    def __init__(self):
        self.lists = {}

    def lpush(self, key, value):
        self.lists.setdefault(key, []).insert(0, value)

    def ltrim(self, key, low, high):
        self.lists[key] = self.lists.get(key, [])[low:high + 1]

    def lrange(self, key, low, high):
        return self.lists.get(key, [])[low:high + 1]

    def expire(self, key, sec):
        return True


class _DeadRedis:
    """Redis, который лёг: каждая команда падает, как живая сеть при отказе."""

    def __getattr__(self, name):
        raise ConnectionError('redis лег')


@pytest.fixture
def why(monkeypatch):
    """Пустой список причин и пустая память `_OPEN` — у каждого теста свои."""
    fake = _WhyRedis()
    monkeypatch.setattr(gift_mod, 'redis_conn_get', lambda: fake)
    gift_mod._OPEN.clear()

    return fake


@pytest.fixture
def allowed(monkeypatch):
    """Всё разрешено, кроме того, что проверяет тест: пределы, медиа, галерея.

    Отдаёт словарь настроек — тест крутит `photo_gift_per_day` на месте.
    """
    from src.dating.site.site_chat_luxee import DatingSiteChatLuxee

    settings = {'photo_gift_per_day': 2, 'photo_gift_gap_hours': 3}

    async def _ok(site_id):
        return True

    async def _seconds(site_id, key):
        return settings[key]

    async def _fresh(*args, **kwargs):
        return [{'id': 1, 'file_id': 'f-1'}]

    async def _open(self, *args):
        return True

    monkeypatch.setattr(gift_mod, 'chat_intention_do_allowed', _ok)
    monkeypatch.setattr(gift_mod, 'site_seconds', _seconds)
    monkeypatch.setattr(gift_mod, 'companion_gallery_fresh', _fresh)
    monkeypatch.setattr(DatingSiteChatLuxee, 'chat_photo_open', _open)
    gift_mod._OPEN.clear()

    yield settings


async def _said(profile_id: int, uid: str, n: int) -> None:
    async with mysql_get_db_async() as db:
        for _ in range(n):
            await db.execute(
                'INSERT INTO message (profile_id, sender_uid, sender_name, text,'
                '  message_uid, direction, created_at)'
                " VALUES (%s, %s, 'Mark', 'hi', %s, 'in', NOW() - INTERVAL 5 MINUTE)",
                (int(profile_id), uid, f'zz-{uuid.uuid4().hex[:12]}'))
        await db.connection.commit()


async def _sent_shot(profile_id: int, uid: str, ago: str) -> None:
    """Его прошлый показанный снимок: `companion_gallery_file` с `sent_at` в прошлом."""
    async with mysql_get_db_async() as db:
        await db.execute(
            'INSERT INTO gallery_file (profile_id, file_id, path, name, mime, kind)'
            " VALUES (%s, %s, 'photo/public/a.jpg', 'a.jpg', 'image/jpeg', 'image')",
            (int(profile_id), f'gift-{uuid.uuid4().hex[:10]}'))
        shot = int(db.lastrowid)
        await db.execute(
            'INSERT INTO companion_gallery_file (profile_id, site_id, uid, gallery_file_id, sent_at)'
            f' SELECT %s, site_id, %s, %s, NOW() - INTERVAL {ago} FROM profile WHERE id = %s',
            (int(profile_id), uid, shot, int(profile_id)))
        await db.connection.commit()


async def _profile(profile_id: int) -> dict:
    async with mysql_get_db_async() as db:
        await db.execute('SELECT * FROM profile WHERE id = %s', (int(profile_id),))

        return dict(await db.fetchone())


def _rows(got: dict, uid: str) -> list[dict]:
    """Записанные причины пары — через публичную дверь чтения."""

    return gift_mod.photo_gift_why_rows(got['site_id'], got['id'], uid)


class TestWhyIsRecorded:
    async def test_day_limit(self, girl, why, allowed):
        allowed['photo_gift_per_day'] = 1
        await _sent_shot(girl, 'u-dl', '1 HOUR')

        assert (await gift_mod.photo_gift_due(await _profile(girl), LUXEE, 'u-dl')) is False

        assert _rows(await _profile(girl), 'u-dl')[0]['why'] == 'day_limit'

    async def test_few_messages(self, girl, why, allowed):
        await _said(girl, 'u-fm', 1)

        assert (await gift_mod.photo_gift_due(await _profile(girl), LUXEE, 'u-fm')) is False

        row = _rows(await _profile(girl), 'u-fm')[0]

        assert row['why'] == 'few_messages'
        assert row['his'] == 1 and 2 <= row['need'] <= 3, 'порог 2–3, посчитан и записан'

    async def test_media_closed_then_cached(self, girl, why, allowed, monkeypatch):
        from src.dating.site.site_chat_luxee import DatingSiteChatLuxee

        async def _closed(self, *args):
            return False

        monkeypatch.setattr(DatingSiteChatLuxee, 'chat_photo_open', _closed)
        await _said(girl, 'u-mc', 3)
        got = await _profile(girl)

        assert (await gift_mod.photo_gift_due(got, LUXEE, 'u-mc')) is False
        assert (await gift_mod.photo_gift_due(got, LUXEE, 'u-mc')) is False, \
            'второй заход берёт ответ из памяти — тот же `False`'

        rows = _rows(got, 'u-mc')

        assert [one['why'] for one in rows] == ['media_closed_cached', 'media_closed'], \
            'свежая впереди; сокет был один, второй ответ — из `_OPEN`'

    async def test_ok(self, girl, why, allowed):
        await _said(girl, 'u-ok', 3)

        assert (await gift_mod.photo_gift_due(await _profile(girl), LUXEE, 'u-ok')) is True

        row = _rows(await _profile(girl), 'u-ok')[0]

        assert row['why'] == 'ok' and row['due'] is True and row['his'] == 3
        assert row['per_day'] == 2 and row['day'] == 0
        assert len(row['at']) == 19 and 'T' not in row['at'], 'UTC `YYYY-MM-DD HH:MM:SS`'

    async def test_no_chat_photo(self, girl, why):
        """Сайт без фото в чате — причина видна, хотя сокет никто не звал."""
        assert (await gift_mod.photo_gift_due(await _profile(girl),
                                              {'driver': 'ladadate'}, 'u-nc')) is False

        assert _rows(await _profile(girl), 'u-nc')[0]['why'] == 'no_chat_photo'

    async def test_error_counts_as_error(self, why):
        """Анкета без `id` — та же неудача, что была, плюс запись `error`."""
        assert (await gift_mod.photo_gift_due({'site_id': 1}, LUXEE, 'u-er')) is False

        row = gift_mod.photo_gift_why_rows(1, 0, 'u-er')[0]

        assert row['why'] == 'error' and row['due'] is False


class TestBoolUnchanged:
    async def test_readback_newest_first(self, girl, why, allowed):
        """Два захода — две записи, свежая впереди; `bool` каждого — прежний."""
        await _said(girl, 'u-rb', 1)
        got = await _profile(girl)

        assert (await gift_mod.photo_gift_due(got, LUXEE, 'u-rb')) is False

        await _said(girl, 'u-rb', 2)

        assert (await gift_mod.photo_gift_due(got, LUXEE, 'u-rb')) is True

        assert [one['why'] for one in _rows(got, 'u-rb')] == ['ok', 'few_messages']

    async def test_limit_trims_the_list(self, girl, why, allowed):
        await _said(girl, 'u-lt', 1)
        got = await _profile(girl)

        for _ in range(gift_mod.PHOTO_GIFT_WHY_MAX + 5):
            await gift_mod.photo_gift_due(got, LUXEE, 'u-lt')

        rows = _rows(got, 'u-lt')

        assert len(rows) == gift_mod.PHOTO_GIFT_WHY_MAX, 'LTRIM держит последние 50'
        assert len(gift_mod.photo_gift_why_rows(got['site_id'], got['id'], 'u-lt',
                                                limit=3)) == 3, 'чтение берёт limit'


class TestRedisFailureIsSwallowed:
    async def test_answer_survives_without_redis(self, girl, monkeypatch, allowed):
        monkeypatch.setattr(gift_mod, 'redis_conn_get',
                            lambda: (_ for _ in ()).throw(ConnectionError('redis лег')))
        await _said(girl, 'u-dead', 3)

        assert (await gift_mod.photo_gift_due(await _profile(girl), LUXEE, 'u-dead')) is True

    async def test_readback_is_empty_when_redis_is_down(self, girl, monkeypatch):
        monkeypatch.setattr(gift_mod, 'redis_conn_get', lambda: _DeadRedis())

        assert gift_mod.photo_gift_why_rows(1, 1, 'u-x') == []
