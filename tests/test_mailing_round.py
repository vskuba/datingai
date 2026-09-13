"""Круг рассылки: пул набирается сам по расписанию сайта.

⚠ **Наружу сюита не ходит** (`testing.md`), а набор пула — это вызовы LLM за
живыми переменными. Поэтому здесь проверяется **решение круга**, а не сам набор:
кого он берёт в работу, когда считает, что пора, и что делает с отметкой. Набор
проверяется отдельно (`test_mailing.py`), а его качество — живыми прогонами.

Отсюда приём: подменяется `dating_mailing_make` — единственная дорогая часть.
Всё, что до неё, работает по-настоящему, с живой базой и живым Redis.

⚠ **Круг готовит письма, но не отправляет их.** Это проверяется прямо: строка,
которую он кладёт, обязана быть `draft`. Отправка — отдельный выключатель, и
включённое расписание согласием на неё не является.
"""

import pytest

from src.dating import dating_mailing_round as round_
from src.dating.dating_mailing_round import (DATING_MAILING_EVERY_DEFAULT,
                                             DATING_MAILING_POOL_MAX,
                                             DATING_MAILING_POOL_MAX_DEFAULT,
                                             DATING_MAILING_ROUND_KEY,
                                             _due, _mark, _minutes_now, _pool_max,
                                             _pool_size, dating_mailing_round_run)

pytestmark = pytest.mark.mail

# Выдуманный сайт: у соседа его нет, живую рассылку задеть невозможно.
SITE = 999_777

# Смена во все сутки — чтобы проверка «идёт ли смена» не зависела от часа прогона.
# ⚠ **`24:00`, а не `23:45`.** Смена задумана «во все сутки», чтобы проверка
# «идёт ли смена» не зависела от часа прогона, — но `23:45` оставлял дыру в
# четверть часа, и сюита честно краснела каждый вечер между 23:45 и полуночью.
# Поймано живьём: двадцать три теста в трёх файлах разом, и все по этой причине.
#
# `24:00` — законный конец суток и здесь, и на проде: правая граница смены
# строгая (`from <= minutes < to`), так что 1440 включает и последнюю минуту.
ALWAYS = [{'from': '00:00', 'to': '24:00'}]


def _hhmm(minutes: int) -> str:
    """Минуты суток в «ЧЧ:ММ». ⚠ Одним местом, а не арифметикой по частям: она
    даёт «19:60» на исходе часа, и такое расписание ручка отклоняет."""
    minutes %= 24 * 60

    return f'{minutes // 60:02d}:{minutes % 60:02d}'


@pytest.fixture
async def site(auth_client):
    """Сайт с включённым расписанием рассылки. Убирает за собой всё, что тронул.

    ⚠ **Метку «ушёл» снимаем сами, и без неё тест не работал бы вовсе.** Сайта с
    выдуманным номером у соседа нет, фоновая сверка честно ставит ему `is_gone`, а
    круг такие пропускает — писать некому. Ручки «вернуть сайт» здесь нет и быть не
    должно (список ведёт RhapsodyAi), поэтому правим строку снимка напрямую.
    """
    from mysql_.mysql_ import mysql_get_db_async

    before = (await auth_client.get(
        f'/api/dating/site/{SITE}/mailing-schedule')).json()['result']

    await auth_client.put(f'/api/dating/site/{SITE}/mailing-schedule',
                          json={'schedule': ALWAYS})
    await auth_client.put(f'/api/dating/site/{SITE}/mailing-schedule-on',
                          json={'mailing_schedule_on': True})

    async with mysql_get_db_async() as db:
        await db.execute('SELECT is_gone FROM dating_site WHERE site_id = %s', (SITE,))
        row = await db.fetchone()
        gone = int((row or {}).get('is_gone') or 0)
        await db.execute('UPDATE dating_site SET is_gone = 0 WHERE site_id = %s', (SITE,))
        await db.connection.commit()

    yield SITE

    await auth_client.put(f'/api/dating/site/{SITE}/mailing-schedule',
                          json={'schedule': before['schedule']})
    await auth_client.put(f'/api/dating/site/{SITE}/mailing-schedule-on',
                          json={'mailing_schedule_on': before['mailing_schedule_on']})

    async with mysql_get_db_async() as db:
        await db.execute('UPDATE dating_site SET is_gone = %s WHERE site_id = %s',
                         (gone, SITE))
        await db.connection.commit()


class _FakeRedis:
    """Redis настолько, насколько его читает круг: `get` и `setex`.

    ⚠ Подделка, а не живой Redis, и не ради скорости: сюита идёт **с хоста**, а
    Redis живёт в контейнере под своим именем — снаружи его просто нет. Тот же
    приём и та же причина, что у круга импорта (`test_chat_import_round.py`).
    """

    def __init__(self):
        self.store: dict[str, str] = {}
        self.ttl: dict[str, int] = {}

    def get(self, key):
        return self.store.get(key)

    def setex(self, key, seconds, value):
        self.store[key] = value
        self.ttl[key] = int(seconds)

    # ⚠ Нужен по-настоящему: `_unmark` снимает отметку именно им, а без метода
    # подделка бросала бы `AttributeError` — и круг проглотил бы его молча, как
    # любой сбой Redis. Проверка «слот отпущен» тогда всегда зеленела бы,
    # проверяя вместо правила устройство заглушки.
    def delete(self, key):
        self.store.pop(key, None)
        self.ttl.pop(key, None)


@pytest.fixture
def clean_mark(monkeypatch):
    """Пустая память отметок на каждый тест.

    ⚠ Иначе тесты влияли бы друг на друга: первый ставит отметку на полчаса, и все
    следующие видят «ещё не пора».
    """
    fake = _FakeRedis()
    monkeypatch.setattr(round_, 'redis_conn_get', lambda: fake)

    return fake


@pytest.fixture
def made(monkeypatch):
    """Подменяет набор пула: считает вызовы вместо походов к LLM.

    Итог захода настраивается — `made.result.update(...)`: от него зависит, держит
    круг получасовой слот или отпускает его следующему тику.
    """
    # ⚠ Список с полем, а не голый список: тесты читают вызовы как список (так уже
    # написан десяток проверок), а настроить им нужно ещё и итог. Обычному списку
    # поле не присвоить — отсюда наследник.
    class _Calls(list):
        pass

    calls = _Calls()
    # ⚠ Свой словарь на каждый тест, а не поле класса: `made.result.update(...)`
    # правит его на месте, и общий словарь протёк бы в следующий тест.
    calls.result = {'made': 1, 'skipped': 0, 'reasons': [], 'left': 0, 'stopped': ''}

    async def fake(site_id, templates, limit):
        calls.append({'site_id': int(site_id), 'templates': len(templates),
                      'limit': int(limit)})

        return dict(calls.result)

    monkeypatch.setattr(round_, 'dating_mailing_make', fake)

    return calls


@pytest.fixture
async def template(auth_client):
    """Включённый шаблон: без него набирать нечем и круг сайт пропустит."""
    import uuid

    created = await auth_client.post('/api/dating/mail', json={
        'site_id': SITE, 'name': f'tst-{uuid.uuid4().hex[:6]}',
        'body': 'Привет!', 'enabled': True})
    made_id = created.json()['result']['id']

    yield made_id

    await auth_client.delete(f'/api/dating/mail/{made_id}')


@pytest.fixture
async def profile(auth_client):
    """Анкета: без неё письмо в пул не ляжет — `profile_id` внешний ключ.

    ⚠ Заводится на **живом** сайте (343 у поддельного соседа): заведение сверяет
    номер, и выдуманный он отвергает. Письма при этом кладутся на выдуманный —
    ничто их не связывает, `site_id` у письма просто число. Тот же развод и та же
    причина, что в `test_mailing.py`: однажды очистка пула на живом сайте унесла
    полсотни писем, набранных руками.
    """
    import uuid

    # ⚠ Стенд, а не рабочий сайт: анкета сюиты не должна попадать в круги
    # воркера (`test_mail_in.py`, `STAND_SITE`).
    created = await auth_client.post('/api/dating/profile', json={
        'site_id': 999_343, 'login': f'round-{uuid.uuid4().hex[:8]}@example.com',
        'password': 'x'})
    made = created.json()['result']

    yield made

    await auth_client.delete(f'/api/dating/profile/{made}')


@pytest.fixture
async def pool(profile):
    """Кладёт письма в пул выдуманного сайта. Возвращает «положить N со статусом».

    ⚠ Через базу, а не через набор: набор требует адресатов в сети, а у
    выдуманного сайта снимка ленты нет и быть не должно.

    ⚠ **Анкету ставим в сеть**, и это условие теста, а не обстановка. Круг чистит
    пул от черновиков ушедших анкет **перед** тем, как считать в нём место
    (`api.md`, §3.7.8): у офлайн-анкеты письма исчезали бы, и потолок проверялся бы
    на пустом пуле. Здесь же проверяется потолок, а он про письма, которым есть
    кому уйти.
    """
    from mysql_.mysql_ import mysql_get_db_async

    async def put(count: int, status: str = 'draft') -> None:
        async with mysql_get_db_async() as db:
            await db.execute("UPDATE dating_profile SET status = 'online' WHERE id = %s",
                             (profile,))
            for n in range(count):
                await db.execute(
                    'INSERT INTO dating_mailing'
                    ' (site_id, profile_id, mail_id, uid, name, body, status)'
                    ' VALUES (%s, %s, NULL, %s, %s, %s, %s)',
                    (SITE, profile, f'tst-{status}-{n}', 'Тестовый', 'Текст', status))
            await db.connection.commit()

    yield put

    # Каскад по анкете уберёт всё сам, но ждать её удаления незачем: следующий
    # тест считает письма этого же сайта.
    async with mysql_get_db_async() as db:
        await db.execute('DELETE FROM dating_mailing WHERE profile_id = %s', (profile,))
        await db.connection.commit()


class TestWhoIsTaken:
    """Кого круг берёт в работу."""

    async def test_site_with_schedule_is_taken(self, site, template, made,
                                               clean_mark):
        """⚠ Ищем **свой сайт среди прочих**, а не сверяем список целиком. На
        живой базе расписание рассылки бывает включено и у настоящих сайтов, и
        равенство ломалось бы от чужой настройки, ничего не проверяя по
        существу."""
        await dating_mailing_round_run(0)

        assert SITE in [call['site_id'] for call in made], \
            'сайт со включённым расписанием пропущен'

    async def test_schedule_off_is_skipped(self, auth_client, site, template,
                                           made, clean_mark):
        """⚠ Отбор по **выключателю**, а не по наличию смен. Смены правят
        ползунком, и промежуточные состояния законны: исполняй круг «по факту
        наличия», рассылка уходила бы на каждое движение ручки."""
        await auth_client.put(f'/api/dating/site/{SITE}/mailing-schedule-on',
                              json={'mailing_schedule_on': False})

        await dating_mailing_round_run(0)

        assert not [call for call in made if call['site_id'] == SITE]

    async def test_outside_the_shift_is_skipped(self, auth_client, site, template,
                                                made, clean_mark):
        """Смена кончилась — не набираем. Ставим её на час, уже прошедший.

        ⚠ **Время считаем в минутах и переводим в строку одним местом.** Первая
        версия собирала его арифметикой над частями («HH:{м // 15 * 15 + 15}») и
        на 45-й минуте часа выдавала «19:60». Ручка такое отклоняла, расписание
        оставалось круглосуточным, и тест падал — но только в те четверть часа,
        когда полная сюита до него добиралась. Плавающий тест хуже отсутствующего:
        он учит не верить сюите.
        """
        now = _minutes_now(0)
        # Час назад, длиной в четверть часа: текущая минута в него не попадёт.
        start = ((now - 60) % (24 * 60)) // 15 * 15
        await auth_client.put(
            f'/api/dating/site/{SITE}/mailing-schedule',
            json={'schedule': [{'from': _hhmm(start), 'to': _hhmm(start + 15)}]})

        await dating_mailing_round_run(0)

        assert not [call for call in made if call['site_id'] == SITE]

    async def test_gone_site_is_skipped(self, auth_client, site, template, made,
                                        clean_mark):
        """⚠ Ушедший у соседа сайт круг не берёт, даже с включённым расписанием.

        `is_gone` значит, что сайта у RhapsodyAi больше нет: анкеты на нём не
        войдут, адресатов не снять, и письма готовились бы в никуда. Настройки его
        при этом остаются — сайт может вернуться (`api.md`, §3.5).
        """
        from mysql_.mysql_ import mysql_get_db_async

        async with mysql_get_db_async() as db:
            await db.execute('UPDATE dating_site SET is_gone = 1 WHERE site_id = %s',
                             (SITE,))
            await db.connection.commit()

        await dating_mailing_round_run(0)

        assert not [call for call in made if call['site_id'] == SITE]

    async def test_site_without_templates_is_skipped(self, site, made, clean_mark):
        """⚠ Шаблонов нет — набирать нечем, и звать набор незачем. Сайт при этом
        остаётся в списке проверенных: он настроен, просто пуст."""
        result = await dating_mailing_round_run(0)

        assert not [call for call in made if call['site_id'] == SITE]
        assert result['checked'] >= 1


class TestHowOften:
    """Второй срок: как часто набираем внутри смены."""

    async def test_second_round_is_skipped(self, site, template, made, clean_mark):
        """⚠ **Смены задают часы, а не частоту.** Круг просыпается раз в минуту, и
        без второго срока девятичасовая смена означала бы пятьсот сорок заходов
        вместо восемнадцати получасовых — каждый ценой в вызовы LLM."""
        await dating_mailing_round_run(0)
        await dating_mailing_round_run(0)

        assert len([call for call in made if call['site_id'] == SITE]) == 1

    async def test_mark_is_set_before_work(self, site, template, made, clean_mark):
        """⚠ Отметка ставится **до** набора, а не после: он длится минуты, и
        следующий тик круга завёл бы второй, пока идёт первый."""
        await dating_mailing_round_run(0)

        assert not _due(SITE), 'отметка не поставлена — круг зайдёт повторно'

    async def test_mark_carries_the_setting_as_its_life(self, clean_mark):
        """⚠ Срок отметки **и есть** настройка: Redis сам погасит ключ, когда
        придёт пора.

        Считать разницу дат было бы тем же самым, только руками — и с риском
        разойтись, когда настройку поменяют.
        """
        _mark(SITE, 1800)

        assert not _due(SITE)
        assert clean_mark.ttl[DATING_MAILING_ROUND_KEY.format(site_id=SITE)] == 1800

    async def test_mark_life_is_never_zero(self, clean_mark):
        """Ноль или мусор в настройке — ключ всё равно живёт: вечная отметка хуже,
        но и мгновенная означала бы набор на каждом тике."""
        _mark(SITE, 0)

        assert clean_mark.ttl[DATING_MAILING_ROUND_KEY.format(site_id=SITE)] >= 1

    async def test_default_is_half_an_hour(self):
        """Умолчание — полчаса: за это время лента онлайна успевает смениться
        (снимается раз в две минуты), и появляются новые адресаты."""
        assert DATING_MAILING_EVERY_DEFAULT == 1800


class TestBatch:
    """Сколько берём за заход — та же настройка, что у кнопки."""

    async def test_limit_comes_from_the_setting(self, auth_client, site, template,
                                                made, clean_mark):
        before = (await auth_client.get(
            f'/api/dating/site/{SITE}')).json()['result']['settings']
        try:
            await auth_client.put(f'/api/dating/site/{SITE}',
                                  json={'settings': {**before, 'mailing_batch': '10'}})
            await dating_mailing_round_run(0)
        finally:
            await auth_client.put(f'/api/dating/site/{SITE}', json={'settings': before})

        assert [call['limit'] for call in made if call['site_id'] == SITE] == [10]

    async def test_all_enabled_templates_are_taken(self, auth_client, site,
                                                   template, made, clean_mark):
        """⚠ Круг берёт **все включённые** шаблоны: выбирать некому, человека у
        экрана нет. Галочка «участвует в рассылке» и есть выбор, сделанный
        заранее."""
        import uuid

        second = (await auth_client.post('/api/dating/mail', json={
            'site_id': SITE, 'name': f'tst-{uuid.uuid4().hex[:6]}',
            'body': 'Ещё письмо', 'enabled': True})).json()['result']['id']
        off = (await auth_client.post('/api/dating/mail', json={
            'site_id': SITE, 'name': f'tst-{uuid.uuid4().hex[:6]}',
            'body': 'Выключенное', 'enabled': False})).json()['result']['id']
        try:
            await dating_mailing_round_run(0)
        finally:
            await auth_client.delete(f'/api/dating/mail/{second}')
            await auth_client.delete(f'/api/dating/mail/{off}')

        # Два включённых, выключенный не в счёт.
        assert [call['templates'] for call in made if call['site_id'] == SITE] == [2]


class TestPoolCeiling:
    """⚠ Пул — **запас, а не поток**: круг добирает его до потолка и встаёт.

    Настройка `mailing_pool_max` (обычно 50) говорит, сколько неотправленных
    писем держать. Без потолка круг набирал бы порцию каждый заход, пока отправка
    не поспевает, — и человек читал бы письма, написанные вчера про тех, кто давно
    ушёл с сайта.
    """

    async def test_limit_is_what_is_missing_to_the_ceiling(
            self, auth_client, site, template, made, clean_mark, pool):
        """В пуле восемнадцать при потолке двадцать — берём два, а не порцию.

        ⚠ Числа не круглые не от лени: значения настроек берутся **из набора
        реестра** (`dating_site_seconds` чужое отвергает), и «потолок 5» проверял
        бы не круг, а нашу выдумку о том, что настройке можно задать что угодно.
        """
        await pool(18)
        before = (await auth_client.get(
            f'/api/dating/site/{SITE}')).json()['result']['settings']
        try:
            await auth_client.put(f'/api/dating/site/{SITE}', json={
                'settings': {**before, 'mailing_pool_max': '20',
                             'mailing_batch': '50'}})
            await dating_mailing_round_run(0)
        finally:
            await auth_client.put(f'/api/dating/site/{SITE}', json={'settings': before})

        assert [call['limit'] for call in made if call['site_id'] == SITE] == [2]

    async def test_full_pool_is_not_touched(self, auth_client, site, template,
                                            made, clean_mark, pool):
        """Пул полон — набора нет вовсе. ⚠ Не «набрали ноль», а не пошли: набор
        стоит вызовов LLM, и заводить его, чтобы он сам ничего не сделал, значит
        платить за пустоту."""
        await pool(20)
        before = (await auth_client.get(
            f'/api/dating/site/{SITE}')).json()['result']['settings']
        try:
            await auth_client.put(f'/api/dating/site/{SITE}', json={
                'settings': {**before, 'mailing_pool_max': '20'}})
            await dating_mailing_round_run(0)
        finally:
            await auth_client.put(f'/api/dating/site/{SITE}', json={'settings': before})

        assert [call for call in made if call['site_id'] == SITE] == []

    async def test_batch_still_caps_one_pass(self, auth_client, site, template,
                                             made, clean_mark, pool):
        """⚠ Порция остаётся пределом захода. До потолка сотня писем — но заход,
        готовящий сотню, длится часами: круг вернётся через свой срок и доберёт."""
        before = (await auth_client.get(
            f'/api/dating/site/{SITE}')).json()['result']['settings']
        try:
            await auth_client.put(f'/api/dating/site/{SITE}', json={
                'settings': {**before, 'mailing_pool_max': '100',
                             'mailing_batch': '10'}})
            await dating_mailing_round_run(0)
        finally:
            await auth_client.put(f'/api/dating/site/{SITE}', json={'settings': before})

        assert [call['limit'] for call in made if call['site_id'] == SITE] == [10]

    async def test_sent_letters_do_not_hold_a_place(self, auth_client, site, pool):
        """⚠ Считаем только черновики. Ушедшие своё дело сделали, неудавшиеся
        больше не пойдут — держи они места в пуле, набор встал бы навсегда: к
        сотому письму потолок был бы занят историей."""
        await pool(2)
        await pool(3, 'sent')
        await pool(1, 'failed')

        assert await _pool_size(SITE) == 2

    async def test_ceiling_comes_from_the_setting(self, auth_client, site):
        before = (await auth_client.get(
            f'/api/dating/site/{SITE}')).json()['result']['settings']
        try:
            await auth_client.put(f'/api/dating/site/{SITE}', json={
                'settings': {**before, 'mailing_pool_max': '100'}})

            assert await _pool_max(SITE) == 100
        finally:
            await auth_client.put(f'/api/dating/site/{SITE}', json={'settings': before})

    async def test_ceiling_has_a_hard_cap(self, monkeypatch):
        """Жёсткий потолок поверх настройки — вторая линия, и проверяется как
        вторая.

        ⚠ **Через форму до неё не добраться**, и это не упущение теста:
        `dating_site_seconds` принимает только значения из набора реестра, а там
        максимум 200. Потолок стережёт другое — строку, правленную мимо панели
        (руками в базе, миграцией, будущим набором пошире). Поэтому подменяется
        чтение настройки, а не пишется невозможное значение: тест на «положим 5000
        через ручку» проверял бы нашу выдумку о том, что ручка это примет.
        """
        async def huge(site_id, key):
            return 5000

        import src.uvicorn.routers.dating.dating_site as site_mod
        monkeypatch.setattr(site_mod, 'dating_site_seconds', huge)

        assert await _pool_max(SITE) == DATING_MAILING_POOL_MAX

    async def test_default_is_fifty(self):
        """Сайт без настройки — пятьдесят: столько человек прочитывает за раз."""
        assert DATING_MAILING_POOL_MAX_DEFAULT == 50
        assert await _pool_max(999_111) == 50


class TestRoundDoesNotSend:
    """⚠ Самое важное: круг **готовит письма, а не отправляет их**."""

    async def test_round_does_not_touch_send_flag(self, auth_client, site,
                                                  template, made, clean_mark):
        """Включённое расписание не включает отправку. Это разные решения: человек
        заводит смены, чтобы к утру был готов пул, который он прочтёт."""
        await dating_mailing_round_run(0)

        row = (await auth_client.get(
            f'/api/dating/site/{SITE}/mailing-schedule')).json()['result']

        assert row['mailing_send_on'] is False, 'круг включил отправку'


class TestRedisFailure:
    """Сбой Redis. ⚠ Решается **в сторону бездействия**, в отличие от импорта."""

    async def test_unreadable_mark_stops_the_round(self, monkeypatch):
        """Отметку не прочитать — не идём.

        У импорта переписки наоборот: там пропуск стоит задержки сообщения, а
        лишний прогон — сорока секунд браузера. Здесь лишний заход это вызовы LLM
        и письма живым людям. Когда цена ошибки несимметрична, сомнение решается в
        сторону бездействия.
        """
        def broken():
            raise RuntimeError('redis лежит')

        monkeypatch.setattr(round_, 'redis_conn_get', broken)

        assert _due(SITE) is False


class TestSlotAfterAWastedPass:
    """⚠ Получасовой слот — награда за работу, а не за попытку.

    Живой случай, и он повторился дважды. Рассылка стояла у четырёх анкет, а
    `girl_name` был заполнен у одной: заход попробовал 48 пар, сделал 5 и
    пропустил 43. Пул остался на семи письмах, слот встал на полчаса — «7
    сгенерировало и зависло». Причину нашли и починили за минуту, но круг всё
    равно молчал до конца чужого срока.

    ⚠ Проверяется здесь **решение о слоте**, а не набор: `made` подменён, и
    итог захода задаётся полем `made.result`.
    """

    async def test_free_skips_release_the_slot(self, site, template, made,
                                               clean_mark):
        """Пропусков больше, чем писем, и все бесплатные — слот отпускаем.

        Следующий тик зайдёт снова, и починенная переменная сработает сразу.
        """
        made.result.update({'made': 5, 'skipped': 43,
                            'reasons': ['не хватает переменных: girl_name']})

        await dating_mailing_round_run(0)

        assert _due(SITE), 'слот занят после захода, потратившего себя на пропуски'

    async def test_healthy_pass_keeps_the_slot(self, site, template, made,
                                               clean_mark):
        """⚠ Один пропуск из полусотни слот **не** отпускает.

        Такой заход своё отработал: пул набран, письма ждут человека. Отпусти мы
        слот из-за единственного безымянного адресата — круг пошёл бы по кругу
        каждую минуту, и `mailing_every` перестал бы значить хоть что-то.
        """
        made.result.update({'made': 47, 'skipped': 1,
                            'reasons': ['не хватает переменных: girl_name']})

        await dating_mailing_round_run(0)

        assert not _due(SITE), 'слот отпущен после здорового захода'

    async def test_paid_skips_keep_the_slot(self, site, template, made, clean_mark):
        """⚠ «Агент не ответил» слот держит, хотя пропусков и больше.

        Это уже стоило вызова LLM на каждую пару, и повтор через минуту сжигал бы
        их кругами. Причина такая сама за минуту не рассасывается — в отличие от
        незаполненной переменной, которую человек правит руками и сразу.
        """
        made.result.update({'made': 2, 'skipped': 30,
                            'reasons': ['агент не ответил: question_to_companion']})

        await dating_mailing_round_run(0)

        assert not _due(SITE), 'слот отпущен после захода, стоившего вызовов LLM'

    async def test_mixed_reasons_keep_the_slot(self, site, template, made,
                                              clean_mark):
        """Хоть одна платная причина — слот держим.

        ⚠ Сомнение решается в сторону ожидания: лишние полчаса стоят задержки,
        лишний круг — вызовов LLM на каждую пару. Цена ошибок несимметрична.
        """
        made.result.update({'made': 1, 'skipped': 20, 'reasons': [
            'не хватает переменных: girl_name',
            'агент не ответил: question_to_companion']})

        await dating_mailing_round_run(0)

        assert not _due(SITE), 'слот отпущен, хотя часть пропусков была платной'

    async def test_silence_about_reasons_keeps_the_slot(self, site, template, made,
                                                        clean_mark):
        """⚠ Пропуски есть, причин нет — слот держим.

        Причины хранятся первыми пятью, и пустой список значит «не знаем, за что
        платили», а не «ничего не потратили». Толкуй мы молчание как
        бесплатность — круг ходил бы каждую минуту по любой незнакомой беде.
        """
        made.result.update({'made': 0, 'skipped': 12, 'reasons': []})

        await dating_mailing_round_run(0)

        assert not _due(SITE), 'слот отпущен по незнакомой причине'

    async def test_slot_holds_when_nothing_was_skipped(self, site, template, made,
                                                       clean_mark):
        """Пропусков нет вовсе — обычный заход, обычный слот."""
        made.result.update({'made': 20, 'skipped': 0, 'reasons': []})

        await dating_mailing_round_run(0)

        assert not _due(SITE), 'слот отпущен после захода без единого пропуска'

    async def test_released_slot_lets_the_next_tick_in(self, site, template, made,
                                                       clean_mark):
        """Отпущенный слот означает **заход на следующем тике**, а не запись в лог.

        ⚠ Ради этого правило и заведено: человек чинит переменную и видит письма
        через минуту, а не через полчаса.
        """
        made.result.update({'made': 5, 'skipped': 43,
                            'reasons': ['не хватает переменных: girl_name']})
        await dating_mailing_round_run(0)

        made.result.update({'made': 43, 'skipped': 0, 'reasons': []})
        await dating_mailing_round_run(0)

        assert len([call for call in made if call['site_id'] == SITE]) == 2, \
            'второй заход не состоялся, хотя слот был отпущен'
