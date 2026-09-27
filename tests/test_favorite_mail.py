"""Переписка с избранными: кого берём в работу и как часто ему пишем.

⚠ **Наружу сюита не ходит** (`testing.md`): письмо — это вызов LLM у соседа, а
отправка ещё и письмо живому человеку. Проверяется то, что решается у нас и что
ломается:

* `_quiet` — кого берём. Цена ошибки высока в обе стороны: пропустим — разговор
  так и останется затихшим, возьмём лишнего — напишем тому, кто ждёт ответа на
  своё письмо, и наше «а расскажи ещё» придёт вместо ответа.
* `_too_soon` — сколько писем в сутки получает **человек**: считается всё
  ушедшее ему **от любой нашей анкеты** и из любого пула. Письма считает
  получатель, а не наши таблицы и не наши девушки.
* `favorite_mail_girl_soon` — как часто пишет **девушка**, неважно кому. Ключ у
  предела другой, и спрашивают его двое: набор пула и круг отправки.

Анкеты заводятся на стенде (`STAND_SITE`), а не на рабочем сайте — иначе их
подхватили бы круги воркера.
"""

import json
import time
import uuid

import pytest

from mysql_.mysql_ import mysql_get_db_async

from src.dating import favorite_mail
from src.dating.favorite_mail import (_quiet, _too_soon,
                                      favorite_mail_girl_free_sql,
                                      favorite_mail_girl_soon)

pytestmark = pytest.mark.mail
# Стенд у соседа: круги воркера про него не знают — его нет в `site_`.
STAND_SITE = 999_343


def _source_without_doc(func) -> str:
    """Исходник функции без докстринга.

    ⚠ Нужен сторожам, которые ищут в коде запрещённую строку: в описании та же
    строка часто стоит нарочно — «здесь было так, и вот почему это неверно».
    Проверка по всему исходнику приняла бы объяснение за нарушение.
    """
    import ast
    import inspect
    import textwrap

    tree = ast.parse(textwrap.dedent(inspect.getsource(func)))
    body = tree.body[0].body
    if (body and isinstance(body[0], ast.Expr)
            and isinstance(body[0].value, ast.Constant)
            and isinstance(body[0].value.value, str)):
        body.pop(0)

    return ast.unparse(tree)


async def _profile(auth_client, tag: str) -> int:
    made = (await auth_client.post('/api/dating/profile', json={
        'site_id': STAND_SITE,
        'login': f'{tag}-{uuid.uuid4().hex[:8]}@example.com',
        'password': 'x'})).json()['result']

    return int(made)


def _answer(value):
    """Подменённый агент письма: всегда один и тот же текст.

    ⚠ Возвращает **пару** `(текст, ошибка)` — так устроен `answer_mail_write`:
    отказ соседа и молчание агента здесь разные исходы, и первый обрывает заход,
    а второй пропускает одну пару.
    """
    async def _got(*args, **kwargs):
        return value, ''

    return _got


def _agent_id(number: int = 1):
    """Подменённый поиск агента: тест не ходит к соседу за номером."""
    async def _got(*args, **kwargs):
        return number

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

    ⚠⚠ **И смена у стенда — во все сутки.** Набор пула теперь ходит по
    расписанию сайта (`favorite_mail_schedule`), а пустые смены значат «не
    работаем». Без этой строки шапка пула отвечала бы «сейчас не смена» на любой
    вопрос, и файл проверял бы часы вместо того, что в нём написано.

    ⚠ `24:00`, а не `23:45`: правая граница смены строгая, и четверть часа перед
    полуночью иначе осталась бы дырой — сюита краснела бы каждый вечер. Ровно на
    этом уже спотыкались двадцать три теста в трёх файлах.

    ⚠⚠ **И `is_gone = 0` заново.** Сайта с выдуманным номером у соседа в списке
    `dating` нет — так и задумано, — поэтому фоновая сверка честно метит его
    «ушёл». Ходит она каждые 30 секунд, то есть посреди прогона, а круг ушедшие
    сайты пропускает: без этой строки тесты краснели бы вразнобой и выглядели
    плавающими, а не сломанными.

    Всё это — **одной точкой на файл**: порознь о таких строках забывают.
    """
    made = await _profile(auth_client, 'fav')

    async with mysql_get_db_async() as db:
        await db.execute("UPDATE profile SET status = 'online' WHERE id = %s",
                         (made,))
        await db.execute(
            'INSERT INTO site (site_id, favorite_mail_schedule,'
            ' favorite_mail_make_on, is_gone) VALUES (%s, %s, 1, 0)'
            ' ON DUPLICATE KEY UPDATE'
            ' favorite_mail_schedule = VALUES(favorite_mail_schedule),'
            ' favorite_mail_make_on = 1, is_gone = 0',
            (STAND_SITE, '[{"from": "00:00", "to": "24:00"}]'))
        await db.connection.commit()

    yield made

    async with mysql_get_db_async() as db:
        await db.execute('DELETE FROM favorite WHERE profile_id = %s', (made,))
        # ⚠ Генерацию снимаем: стенд общий на десяток файлов сюиты, и оставленный
        # включённым он попал бы под круги соседних тестов.
        await db.execute('UPDATE site SET favorite_mail_make_on = 0'
                         ' WHERE site_id = %s', (STAND_SITE,))
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

    async def test_a_pending_draft_blocks_the_whole_girl(self, girl):
        """⚠⚠ И не только этому человеку: черновик занимает **ближайшую очередь
        девушки** целиком.

        Прежде условие стояло по паре, и этого хватало, пока пауза считалась по
        получателю. Теперь пауза считает отправительницу: лежащий черновик уйдёт
        первым и займёт её срок, а набранное сверх него пролежит до вечера и
        протухнет — отбор берёт того, кто **сейчас** в сети.
        """
        first, second = f'u{uuid.uuid4().hex[:8]}', f'u{uuid.uuid4().hex[:8]}'
        for uid in (first, second):
            await _star(girl, uid)
            await _his(girl, uid, '2026-09-01 10:00:00')
            await _hers(girl, uid, '2026-09-01 12:00:00')

        assert len([r for r in await _quiet(STAND_SITE)
                    if r['uid'] in (first, second)]) == 2

        await _draft(girl, first)

        assert not [r for r in await _quiet(STAND_SITE)
                    if r['uid'] in (first, second)], \
            'черновик занял очередь девушки, а отбор предложил ей второго'

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

    def test_the_history_travels_to_the_agent(self):
        """⚠⚠ Переписка уезжает агенту — и это ровно то, что было наоборот.

        Прежний шаблон не видел истории **никогда**: он был один на весь пул, и
        промпт прямо запрещал ссылаться на прошлое. Здесь стоял тест, который это
        утверждал.

        Так и вышла беда: избранный — человек, с которым уже говорили, а письмо
        приходило от женщины, «не помнящей» разговора. Теперь письмо пишет агент
        чата через `dating_talk`, и переписка пары — первое, что он получает.

        ⚠ Проверка перевёрнута намеренно, а не удалена: она сторожит то же место
        с другой стороны. Вернись шаблон — покраснеет.
        """
        import inspect

        # ⚠⚠ **Сторож переехал на ключи, а не ослаб.** Ленту собирает сам сосед
        # из `companion_session` — считать её здесь значило бы держать разговор
        # в двух домах. Но найти её он может только по ключам пары, и шлём их
        # мы: отвались любой — письмо уйдёт от женщины, «не помнящей» разговора,
        # ровно как до всей этой затеи.
        from src.dating import answer

        code = inspect.getsource(answer._agent_ask)

        assert 'agent_vars(' in code and 'companion_vars(' in code, \
            'ключи пары не уезжают — сосед не найдёт переписку'
        # ⚠ А вот **какой** веткой сочинять решает само письмо: у соседа три
        # шага сочинения, и развилка смотрит на `mail_kind`.
        assert 'mail=ANSWER_MAIL_WRITE' in inspect.getsource(answer.answer_mail_write), \
            'письмо уходит не веткой письма: получится реплика в двадцать слов'


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
        monkeypatch.setattr(mod, 'answer_mail_write',
                            _answer('Давно не общались, как ты?'))
        monkeypatch.setattr(mod, '_agent', _agent_id())
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

        async def _write(*args, **kwargs):
            called.append(args)

            return 'письмо', ''

        monkeypatch.setattr(favorite_mail, 'answer_mail_write', _write)
        monkeypatch.setattr(favorite_mail, '_agent', _agent_id())

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

        why = await _too_soon(STAND_SITE, uid, per_day=2)

        assert 'за сутки' in why, why
        # ⚠ Склонение — не украшение: «ушло 2 писем» читается как сбой
        # подстановки, то есть заставляет усомниться в самом числе.
        assert '2 письма' in why, why

    async def test_a_long_silence_passes_the_daily_cap(self, girl):
        uid = f'u{uuid.uuid4().hex[:8]}'
        await _hers(girl, uid, _now_minus_hours(30))

        assert await _too_soon(STAND_SITE, uid, per_day=2) == ''

    async def test_the_daily_cap_does_not_answer_for_the_pause(self, girl):
        """⚠ Сторож разделения: письмо часовой давности дневного предела не
        трогает, и `_too_soon` обязан пропустить.

        Прежде пауза стояла здесь же и ловила такой случай сама. Теперь её
        считает `favorite_mail_girl_soon`, и если она снова заведётся тут —
        предел получателя начнёт гасить письма другим людям.
        """
        uid = f'u{uuid.uuid4().hex[:8]}'
        await _hers(girl, uid, _now_minus_hours(1))

        assert await _too_soon(STAND_SITE, uid, per_day=5) == '', \
            'дневной предел снова считает паузу — это разные правила'


class TestThePauseBelongsToTheGirl:
    """⚠⚠ Пауза — про отправительницу, а не про получателя.

    Настройка значила «одному человеку не чаще раза в срок» и про девушку не
    говорила ничего. Круг отправки уносит по письму в минуту, и 21 сентября от
    одной Елены ушли четыре письма **четверым** за восемь минут — 18:29, 18:30,
    18:32, 18:37. Каждая пара при этом была чиста: ни одному из четверых она не
    писала дважды.

    Сайт считает иначе, и теперь считаем так же: одна анкета — одно письмо за
    срок, кому угодно.
    """

    async def test_her_letter_to_someone_else_holds_the_pause(self, girl):
        """Тот самый случай: написала одному — второму сейчас нельзя."""
        await _hers(girl, f'u{uuid.uuid4().hex[:8]}', _now_minus_hours(0))

        why = await favorite_mail_girl_soon(girl, 4 * 3600)

        # ⚠ Проверяем **суть, а не слова**: причина идёт человеку на экран, её
        # переписывают, и тест, приколоченный к формулировке, падал бы на каждой
        # правке текста. Здесь важно одно — отказ назван и назван срок.
        assert why, 'пауза не удержала второе письмо той же девушки'
        assert 'можно через' in why, why

    async def test_a_long_silence_frees_her(self, girl):
        await _hers(girl, f'u{uuid.uuid4().hex[:8]}', _now_minus_hours(30))

        assert await favorite_mail_girl_soon(girl, 4 * 3600) == ''

    async def test_yesterdays_letter_still_counts(self, girl):
        """⚠ Пауза считается от последнего ушедшего **за любой срок**, а не за
        сутки: письмо 25-часовой давности в дневной счёт не попадает, и считай
        мы паузу тем же окном — она отсчитывалась бы от пустоты."""
        await _hers(girl, f'u{uuid.uuid4().hex[:8]}', _now_minus_hours(25))

        why = await favorite_mail_girl_soon(girl, 48 * 3600)

        assert why, 'вчерашнее письмо не попало в счёт паузы'
        assert 'можно через' in why, why

    async def test_another_girls_letter_does_not_hold_her(self, girl,
                                                          auth_client):
        """⚠⚠ Обратная сторона, и она — суть правки.

        Соседняя анкета только что написала; эта молчала сутки. Считай пауза
        по-прежнему получателя или по сайту целиком — одна активная девушка
        запирала бы всех остальных.
        """
        other = await _profile(auth_client, 'fav2')
        await _hers(other, f'u{uuid.uuid4().hex[:8]}', _now_minus_hours(0))

        try:
            assert await favorite_mail_girl_soon(girl, 4 * 3600) == '', \
                'письмо соседней анкеты заперло девушку, которая молчала'
        finally:
            await auth_client.delete(f'/api/dating/profile/{other}')

    async def test_every_pool_counts(self, girl):
        """⚠ Для сайта письмо есть письмо: ответ и рассылка держат паузу так же,
        как возобновление. Иначе девушка, ответившая минуту назад, тут же ушла
        бы со вторым письмом — и сайт увидел бы от неё пачку."""
        await _hers(girl, f'u{uuid.uuid4().hex[:8]}', _now_minus_hours(0),
                    'favorite_mail')

        assert await favorite_mail_girl_soon(girl, 4 * 3600), \
            'письмо из пула избранных паузу не удержало'

    async def test_zero_means_no_pause(self, girl):
        """⚠ Ноль — законное значение, а не «настройки нет»: человек выбрал
        «без паузы», и выдумывать за него умолчание нельзя."""
        await _hers(girl, f'u{uuid.uuid4().hex[:8]}', _now_minus_hours(0))

        assert await favorite_mail_girl_soon(girl, 0) == ''
        assert favorite_mail_girl_free_sql(0) == '', \
            'условие без паузы всё равно строится — оно всегда истинно'


class TestTheLimitIsPerPersonNotPerPair:
    """⚠⚠ Предел считается по человеку, а не по паре «анкета + человек».

    Здесь стояло `profile_id = %s AND uid = %s`, и обе настройки из-за этого не
    работали: каждая девушка честно выдерживала свои два письма в сутки и свои
    четыре часа, но про других не знала.

    Поймано на живых данных: собеседник получил **четыре письма от четырёх разных
    девушек за одиннадцать минут** — 18:28, 18:31, 18:34, 18:39. Ни одна пара
    предела не превысила: у каждой было по одному письму при разрешённых двух.
    """

    async def test_another_girls_letter_counts_for_the_daily_cap(self, girl,
                                                                 auth_client):
        """Второй девушке нельзя, если первая уже исчерпала предел человека."""
        uid = f'u{uuid.uuid4().hex[:8]}'
        other = await _profile(auth_client, 'fav2')

        await _hers(girl, uid, _now_minus_hours(5))
        await _hers(girl, uid, _now_minus_hours(6), 'favorite_mail')

        try:
            why = await _too_soon(STAND_SITE, uid, per_day=2)

            assert 'за сутки' in why, \
                f'письма другой девушки в счёт не попали: {why!r}'
        finally:
            await auth_client.delete(f'/api/dating/profile/{other}')

    async def test_the_daily_cap_counts_letters_of_every_girl(self, girl,
                                                              auth_client):
        """⚠⚠ Тот самый случай: четыре письма за одиннадцать минут.

        Держит его теперь **дневной предел** — он про получателя и считает всё
        ушедшее ему от любой нашей анкеты. Паузы в этом счёте больше нет: она
        уехала к девушке (`TestThePauseBelongsToTheGirl`), и требовать её здесь
        значило бы запирать одного человека из-за письма, отправленного другому.
        """
        uid = f'u{uuid.uuid4().hex[:8]}'
        other = await _profile(auth_client, 'fav2')

        await _hers(girl, uid, _now_minus_hours(0))
        await _hers(girl, uid, _now_minus_hours(2))

        try:
            why = await _too_soon(STAND_SITE, uid, per_day=2)

            assert 'за сутки' in why, \
                f'письма другой девушки в счёт получателя не попали: {why!r}'
        finally:
            await auth_client.delete(f'/api/dating/profile/{other}')

    async def test_other_people_are_not_affected(self, girl):
        """⚠ Обратная сторона: предел на **этого** человека, а не на сайт целиком.

        Письма одному не должны закрывать дорогу к другому — иначе одна активная
        пара застопорила бы всю переписку сайта.
        """
        busy, free = f'u{uuid.uuid4().hex[:8]}', f'u{uuid.uuid4().hex[:8]}'
        await _hers(girl, busy, _now_minus_hours(0))
        await _hers(girl, busy, _now_minus_hours(1))

        assert await _too_soon(STAND_SITE, free, per_day=2) == '', \
            'предел одного человека закрыл дорогу к другому'

    def test_the_query_keys_on_the_site_not_the_profile(self):
        """⚠ Сторож от возврата: `profile_id` в условии означал бы счёт по паре.

        ⚠ Смотрим на **условие отбора**, а не на весь текст запроса: таблицы
        теперь перечисляет общий сборщик (`mail_sent_union`), и запрос целиком
        в исходнике больше не лежит. Приколоти проверку к буквам SQL — она
        краснела бы на каждой перестановке строк, ничего не защищая.

        ⚠⚠ И **без докстринга**: в нём разобрана та самая прежняя ошибка, со
        строкой `profile_id = %s AND uid = %s` в тексте. Проверка по всему
        исходнику нашла бы её там и объявила поломкой — то есть запрещала бы
        объяснять, почему так делать нельзя.
        """
        code = _source_without_doc(_too_soon)

        assert "site_id = %s AND uid = %s" in code, \
            'дневной предел больше не считает по человеку'
        assert "profile_id = %s AND uid = %s" not in code, \
            'счёт снова ведётся по паре — предел перестанет работать'

    def test_the_daily_cap_counts_every_pool(self):
        """⚠⚠ И считает **все** пулы, а не те, что кто-то вспомнил.

        Список таблиц один на проект (`MAIL_SEND_TABLES`), и счёт обязан брать
        его целиком: посчитай он два пула из трёх, предел молча отпускал бы
        лишнее письмо — запрос от недостающей таблицы не падает.
        """
        from src.dating.mail_send import MAIL_SEND_TABLES, mail_sent_union

        code = _source_without_doc(_too_soon)

        assert 'mail_sent_union' in code, \
            'дневной предел снова перечисляет таблицы сам'

        built = mail_sent_union("site_id = %s AND uid = %s AND status = 'sent'")
        for table in MAIL_SEND_TABLES:
            assert f'FROM {table} ' in built, f'{table} выпал из счёта'


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


class TestOneLetterPerGirlPerPass:
    """⚠⚠ За один заход девушка получает одно письмо, а не пачку.

    Пауза говорит: от анкеты уходит одно письмо за срок. Набери ей заход пять —
    четыре пролежали бы в пуле до вечера и ушли бы протухшими: отбор берёт того,
    кто **сейчас** в сети, а через шестнадцать часов это уже неправда.

    ⚠ Это следствие правки паузы, а не отдельное желание. Пока срок считался по
    получателю, пачка от одной девушки была законной — она и уходила: четыре
    письма Елены четверым за восемь минут.
    """

    async def test_a_second_pair_of_the_same_girl_is_skipped(self, girl,
                                                             monkeypatch):
        import src.dating.favorite_mail as mod

        uids = [f'u{uuid.uuid4().hex[:8]}' for _ in range(3)]
        for uid in uids:
            await _star(girl, uid)
            await _hers(girl, uid, '2026-09-01 10:00:00')

        monkeypatch.setattr(mod, 'answer_mail_write',
                            _answer('Давно не общались, как ты?'))
        monkeypatch.setattr(mod, '_agent', _agent_id())
        monkeypatch.setattr(mod, '_sync_stars', _nothing())

        got = await mod.favorite_mail_make(STAND_SITE, limit=5, profile_id=girl)

        assert got['made'] == 1, \
            f"за заход девушке набрали {got['made']} писем вместо одного"
        assert any('этим заходом' in one for one in got['reasons']), got['reasons']

    async def test_a_girl_in_pause_gets_nothing(self, girl, monkeypatch):
        """⚠ Пауза проверяется **и в наборе**, а не только в отправке: письмо —
        это вызов LLM, и платить за него, чтобы черновик лежал до вечера, незачем.
        """
        import src.dating.favorite_mail as mod

        uid = f'u{uuid.uuid4().hex[:8]}'
        await _star(girl, uid)
        await _hers(girl, uid, _now_minus_hours(1))

        monkeypatch.setattr(mod, 'answer_mail_write',
                            _answer('Давно не общались, как ты?'))
        monkeypatch.setattr(mod, '_agent', _agent_id())
        monkeypatch.setattr(mod, '_sync_stars', _nothing())

        got = await mod.favorite_mail_make(STAND_SITE, limit=5, profile_id=girl)

        assert got['made'] == 0, 'письмо набралось, хотя девушка в паузе'
        assert any('можно через' in one for one in got['reasons']), got['reasons']


class TestTheRoundKeepsToItsShifts:
    """⚠⚠ Набор пула ходит по сменам сайта, а не круглые сутки.

    Прежде расписания здесь не было, и довод был такой: «набор ничего не
    отправляет, второе расписание отвечало бы вторым голосом на вопрос часов
    отправки». Довод неверен в одном месте, и это место решающее: **пул
    набирается под тех, кто в сети сейчас**. Сочинённое ночью к утру протухает —
    человек ушёл, а письмо лежит и держит его место в пуле.

    Смены отвечают на «когда сочиняем», `mailing_schedule` — на «когда
    отправляем». Вопроса два, потому что разное и дорого: сочинение стоит вызовов
    LLM и наружу не пишет, отправка не стоит ничего и уходит живому человеку.
    """

    async def _shifts(self, value) -> None:
        """Кладёт смены стенду. `None` — стирает расписание вовсе."""
        async with mysql_get_db_async() as db:
            await db.execute(
                'INSERT INTO site (site_id, favorite_mail_schedule)'
                ' VALUES (%s, %s) ON DUPLICATE KEY UPDATE'
                ' favorite_mail_schedule = VALUES(favorite_mail_schedule)',
                (STAND_SITE, value))
            await db.connection.commit()

    async def _ran(self, girl_id: int, monkeypatch) -> dict:
        """Один заход круга по стенду. Наружу не ходит: агент подменён."""
        import src.dating.favorite_mail as mod
        import src.dating.favorite_mail_round as round_

        monkeypatch.setattr(mod, 'answer_mail_write',
                            _answer('Давно не общались?'))
        monkeypatch.setattr(mod, '_agent', _agent_id())
        monkeypatch.setattr(mod, '_sync_stars', _nothing())

        # ⚠ Отметка «недавно ходили» живёт в Redis и переживает тесты: без снятия
        # второй заход в том же прогоне молчал бы по чужой причине.
        monkeypatch.setattr(round_, 'round_slot_due', lambda key, site_id, what: True)
        monkeypatch.setattr(round_, 'round_slot_mark', lambda *a: None)
        monkeypatch.setattr(round_, '_fresh_feed', lambda site_id: False)

        # Только наш стенд: у соседних сайтов свои смены и свои анкеты.
        real = round_._sites

        async def only_stand():
            return [one for one in await real()
                    if int(one['site_id']) == STAND_SITE]

        monkeypatch.setattr(round_, '_sites', only_stand)

        return await round_.favorite_mail_round_run(0)

    async def test_an_empty_schedule_means_silence(self, girl, monkeypatch):
        """⚠ Пустые смены — «не работаем», а не «работаем всегда».

        Правило общее с рассылкой и инвайтами. Второе прочтение означало бы, что
        человек, стерев все смены, получает круглосуточную работу — то есть
        ровно обратное тому, что он сделал.
        """
        uid = f'u{uuid.uuid4().hex[:8]}'
        await _star(girl, uid)
        await _hers(girl, uid, '2026-09-01 10:00:00')
        await self._shifts(None)

        got = await self._ran(girl, monkeypatch)

        assert got['made'] == 0, 'круг набрал пул без расписания'

    async def test_a_shift_now_lets_the_round_work(self, girl, monkeypatch):
        uid = f'u{uuid.uuid4().hex[:8]}'
        await _star(girl, uid)
        await _hers(girl, uid, '2026-09-01 10:00:00')
        await self._shifts('[{"from": "00:00", "to": "24:00"}]')

        got = await self._ran(girl, monkeypatch)

        assert got['made'] == 1, got

    async def test_the_round_asks_the_shared_predicate(self):
        """⚠ Сторож общей точки: круг и шапка пула обязаны спрашивать **одно**.

        Заведи круг свою проверку — панель писала бы «идёт смена» там, где он
        уходит ни с чем, и человек искал бы поломку в пределах и анкетах.

        ⚠⚠ Смотрим на **разобранный код**, а не на исходник строкой. `getsource`
        отдаёт и комментарии, а над самой проверкой стоит пояснение с именем
        предиката — сторож проходил бы по нему и при вырезанной проверке.
        Поймано ровно так: правку сняли, тест остался зелёным.
        """
        from src.dating import favorite_mail_round

        code = _source_without_doc(favorite_mail_round.favorite_mail_round_run)

        assert 'favorite_mail_shift_now' in code, \
            'круг снова проверяет смены сам'
        assert 'schedule_now(shifts, offset)' not in code, \
            'сдвиг часового пояса снова передан как минуты суток'


class TestOutsideTheShiftTheHeaderSaysSo:
    """⚠ Молчащий пул обязан объяснять себя, и **часами в первую очередь**.

    Вне смены круг не наберёт ничего, сколько бы пар ни ждало. Скажи шапка про
    пары или пределы — человек чинил бы их, пока дело в расписании.
    """

    async def _shifts(self, value) -> None:
        async with mysql_get_db_async() as db:
            await db.execute(
                'INSERT INTO site (site_id, favorite_mail_schedule)'
                ' VALUES (%s, %s) ON DUPLICATE KEY UPDATE'
                ' favorite_mail_schedule = VALUES(favorite_mail_schedule)',
                (STAND_SITE, value))
            await db.connection.commit()

    async def test_no_schedule_is_explained_by_the_schedule(self, girl):
        from src.dating.favorite_mail import favorite_mail_why

        uid = f'u{uuid.uuid4().hex[:8]}'
        await _star(girl, uid)
        await _hers(girl, uid, '2026-09-01 10:00:00')
        await self._shifts(None)

        try:
            why = await favorite_mail_why(STAND_SITE)

            assert 'не смена' in why, why
        finally:
            await self._shifts('[{"from": "00:00", "to": "24:00"}]')

    async def test_inside_the_shift_the_reason_is_about_the_work(self, girl):
        """Смена идёт — повод снова про работу, а не про часы."""
        from src.dating.favorite_mail import favorite_mail_why

        await self._shifts('[{"from": "00:00", "to": "24:00"}]')

        why = await favorite_mail_why(STAND_SITE)

        assert 'не смена' not in why, why


class TestTheGapIsInMinutes:
    """⚠⚠ Пауза между письмами: в базе минуты, в коде секунды.

    Настройка была набором из четырёх сроков в **секундах** — `3600` «1 час»,
    `14400` «4 часа». Два изъяна: набор запрещал нужное (осмысленна любая пауза,
    сайты разные), а секунды не читаются — «240» человек складывает в четыре
    часа сходу, «14400» ни во что.

    Стало числовое поле в минутах, единица в заголовке: «Пауза между письмами,
    мин».

    ⚠⚠⚠ **Перевод стоит в одном месте — в чтении.** Спрашивающих двое: круг
    отправки и набор пула. Умножай каждый сам — первый же забывший сравнивал бы
    минуты с секундами, а это разница в шестьдесят раз: пауза в четыре часа
    стала бы четырьмя минутами, и сайт ответил бы отказом по своим пределам.
    """

    async def test_the_reader_returns_seconds(self, monkeypatch):
        """Настройка в минутах — наружу секунды."""
        from src.dating import favorite_mail

        async def _site_seconds(site_id, key):
            return 60 if key == favorite_mail.FAVORITE_MAIL_GAP_SETTING else 0

        monkeypatch.setattr(
            'src.uvicorn.routers.dating.site_.site_seconds', _site_seconds)

        assert await favorite_mail.favorite_mail_gap_sec(1) == 3600, \
            'шестьдесят минут это ровно час'

    async def test_the_default_is_four_hours(self, monkeypatch):
        """⚠ Умолчание тоже в минутах: 240 — те же четыре часа, что были."""
        from src.dating import favorite_mail

        async def _nothing(site_id, key):
            return 0

        monkeypatch.setattr(
            'src.uvicorn.routers.dating.site_.site_seconds', _nothing)

        assert favorite_mail.FAVORITE_MAIL_GAP_DEFAULT_MIN == 240
        assert await favorite_mail.favorite_mail_gap_sec(1) == 240 * 60

    def test_the_setting_is_a_number_not_a_choice(self):
        """⚠ Числовое поле, а не набор: осмысленна любая пауза.

        Набор отвечает на вопрос «какие значения осмысленны», и для многих
        сроков это верно. Здесь он заставлял брать ближайший вариант, а не свой.
        """
        from src.uvicorn.routers.dating.site_ import SITE_KNOWN

        known = SITE_KNOWN['favorite_mail_gap']

        assert known.get('kind') == 'number', 'пауза снова стала набором'
        assert not known.get('options'), 'у числового поля не бывает вариантов'
        assert known.get('min') == 1, 'ноль минут это «паузы нет», а не пауза'

    def test_the_title_names_the_unit(self):
        """⚠⚠ Единица — в заголовке, и это не украшение.

        Число без подписи читается как секунды по привычке: так эта настройка и
        жила. «240» без «мин» — это сколько?
        """
        from src.uvicorn.routers.dating.site_ import SITE_KNOWN

        assert 'мин' in SITE_KNOWN['favorite_mail_gap']['title']

    def test_the_default_in_the_registry_matches_the_code(self):
        """⚠ Два умолчания об одной паузе — в реестре и в коде.

        Разойдись они, сайт без настройки вёл бы себя не так, как показывает
        форма: человек видит 240, а круг ждёт другое.
        """
        from src.dating.favorite_mail import FAVORITE_MAIL_GAP_DEFAULT_MIN
        from src.uvicorn.routers.dating.site_ import SITE_KNOWN

        assert int(SITE_KNOWN['favorite_mail_gap']['default']) == \
            FAVORITE_MAIL_GAP_DEFAULT_MIN


class TestTheGapLabelReadsMinutes:
    """⚠⚠ Подпись под формой и число в форме обязаны говорить одно.

    Настройку человек вводит в минутах, а подпись рядом пересказывает её словами:
    «Одна анкета пишет не чаще, чем раз в 4 часа». Пока функция делила на 3600,
    после перевода настройки час показался бы как «0 часов» — подпись спорила бы
    с числом, стоящим в двух сантиметрах от неё.

    ⚠ Проверяем **саму функцию страницы**, вырезая её из разметки и прогоняя
    `node`: копия правила в тесте разошлась бы с оригиналом при первой правке.
    """

    def test_the_words_match_the_minutes(self):
        import os
        import re
        import shutil
        import subprocess
        import tempfile

        node = shutil.which('node')
        if not node:
            pytest.skip('node не установлен')

        page = open('data/uvicorn/template/admin/dating/profile.html',
                    encoding='utf-8').read()

        found = re.search(r'function favMailGapWords\(minutes\) \{.*?\n    \}',
                          page, re.S)
        assert found, 'функция подписи паузы не нашлась на странице'

        probe = (found.group(0) + '\n'
                 + 'const cases = [[45, "45 мин"], [59, "59 мин"], [60, "1 час"],'
                 + ' [240, "4 часа"], [600, "10 часов"], [0, "0 мин"]];\n'
                 + 'for (const [value, want] of cases) {\n'
                 + '  const got = favMailGapWords(value);\n'
                 + '  if (got !== want) {\n'
                 + '    console.log(`${value} → ${got}, ждали ${want}`);\n'
                 + '    process.exit(1);\n'
                 + '  }\n'
                 + '}\n')

        fd, path = tempfile.mkstemp(suffix='.mjs')
        try:
            with os.fdopen(fd, 'w', encoding='utf-8') as f:
                f.write(probe)

            result = subprocess.run([node, path], capture_output=True, text=True)

            assert result.returncode == 0, \
                f'подпись паузы считает неверно: {result.stdout.strip()}'
        finally:
            os.remove(path)
