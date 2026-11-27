"""Ротация очереди по `try_at` и причина, по которой карточка ждёт.

⚠ **Написано по замеру на проде 26.11.2026.** Анкета, которой сайт временно
закрыл письма, стояла в пуле первой по номеру, и круг тыкал в неё каждые 6
секундов (`favorite_mail` #2276): пока держала причина, до остальных писем
очередь не доходила вовсе, а почему она стоит — было видно только в журнале
воркера, на карточке молчание.

Проверяются две половинки одной починки:

* **ротация** — каждая неудачная попытка оставляет на строке `try_at`
  (одна дверь, `mail_send_fail`), и `_oldest` ставит тронутое письмо за
  нетронутые, а между тронутыми — по давности попытки;
* **причина** — список пула приносит для каждой ожидающей карточки `wait`,
  посчитанный **правилами очереди** (`send_round.send_card_wait`), а не
  вторым пересказом.

⚠⚠ **Ни одно письмо наружу не уходит.** Подменяется `mail_send_one` —
единственная функция, которая ходит на сайт. И отбор круга сужен до стенда:
этот же круг крутит живой воркер в соседнем контейнере по той же базе, и
поднятый в базе флаг он увидел бы раньше теста (приём — из
`test_invite_send_round.py`).

⚠⚠ **Тесты со `send_round_run` и `send_card_wait` считаются в этом процессе** —
подмена тут видна. Тесты со `auth_client` идут **по HTTP одноразовому серверу
копии**, и monkeypatch до того процесса не доездает вовсе: там проверяется
только то, что видно серверу — строки базы. Правила, живущие в Redis (охлада
адресата, пауза анкеты), списком проверяются на самом помощнике; путь
«список → помощник» накрывается случаем из базы (офлайн, пауза пары), а кнопка
падает по-настоящему: у выдуманного сайта нет адреса API, и реальная дверь
помечает попытку без всякой подмены.
"""

import subprocess
import time
import uuid

import pytest

from mysql_.mysql_ import mysql_get_db_async

from src.dating import send_round as round_
from src.dating.mail_send import DatingMailWaitError, mail_send_fail
from src.dating.mail_wait import MAIL_WAIT_GIRL_CHIP, MAIL_WAIT_KEY
from src.dating.send_round import _oldest, send_card_wait, send_round_run

pytestmark = pytest.mark.mail

# Выдуманный сайт: у соседа его нет, живую отправку задеть невозможно.
SITE = 999_781

# Смена во все сутки — чтобы проверки про круг не зависели от часа прогона.
# (`24:00`, а не `23:45`: правая граница строгая, дыры в четверть часа не
# остаётся — разбор в `test_send_round.py`.)
ALWAYS = '[{"from": "00:00", "to": "24:00"}]'


@pytest.fixture
def no_wait(monkeypatch):
    """Остуду адресатов гасит и учётку пауз чистит: тест — про порядок, не про Redis.

    Живого Redis в сюите нет, и помощники честно отвечают пустотой, — но
    `round_mem_due` держит состояние между тестами в модуле, и нечищеная
    учётка отдавала бы следующий тест тому же письму.
    """
    monkeypatch.setattr(round_, 'mail_wait_uids', lambda site_id: [])
    round_._sent_at.clear()

    yield

    round_._sent_at.clear()


@pytest.fixture
def stand(monkeypatch):
    """⚠⚠ Сужает **отбор** круга до стенда, флаг в базе не трогает вовсе.

    Тот же приём и та же причина, что в `test_invite_send_round.py`: живой
    воркер крутит круг по той же базе, и строка сайта с поднятым флагом
    досталась бы ему раньше теста — а его отправку monkeypatch не подменяет,
    это другой процесс. Строки сайта в базе у выдуманного номера нет вовсе.
    """
    async def ours():
        return [{'site_id': SITE, 'schedule': ALWAYS,
                 'mailing_send_on': 1, 'reply_send_on': 0,
                 'favorite_mail_send_on': 0, 'chat_intention_do_on': 0}]

    monkeypatch.setattr(round_, '_sites', ours)

    return SITE


async def _try(id_: int, seconds_ago: int) -> None:
    """Ставит отметку попытки прямо в базу — для проверок самого порядка.

    Дверь `mail_send_fail` ставит `NOW()`; давность подкручивается здесь,
    потому что проверка «между тронутыми первым идёт давний» иначе значит
    гонять секунды.
    """
    async with mysql_get_db_async() as db:
        await db.execute(
            'UPDATE mailing SET try_at = NOW() - INTERVAL %s SECOND'
            ' WHERE id = %s', (seconds_ago, int(id_)))
        await db.connection.commit()


async def _letter(profile_id: int, table: str = 'mailing',
                  uid: str = '') -> int:
    """Кладёт черновик в пул напрямую: набор требует адресатов в сети."""
    uid = uid or f'rot-{table}-{uuid.uuid4().hex[:8]}'

    async with mysql_get_db_async() as db:
        await db.execute(
            f'INSERT INTO {table} (site_id, profile_id, uid, name, body,'
            " status, error) VALUES (%s, %s, %s, 'Тест', 'Текст письма',"
            " 'draft', '')",
            (SITE, profile_id, uid))
        await db.connection.commit()
        await db.execute('SELECT LAST_INSERT_ID() AS id')

        return int((await db.fetchone())['id'])


async def _try_at(id_: int, table: str = 'mailing'):
    """Отметка попытки на строке — или None, если её не было."""
    async with mysql_get_db_async() as db:
        await db.execute(f'SELECT try_at FROM {table} WHERE id = %s',
                         (int(id_),))

        return (await db.fetchone())['try_at']


def _arm_cooling(uid: str, seconds: int = 3600) -> None:
    """Заводит остуду адресата в настоящем Redis — та же запись, что ставит
    отказ отправки (`mail_wait_mark`: zset, score — предел времени).

    ⚠ Списки отвечает одноразовый сервер в другом процессе, подмена его не
    касается; выдуманный номер сайта не пересекается с чужими ключами.
    """
    got = subprocess.run(
        ['docker', 'exec', 'dating_redis', 'redis-cli', 'ZADD',
         MAIL_WAIT_KEY.format(site_id=SITE),
         str(int(time.time()) + int(seconds)), str(uid)],
        capture_output=True, text=True, timeout=30)
    if got.returncode != 0:
        pytest.skip(f' dating_redis нет под рукой: {got.stderr[:120]}')


def _drop_cooling() -> None:
    """Снимает остуду выдуманного сайта: ключ весь принадлежит тестам."""
    subprocess.run(['docker', 'exec', 'dating_redis', 'redis-cli', 'DEL',
                    MAIL_WAIT_KEY.format(site_id=SITE)],
                   capture_output=True, timeout=30)


class TestNeverStampedBeforeTry:
    """⚠ Колонка — NULL **без DEFAULT**, и это не опрятность.

    Ставь кто на правку схемы DEFAULT CURRENT_TIMESTAMP, каждая строка в
    четырёх пулах получила бы время миграции — и очередь на первом же тике
    выстроилась бы по нему, а не по попыткам. Тот же урок, что с датой
    неудачного письма (`CLAUDE.md`).
    """

    async def test_a_fresh_draft_carries_no_mark(self, no_wait, girl_make):
        girl = await girl_make('rot')
        letter = await _letter(girl)

        assert await _try_at(letter) is None


class TestRotation:
    """Тронутое письмо уступает место нетронутому."""

    async def test_a_waiting_card_gives_way_to_an_untried_one(self, no_wait,
                                                              girl_make):
        """Сам замер с прода: ждущая карта больше не первая, к ней вернутся позже."""
        girl = await girl_make('rot')
        first = await _letter(girl, uid='rot-a')
        second = await _letter(girl, uid='rot-b')

        row = await _oldest(SITE, 'mailing')
        assert int(row['id']) == first, 'первой зовут нетронутую по номеру'

        await mail_send_fail(row, DatingMailWaitError('ящик полон'),
                             table='mailing')

        assert await _try_at(first) is not None, 'дверь не поставила метку'

        row = await _oldest(SITE, 'mailing')
        assert int(row['id']) == second, 'ждущая карта не уступила очередь'

    async def test_the_waiting_card_stays_payable(self, no_wait, girl_make):
        """Метка меняет **порядок**, но не годность: строка осталась черновиком
        без причины — круг к ней вернётся, когда пройдёт причина."""
        girl = await girl_make('rot')
        letter = await _letter(girl)
        row = await _oldest(SITE, 'mailing')

        await mail_send_fail(row, DatingMailWaitError('ящик полон'),
                             table='mailing')

        async with mysql_get_db_async() as db:
            await db.execute('SELECT status, error FROM mailing WHERE id = %s',
                             (int(letter),))
            row = await db.fetchone()

        assert row['status'] == 'draft' and row['error'] == ''

    async def test_between_the_tried_the_longer_waiting_goes_first(self,
                                                                   no_wait,
                                                                   girl_make):
        """Между тронутыми — по давности попытки, а не по номеру."""
        girl = await girl_make('rot')
        older = await _letter(girl, uid='rot-old')
        newer = await _letter(girl, uid='rot-new')

        await _try(newer, 60)
        await _try(older, 3600)

        row = await _oldest(SITE, 'mailing')
        assert int(row['id']) == older

    async def test_an_untried_one_beats_any_tried(self, no_wait, girl_make):
        """Свежее письмо идёт в очередь — ждать своей очереди на хвосте, а не вечно."""
        girl = await girl_make('rot')
        tried = await _letter(girl, uid='rot-tried')
        fresh = await _letter(girl, uid='rot-fresh')

        await _try(tried, 86400)

        row = await _oldest(SITE, 'mailing')
        assert int(row['id']) == fresh


class TestRoundRotates:
    """Ротация на бегу: круг, который ткнулся в ждущую, в следующий тик шлёт другую."""

    async def test_a_waiting_letter_does_not_block_the_pool(self, stand,
                                                            no_wait, girl_make,
                                                            monkeypatch):
        girl = await girl_make('rot')
        first = await _letter(girl, uid='rot-w1')
        second = await _letter(girl, uid='rot-w2')

        touched = []

        async def fake(letter, profile, base, password, table='mailing'):
            touched.append(int(letter['id']))
            raise DatingMailWaitError('сайт сейчас не даёт: предел входящих')

        monkeypatch.setattr(round_, 'mail_send_one', fake)

        got = await send_round_run(0)
        assert got['waited'] == 1
        assert touched == [first]

        # Пауза «подождать» не съедена — второй заход приходит сразу.
        got = await send_round_run(0)
        assert got['waited'] == 1
        assert touched == [first, second], 'вторая карта так и не дождалась хода'

    async def test_a_crash_marks_the_attempt_too(self, stand, no_wait,
                                                 girl_make, monkeypatch):
        """Сбой панели (не отказ сайта) — тоже попытка: без метки он крутился бы
        каждые 5 секунд, ровно как та петля с прода, только тише."""
        girl = await girl_make('rot')
        letter = await _letter(girl)

        async def fake(letter_, profile, base, password, table='mailing'):
            raise RuntimeError('агент упал')

        monkeypatch.setattr(round_, 'mail_send_one', fake)

        got = await send_round_run(0)
        assert got['failed'] == 1

        assert await _try_at(letter) is not None, 'сбой не оставил метки попытки'


class TestTheButtonSharesTheDoor:
    """Кнопка «отправить сейчас» помечает попытку тем же путём, что круг.

    ⚠ Без всякой подмены: запрос идёт одноразовому серверу в другом процессе,
    monkeypatch его не касается. Клик падает **по-настоящему** — у выдуманного
    сайта нет адреса API, и настоящая `mail_send_one` честно отказывает. Пометка
    остаётся настоящей дверью, что ставит её в проде.
    """

    async def test_a_failed_click_leaves_the_mark(self, auth_client,
                                                  girl_make):
        girl = await girl_make('rot')
        letter = await _letter(girl, 'favorite_mail')

        got = await auth_client.post(
            f'/api/dating/favorite-mail/{letter}/send')
        assert got.status_code == 409, got.text[:200]

        assert await _try_at(letter, 'favorite_mail') is not None


class TestWaitReasons:
    """`send_card_wait` отвечает теми же правилами, по которым очередь её пропускает."""

    async def test_a_girl_closed_to_noncontacts_is_named(self, no_wait,
                                                         monkeypatch):
        monkeypatch.setattr(
            round_, 'mail_wait_girl_words',
            lambda site_id, table=None: {4242: f'{MAIL_WAIT_GIRL_CHIP} до 00:00 UTC'})

        wait = await send_card_wait(SITE, 'favorite_mail',
                                    [{'id': 1, 'profile_id': 4242,
                                      'uid': 'u',
                                      # ⚠ пробованная: пауза держит только её
                                      'try_at': '2026-11-28 10:00:00'}])

        assert wait[1].startswith(MAIL_WAIT_GIRL_CHIP), wait[1]
        assert '00:00 UTC' in wait[1], 'срок паузы анкеты не назван'

    async def test_a_cooling_recipient_is_named(self, monkeypatch):
        monkeypatch.setattr(round_, 'mail_wait_uids', lambda site_id: ['u1'])
        monkeypatch.setattr(round_, 'mail_wait_left',
                            lambda site_id, uid: 3600)

        wait = await send_card_wait(SITE, 'mailing',
                                    [{'id': 1, 'profile_id': 1, 'uid': 'u1'}])

        assert 'предел входящих' in wait[1] and '1 ч' in wait[1]

    async def test_a_pair_gap_is_named(self, monkeypatch):
        async def busy(site_id):
            return {(4242, 'u1'): {'left': 5400, 'gap': 7200}}

        monkeypatch.setattr('src.dating.favorite_mail._pairs_busy', busy)
        monkeypatch.setattr(round_, 'mail_wait_uids', lambda site_id: [])

        wait = await send_card_wait(SITE, 'favorite_mail',
                                    [{'id': 1, 'profile_id': 4242,
                                      'uid': 'u1'}])

        assert wait[1] == 'пауза пары ещё 1 ч 30 мин'

    async def test_a_girl_offline_is_named(self, no_wait, girl_make):
        """Офлайн — настоящей анкетой: та же проверка `status`, что в отборе пула."""
        girl = await girl_make('rot')  # не в сети — фабрика её в сеть не ставит

        wait = await send_card_wait(SITE, 'favorite_mail',
                                    [{'id': 1, 'profile_id': girl,
                                      'uid': 'u1'}])

        assert wait[1] == 'анкета не в сети'

    async def test_an_online_girl_is_not_held_by_that_rule(self, no_wait,
                                                          girl_make):
        girl = await girl_make('rot', online=True)

        wait = await send_card_wait(SITE, 'favorite_mail',
                                    [{'id': 1, 'profile_id': girl,
                                      'uid': 'u1'}])

        assert wait == {}

    async def test_the_first_match_wins_from_general_to_particular(self,
                                                                   no_wait,
                                                                   monkeypatch,
                                                                   girl_make):
        """Девушка, закрытая пулом, не «ждёт офлайна»: общий повод первее частного."""
        girl = await girl_make('rot')
        monkeypatch.setattr(
            round_, 'mail_wait_girl_words',
            lambda site_id, table=None: {girl: f'{MAIL_WAIT_GIRL_CHIP} до 00:00 UTC'})

        wait = await send_card_wait(SITE, 'favorite_mail',
                                    [{'id': 7, 'profile_id': girl,
                                      'uid': 'u1',
                                      'try_at': '2026-11-28 10:00:00'}])

        assert wait[7] == f'{MAIL_WAIT_GIRL_CHIP} до 00:00 UTC'

    async def test_a_letter_with_no_halt_has_no_reason(self, no_wait,
                                                       girl_make):
        girl = await girl_make('rot', online=True)

        wait = await send_card_wait(SITE, 'favorite_mail',
                                    [{'id': 1, 'profile_id': girl,
                                      'uid': 'u1'}])

        assert wait == {}

    async def test_an_unknown_table_answers_empty(self, no_wait):
        """Не свой таблице список не доверяют: правила на неё не писали."""
        wait = await send_card_wait(SITE, 'site_ad_clicks',
                                    [{'id': 1, 'profile_id': 1, 'uid': 'u'}])

        assert wait == {}


class TestListCarriesTheWait:
    """⚠ Список пула приносит `wait` и `try_at` со значением: карточка рисует
    чип только по значению, а молчаливая потеря поля выглядела бы как «ждёт 0»."""

    async def test_the_favourites_list_names_a_girl_offline(self, auth_client,
                                                            no_wait,
                                                            girl_make):
        girl = await girl_make('rot')
        letter = await _letter(girl, 'favorite_mail')

        got = await auth_client.get('/api/dating/favorite-mail',
                                    params={'site_id': SITE})
        assert got.status_code == 200, got.text[:200]

        row = _row_of(got.json()['result'], letter)
        assert row['wait'] == 'анкета не в сети'
        assert row['try_at'] == ''

    async def test_the_mark_of_a_try_reaches_the_card(self, auth_client,
                                                      girl_make):
        """Метка, оставленная настоящей кнопкой (сбой — сайт без API), доходит
        до карточки строкой времени, а не объектом JSON."""
        girl = await girl_make('rot')
        letter = await _letter(girl, 'favorite_mail')

        got = await auth_client.post(f'/api/dating/favorite-mail/{letter}/send')
        assert got.status_code == 409, got.text[:200]

        got = (await auth_client.get('/api/dating/favorite-mail',
                                     params={'site_id': SITE})).json()
        row = _row_of(got['result'], letter)
        assert row['try_at'].startswith('202'), f'метка попытки не дошла: {row}'

    async def test_the_mailing_list_names_a_cooling_recipient(self, auth_client,
                                                              girl_make):
        """Правило остуды в списке — по **настоящему** Redis: на_process-подмена
        до сервера не доездает, поэтому заводю ту же запись, что ставит отказ
        отправки, и под выдуманным номером сайта."""
        girl = await girl_make('rot')
        letter = await _letter(girl, 'mailing', uid='991234')

        _arm_cooling('991234')
        try:
            got = await auth_client.get('/api/dating/mailing',
                                        params={'site_id': SITE})
            assert got.status_code == 200, got.text[:200]

            row = _row_of(got.json()['result'], letter)
            assert 'предел входящих' in row['wait']
        finally:
            _drop_cooling()

    async def test_a_sent_letter_carries_an_empty_wait(self, auth_client,
                                                      girl_make):
        """У ушедшего письма вопроса «почему ждёт» нет — но поле обязано быть:
        страница читает его у всех строк."""
        girl = await girl_make('rot')
        letter = await _letter(girl)

        async with mysql_get_db_async() as db:
            await db.execute("UPDATE mailing SET status = 'sent',"
                             " sent_at = NOW() WHERE id = %s", (letter,))
            await db.connection.commit()

        got = await auth_client.get('/api/dating/mailing',
                                    params={'site_id': SITE,
                                            'status_': 'all'})
        row = _row_of(got.json()['result'], letter)
        assert 'wait' in row and row['wait'] == ''


def _row_of(rows: list, letter: int) -> dict:
    """Строка списка по номеру письма."""
    for one in rows:
        if int(one['id']) == int(letter):
            return one

    raise AssertionError(f'письма #{letter} нет в списке: {rows[:2]}')
