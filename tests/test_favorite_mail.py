"""Переписка с избранными: кого берём в работу и как часто ему пишем.

⚠ **Наружу сюита не ходит** (`testing.md`): письмо — это вызов LLM у соседа, а
отправка ещё и письмо живому человеку. Проверяется то, что решается у нас и что
ломается:

* `_quiet` — кого берём. Цена ошибки высока в обе стороны: пропустим — разговор
  так и останется затихшим, возьмём лишнего — напишем тому, кто ждёт ответа на
  своё письмо, и наше «а расскажи ещё» придёт вместо ответа.
* `_too_soon` — как часто пишем. Оба предела **про человека**, а не про сайт, и
  считают они **всё ушедшее ему**: письма считает получатель, а не наши таблицы.

Анкеты заводятся на стенде (`STAND_SITE`), а не на рабочем сайте — иначе их
подхватили бы круги воркера.
"""

import json
import time
import uuid

import pytest

from mysql_.mysql_ import mysql_get_db_async

from src.dating import favorite_mail
from src.dating.favorite_mail import _quiet, _too_soon

pytestmark = pytest.mark.mail
# Стенд у соседа: круги воркера про него не знают — его нет в `site_`.
STAND_SITE = 999_343


async def _profile(auth_client, tag: str) -> int:
    made = (await auth_client.post('/api/dating/profile', json={
        'site_id': STAND_SITE,
        'login': f'{tag}-{uuid.uuid4().hex[:8]}@example.com',
        'password': 'x'})).json()['result']

    return int(made)


def _answer(value):
    """Подменённый агент: всегда один и тот же текст."""
    async def _got(*args, **kwargs):
        return value

    return _got


def _nothing():
    """Подменённая сверка звёзд: тест её не проверяет и на сайт не ходит."""
    async def _got(*args, **kwargs):
        return None

    return _got


async def _star(profile_id: int, uid: str, state: str = 'done') -> None:
    """Человек в избранных у этой анкеты — с него и начинается отбор.

    ⚠ **И в сети**: отбор берёт только тех, кто там сейчас (фикстура `online`).
    Отмечается здесь, а не отдельным вызовом в каждом тесте: звёздочка и
    присутствие — два обязательных условия одного и того же, и порознь о втором
    забывают.
    """
    if str(uid) not in _ONLINE:
        _ONLINE.append(str(uid))

    async with mysql_get_db_async() as db:
        await db.execute(
            'INSERT INTO favorite (site_id, profile_id, uid, state)'
            ' VALUES (%s, %s, %s, %s)'
            ' ON DUPLICATE KEY UPDATE state = VALUES(state)',
            (STAND_SITE, int(profile_id), str(uid), state))
        await db.connection.commit()


async def _his(profile_id: int, uid: str, when: str) -> None:
    """Его письмо: последнее слово за ним."""
    async with mysql_get_db_async() as db:
        await db.execute(
            'INSERT INTO mail_in'
            ' (site_id, profile_id, message_id, uid, name, body, date_post)'
            " VALUES (%s, %s, %s, %s, 'X', 'hi', %s)",
            (STAND_SITE, int(profile_id), int(uuid.uuid4().int % 10 ** 9), str(uid),
             when))
        await db.connection.commit()


async def _hers(profile_id: int, uid: str, when: str,
                table: str = 'mailing') -> None:
    """Наше ушедшее письмо. Таблица — чтобы проверить, что считаются все три."""
    async with mysql_get_db_async() as db:
        if table == 'mailing':
            await db.execute(
                'INSERT INTO mailing'
                ' (site_id, profile_id, mail_id, uid, name, body, status, sent_at)'
                " VALUES (%s, %s, NULL, %s, 'X', 'hi', 'sent', %s)",
                (STAND_SITE, int(profile_id), str(uid), when))
        elif table == 'favorite_mail':
            await db.execute(
                'INSERT INTO favorite_mail'
                ' (site_id, profile_id, uid, name, body, status, sent_at)'
                " VALUES (%s, %s, %s, 'X', 'hi', 'sent', %s)",
                (STAND_SITE, int(profile_id), str(uid), when))
        else:
            raise AssertionError(f'неизвестная таблица: {table}')

        await db.connection.commit()


async def _draft(profile_id: int, uid: str) -> None:
    """Черновик в пуле: такому человеку второй не готовим."""
    async with mysql_get_db_async() as db:
        await db.execute(
            'INSERT INTO favorite_mail'
            ' (site_id, profile_id, uid, name, body, status)'
            " VALUES (%s, %s, %s, 'X', 'hi', 'draft')",
            (STAND_SITE, int(profile_id), str(uid)))
        await db.connection.commit()


# Кто «сейчас в сети» по мнению подменённого снимка ленты. Список, а не
# переменная: его пополняет `_star`, а читает фикстура `online` — из вложенной
# функции переменную не присвоить без `global`.
_ONLINE: list[str] = []


class _FakeRedis:
    """Redis настолько, насколько его читает `online_show`.

    ⚠ **Подмена, а не живой Redis.** Сюита гоняется с хоста, а `dating_redis`
    виден только внутри docker-сети: порт наружу не выведен, и обращение к нему
    падает `Name or service not known`. Тот же приём и та же причина, что в
    `test_online_guard.py`.
    """

    def get(self, key):
        return json.dumps({'at': time.time(),
                           'people': [{'uid': one} for one in _ONLINE]})


@pytest.fixture(autouse=True)
def online(monkeypatch):
    """Снимок ленты: в сети те, кого завёл `_star`.

    ⚠ `autouse`: **отбор берёт только тех, кто сейчас в сети**, и без подмены
    `_quiet` не вернул бы ничего — половина здешних проверок молча проверяла бы
    пустоту. Забыть её в новом тесте проще, чем заметить такую тишину.
    """
    _ONLINE.clear()
    monkeypatch.setattr(favorite_mail, 'online_show',
                        lambda site_id, uid: {'online': str(uid) in _ONLINE,
                                              'known': True, 'age_sec': 5})

    yield

    _ONLINE.clear()


@pytest.fixture
async def girl(auth_client):
    """Анкета на стенде, **в сети**: иначе отбор её не берёт вовсе.

    Убирается вместе со своими письмами — по внешнему ключу.
    """
    made = await _profile(auth_client, 'fav')

    async with mysql_get_db_async() as db:
        await db.execute("UPDATE profile SET status = 'online' WHERE id = %s",
                         (made,))
        await db.connection.commit()

    yield made

    async with mysql_get_db_async() as db:
        await db.execute('DELETE FROM favorite WHERE profile_id = %s', (made,))
        await db.connection.commit()

    await auth_client.delete(f'/api/dating/profile/{made}')


class TestWhoIsQuiet:
    """Кого берём в работу: разговор затих, последнее слово наше."""

    async def test_our_last_word_makes_the_pair_quiet(self, girl):
        uid = f'u{uuid.uuid4().hex[:8]}'
        await _star(girl, uid)
        await _his(girl, uid, '2026-09-01 10:00:00')
        await _hers(girl, uid, '2026-09-01 12:00:00')

        row = next((r for r in await _quiet(STAND_SITE) if r['uid'] == uid), None)

        assert row, 'затихший разговор не найден'
        assert str(row['last_side']) == 'her'

    async def test_his_last_word_is_not_our_business(self, girl):
        """⚠ Ответить ему должен **круг ответов**, а не этот.

        Напиши мы «а расскажи ещё» поверх его вопроса — он получил бы вместо
        ответа смену темы, то есть ровно то, из-за чего люди и перестают отвечать.
        """
        uid = f'u{uuid.uuid4().hex[:8]}'
        await _star(girl, uid)
        await _hers(girl, uid, '2026-09-01 10:00:00')
        await _his(girl, uid, '2026-09-01 12:00:00')

        assert not [r for r in await _quiet(STAND_SITE) if r['uid'] == uid]

    async def test_a_pending_draft_blocks_a_second_one(self, girl):
        """⚠ Второй черновик тому же человеку — это два письма подряд, просто
        отложенных: отправитель шлёт их по одному, но оба уйдут ему."""
        uid = f'u{uuid.uuid4().hex[:8]}'
        await _star(girl, uid)
        await _his(girl, uid, '2026-09-01 10:00:00')
        await _hers(girl, uid, '2026-09-01 12:00:00')
        assert [r for r in await _quiet(STAND_SITE) if r['uid'] == uid]

        await _draft(girl, uid)

        assert not [r for r in await _quiet(STAND_SITE) if r['uid'] == uid]

    async def test_someone_who_is_not_a_favorite_is_skipped(self, girl):
        """Избранным человек стал потому, что ответил на письмо. Не ответивший —
        это рассылка, а не затихший разговор."""
        uid = f'u{uuid.uuid4().hex[:8]}'
        await _hers(girl, uid, '2026-09-01 12:00:00')

        assert not [r for r in await _quiet(STAND_SITE) if r['uid'] == uid]

    async def test_the_profile_filter_narrows_not_replaces(self, girl, auth_client):
        """Номер анкеты сужает отбор, а не отбирает заново: чужая анкета своих
        затихших разговоров здесь не увидит."""
        uid = f'u{uuid.uuid4().hex[:8]}'
        await _star(girl, uid)
        await _his(girl, uid, '2026-09-01 10:00:00')
        await _hers(girl, uid, '2026-09-01 12:00:00')

        other = await _profile(auth_client, 'other')
        try:
            ours = await _quiet(STAND_SITE, girl)
            others = await _quiet(STAND_SITE, other)

            assert [r for r in ours if r['uid'] == uid]
            assert not [r for r in others if r['uid'] == uid]
        finally:
            await auth_client.delete(f'/api/dating/profile/{other}')

    async def test_the_oldest_silence_comes_first(self, girl):
        """Порядок — по давности: заход обрывается по потолку, и обрывать он
        должен свежих, а не забытых."""
        old, fresh = f'u{uuid.uuid4().hex[:8]}', f'u{uuid.uuid4().hex[:8]}'
        for uid, when in ((old, '2026-08-01 10:00:00'),
                          (fresh, '2026-09-05 10:00:00')):
            await _star(girl, uid)
            await _his(girl, uid, '2026-07-01 10:00:00')
            await _hers(girl, uid, when)

        rows = [r['uid'] for r in await _quiet(STAND_SITE, girl)]

        assert rows.index(old) < rows.index(fresh)


class TestAStarIsEnough:
    """⚠⚠ Переписки в нашей базе быть не обязано — хватает звёздочки.

    Поймано заказчиком: вкладка отвечала «писать некому: у избранных нет затихших
    разговоров» при полутора десятках избранных у каждой анкеты. Звёздочку ставят
    тому, кто отвечал; разговор шёл на сайте — возможно, задолго до панели, — и в
    нашу базу мог не приехать вовсе.
    """

    async def test_a_favorite_without_any_letters_is_taken(self, girl):
        uid = f'u{uuid.uuid4().hex[:8]}'
        await _star(girl, uid)

        row = next((r for r in await _quiet(STAND_SITE) if r['uid'] == uid), None)

        assert row, 'избранный без переписки в базе выпал из отбора'
        assert row['last_side'] is None, 'последним не говорил никто — так и должно'

    async def test_he_still_loses_to_the_reply_round(self, girl):
        """⚠ Правило «последним говорил не он» осталось: есть его письмо —
        отвечает круг ответов, а не приставание."""
        uid = f'u{uuid.uuid4().hex[:8]}'
        await _star(girl, uid)
        await _his(girl, uid, '2026-09-01 10:00:00')

        assert not [r for r in await _quiet(STAND_SITE) if r['uid'] == uid]

    async def test_those_never_written_to_come_first(self, girl):
        """Повод у них старше всех: им не писали никогда."""
        never = f'u{uuid.uuid4().hex[:8]}'
        talked = f'u{uuid.uuid4().hex[:8]}'
        await _star(girl, never)
        await _star(girl, talked)
        await _hers(girl, talked, '2026-09-01 12:00:00')

        order = [r['uid'] for r in await _quiet(STAND_SITE)
                 if r['uid'] in (never, talked)]

        assert order == [never, talked], order

    def test_the_history_does_not_travel_to_the_agent_at_all(self):
        """⚠⚠ Истории агент больше не видит **никогда** — шаблон один на весь пул.

        Прежде она уезжала переменной, и пустая была особым случаем: промпт у
        соседа ветвился `%if mail_history%` и в этом случае запрещал ссылаться на
        прошлые слова. Теперь история пуста всегда — запрет стал безусловным, а
        переменная отвязана от workflow.

        Проверяется здесь именно это: письмо не притворяется продолжением
        разговора, которого агент не видел.
        """
        import inspect

        from src.dating import mail_agent

        code = inspect.getsource(mail_agent.favorite_mail_template)

        assert 'mail_history' not in code, \
            'переписка снова уезжает агенту — шаблон общий, ей там не место'
        assert 'girl_card' not in code and 'companion_card' not in code, \
            'карточки снова уезжают агенту: подстраиваться шаблону не подо что'


class TestTheBatchCountsLetters:
    """⚠⚠ Потолок захода считает **написанное**, а не просмотренное.

    Поймано на живом проде: у сайта 88 избранных, в сети из них трое, и первые
    десять по порядку (а порядок — по давности разговора) оказывались офлайн.
    Круг честно докладывал «готово 0, пропущено 10, осталось 78» — и так каждый
    заход, пока пул стоял пустым.

    Пропуск стоит чтения Redis и одного запроса в базу; письмо — вызова LLM.
    Ограничивать надо второе.
    """

    async def test_skips_do_not_eat_the_batch(self, girl, monkeypatch):
        """Девять офлайн впереди и один в сети — письмо всё равно готовится."""
        import src.dating.favorite_mail as mod

        away = [f'u{uuid.uuid4().hex[:8]}' for _ in range(9)]
        near = f'u{uuid.uuid4().hex[:8]}'
        for uid in (*away, near):
            await _star(girl, uid)
            await _hers(girl, uid, '2026-09-01 10:00:00')

        # ⚠ Порядок задаём сами: у всех пар одно время, и полагаться на него
        # значило бы проверять сортировку базы, а не правило. Настоящий отбор при
        # этом остаётся — подменяется только порядок и состав.
        real = mod._quiet
        order = {uid: n for n, uid in enumerate((*away, near))}

        async def quiet(site_id, profile_id=0):
            rows = await real(site_id, profile_id)

            return sorted([one for one in rows if str(one['uid']) in order],
                          key=lambda one: order[str(one['uid'])])

        monkeypatch.setattr(mod, '_quiet', quiet)
        monkeypatch.setattr(mod, 'online_show',
                            lambda site_id, uid: {'online': str(uid) == near,
                                                  'known': True})
        monkeypatch.setattr(mod, '_template',
                            _answer('Давно не общались, как ты?'))
        monkeypatch.setattr(mod, '_sync_stars', _nothing())

        got = await mod.favorite_mail_make(STAND_SITE, limit=1)

        assert got['made'] == 1, got
        assert got['skipped'] == 9, 'пропуски перестали считаться вовсе'


class TestBothMustBeOnline:
    """⚠ Письмо готовится, только когда **оба** на сайте.

    Возобновление разговора застаёт человека в почте, пока он там сидит, и
    отвечает он девушке — а та должна быть на месте. Написанное в отсутствие
    уйдёт в груду непрочитанного, и предел в два письма в сутки будет потрачен
    впустую.
    """

    async def test_a_sleeping_profile_writes_nobody(self, girl):
        uid = f'u{uuid.uuid4().hex[:8]}'
        await _star(girl, uid)
        await _his(girl, uid, '2026-09-01 10:00:00')
        await _hers(girl, uid, '2026-09-01 12:00:00')
        assert [r for r in await _quiet(STAND_SITE) if r['uid'] == uid]

        async with mysql_get_db_async() as db:
            await db.execute("UPDATE profile SET status = 'offline'"
                             ' WHERE id = %s', (girl,))
            await db.connection.commit()

        assert not [r for r in await _quiet(STAND_SITE) if r['uid'] == uid], \
            'анкета не в сети, а письмо ей готовится'

    async def test_an_absent_companion_is_skipped_with_a_reason(self, girl):
        """⚠ Отбор его вернёт — присутствие смотрит уже набор, и смотрит по
        снимку ленты, которого в SQL нет. Проверяем по самому набору: пара
        пропущена, и причина названа человеком."""
        from src.dating.favorite_mail import favorite_mail_make

        uid = f'u{uuid.uuid4().hex[:8]}'
        await _star(girl, uid)
        await _his(girl, uid, '2026-09-01 10:00:00')
        await _hers(girl, uid, '2026-09-01 12:00:00')

        # Ушёл с сайта, пока мы думали.
        _ONLINE.remove(uid)

        got = await favorite_mail_make(STAND_SITE, 5, girl)
        reasons = [one for one in got['reasons'] if uid in one]

        assert got['made'] == 0, 'письмо ушедшему всё-таки написали'
        assert reasons, f'пропуск не назван: {got["reasons"]}'
        assert 'не в сети' in reasons[0], reasons[0]

    async def test_limits_are_checked_before_the_agent(self, girl, monkeypatch):
        """⚠ **Дешёвое раньше платного.** Каждое письмо — вызов LLM, и платить за
        него ради того, чтобы потом выбросить по пределу, незачем. Проверяем
        прямо: агент не должен быть позван вовсе."""
        from src.dating.favorite_mail import favorite_mail_make

        called = []

        async def _agent(*args, **kwargs):
            called.append(args)

            return 'письмо'

        monkeypatch.setattr(favorite_mail, '_template', _agent)

        uid = f'u{uuid.uuid4().hex[:8]}'
        await _star(girl, uid)
        await _his(girl, uid, '2026-09-01 10:00:00')
        # Два письма за сутки — предел исчерпан.
        await _hers(girl, uid, _now_minus_hours(3))
        await _hers(girl, uid, _now_minus_hours(4))

        got = await favorite_mail_make(STAND_SITE, 5, girl)

        assert got['made'] == 0
        assert not called, 'агента позвали, хотя предел уже исчерпан'


class TestHowOften:
    """⚠ Оба предела — про человека, а не про сайт, и считают всё ушедшее ему."""

    async def test_the_daily_cap_counts_every_pool(self, girl):
        """⚠ Считается и рассылка, и возобновление: в почту они приходят вместе,
        и разделять их стал бы только тот, кто смотрит в наши таблицы."""
        uid = f'u{uuid.uuid4().hex[:8]}'
        await _hers(girl, uid, _now_minus_hours(5))
        await _hers(girl, uid, _now_minus_hours(6), 'favorite_mail')

        why = await _too_soon(girl, uid, per_day=2, gap=3600)

        assert 'за сутки' in why, why
        # ⚠ Склонение — не украшение: «ушло 2 писем» читается как сбой
        # подстановки, то есть заставляет усомниться в самом числе.
        assert '2 письма' in why, why

    async def test_the_gap_holds_even_under_the_daily_cap(self, girl):
        """⚠ Второй предел про другое: два письма подряд за час выдают робота,
        даже когда за сутки их всего два."""
        uid = f'u{uuid.uuid4().hex[:8]}'
        await _hers(girl, uid, _now_minus_hours(1))

        why = await _too_soon(girl, uid, per_day=5, gap=4 * 3600)

        # ⚠ Проверяем **суть, а не слова**: причина идёт человеку на экран, её
        # переписывают, и тест, приколоченный к формулировке, падал бы на каждой
        # правке текста. Здесь важно одно — отказ назван и назван срок.
        assert why, 'пауза не сработала'
        assert 'можно через' in why, why

    async def test_a_long_silence_passes_both(self, girl):
        uid = f'u{uuid.uuid4().hex[:8]}'
        await _hers(girl, uid, _now_minus_hours(30))

        assert await _too_soon(girl, uid, per_day=2, gap=4 * 3600) == ''

    async def test_yesterdays_letter_still_counts_for_the_gap(self, girl):
        """⚠ Пауза считается от последнего ушедшего **за любой срок**, а не за
        сутки. Письмо, ушедшее 25 часов назад, в дневной счёт не попадает — и без
        отдельного запроса пауза считалась бы от пустоты.
        """
        uid = f'u{uuid.uuid4().hex[:8]}'
        await _hers(girl, uid, _now_minus_hours(25))

        # Дневной предел свободен, а пауза в двое суток — нет.
        why = await _too_soon(girl, uid, per_day=2, gap=48 * 3600)

        assert why, 'вчерашнее письмо не попало в счёт паузы'
        assert 'можно через' in why, why


class TestReasonNamesTheRecipient:
    """⚠ Причина пропуска обязана называть, **о ком** она.

    «За сутки ему уже ушло два письма» — правда, но про кого? В заходе десятки
    пар, причины копятся списком, и безымянная строка читается как отказ
    неизвестно по кому: человек видит её и не знает, ждать ли и кого проверять.
    Поймано вопросом «непонятно, кому ушло».
    """

    async def test_the_skip_reason_carries_name_and_uid(self, girl):
        from src.dating.favorite_mail import favorite_mail_make

        uid = f'u{uuid.uuid4().hex[:8]}'
        await _star(girl, uid)
        await _his(girl, uid, '2026-09-01 10:00:00')
        # Последнее слово наше — пара попадает в отбор; и два письма за сутки,
        # чтобы она тут же упёрлась в предел.
        await _hers(girl, uid, _now_minus_hours(3))
        await _hers(girl, uid, _now_minus_hours(4))

        async with mysql_get_db_async() as db:
            await db.execute(
                'UPDATE companion SET name = %s'
                ' WHERE site_id = %s AND uid = %s', ('Semen', STAND_SITE, uid))
            await db.execute(
                'INSERT INTO contact (profile_id, uid, name, photo)'
                " VALUES (%s, %s, 'Semen', '')", (girl, uid))
            await db.connection.commit()

        got = await favorite_mail_make(STAND_SITE, 5, girl)
        reasons = [one for one in got['reasons'] if uid in one]

        assert reasons, f'адресата нет ни в одной причине: {got["reasons"]}'
        assert 'Semen' in reasons[0], reasons[0]


class TestWhyThePoolIsEmpty:
    """⚠ Пустой пул обязан объяснять себя **на странице**, а не в журнале воркера.

    Наблюдалось живьём и этим чинится: анкета в сети, её избранный в сети,
    генерация включена — а пул пуст, потому что этому человеку писали двадцать
    минут назад при паузе в четыре часа. В шапке висело «ждёт, когда избранные
    будут в сети», то есть панель говорила неправду о собственной работе.

    ⚠ Срок круга на этот вопрос **не отвечает**: круг заходит каждые полчаса и
    каждый раз уходит ни с чем, пока держит пауза. Один срок человек читает как
    обещание набора к этому сроку.
    """

    async def test_pause_is_explained_with_the_name(self, girl):
        from src.dating.favorite_mail import favorite_mail_why

        uid = f'u{uuid.uuid4().hex[:8]}'
        await _star(girl, uid)
        await _his(girl, uid, '2026-09-01 10:00:00')
        # Последнее слово наше — пара в отборе; письмо час назад — пауза держит.
        await _hers(girl, uid, _now_minus_hours(1))

        why = await favorite_mail_why(STAND_SITE)

        assert 'пауза' in why, why
        assert uid in why, f'причина не называет адресата: {why}'

    async def test_nobody_to_write_is_an_answer_too(self, girl):
        """⚠ «Писать некому» — обычное состояние, а не поломка.

        Все разговоры живы, либо избранных нет вовсе. Молчание вместо ответа
        человек читает как «оно сломалось».
        """
        from src.dating.favorite_mail import favorite_mail_why

        assert 'некому' in await favorite_mail_why(STAND_SITE)

    async def test_a_writable_pair_means_no_excuse(self, girl):
        """Есть кому писать — повода объясняться нет: круг наберёт на заходе.

        Пустая строка здесь и означает «жди набора», а страница показывает вместо
        неё срок круга.
        """
        from src.dating.favorite_mail import favorite_mail_why

        uid = f'u{uuid.uuid4().hex[:8]}'
        await _star(girl, uid)
        await _his(girl, uid, '2026-09-01 10:00:00')
        # Наше письмо давнее — пауза в четыре часа уже прошла.
        await _hers(girl, uid, _now_minus_hours(9))

        assert await favorite_mail_why(STAND_SITE) == ''


def _now_minus_hours(hours: int) -> str:
    """Метка «столько-то часов назад» в том же виде, в каком её хранит база.

    ⚠ Считаем по часам **процесса**, а не базы, и здесь это допустимо: значения
    берутся с запасом (час против четырёх), и расхождение в минуты ничего не
    решает. Само правило в коде считает база (`_since`) — там разница между
    тремя и пятью часами меняет ответ.
    """
    from datetime import datetime, timedelta, timezone

    return (datetime.now(timezone.utc) - timedelta(hours=hours)).strftime(
        '%Y-%m-%d %H:%M:%S')
