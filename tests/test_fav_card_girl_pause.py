"""Карточка закрытой сайтом анкеты называет причину, а не «можно отправлять».

## ⚠⚠ Что чинится

Сайт закрыл анкете письма не-контактам до конца суток — отказ
`ERROR_MESSAGE_USER_NON_CONTACT_DAILY_SEND_LIMIT`. Пул «приставания» стоит,
шапка пула честно говорит «сайт закрыл анкетам пула…», а **карточка** той же
анкеты — «можно отправлять». Человек смотрит на живое письмо и не понимает,
почему оно не уходит.

Причин оказалось две, и обе — про одно правило в двух местах:

* карточка носила причину в `wait`, но секундный `replyWaitTick` на странице
  выбирал **все** `.mail-card-wait` и перезатирал чужие чипы своей болтовнёй
  «можно отправлять» — серверное слово до глаз не доходило;
* само слово карточки было вторым пересказом правила («у анкеты выбран
  суточный предел…»), не тем, которым говорит шапка.

Теперь причину карточке даёт та же дверь — `mail_wait_girl_words`: «сайт
закрыл анкете письма не-контактам до HH:MM UTC», час — из той же отметки, что
держит паузу; шапка и чип собраны из одной фразы. Тик трогает только свои
счётчики с `data-ready`.

Проверяются три этажа: пометка → слово помощника → `send_card_wait` →
список через HTTP (настоящий Redis, заведенный тем же `ZADD`, что ставит
отказ отправки), и сторож страницы — тик не совает нос в чужой чип.

⚠⚠ **Ни одно письмо наружу не уходит:** выдуманный сайт, строк которого нет
в таблице сайтов, — круг соседа их не видит (приём `test_send_rotate.py`).
"""

import re
import subprocess
import time

import pytest

from mysql_.mysql_ import mysql_get_db_async

from src.dating import mail_wait as mw
from src.dating import send_round as round_
from src.dating.mail_wait import (MAIL_WAIT_GIRL_CHIP, MAIL_WAIT_GIRL_KEY,
                                  mail_wait_girl_mark, mail_wait_girl_words,
                                  mail_wait_girls)
from src.dating.send_round import send_card_wait

pytestmark = pytest.mark.mail

# Сайт для писем — выдуманный: своей строки в таблице сайтов нет, живая
# отправка и соседние тесты не пересекаются с нашими ключами.
SITE = 999_791

NON_CONTACT = ('сайт отказал: Daily message sending limit for non-contacts '
               'exceeded (ERROR_MESSAGE_USER_NON_CONTACT_DAILY_SEND_LIMIT) (→ 403)')


class _GirlsRedis:
    """Отметки анкет: zset со сроками — та же подделка, что в `test_mail_wait.py`.

    ⚠ сюита идёт с хоста, адрес в `.env` — внутренний адрес контейнера;
    живого Redis в этом процессе нет, помощник честно отвечает пустотой.
    """

    def __init__(self):
        self.keys = {}

    def zadd(self, key, items):
        self.keys.setdefault(key, {}).update(
            {str(k): float(v) for k, v in items.items()})

    def expire(self, key, sec):
        return True

    def zremrangebyscore(self, key, low, high):
        for one in self._through(key, low, high):
            self.keys.get(key, {}).pop(one, None)

    def zrangebyscore(self, key, low, high, withscores=False):
        rows = [(one, self.keys[key][one])
                for one in self._through(key, low, high)]

        return rows if withscores else [one for one, _ in rows]

    def _through(self, key, low, high):
        low = float('-inf') if low == '-inf' else float(low)
        high = float('inf') if high == '+inf' else float(high)

        return [one for one, score in self.keys.get(key, {}).items()
                if low <= score <= high]


@pytest.fixture
def girls(monkeypatch):
    """Пустое хранилище отметок анкет — у каждого теста своё."""
    fake = _GirlsRedis()
    monkeypatch.setattr(mw, 'redis_conn_get', lambda: fake)

    return fake


async def _letter(profile_id: int, uid: str) -> int:
    """Черновик «приставания» напрямую: набор требует адресатов в сети."""
    async with mysql_get_db_async() as db:
        await db.execute(
            'INSERT INTO favorite_mail'
            " (site_id, profile_id, uid, name, body, status, error)"
            " VALUES (%s, %s, %s, 'Тест', 'Текст письма', 'draft', '')",
            (SITE, profile_id, uid))
        await db.connection.commit()
        await db.execute('SELECT LAST_INSERT_ID() AS id')

        return int((await db.fetchone())['id'])


class TestTheWordComesFromTheMark:
    """`mail_wait_girl_words` — те же слова, что у шапки, и срок из отметки."""

    def test_a_paused_girl_is_named_with_the_hours(self, girls):
        left = mail_wait_girl_mark(SITE, 52, NON_CONTACT, 'favorite_mail')

        got = mail_wait_girl_words(SITE, 'favorite_mail')

        assert left > 0 and 52 in got
        assert got[52].startswith(f'{MAIL_WAIT_GIRL_CHIP} до '), got[52]
        assert re.search(r'до \d\d:\d\d UTC$', got[52]), \
            'срок паузы назван не временем — второй копией правила'

    def test_the_hours_are_the_mark_not_a_copy_of_midnight(self, girls):
        """Час — из score отметки, а не из слова «полночь»: сдвинешь отметку —
        чип скажет новый срок, а не упрямое 00:00."""
        until = int(time.time()) + 7200
        girls.zadd(mw._girl_key(SITE, 'favorite_mail'), {52: until})

        got = mail_wait_girl_words(SITE, 'favorite_mail')

        assert got[52] == (f"{MAIL_WAIT_GIRL_CHIP} до "
                           f"{time.strftime('%H:%M', time.gmtime(until))} UTC")

    def test_another_girl_is_not_in_the_words(self, girls):
        """Замолчала одна — карточки остальных молчат: пауза на анкету."""
        mail_wait_girl_mark(SITE, 52, NON_CONTACT, 'favorite_mail')

        assert mail_wait_girl_words(SITE, 'favorite_mail').keys() == {52}

    def test_the_pause_belongs_to_its_pool(self, girls):
        """Отказ рассылки держит только рассылку — карточки избранных свободны."""
        mail_wait_girl_mark(SITE, 52, NON_CONTACT)  # рассылка

        assert mail_wait_girl_words(SITE, 'favorite_mail') == {}
        assert set(mail_wait_girl_words(SITE)) == {52}
        assert mail_wait_girls(SITE, 'favorite_mail') == []

    def test_an_expired_pause_says_nothing(self, girls):
        girls.zadd(mw._girl_key(SITE, 'favorite_mail'),
                   {52: time.time() - 10})

        assert mail_wait_girl_words(SITE, 'favorite_mail') == {}


class TestCardWaitAsksTheSameDoor:
    """`send_card_wait` называет закрытую анкету — и только её."""

    async def test_a_paused_girl_gets_the_reason_and_a_free_one_not(
            self, girl_make, monkeypatch):
        """Главный случай задачи: черновики двух девушек, пауза — на одной.

        Свободная — в сети, без остуды и без паузы пары: её строка обязана
        остаться без причины, иначе чип наврал бы и там.
        """
        paused_girl = await girl_make('favp')
        free_girl = await girl_make('favf', online=True)
        monkeypatch.setattr(
            round_, 'mail_wait_girl_words',
            lambda site_id, table=None: {paused_girl: f'{MAIL_WAIT_GIRL_CHIP} до 00:00 UTC'})
        monkeypatch.setattr(round_, 'mail_wait_uids', lambda site_id: [])

        wait = await send_card_wait(SITE, 'favorite_mail',
                                    [{'id': 1, 'profile_id': paused_girl,
                                      'uid': 'p1'},
                                     {'id': 2, 'profile_id': free_girl,
                                      'uid': 'f1'}])

        assert wait.get(1) == f'{MAIL_WAIT_GIRL_CHIP} до 00:00 UTC'
        assert 2 not in wait, 'свободной анкете приписана чужая пауза'


def _arm_girl_pause(profile_id: int, seconds: int = 7200) -> int:
    """Заводит паузу анкеты в настоящем Redis — та же запись, что ставит
    отказ отправки (`mail_wait_girl_mark`), под выдуманным номером сайта.

    Returns:
        int: час исхода отметки — тем же числом, что лёг в `ZADD`, чтобы
        проверка чипа не разминулась с записью на полсекунды.
    """
    key = MAIL_WAIT_GIRL_KEY.format(site_id=SITE) + ':favorite_mail'
    until = int(time.time()) + int(seconds)

    got = subprocess.run(
        ['docker', 'exec', 'dating_redis', 'redis-cli', 'ZADD', key,
         str(until), str(profile_id)],
        capture_output=True, text=True, timeout=30)
    if got.returncode != 0:
        pytest.skip(f'dating_redis нет под рукой: {got.stderr[:120]}')

    return until


def _drop_girl_pause() -> None:
    """Снимает паузу выдуманного сайта: ключ весь принадлежит тестам."""
    subprocess.run(
        ['docker', 'exec', 'dating_redis', 'redis-cli', 'DEL',
         MAIL_WAIT_GIRL_KEY.format(site_id=SITE) + ':favorite_mail'],
        capture_output=True, timeout=30)


def _row_of(rows: list, letter: int) -> dict:
    """Строка списка по номеру письма."""
    for one in rows:
        if int(one['id']) == int(letter):
            return one

    raise AssertionError(f'письма #{letter} нет в списке: {rows[:2]}')


class TestListCarriesTheChip:
    """⚠ Путь «список → карточка» — через HTTP одноразового сервера копии:
    подмена сюда не доездает, поэтому пауза заведена той же записью, что
    ставит настоящий отказ сайта (приём `test_send_rotate.py`)."""

    async def test_the_paused_draft_names_the_hours_and_the_free_one_not(
            self, auth_client, girl_make):
        paused_girl = await girl_make('favh', online=True)
        free_girl = await girl_make('favh2', online=True)
        held = await _letter(paused_girl, 'pause-1')
        free = await _letter(free_girl, 'free-1')

        until = _arm_girl_pause(paused_girl)
        try:
            got = await auth_client.get('/api/dating/favorite-mail',
                                        params={'site_id': SITE})
            assert got.status_code == 200, got.text[:200]

            rows = got.json()['result']
            when = time.strftime('%H:%M', time.gmtime(until))

            assert _row_of(rows, held)['wait'] == (
                f'{MAIL_WAIT_GIRL_CHIP} до {when} UTC'), 'пауза не дошла до карточки'
            assert _row_of(rows, free)['wait'] == '', \
                'свободной анкете влезла чужая пауза'
        finally:
            _drop_girl_pause()


class TestThePageTickKeepsItsHandsOff:
    """Сторож страницы: секундный тик не перезатирает серверное «ждёт: …».

    Именно он прятал причину под «можно отправлять»: выбирал все
    `.mail-card-wait`, а чип причины носит тот же класс без `data-ready`.
    """

    def test_the_tick_ticks_only_its_own_counters(self):
        from pathlib import Path

        page = (Path(__file__).resolve().parent.parent
                / 'data/uvicorn/template/admin/dating/profile.html'
                ).read_text()
        start = page.index('function replyWaitTick')
        body = page[start:page.index('function favReplyCard')]

        assert "'.mail-card-wait[data-ready]'" in body, \
            'тик снова лезет в чужие чипы и сотрёт «ждёт»'
        assert "'.mail-card-wait'" not in body
