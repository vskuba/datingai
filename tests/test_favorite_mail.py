"""Переписка с избранными: кого берём в работу и как часто ему пишем.

⚠ **Наружу сюита не ходит** (`testing.md`): письмо — это вызов LLM у соседа, а
отправка ещё и письмо живому человеку. Проверяется то, что решается у нас и что
ломается:

* `_quiet` — кого берём. Цена ошибки высока в обе стороны: пропустим — разговор
  так и останется затихшим, возьмём лишнего — напишем тому, кто ждёт ответа на
  своё письмо, и наше «а расскажи ещё» придёт вместо ответа.
* `favorite_mail_person_soon` — сколько писем в сутки уходит **паре** «анкета +
  человек» (решение владельца 25.11.2026): считаются только письма избранным
  (`FAVORITE_MAIL_DAY_TABLES`), счёт дневной, а не интервальный.
* `favorite_mail_girl_soon` — как часто пишет **та же пара**: письмо этому
  мужчине задерживает письмо этому мужчине, другому — нет (решение владельца
  26.11.2026). Считает все пулы кроме рассылки (`MAIL_SEND_GAP_TABLES`), счёт
  интервальный. Ключ у предела другой, и спрашивают его двое: набор пула и круг
  отправки.

Анкеты заводятся на стенде (`STAND_SITE`), а не на рабочем сайте — иначе их
подхватили бы круги воркера.
"""

import json
import time
import uuid

import pytest

from mysql_.mysql_ import mysql_get_db_async

from src.dating import favorite_mail
from src.dating.favorite_mail import (_quiet, favorite_mail_gap_of,
                                      favorite_mail_gap_words,
                                      favorite_mail_girl_soon,
                                      favorite_mail_pairs_busy_sql,
                                      favorite_mail_person_sent,
                                      favorite_mail_person_soon)
from src.dating.site_setting import SITE_KNOWN

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


def _answer(value, message_id: int = 0):
    """Подменённый агент письма: всегда один и тот же текст.

    ⚠ Возвращает **четвёрку** `(текст, ошибка, номер, решения)` — так устроен
    `answer_mail_write`: отказ соседа и молчание агента здесь разные исходы, и
    первый обрывает заход, а второй пропускает одну пару. Номер обмена нулю по
    умолчанию — подменённый агент ни к кому не ходил; тесты, которым номер
    важен, задают его явно.

    ⚠⚠ Четвёртое поле — **решения прогона** (`extra`), и пустое значит «снимка
    не просили». Жребий живёт у соседа, и подменённый агент его не бросает:
    письмо уйдёт текстом, как и до появления ключа.
    """
    async def _got(*args, **kwargs):
        return value, '', message_id, {}

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


async def _online(profile_id: int) -> None:
    """Анкета в сети и включена — без этого отбор её не видит вовсе.

    ⚠ Оба поля сразу: `_quiet` требует и `enabled = 1`, и `status = 'online'`, и
    проверка порознь даёт пустой отбор по причине, которую тест не называет.
    """
    async with mysql_get_db_async() as db:
        await db.execute("UPDATE profile SET status = 'online', enabled = 1"
                         ' WHERE id = %s', (int(profile_id),))
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
    """Наше ушедшее письмо. Таблица — чтобы проверить, какие пулы считаются.

    ⚠ Дневной предел считает **только письма избранным**
    (`FAVORITE_MAIL_DAY_TABLES`, решение владельца 25.11.2026): его сторожа
    кладут письмо доводом `table='favorite_mail'`.
    """
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


async def _reply_sent(profile_id: int, uid: str, when: str) -> None:
    """Наш **ответ на его письмо** — ушедший.

    ⚠ Отдельно от `_hers`: у ответа своя роль в пределе навязчивости — он её
    не тратит, — и тест, кладущий его как «просто письмо», проверял бы не то.

    ⚠ Ответ цепляется к **настоящему** входящему: `mail_in_id` в таблице
    обязателен, и это правильно — ответа без письма не бывает. Берём последнее
    его письмо; нет ни одного — заводим, иначе посев соврал бы о природе строки.
    """
    async with mysql_get_db_async() as db:
        await db.execute(
            'SELECT id FROM mail_in WHERE profile_id = %s AND uid = %s'
            ' ORDER BY id DESC LIMIT 1', (int(profile_id), str(uid)))
        row = await db.fetchone()

    if not row:
        await _his(profile_id, uid, when)
        async with mysql_get_db_async() as db:
            await db.execute(
                'SELECT id FROM mail_in WHERE profile_id = %s AND uid = %s'
                ' ORDER BY id DESC LIMIT 1', (int(profile_id), str(uid)))
            row = await db.fetchone()

    async with mysql_get_db_async() as db:
        await db.execute(
            'INSERT INTO mail_reply'
            ' (site_id, profile_id, mail_in_id, uid, name, body, status, sent_at)'
            " VALUES (%s, %s, %s, %s, 'X', 'hi', 'sent', %s)",
            (STAND_SITE, int(profile_id), int(row['id']), str(uid), when))
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


async def _letters(site_id: int = STAND_SITE) -> list[dict]:
    """Строки пула письма избранным — для проверки того, что в них реально
    легло, а не только того, что набор отчитался числом."""
    async with mysql_get_db_async() as db:
        await db.execute(
            'SELECT profile_id, uid, body, status, message_id'
            ' FROM favorite_mail WHERE site_id = %s ORDER BY id',
            (int(site_id),))

        return [dict(row) for row in await db.fetchall() or []]


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

    async def test_a_pending_draft_blocks_its_own_pair(self, girl):
        """⚠⚠ Черновик занимает очередь **своей пары**, не всей
        анкеты.

        С паузой на отправительнице (сентябрь–ноябрь 2026)
        условие по всей анкете было правдой: любой лежащий
        черновик занимал единственный на всю анкету срок.
        С сроком на пару (26.11.2026) черновик другому мужчине
        этой паре не помеха — она свободна и уйдет своим
        кругом, и держать её за чужой лист — замораживать
        занятие.
        """
        first, second = f'u{uuid.uuid4().hex[:8]}', f'u{uuid.uuid4().hex[:8]}'
        for uid in (first, second):
            await _star(girl, uid)
            await _his(girl, uid, '2026-09-01 10:00:00')
            await _hers(girl, uid, '2026-09-01 12:00:00')

        assert len([r for r in await _quiet(STAND_SITE)
                    if r['uid'] in (first, second)]) == 2

        await _draft(girl, first)

        taken = [r for r in await _quiet(STAND_SITE)
                 if r['uid'] in (first, second)]

        assert [r['uid'] for r in taken] == [second], \
            'черновик одной пары снова запирает очередь всей анкеты'

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

        # ⚠ Дверь собеседника — `_known`: имя она берёт у карточки, а не из
        # реплики. Пустое имя вырождает ключ в «номер_номер», и лента найдётся
        # не та (`test_companion_ref.TestTheNameComesFromTheCard`).
        assert 'agent_vars(' in code and 'companion_vars_known(' in code, \
            'ключи пары не уезжают — сосед не найдёт переписку'
        # ⚠ А вот **какой** веткой сочинять решает само письмо: у соседа три
        # шага сочинения; с 24.10.2026 `mail_kind` — его внутренняя переменная,
        # и вычисляет он её по непустому вопросу, который везёт этот вызов.
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

            return 'письмо', '', 0, {}

        monkeypatch.setattr(favorite_mail, 'answer_mail_write', _write)
        monkeypatch.setattr(favorite_mail, '_agent', _agent_id())

        uid = f'u{uuid.uuid4().hex[:8]}'
        await _star(girl, uid)
        await _his(girl, uid, '2026-09-01 10:00:00')
        # Два письма за сутки — предел исчерпан.
        await _hers(girl, uid, _now_minus_hours(3), 'favorite_mail')
        await _hers(girl, uid, _now_minus_hours(4), 'favorite_mail')

        got = await favorite_mail_make(STAND_SITE, 5, girl)

        assert got['made'] == 0
        assert not called, 'агента позвали, хотя предел уже исчерпан'


class TestHowOften:
    """⚠ Оба предела — про человека, а не про сайт, и считают всё ушедшее ему."""

    async def test_the_daily_cap_counts_her_letters(self, girl):
        """⚠ Считаются её письма избранному; рассылка — нет
        (`TestTheLimitIsPerPair`)."""
        uid = f'u{uuid.uuid4().hex[:8]}'
        await _hers(girl, uid, _now_minus_hours(5), 'favorite_mail')
        await _hers(girl, uid, _now_minus_hours(6), 'favorite_mail')

        why = await favorite_mail_person_soon(STAND_SITE, uid, per_day=2,
                                           profile_id=girl)

        assert 'за сутки' in why, why
        # ⚠ Склонение — не украшение: «ушло 2 писем» читается как сбой
        # подстановки, то есть заставляет усомниться в самом числе.
        assert '2 письма' in why, why

    async def test_a_long_silence_passes_the_daily_cap(self, girl):
        uid = f'u{uuid.uuid4().hex[:8]}'
        await _hers(girl, uid, _now_minus_hours(30))

        assert await favorite_mail_person_soon(STAND_SITE, uid, per_day=2,
                                           profile_id=girl) == ''

    async def test_the_daily_cap_does_not_answer_for_the_pause(self, girl):
        """⚠ Сторож разделения: письмо часовой давности дневного предела не
        трогает, и `favorite_mail_person_soon` обязан пропустить.

        Прежде пауза стояла здесь же и ловила такой случай сама. Теперь её
        считает `favorite_mail_girl_soon`, и если она снова заведётся
        тут — предел начнёт гасить письма остальным парам анкеты.
        """
        uid = f'u{uuid.uuid4().hex[:8]}'
        await _hers(girl, uid, _now_minus_hours(1))

        assert await favorite_mail_person_soon(STAND_SITE, uid, per_day=5,
                                           profile_id=girl) == '', \
            'дневной предел снова считает паузу — это разные правила'


class TestThePauseBelongsToThePair:
    """⚠⚠ Пауза — про пару «анкета + человек», а не про анкету целиком.

    История вопроса: настройка значила «одному человеку не чаще раза в срок»
    (срок висел на получателе), в сентябре 2026 её перенесли на отправительницу
    — после случая Елены, от которой 21.09 ушли четыре письма четверым за восемь
    минут (18:29, 18:30, 18:32, 18:37). Решение владельца 26.11.2026 вернуло
    срок паре: **писала этому мужчине — ждём; пишет другому — без ожидания**.

    ⚠ Цена принята осознанно: та самая прыть одной анкеты по разным мужчинам
    возвращается законной частью занятия; от залпа у одного держат дневной
    предел пары и «одному человеку пишет одна анкета за заход» в наборе.
    """

    async def test_a_letter_to_him_holds_the_pause(self, girl):
        """Та же пара, письмо час назад — второму письму ему ждать.

        ⚠ Письмо кладём **в свой пул**: рассылку пауза не считает (см.
        `TestTheGapIgnoresMailing` ниже), и на ней тест проверял бы не правило,
        а список таблиц.
        """
        uid = f'u{uuid.uuid4().hex[:8]}'
        await _hers(girl, uid, _now_minus_hours(1), 'favorite_mail')

        why = await favorite_mail_girl_soon(girl, uid, 4 * 3600, 4 * 3600)

        # ⚠ Проверяем **суть, а не слова**: причина идёт человеку на экран, её
        # переписывают, и тест, приколоченный к формулировке, падал бы на каждой
        # правке текста. Здесь важно одно — отказ назван и назван срок.
        assert why, 'пауза не удержала второе письмо той же пары'
        assert 'можно через' in why, why

    async def test_another_man_goes_without_waiting(self, girl):
        """⚠⚠ Суть правки: та же девушка, другой мужчина — пишет сразу.

        Прежняя версия этого теста утверждала обратное — «написала одному,
        второму нельзя»; так занятие жило с сентября по ноябрь 2026. Решение
        владельца 26.11.2026 развернуло его: срок живёт у пары.
        """
        wrote_to = f'u{uuid.uuid4().hex[:8]}'
        waiting_for = f'u{uuid.uuid4().hex[:8]}'
        await _hers(girl, wrote_to, _now_minus_hours(0), 'favorite_mail')

        assert await favorite_mail_girl_soon(girl, waiting_for,
                                             4 * 3600, 4 * 3600) == '', \
            'пауза одной пары снова держит письма другим мужчинам'

    async def test_a_long_silence_frees_the_pair(self, girl):
        uid = f'u{uuid.uuid4().hex[:8]}'
        await _hers(girl, uid, _now_minus_hours(30), 'favorite_mail')

        assert await favorite_mail_girl_soon(girl, uid,
                                             4 * 3600, 4 * 3600) == ''

    async def test_yesterdays_letter_still_counts(self, girl):
        """⚠ Пауза считается от последнего ушедшего **ей** письма за любой срок,
        а не за сутки: письмо 25-часовой давности в дневной счёт не попадает, и
        считай мы паузу тем же окном — она отсчитывалась бы от пустоты."""
        uid = f'u{uuid.uuid4().hex[:8]}'
        await _hers(girl, uid, _now_minus_hours(25), 'favorite_mail')

        why = await favorite_mail_girl_soon(girl, uid, 48 * 3600, 48 * 3600)

        assert why, 'вчерашнее письмо ему не попало в счёт паузы'
        assert 'можно через' in why, why

    async def test_another_girls_letter_does_not_hold_the_pair(self, girl,
                                                               auth_client):
        """⚠⚠ Соседняя анкета только что написала **тому же человеку** — этой
        паре это не счёт.

        Дневной предел он тронет (тот на пару «анкета + человек»), пауза — нет:
        у каждой пары свой срок и свой жребий.
        """
        other = await _profile(auth_client, 'fav2')
        uid = f'u{uuid.uuid4().hex[:8]}'
        await _hers(other, uid, _now_minus_hours(0), 'favorite_mail')

        try:
            assert await favorite_mail_girl_soon(girl, uid,
                                                 4 * 3600, 4 * 3600) == '', \
                'письмо другой анкеты затронуло счёт этой пары'
        finally:
            await auth_client.delete(f'/api/dating/profile/{other}')

    async def test_her_own_pool_counts(self, girl):
        """⚠ Письмо этой паре паузу держит — иначе правила не было бы вовсе."""
        uid = f'u{uuid.uuid4().hex[:8]}'
        await _hers(girl, uid, _now_minus_hours(0), 'favorite_mail')

        assert await favorite_mail_girl_soon(girl, uid, 4 * 3600, 4 * 3600), \
            'письмо этой паре из её пула паузу не удержало'

    async def test_a_reply_to_him_counts_too(self, girl):
        """⚠ И ответ ему: девушка пишет его в том же разговорном ритме, и уходит
        он тому же человеку в ту же почту.

        ⚠ Кладём настоящим ответом (`_reply_sent`), а не строкой «просто
        письмо»: у ответа своя роль в пределе навязчивости, и посев мимо неё
        проверял бы не ту природу строки.
        """
        uid = f'u{uuid.uuid4().hex[:8]}'
        await _reply_sent(girl, uid, _now_minus_hours(0))

        assert await favorite_mail_girl_soon(girl, uid, 4 * 3600, 4 * 3600), \
            'ответ ему паузу не удержал'


class TestTheGapIgnoresMailing:
    """⚠⚠ Рассылка паузу приставания больше не держит.

    Пауза считала все три пула по доводу «для сайта письмо есть письмо». Довод
    верен, но цену назначал занятию, которое им не распоряжается: рассылка
    уходит раз в три минуты по сайту и выбирала паузу каждой девушки раньше,
    чем до неё доберётся приставание.

    Замер прода 25.10.2026: **122 письма рассылки против 15 приставаний** за
    сутки, и у 18 анкет из 19 паузу держало письмо рассылки — занятие молчало не
    по своему правилу, а по чужому потоку.

    ⚠ Цена размена названа в `mail_send.MAIL_SEND_GAP_TABLES`: девушка может
    отправить рассылку и следом письмо избранному, и держат это теперь её
    собственные сроки, а не эта пауза.
    """

    async def test_a_mailing_letter_does_not_hold_her(self, girl):
        """Письмо рассылки **ему** ушло минуту назад — приставанию не мешает.

        ⚠ Берём того же человека, которому пишу: с парным счётом чужое письмо
        вообще вне вопроса, а проверяем мы список таблиц — он на своём uid.
        """
        uid = f'u{uuid.uuid4().hex[:8]}'
        await _hers(girl, uid, _now_minus_hours(0))

        assert await favorite_mail_girl_soon(girl, uid, 4 * 3600, 4 * 3600) == '', \
            'рассылка снова держит паузу приставания'

    async def test_the_list_lives_next_to_sending(self):
        """⚠ Список пулов — у отправки, рядом с остальными такими же: своё
        перечисление в `favorite_mail.py` было бы его второй копией."""
        from src.dating.mail_send import (MAIL_SEND_GAP_TABLES,
                                          MAIL_SEND_TABLES)

        assert 'mailing' not in MAIL_SEND_GAP_TABLES
        assert set(MAIL_SEND_GAP_TABLES) < set(MAIL_SEND_TABLES), \
            'пауза считает пул, которого нет среди отправляемых'

    async def test_both_queries_use_the_same_list(self, girl):
        """⚠⚠ Счёт по анкете и счёт по сайту обязаны брать **один** список.

        Первый отвечает значку на странице, второй — очереди круга отправки;
        разойдись они, человек видел бы один срок, а круг ждал бы другой. Число
        таблиц в запросе на правду не влияет — он просто считает иначе.
        """
        await _hers(girl, f'u{uuid.uuid4().hex[:8]}', _now_minus_hours(0))

        busy = await favorite_mail.favorite_mail_pairs_busy_sql(STAND_SITE)

        assert str(girl) not in busy, \
            'по сайту рассылка держит, а по анкете нет — списки разошлись'

    async def test_zero_means_no_pause(self, girl, monkeypatch):
        """⚠ Ноль — законное значение, а не «настройки нет»: человек выбрал
        «без паузы», и выдумывать за него умолчание нельзя."""
        uid = f'u{uuid.uuid4().hex[:8]}'
        await _hers(girl, uid, _now_minus_hours(0), 'favorite_mail')

        assert await favorite_mail_girl_soon(girl, uid, 0, 0) == ''

        async def _no_pause(site_id):
            return 0, 0

        monkeypatch.setattr(favorite_mail, 'favorite_mail_gap_range', _no_pause)

        assert await favorite_mail_pairs_busy_sql(STAND_SITE) == '', \
            'условие без паузы всё равно строится — оно всегда истинно'


class TestTheReasonNamesTheGirl:
    """⚠⚠ Причина про анкету обязана называть анкету.

    Подпись у причины — **адресат**, потому что причин про него большинство. Но
    пауза — про пару, и называть обязана **обоих**; в шапке пула выходило так:

        Richie (514296): она писала 6 ч 14 мин назад, пауза 9 часов

    «Она» читается как про Richie: он единственный названный в строке. Спрошено
    человеком, глядящим на прод: «кто это она?» — ответом оказалась STELA,
    которой Richie в строке даже не был виден.
    """

    async def test_the_pause_reason_says_her_name(self, girl):
        """Имя передали — оно и стоит в причине вместо «она»."""
        uid = f'u{uuid.uuid4().hex[:8]}'
        await _hers(girl, uid, _now_minus_hours(0), 'favorite_mail')

        why = await favorite_mail_girl_soon(girl, uid, 4 * 3600, 4 * 3600,
                                            'STELA 643694')

        assert why.startswith('STELA 643694 писала'), why
        assert 'она писала' not in why, 'имя не вытеснило местоимение'

    async def test_the_reason_says_which_man_it_holds(self, girl):
        """⚠⚠ С сроком на пару — и его имя: «писала 6 ч назад»
        у анкеты с тремя избранными не отвечает, **кому** ждать;
        `his` подставляет адресата."""
        uid = f'u{uuid.uuid4().hex[:8]}'
        await _hers(girl, uid, _now_minus_hours(1), 'favorite_mail')

        why = await favorite_mail_girl_soon(girl, uid, 4 * 3600, 4 * 3600,
                                            'STELA 643694', 'Leslie')

        assert why.startswith('STELA 643694 писала Leslie'), why

    async def test_without_a_name_it_falls_back_to_the_pronoun(self, girl):
        """⚠ Имени нет — прежнее «она»: строка без подлежащего хуже неточной.

        Так бывает у анкеты без карточки с сайта, и у вызывающих, которым имя
        взять неоткуда (окно пары — там анкета видна в шапке разговора).
        """
        uid = f'u{uuid.uuid4().hex[:8]}'
        await _hers(girl, uid, _now_minus_hours(0), 'favorite_mail')

        assert (await favorite_mail_girl_soon(girl, uid, 4 * 3600,
                                              4 * 3600)).startswith('она писала')

    def test_the_name_comes_from_the_card(self):
        """⚠ Имя собирается **тем же** `_me_name`, которым анкету называет чат:
        свой разбор карточки был бы третьей копией правила «как зовут нашу
        девушку», а оно уже разъезжалось."""
        from src.dating.favorite_mail import _girl_said

        said = _girl_said({'profile': {'profile': '{"name": "STELA",'
                                                  ' "id": "643694"}'}})

        assert said == 'STELA 643694'
        # ⚠ Карточки нет — остаётся логин: он есть всегда, и строка не теряет
        # подлежащего.
        assert _girl_said({'profile': {'login': 'x@example.com'}}) \
            == 'x@example.com'

    def test_the_batch_reason_has_no_dangling_pronoun(self):
        """⚠ Указательное «эта анкета» рядом с чужим именем в шапке — та же
        поломка, что местоимение. Строка начинается с «уже писала ему», а
        подлежащее ей не нужно: имя пары даёт заголовок строки."""
        from src.dating.favorite_mail import FAVORITE_MAIL_SKIP_BUSY

        assert 'эта анкета' not in FAVORITE_MAIL_SKIP_BUSY.lower()
        assert FAVORITE_MAIL_SKIP_BUSY.startswith('уже писала')


class TestTheQueueTellsWhenSheWrites:
    """⚠⚠ Значок в списке анкет отвечает на «почему она молчит».

    Пул приставания пуст, генерация включена, анкеты в сети — и ничего не
    происходит. Причина законная: у каждой девушки идёт её пауза между письмами,
    — но видно её было только в одной строке шапки, и то про первую пару из ста
    сорока. Очередь (`favorite_mail_queue`) даёт ответ по каждой анкете.

    ⚠ Считается **теми же** выражениями, что решают за круг: паузой `_pairs_busy`
    и отбором пар `_quiet`. Своя копия на странице разошлась бы с ним на первой
    правке — грабля, случавшаяся дважды.
    """

    async def test_a_free_girl_waits_nothing(self, girl, auth_client):
        """Писем от неё не было — ждать нечего, и первый в очереди назван."""
        uid = f'u{uuid.uuid4().hex[:8]}'
        await _star(girl, uid)
        await _online(girl)

        got = await favorite_mail.favorite_mail_queue(STAND_SITE)

        assert girl in got, 'анкеты с избранным нет в очереди'
        assert got[girl]['wait_sec'] == 0
        assert got[girl]['next_uid'] == uid
        assert got[girl]['ready'] >= 1

    async def test_a_recent_letter_puts_her_in_the_queue(self, girl):
        """Она только что писала — очередь называет остаток её паузы.

        ⚠ Срок и пауза приезжают **из одного жребия**: спроси их страница
        порознь, второй вопрос бросил бы его заново по другому письму — и
        человек увидел бы «пауза 9 часов, ждать 10».
        """
        uid = f'u{uuid.uuid4().hex[:8]}'
        await _star(girl, uid)
        await _online(girl)
        # ⚠ Своим пулом и **ему**: с парным счётом письмо постороннему
        # человеку эту пару не держит (`TestTheGapIgnoresMailing`).
        await _hers(girl, uid, _now_minus_hours(0), 'favorite_mail')

        got = (await favorite_mail.favorite_mail_queue(STAND_SITE)).get(girl)

        assert got and got['wait_sec'] > 0, 'пауза не попала в очередь'
        assert got['wait_sec'] <= got['gap_sec'], \
            'ждать дольше самой паузы — значит числа из разных жребиев'

    async def test_one_free_pair_needs_no_wait(self, girl):
        """⚠⚠ Половина паузы — не пауза: свободна хоть одна пара — «пишет сейчас».

        Значок «ждёт 4 часа» анкету с двумя избранными, где одному писали
        минуту назад а другому — вечность, врал бы: круг уйдёт к свободному
        сразу, и ждать человеку нечего.
        """
        wrote_to = f'u{uuid.uuid4().hex[:8]}'
        free_man = f'u{uuid.uuid4().hex[:8]}'
        await _star(girl, wrote_to)
        await _star(girl, free_man)
        await _online(girl)
        await _hers(girl, wrote_to, _now_minus_hours(0), 'favorite_mail')

        got = (await favorite_mail.favorite_mail_queue(STAND_SITE)).get(girl)

        assert got and got['wait_sec'] == 0, \
            'свободная пара спряталась за ждущую'
        assert got['next_uid'] == free_man, \
            'первым назван не тот, кому анкета вправе писать сейчас'

    async def test_all_pairs_held_wait_the_shortest(self, girl):
        """Стоят все пары — значок показывает **меньший** из остатков.

        Круг смотрит ту же пару, что и значок: пока ждущих несколько, первым
        уйдёт скоросвободная, и обещать человеку больший срок — врать на час.
        """
        first = f'u{uuid.uuid4().hex[:8]}'
        second = f'u{uuid.uuid4().hex[:8]}'
        await _star(girl, first)
        await _star(girl, second)
        await _online(girl)
        await _hers(girl, first, _now_minus_hours(0), 'favorite_mail')
        await _hers(girl, second, _now_minus_hours(0), 'favorite_mail')

        got = (await favorite_mail.favorite_mail_queue(STAND_SITE)).get(girl)

        assert got and got['wait_sec'] > 0, 'стоят все пары, а значок «сейчас»'
        assert got['wait_sec'] <= got['gap_sec'], \
            'ждать дольше самой паузы — значит числа из разных жребиев'

    async def test_a_girl_with_nobody_to_write_is_absent(self, girl):
        """⚠ Писать некому — записи нет вовсе: значок «0 в очереди» сообщал бы
        пустоту у каждой второй строки списка."""
        await _online(girl)

        assert girl not in await favorite_mail.favorite_mail_queue(STAND_SITE)

    async def test_the_route_answers_by_site(self, girl, auth_client):
        """⚠ Ручка отдаёт **весь сайт разом**: значок стоит у каждой строки, и
        запрос на анкету означал бы сорок запросов на одну отрисовку."""
        uid = f'u{uuid.uuid4().hex[:8]}'
        await _star(girl, uid)
        await _online(girl)

        got = (await auth_client.get('/api/dating/favorite-mail/queue'
                                     f'?site_id={STAND_SITE}')).json()['result']

        assert str(girl) in got, 'ручка не назвала анкету с избранным'
        assert got[str(girl)]['next_uid'] == uid

    async def test_the_route_without_a_site_is_empty_not_an_error(self,
                                                                 auth_client):
        """⚠ Сайт не выбран — пустой ответ, а не 422: страница зовёт ручку на
        каждой перерисовке списка, и до выбора сайта это обычное состояние."""
        got = await auth_client.get('/api/dating/favorite-mail/queue')

        assert got.status_code == 200 and got.json()['result'] == {}


class TestTheLimitIsPerPair:
    """⚠⚠⚠ Дневной предел — на пару «анкета + человек», и только письма избранным.

    Решение владельца 25.11.2026: каждая анкета вправе написать этому человеку
    своё письмо в сутки, письма других девушек её счёт не тратят, а рассылка с
    избранными не связана вовсе. Прежде ключом был человек и считалась
    рассылка: при пределе 1 утренняя рассылка любой девушки закрывала его для
    всех анкет на сутки — около одного письма избранным в сутки на анкету.
    """

    async def test_her_own_letter_counts(self, girl):
        """Её письмо ему — в счёт её предела."""
        uid = f'u{uuid.uuid4().hex[:8]}'
        await _hers(girl, uid, _now_minus_hours(2), 'favorite_mail')

        why = await favorite_mail_person_soon(STAND_SITE, uid, per_day=1,
                                              profile_id=girl)

        assert 'за сутки' in why, why

    async def test_another_girls_letter_does_not_count(self, girl, auth_client):
        """Письмо другой нашей девушки ему этой анкете не мешает."""
        uid = f'u{uuid.uuid4().hex[:8]}'
        other = await _profile(auth_client, 'fav2')

        try:
            await _hers(other, uid, _now_minus_hours(1), 'favorite_mail')
            await _hers(other, uid, _now_minus_hours(2), 'favorite_mail')

            assert await favorite_mail_person_soon(
                STAND_SITE, uid, per_day=1, profile_id=girl) == '', \
                'письма другой девушки снова тратят чужой предел'
        finally:
            await auth_client.delete(f'/api/dating/profile/{other}')

    async def test_mailing_does_not_count(self, girl):
        """⚠⚠ Рассылка с избранными не связана — даже её собственная."""
        uid = f'u{uuid.uuid4().hex[:8]}'
        for hours in (1, 2, 3, 4):
            await _hers(girl, uid, _now_minus_hours(hours), 'mailing')

        assert await favorite_mail_person_soon(STAND_SITE, uid, per_day=1,
                                               profile_id=girl) == '', \
            'рассылка снова съедает предел избранных'

    async def test_other_people_are_not_affected(self, girl):
        """⚠ Письма одному не закрывают дорогу к другому."""
        busy, free = f'u{uuid.uuid4().hex[:8]}', f'u{uuid.uuid4().hex[:8]}'
        await _hers(girl, busy, _now_minus_hours(0), 'favorite_mail')

        assert await favorite_mail_person_soon(STAND_SITE, free, per_day=1,
                                               profile_id=girl) == ''

    def test_the_query_keys_on_the_pair(self):
        """⚠ Сторож от возврата: условие отбора обязано нести анкету.

        ⚠ Без докстринга — в нём разобрана прежняя редакция.
        """
        code = _source_without_doc(favorite_mail_person_sent)

        assert 'AND profile_id = %s' in code, \
            'дневной предел снова считает по человеку, а не по паре'

    def test_only_favorite_letters_are_counted(self):
        """⚠ Список пулов — один, и в нём только письма избранным."""
        from src.dating.favorite_mail import FAVORITE_MAIL_DAY_TABLES

        assert FAVORITE_MAIL_DAY_TABLES == ('favorite_mail',)
        assert 'FAVORITE_MAIL_DAY_TABLES' in _source_without_doc(
            favorite_mail_person_sent)


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
        await _hers(girl, uid, _now_minus_hours(3), 'favorite_mail')
        await _hers(girl, uid, _now_minus_hours(4), 'favorite_mail')

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
        # ⚠ Своим пулом: рассылку пауза не считает (`TestTheGapIgnoresMailing`).
        await _hers(girl, uid, _now_minus_hours(1), 'favorite_mail')

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


class TestOneLetterPerPairPerPass:
    """⚠⚠ За один заход пара получает одно письмо, а человеку — одна анкета.

    Пауза говорит: от пары уходит одно письмо за срок. Набери паре заход пять —
    четыре пролежали бы в пуле до вечера и ушли бы протухшими: отбор берёт того,
    кто **сейчас** в сети, а через шестнадцать часов это уже неправда.

    ⚠ С 26.11.2026 «одно письмо за заход» — правило **про пару**, а не про
    анкету: той же девушке с разными мужчинами набирать можно (цена, принятая
    владельцем, — случай Елены 21.09). Встречную сторону держит `written`:
    одному человеку за заход пишет только одна наша анкета.
    """

    async def test_the_second_pair_of_the_same_girl_is_written(self, girl,
                                                               monkeypatch):
        """⚠ Те же девушка, разные мужчины — три письма за заход законны.

        Это та самая принятая цена: прыть одной анкеты по разным адресатам.
        Прежний тест утверждал обратное («второй паре той же девушки —
        отказ») — так набор жил с сентября по ноябрь 2026.
        """
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

        assert got['made'] == 3, \
            f"разные мужчины той же девушки снова один отказ: {got['made']}"

    async def test_the_same_man_is_written_once_per_pass(self, girl, auth_client,
                                                         monkeypatch):
        """⚠⚠ Встречная цена: одному человеку за заход пишет одна анкета.

        Это не пауза (те пары свободны обе), а `written` в наборе — иначе
        избранный, общий у анкет, получил бы залп от всего сайта за минуту.
        """
        import src.dating.favorite_mail as mod

        uid = f'u{uuid.uuid4().hex[:8]}'
        other = await _profile(auth_client, 'fav2')
        await _online(other)

        try:
            await _star(girl, uid)
            await _hers(girl, uid, '2026-09-01 10:00:00')
            await _star(other, uid)
            await _hers(other, uid, '2026-09-01 10:00:00')

            monkeypatch.setattr(mod, 'answer_mail_write',
                                _answer('Давно не общались, как ты?'))
            monkeypatch.setattr(mod, '_agent', _agent_id())
            monkeypatch.setattr(mod, '_sync_stars', _nothing())

            got = await mod.favorite_mail_make(STAND_SITE, limit=5)

            assert got['made'] == 1, \
                f"одному человеку за заход написали {got['made']} анкеты"
            assert any('этим заходом' in one for one in got['reasons']), \
                got['reasons']
        finally:
            await auth_client.delete(f'/api/dating/profile/{other}')

    async def test_a_pair_in_pause_gets_nothing(self, girl, monkeypatch):
        """⚠ Пауза проверяется **и в наборе**, а не только в отправке: письмо —
        это вызов LLM, и платить за него, чтобы черновик лежал до вечера, незачем.
        """
        import src.dating.favorite_mail as mod

        uid = f'u{uuid.uuid4().hex[:8]}'
        await _star(girl, uid)
        # ⚠ Своим пулом: рассылку пауза не считает (`TestTheGapIgnoresMailing`).
        await _hers(girl, uid, _now_minus_hours(1), 'favorite_mail')

        monkeypatch.setattr(mod, 'answer_mail_write',
                            _answer('Давно не общались, как ты?'))
        monkeypatch.setattr(mod, '_agent', _agent_id())
        monkeypatch.setattr(mod, '_sync_stars', _nothing())

        got = await mod.favorite_mail_make(STAND_SITE, limit=5, profile_id=girl)

        assert got['made'] == 0, 'письмо набралось, хотя пара в паузе'
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
        monkeypatch.setattr(round_, 'round_slot_due',
                            lambda key, site_id, what, every=None: True)
        # ⚠ Отметку круг **не ставит сам** — её ставит обвязка слоя внутри
        # `round_sites_run`. Здесь стояла подмена `round_slot_mark` у модуля, и
        # она ничего не отменяла: модуль этого имени не зовёт. Держалась она на
        # том, что имя было импортировано — то есть на мёртвом импорте, который
        # и выглядел живым только из-за этой строки.
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

        code = _source_without_doc(favorite_mail_round._gate)

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
            'src.dating.favorite_mail.site_seconds', _site_seconds)

        assert await favorite_mail.favorite_mail_gap_range(1) == (3600, 3600), \
            'шестьдесят минут это ровно час'

    async def test_the_default_is_four_hours(self, monkeypatch):
        """⚠ Умолчание тоже в минутах: 240 — те же четыре часа, что были."""
        from src.dating import favorite_mail

        async def _nothing(site_id, key):
            return 0

        monkeypatch.setattr(
            'src.dating.favorite_mail.site_seconds', _nothing)

        assert favorite_mail.FAVORITE_MAIL_GAP_DEFAULT_MIN == 240
        assert await favorite_mail.favorite_mail_gap_range(1) == (240 * 60,
                                                                  240 * 60)

    async def test_the_upper_bound_falls_back_to_the_lower(self, monkeypatch):
        """⚠⚠ «До» ниже «от» — описка, и работать по ней нельзя.

        Ноль здесь законен и значит «без разброса» — это умолчание, то есть
        прежнее поведение. Меньшее числом — уже описка, и взять её значило бы
        писать **чаще**, чем человек велел: нижняя граница перестала бы держать.
        """
        from src.dating import favorite_mail

        async def _site_seconds(site_id, key):
            return {favorite_mail.FAVORITE_MAIL_GAP_SETTING: 240,
                    favorite_mail.FAVORITE_MAIL_GAP_MAX_SETTING: 60}.get(key, 0)

        monkeypatch.setattr(
            'src.dating.favorite_mail.site_seconds', _site_seconds)

        assert await favorite_mail.favorite_mail_gap_range(1) == (240 * 60,
                                                                  240 * 60)

    async def test_the_range_reaches_the_reader(self, monkeypatch):
        """Обе настройки прочитаны и обе в минутах."""
        from src.dating import favorite_mail

        async def _site_seconds(site_id, key):
            return {favorite_mail.FAVORITE_MAIL_GAP_SETTING: 240,
                    favorite_mail.FAVORITE_MAIL_GAP_MAX_SETTING: 360}.get(key, 0)

        monkeypatch.setattr(
            'src.dating.favorite_mail.site_seconds', _site_seconds)

        assert await favorite_mail.favorite_mail_gap_range(1) == (240 * 60,
                                                                  360 * 60)

    def test_the_setting_is_a_number_not_a_choice(self):
        """⚠ Числовое поле, а не набор: осмысленна любая пауза.

        Набор отвечает на вопрос «какие значения осмысленны», и для многих
        сроков это верно. Здесь он заставлял брать ближайший вариант, а не свой.
        """
        known = SITE_KNOWN['favorite_mail_gap']

        assert known.get('kind') == 'number', 'пауза снова стала набором'
        assert not known.get('options'), 'у числового поля не бывает вариантов'
        assert known.get('min') == 1, 'ноль минут это «паузы нет», а не пауза'

    def test_the_title_names_the_unit(self):
        """⚠⚠ Единица — в заголовке, и это не украшение.

        Число без подписи читается как секунды по привычке: так эта настройка и
        жила. «240» без «мин» — это сколько?
        """
        assert 'мин' in SITE_KNOWN['favorite_mail_gap']['title']

    def test_the_default_in_the_registry_matches_the_code(self):
        """⚠ Два умолчания об одной паузе — в реестре и в коде.

        Разойдись они, сайт без настройки вёл бы себя не так, как показывает
        форма: человек видит 240, а круг ждёт другое.
        """
        from src.dating.favorite_mail import FAVORITE_MAIL_GAP_DEFAULT_MIN
        assert int(SITE_KNOWN['favorite_mail_gap']['default']) == \
            FAVORITE_MAIL_GAP_DEFAULT_MIN


class TestTheGapIsARange:
    """⚠⚠ Пауза — промежуток, и срок каждой паре бросается жребием.

    Одно число на сайт значило бы, что письма уходят от девушки с ровно
    одинаковым шагом: сайт видит не только частоту, но и ровность, а ровность у
    живого человека не встречается. Второе число — «до», и жребий между ними.
    """

    def test_the_upper_bound_is_a_second_setting(self):
        """⚠ Своя настройка, числовая, с нулём в нижней границе: ноль — «ровно
        от», то есть законное «без разброса»."""
        known = SITE_KNOWN['favorite_mail_gap_max']

        assert known.get('kind') == 'number'
        assert known.get('min') == 0, 'ноль здесь значит «без разброса»'
        assert known.get('group') == SITE_KNOWN['favorite_mail_gap']['group'], \
            'границы одной паузы разъехались по разным вкладкам'

    def test_the_default_keeps_the_old_behaviour(self):
        """⚠⚠ Умолчание ноль — выкладка не удлиняет паузы молча.

        Нижнюю границу сайты выставили руками; добавь мы к ней разброс вверх по
        умолчанию, письма пошли бы реже там, где об этом не просили.
        """
        from src.dating.favorite_mail import FAVORITE_MAIL_GAP_MAX_DEFAULT_MIN

        assert int(SITE_KNOWN['favorite_mail_gap_max']['default']) == \
            FAVORITE_MAIL_GAP_MAX_DEFAULT_MIN == 0

    def test_both_halves_stand_in_one_row(self):
        """⚠⚠ Две границы — один вопрос, и строка в таблице у них одна.

        Ведущая называет ключ ведомой (`pair`), ведомая помечена `paired` —
        своей строки у неё нет. Разойдись эти два поля, страница показала бы
        либо две независимые настройки, либо потеряла бы вторую вовсе.
        """
        low = SITE_KNOWN['favorite_mail_gap']
        high = SITE_KNOWN['favorite_mail_gap_max']

        assert low.get('pair') == 'favorite_mail_gap_max'
        assert high.get('paired') is True
        assert low.get('label') and high.get('label'), \
            'поля пары без подписей «от» и «до» не различить'

    async def test_the_pair_reaches_the_page(self, auth_client):
        """⚠ Признаки пары доезжают **ручкой**: страница рисует два поля в одной
        строке по ним, и своего списка парных настроек у неё быть не должно.

        ⚠⚠ Ведомую отдаём тоже — иначе страница сочтёт ключ чужим и покажет его
        в «Своих настройках»: он не исчезнет, а переедет на соседнюю вкладку.
        Тот же довод, что у `hidden` (`test_mail_round.py`).
        """
        known = (await auth_client.get('/api/dating/site')).json()['result']['known']
        mine = {row['key']: row for row in known}

        assert mine['favorite_mail_gap']['pair'] == 'favorite_mail_gap_max'
        assert mine['favorite_mail_gap_max']['paired'] is True
        assert mine['favorite_mail_gap']['label'] == 'от'
        assert mine['favorite_mail_gap_max']['label'] == 'до'
        # Соседняя настройка рядом — обычная, без пары.
        assert mine['favorite_mail_per_day']['pair'] == ''
        assert mine['favorite_mail_per_day']['paired'] is False

    def test_the_dice_is_steady_within_the_bounds(self):
        """⚠⚠ Жребий устойчив: пока письмо то же, срок тот же.

        Круг отправки просыпается раз в минуту; бросай он случай на каждом тике,
        письмо уходило бы на первом низком выпадении — работала бы одна «от».
        """
        low, high = 4 * 3600, 6 * 3600

        first = favorite_mail_gap_of(7, 'u42', '2026-10-25 10:00:00', low, high)
        again = favorite_mail_gap_of(7, 'u42', '2026-10-25 10:00:00', low, high)

        assert first == again, 'срок той же пары и того же письма переменился'
        assert low <= first <= high, 'жребий вышел за границы промежутка'

    def test_a_new_letter_rolls_a_new_term(self):
        """⚠ Время последнего письма ей — часть ключа: без него у пары была бы
        одна пауза навсегда, и разброс был бы между парами, а не между
        письмами."""
        low, high = 1 * 3600, 20 * 3600

        terms = {favorite_mail_gap_of(7, 'u42', f'2026-10-25 1{n}:00:00',
                                      low, high)
                 for n in range(10)}

        assert len(terms) > 1, 'срок не меняется с новым письмом'

    def test_each_man_gets_his_own_term(self):
        """⚠⚠ Получатель — часть ключа: жребий одной пары не разброс для
        остальных. Убери uid из ключа — все мужчины одной девушки получили бы
        один срок, и разброс был бы между девушками, а не между адресатами."""
        low, high = 1 * 3600, 20 * 3600

        terms = {favorite_mail_gap_of(7, f'u{n}', '2026-10-25 10:00:00',
                                      low, high)
                 for n in range(10)}

        assert len(terms) > 1, 'разные получатели бросают один и тот же срок'

    def test_no_spread_means_exactly_the_lower_bound(self):
        """⚠ «До» не больше «от» — пауза ровно «от», прежнее поведение."""
        assert favorite_mail_gap_of(7, 'u42', '2026-10-25 10:00:00',
                                    4 * 3600, 0) == 4 * 3600

    def test_the_words_name_the_whole_range(self):
        """⚠ Подпись называет промежуток целиком: «4 часа» при выставленных «до
        6» отправило бы человека искать поломку — ждать-то дольше."""
        assert favorite_mail_gap_words(4 * 3600, 6 * 3600) == '4–6 часов'
        assert favorite_mail_gap_words(4 * 3600, 0) == '4 часа'


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

        # ⚠ Функций две: одна называет промежуток, вторая — одну границу. Берём
        # обе: подпись собирается из них вместе, и проверять половину значило бы
        # не проверять ничего.
        found = re.search(r'function favMailGapWords\(minutes, upto\) \{.*?'
                          r'\n    \}.*?function favMailGapOne\(minutes\) \{.*?'
                          r'\n    \}', page, re.S)
        assert found, 'функция подписи паузы не нашлась на странице'

        # ⚠ Последние два случая — про промежуток: подпись обязана назвать обе
        # границы, а «до» ниже «от» читать как «без разброса», ровно как сервер.
        probe = (found.group(0) + '\n'
                 + 'const cases = [[45, 0, "45 мин"], [59, 0, "59 мин"],'
                 + ' [60, 0, "1 час"], [240, 0, "4 часа"], [600, 0, "10 часов"],'
                 + ' [0, 0, "0 мин"], [240, 360, "4 часа — 6 часов"],'
                 + ' [240, 60, "4 часа"]];\n'
                 + 'for (const [value, upto, want] of cases) {\n'
                 + '  const got = favMailGapWords(value, upto);\n'
                 + '  if (got !== want) {\n'
                 + '    console.log(`${value}/${upto} → ${got}, ждали ${want}`);\n'
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


class TestMessageIdReachesThePool:
    """⚠⚠ Номер обмена обязан доехать до строки пула, а не только пройти форму.

    Задача 7 научила `_put` принимать довод `message_id`, но ревью поймало дыру
    мутационным тестом: убери колонку из `INSERT`, оставив сигнатуры прежними —
    и вся сюита `mail` остаётся зелёной, потому что существующие тесты проверяют
    лишь то, что набор не падает на распаковке тройки `answer_mail_write`.
    Здесь стережём само содержимое строки: номер, который отдал агент, должен
    лежать в колонке `favorite_mail.message_id` — без него круг отправки не
    сможет отметить у соседа, что письмо дошло.
    """

    async def test_message_id_lands_in_the_pool_row(self, girl, monkeypatch):
        """Агент отдал ненулевой номер обмена — он обязан лежать в столбце, а не
        потеряться между сочинением письма и записью в пул."""
        import src.dating.favorite_mail as mod

        uid = f'u{uuid.uuid4().hex[:8]}'
        await _star(girl, uid)
        await _hers(girl, uid, '2026-09-01 10:00:00')

        monkeypatch.setattr(mod, 'answer_mail_write',
                            _answer('Давно не общались, как ты?', 9911))
        monkeypatch.setattr(mod, '_agent', _agent_id())
        monkeypatch.setattr(mod, '_sync_stars', _nothing())

        got = await mod.favorite_mail_make(STAND_SITE, limit=5, profile_id=girl)
        rows = [r for r in await _letters() if r['uid'] == uid]

        assert got['made'] == 1
        assert rows and rows[0]['message_id'] == 9911, \
            'номер обмена не доехал до строки пула'

    async def test_silent_agent_leaves_null_not_zero(self, girl, monkeypatch):
        """Агент не дал номера (вернул ноль — обычное поведение подмены
        `_answer` без явного номера) — в строке обязан лежать `NULL`, а не `0`.

        «Не знаем» и «ноль» — разные вещи: отметка доставки у соседа идёт по
        номеру, и ложный ноль в колонке выглядел бы настоящим обменом №0, а не
        его отсутствием — отметка уйдёт в никуда молча, без ошибки в логах.
        """
        import src.dating.favorite_mail as mod

        uid = f'u{uuid.uuid4().hex[:8]}'
        await _star(girl, uid)
        await _hers(girl, uid, '2026-09-01 10:00:00')

        monkeypatch.setattr(mod, 'answer_mail_write',
                            _answer('Давно не общались, как ты?'))
        monkeypatch.setattr(mod, '_agent', _agent_id())
        monkeypatch.setattr(mod, '_sync_stars', _nothing())

        got = await mod.favorite_mail_make(STAND_SITE, limit=5, profile_id=girl)
        rows = [r for r in await _letters() if r['uid'] == uid]

        assert got['made'] == 1
        assert rows and rows[0]['message_id'] is None, \
            'отсутствие номера легло нулём, а не NULL'


class TestHisLetterResetsTheDailyCap:
    """⚠⚠ Дневной предел пары обнуляет **его письмо**.

    Прежде счёт шёл просто «за последние сутки», и письма, ушедшие **до** его
    ответа, держали пару до конца суток. Поймано на проде 11.10.2026: Diana ↔
    Bruce — два письма ушли, он ответил в 10:16, и приставание молчало с
    «за сутки уже ушло 2 письма — предел 1 в сутки», хотя оба ушли раньше его
    ответа.

    Довод тот же, по которому неделей раньше чинили инициативу: его ответ —
    ровно то событие, ради которого занятие и работает, и наказывать за прошлое
    живой разговор нельзя.

    ⚠ Граница **двойная**: `GREATEST(сутки назад, его последнее письмо)`.
    Настройка зовётся «в сутки», и суточное окно никуда не делось — его письмо
    лишь поднимает нижний край.
    """

    async def test_his_letter_clears_what_went_before(self, girl):
        """Письма ушли до его ответа — счёт их больше не держит."""
        uid = f'cap-{uuid.uuid4().hex[:8]}'
        await _hers(girl, uid, _now_minus_hours(5), 'favorite_mail')
        await _hers(girl, uid, _now_minus_hours(4), table='favorite_mail')
        await _his(girl, uid, _now_minus_hours(3))

        got = await favorite_mail_person_sent(STAND_SITE, uid, girl)

        assert got['count'] == 0, 'его письмо не обнулило счёт'
        assert await favorite_mail_person_soon(STAND_SITE, uid, 1, girl) == '', \
            'предел держит пару, которая только что ответила'

    async def test_letters_after_his_reply_do_count(self, girl):
        """А ушедшее **после** его письма считается — предел не снят совсем."""
        uid = f'cap-{uuid.uuid4().hex[:8]}'
        await _his(girl, uid, _now_minus_hours(3))
        await _hers(girl, uid, _now_minus_hours(2), table='favorite_mail')

        got = await favorite_mail_person_sent(STAND_SITE, uid, girl)

        assert got['count'] == 1
        assert await favorite_mail_person_soon(STAND_SITE, uid, 1, girl) != '', \
            'предел перестал держать вовсе'

    async def test_without_his_letters_the_day_window_stands(self, girl):
        """Он не писал ни разу — прежнее поведение, счёт за сутки.

        ⚠ Это и есть случай, ради которого в `GREATEST` нужен `COALESCE`: без
        него граница вышла бы `NULL`, счёт — нулём, и предел снялся бы у всех,
        кто нам не писал, то есть у большинства.
        """
        uid = f'cap-{uuid.uuid4().hex[:8]}'
        await _hers(girl, uid, _now_minus_hours(5), 'favorite_mail')

        got = await favorite_mail_person_sent(STAND_SITE, uid, girl)

        assert got['count'] == 1
        assert await favorite_mail_person_soon(STAND_SITE, uid, 1, girl) != ''

    async def test_the_day_window_still_cuts(self, girl):
        """Сутки режут и при давнем его письме: настройка зовётся «в сутки».

        ⚠ Его письмо **поднимает** нижний край, а не заменяет его. Иначе счёт
        шёл бы от начала времён, и предел стал бы вечным вместо суточного.
        """
        uid = f'cap-{uuid.uuid4().hex[:8]}'
        await _his(girl, uid, _now_minus_hours(72))
        await _hers(girl, uid, _now_minus_hours(48), table='favorite_mail')

        got = await favorite_mail_person_sent(STAND_SITE, uid, girl)

        assert got['count'] == 0, 'письмо двухдневной давности попало в сутки'

    async def test_the_border_keys_on_the_person_not_the_pair(self, girl_make):
        """⚠⚠ Сбрасывает его письмо **любой** нашей девушке.

        Счёт с 25.11.2026 ведётся по паре, а граница — по человеку: его письмо
        любой нашей девушке значит, что почту он читает.
        """
        one = await girl_make()
        two = await girl_make()
        uid = f'cap-{uuid.uuid4().hex[:8]}'
        await _hers(one, uid, _now_minus_hours(5), table='favorite_mail')
        # Написал он **второй** анкете — граница по человеку, сброс общий.
        await _his(two, uid, _now_minus_hours(3))

        got = await favorite_mail_person_sent(STAND_SITE, uid, one)

        assert got['count'] == 0, 'граница посчитала пару, а не человека'


class TestOurReplyDoesNotEatTheCap:
    """⚠⚠ Ответ на **его** письмо предел навязчивости не тратит.

    Предел сторожит навязчивость, а ответ на его письмо — вежливость: считать
    его значит наказывать за ответ.

    Поймано на проде 11.10.2026, числами. При пределе «1 письмо» после его
    ответа счёт показывал ровно 1 — и это был **наш же ответ ему**. Выходил
    замкнутый круг: он пишет → мы отвечаем → предел исчерпан → приставание
    молчит → разговор затихает навсегда. У Bruce и Brian Ritter остаток
    состоял из ответа целиком.

    ⚠ Рассылки в счёте нет с 25.11.2026: она с избранными не связана
    (`TestTheLimitIsPerPair`).
    """

    async def test_a_reply_is_not_counted(self, girl):
        """Наш ответ ему не тратит предел."""
        uid = f'cap-{uuid.uuid4().hex[:8]}'
        await _his(girl, uid, _now_minus_hours(3))
        await _reply_sent(girl, uid, _now_minus_hours(2))

        got = await favorite_mail_person_sent(STAND_SITE, uid, girl)

        assert got['count'] == 0, 'ответ на его письмо съел предел'
        assert await favorite_mail_person_soon(STAND_SITE, uid, 1, girl) == '', \
            'после ответа приставанию по-прежнему нельзя'

    async def test_a_mailing_is_not_counted(self, girl):
        """⚠⚠ Рассылка не тратит: она с избранными не связана (25.11.2026)."""
        uid = f'cap-{uuid.uuid4().hex[:8]}'
        await _his(girl, uid, _now_minus_hours(3))
        await _hers(girl, uid, _now_minus_hours(2))

        got = await favorite_mail_person_sent(STAND_SITE, uid, girl)

        assert got['count'] == 0
        assert await favorite_mail_person_soon(STAND_SITE, uid, 1, girl) == ''

    async def test_a_nudge_is_counted(self, girl):
        """И приставание тратит — оно и есть то, что предел считает."""
        uid = f'cap-{uuid.uuid4().hex[:8]}'
        await _his(girl, uid, _now_minus_hours(3))
        await _hers(girl, uid, _now_minus_hours(2), table='favorite_mail')

        got = await favorite_mail_person_sent(STAND_SITE, uid, girl)

        assert got['count'] == 1

    async def test_the_prod_case_unblocks(self, girl):
        """⚠ Случай с прода целиком: два письма, его ответ, наш ответ на него.

        При пределе 1 пара обязана стать годной — ровно то, чего не хватало
        Diana ↔ Bruce.
        """
        uid = f'cap-{uuid.uuid4().hex[:8]}'
        await _hers(girl, uid, _now_minus_hours(8))
        await _hers(girl, uid, _now_minus_hours(7), table='favorite_mail')
        await _his(girl, uid, _now_minus_hours(4))
        await _reply_sent(girl, uid, _now_minus_hours(3))

        assert await favorite_mail_person_soon(STAND_SITE, uid, 1, girl) == '', \
            'пара всё ещё заперта пределом после его ответа'


class TestManualLettersDoNotHoldTheRound:
    """⚠⚠⚠ Ручная пачка места круга не занимает.

    25.11.2026 оператор одним нажатием поставил Diana 24 письма; адресаты были
    не в сети, и 23 черновика держали потолок пула (20 на сайт) — круг
    перестал сочинять всем девятнадцати анкетам.
    """

    async def _put(self, profile_id: int, message_id):
        async with mysql_get_db_async() as db:
            await db.execute(
                'INSERT INTO favorite_mail'
                ' (site_id, profile_id, uid, name, body, status, message_id)'
                " VALUES (%s, %s, %s, 'X', 'hi', 'draft', %s)",
                (STAND_SITE, int(profile_id), f'm{uuid.uuid4().hex[:8]}',
                 message_id))
            await db.connection.commit()

    async def test_the_round_does_not_count_manual(self, girl):
        from src.dating.favorite_mail import favorite_mail_pool_left

        before_all = await favorite_mail_pool_left(STAND_SITE, ready_only=True)
        before_round = await favorite_mail_pool_left(STAND_SITE, ready_only=True,
                                                     manual=False)
        await self._put(girl, None)       # ручное — номера обмена нет
        await self._put(girl, 4242)       # круга — номер обмена есть

        assert await favorite_mail_pool_left(
            STAND_SITE, ready_only=True) == before_all + 2, \
            'страница обязана видеть пул целиком'
        assert await favorite_mail_pool_left(
            STAND_SITE, ready_only=True, manual=False) == before_round + 1, \
            'ручное письмо снова держит потолок круга'

    def test_the_round_asks_without_manual(self):
        from src.dating import favorite_mail_round

        code = _source_without_doc(favorite_mail_round._work)

        assert 'manual=False' in code
