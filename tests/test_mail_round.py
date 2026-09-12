"""Круг шаблонов: заготовки пишутся сами и старые уходят сами.

⚠ **Наружу сюита не ходит** (`testing.md`): написание заготовки — это вызов LLM у
соседа. Поэтому подменяется `dating_mail_write` — единственная дорогая часть; всё
остальное работает по-настоящему, с живой базой.

⚠ **Сайт выдуманный.** Своей таблицы сайтов здесь нет, `site_id` — просто ключ, и
живые шаблоны задеть невозможно в принципе. Это здесь важнее обычного: круг **удаляет
шаблоны**, и промах в одну цифру стоил бы чужих текстов.

## Что здесь ловится

**Порядок.** Уборка идёт до написания. Обратный порядок неверен по счёту: свежая
заготовка, написанная в этом же заходе, попадала бы в «самые свежие», которые уборка
бережёт, — и вытесняла бы оттуда вчерашнюю, оставляя список без запаса.

**Предохранитель.** Уборка никогда не оставляет сайт без текстов: самые свежие числом
в дневную норму не трогаются даже старыми. Без него первое же включение расписания
опустошало бы список — у живого сайта пять шаблонов недельной давности при сроке в
двое суток.

**Дневная норма.** Считается по базе, а не в памяти: перезапуск смены держателя не
должен давать вторую норму за день.
"""

import uuid

import pytest

from src.dating import dating_mail_round as round_
from src.dating.dating_mail_round import (DATING_MAIL_ROUND_KEY,
                                          dating_mail_round_run, _made_today,
                                          _sweep, _today, _write)

pytestmark = pytest.mark.mail

# Выдуманный сайт: у соседа его нет, живые шаблоны задеть невозможно.
SITE = 999_777

# Смена во все сутки — чтобы проверка «идёт ли смена» не зависела от часа прогона.
ALWAYS = [{'from': '00:00', 'to': '23:45'}]


@pytest.fixture
async def clean():
    """Пустой список шаблонов выдуманного сайта до и после теста.

    ⚠ Убираем **и до**: прошлый прогон мог упасть на середине, и его заготовки
    испортили бы счёт этому.
    """
    from mysql_.mysql_ import mysql_get_db_async

    async def wipe():
        async with mysql_get_db_async() as db:
            await db.execute('DELETE FROM dating_mail WHERE site_id = %s', (SITE,))
            await db.connection.commit()

    await wipe()

    yield

    await wipe()


@pytest.fixture
def agent(monkeypatch):
    """Подменяет агента: считает вызовы вместо походов к LLM.

    ⚠ Возвращает **словарь**, как настоящий: `body` и `title`. Отдавай заглушка
    строку — тесты проходили бы на форме ответа, которой в жизни нет; ровно это и
    случилось, когда агент научился придумывать название.
    """
    calls = []

    async def fake(site_id, variables, hint=''):
        calls.append(int(site_id))

        return {'title': f'Draft title {len(calls)}',
                'body': f'Hi [[companion_name]]! Draft #{len(calls)}'}

    monkeypatch.setattr(round_, 'dating_mail_write', fake)

    return calls


async def _put(name: str, days_ago: int = 0, enabled: int = 1) -> int:
    """Кладёт шаблон с заданным возрастом. Возвращает номер."""
    from mysql_.mysql_ import mysql_get_db_async

    async with mysql_get_db_async() as db:
        await db.execute(
            'INSERT INTO dating_mail (site_id, name, body, enabled, created_at)'
            ' VALUES (%s, %s, %s, %s, NOW() - INTERVAL %s DAY)',
            (SITE, f'{name}-{uuid.uuid4().hex[:6]}', 'Текст', int(enabled),
             int(days_ago)))
        await db.connection.commit()
        await db.execute('SELECT LAST_INSERT_ID() AS id')

        return int((await db.fetchone())['id'])


async def _names() -> list[str]:
    """Что осталось в списке, свежие сверху."""
    from mysql_.mysql_ import mysql_get_db_async

    async with mysql_get_db_async() as db:
        await db.execute('SELECT name FROM dating_mail WHERE site_id = %s'
                         ' ORDER BY created_at DESC, id DESC', (SITE,))

        return [str(row['name']) for row in await db.fetchall() or []]


async def _revive():
    """Снимает у выдуманного сайта метку «ушёл»: фоновая сверка ставит её честно —
    такого сайта у соседа нет, — а круг ушедшие пропускает."""
    from mysql_.mysql_ import mysql_get_db_async

    async with mysql_get_db_async() as db:
        await db.execute('UPDATE dating_site SET is_gone = 0 WHERE site_id = %s',
                         (SITE,))
        await db.connection.commit()


async def _settings(auth_client, **values):
    """Ставит настройки выдуманного сайта, возвращает прежние — для отката."""
    was = (await auth_client.get(f'/api/dating/site/{SITE}')).json()['result']['settings']
    await auth_client.put(f'/api/dating/site/{SITE}',
                          json={'settings': {**was, **values}})

    return was


class TestSweep:
    """Уборка старых заготовок."""

    async def test_old_are_gone(self, auth_client, clean):
        """Старше срока — уходят."""
        await _put('старый', days_ago=5)
        await _put('старый2', days_ago=4)
        # Свежих кладём столько, чтобы предохранитель занялся ими, а не старыми.
        for n in range(3):
            await _put(f'свежий{n}', days_ago=0)

        was = await _settings(auth_client, template_keep='2', template_per_day='3')
        try:
            gone = await _sweep(SITE)
        finally:
            await auth_client.put(f'/api/dating/site/{SITE}', json={'settings': was})

        assert gone == 2, 'старые не убрались'
        assert not [one for one in await _names() if one.startswith('старый')]

    async def test_fresh_stay(self, auth_client, clean):
        """Моложе срока — остаются, даже если их много."""
        for n in range(5):
            await _put(f'свежий{n}', days_ago=0)

        was = await _settings(auth_client, template_keep='2', template_per_day='3')
        try:
            gone = await _sweep(SITE)
        finally:
            await auth_client.put(f'/api/dating/site/{SITE}', json={'settings': was})

        assert gone == 0
        assert len(await _names()) == 5

    async def test_never_empties_the_list(self, auth_client, clean):
        """⚠ **Главная проверка.** Все шаблоны старше срока — список всё равно не
        пустеет: самые свежие, числом в дневную норму, уборка не трогает.

        Без этого первое же включение расписания опустошало бы список: у живого
        сайта пять шаблонов недельной давности при сроке в двое суток. Пока агент
        отвечает — полбеды; но занят сосед, и рассылке нечем писать вовсе.
        """
        for n in range(5):
            await _put(f'древний{n}', days_ago=10)

        was = await _settings(auth_client, template_keep='2', template_per_day='3')
        try:
            gone = await _sweep(SITE)
        finally:
            await auth_client.put(f'/api/dating/site/{SITE}', json={'settings': was})

        left = await _names()

        assert gone == 2, 'убрано не то количество'
        assert len(left) == 3, 'список опустел или уборка не сработала'

    async def test_reserve_is_the_freshest(self, auth_client, clean):
        """⚠ Бережём **самые свежие**, а не первые попавшиеся: список сортируется по
        дате, и «запас» должен состоять из тех, что новее прочих.

        ⚠ Числа берутся **из набора настройки** (`3`, а не `2`): `dating_site_seconds`
        значение вне набора отвергает и подставляет умолчание — тест на «двух в
        сутки» проверял бы нашу выдумку о том, что настройке можно задать что угодно.
        """
        await _put('давно', days_ago=30)
        await _put('позавчера', days_ago=9)
        await _put('вчера', days_ago=8)
        await _put('сегодня-старое', days_ago=7)

        was = await _settings(auth_client, template_keep='2', template_per_day='3')
        try:
            gone = await _sweep(SITE)
        finally:
            await auth_client.put(f'/api/dating/site/{SITE}', json={'settings': was})

        left = await _names()

        assert gone == 1, 'убрали не одного самого старого'
        assert len(left) == 3
        assert not any(one.startswith('давно') for one in left), 'сберегли не тех'

    async def test_zero_means_never(self, auth_client, clean):
        """«Не удалять» — не трогаем ничего, как бы старо ни было."""
        for n in range(4):
            await _put(f'древний{n}', days_ago=100)

        was = await _settings(auth_client, template_keep='0')
        try:
            gone = await _sweep(SITE)
        finally:
            await auth_client.put(f'/api/dating/site/{SITE}', json={'settings': was})

        assert gone == 0
        assert len(await _names()) == 4

    async def test_letters_survive(self, auth_client, clean):
        """⚠ Готовые письма уборка не уносит: у `dating_mailing.mail_id` внешний
        ключ с `ON DELETE SET NULL`, и письмо помнит свой текст само."""
        from mysql_.mysql_ import mysql_get_db_async

        mail_id = await _put('старый', days_ago=9)
        for n in range(3):
            await _put(f'свежий{n}')

        # ⚠ Стенд, а не рабочий сайт: анкета сюиты не должна попадать в круги
        # воркера (`test_mail_in.py`, `STAND_SITE`).
        profile = (await auth_client.post('/api/dating/profile', json={
            'site_id': 999_343,
            'login': f'tmpl-{uuid.uuid4().hex[:8]}@example.com',
            'password': 'x'})).json()['result']

        async with mysql_get_db_async() as db:
            await db.execute(
                'INSERT INTO dating_mailing'
                ' (site_id, profile_id, mail_id, uid, name, body, status)'
                " VALUES (%s, %s, %s, 'tst-keep', 'Кому', 'Текст', 'draft')",
                (SITE, profile, mail_id))
            await db.connection.commit()

        was = await _settings(auth_client, template_keep='2', template_per_day='3')
        try:
            await _sweep(SITE)

            rows = (await auth_client.get(
                f'/api/dating/mailing?site_id={SITE}')).json()['result']
            mine = next((one for one in rows if one['uid'] == 'tst-keep'), None)

            assert mine, 'уборка шаблона унесла готовое письмо'
            assert mine['body'] == 'Текст', 'текст письма потерялся'
            assert mine['mail_name'] == 'шаблон удалён'
        finally:
            await auth_client.put(f'/api/dating/site/{SITE}', json={'settings': was})
            await auth_client.delete(f'/api/dating/profile/{profile}')


class TestWrite:
    """Написание дневной нормы."""

    async def test_daily_norm_at_once(self, auth_client, clean, agent):
        """Заход пишет **всю** норму, а не по одной."""
        was = await _settings(auth_client, template_per_day='3')
        try:
            made = await _write(SITE, 0)
        finally:
            await auth_client.put(f'/api/dating/site/{SITE}', json={'settings': was})

        assert made == 3
        assert len(agent) == 3, 'агента позвали не столько раз'

    async def test_second_pass_does_nothing(self, auth_client, clean, agent):
        """⚠ Норма — про сутки, и повторный заход в тот же день молчит."""
        was = await _settings(auth_client, template_per_day='3')
        try:
            await _write(SITE, 0)
            again = await _write(SITE, 0)
        finally:
            await auth_client.put(f'/api/dating/site/{SITE}', json={'settings': was})

        assert again == 0, 'за день написали две нормы'
        assert len(agent) == 3

    async def test_partial_day_is_topped_up(self, auth_client, clean, agent):
        """Из трёх вышла одна — следующий заход добирает две, а не пишет три."""
        await _put('уже-есть', days_ago=0)

        was = await _settings(auth_client, template_per_day='3')
        try:
            made = await _write(SITE, 0)
        finally:
            await auth_client.put(f'/api/dating/site/{SITE}', json={'settings': was})

        assert made == 2, 'дневная норма посчитана без уже заведённых'

    async def test_new_template_is_enabled(self, auth_client, clean, agent):
        """⚠ Заготовка ложится **включённой** — иначе цепочка не замыкается:
        рассылка берёт только включённые шаблоны.

        Безопасно это потому, что круга отправки нет: текст доходит до пула, а из
        пула письма уходят по одному, кнопкой.
        """
        from mysql_.mysql_ import mysql_get_db_async

        was = await _settings(auth_client, template_per_day='3')
        try:
            await _write(SITE, 0)
        finally:
            await auth_client.put(f'/api/dating/site/{SITE}', json={'settings': was})

        async with mysql_get_db_async() as db:
            await db.execute('SELECT enabled FROM dating_mail WHERE site_id = %s',
                             (SITE,))
            rows = await db.fetchall()

        assert all(int(row['enabled']) for row in rows), 'заготовка пришла выключенной'

    async def test_agent_refusal_stops_the_pass(self, auth_client, clean, monkeypatch):
        """Отказ агента обрывает заход, а не одну заготовку: следующие упрутся в то
        же самое, а недостающие доберутся через час."""
        from src.dating.dating_mail_agent import DatingMailAgentError

        calls = []

        async def broken(site_id, variables, hint=''):
            calls.append(1)
            raise DatingMailAgentError('браузер соседа занят')

        monkeypatch.setattr(round_, 'dating_mail_write', broken)

        was = await _settings(auth_client, template_per_day='3')
        try:
            made = await _write(SITE, 0)
        finally:
            await auth_client.put(f'/api/dating/site/{SITE}', json={'settings': was})

        assert made == 0
        assert len(calls) == 1, 'после отказа продолжили дёргать агента'

    async def test_empty_answer_is_not_saved(self, auth_client, clean, monkeypatch):
        """Агент ответил пусто — пустой шаблон в список не кладём."""
        async def empty(site_id, variables, hint=''):
            return {'title': '', 'body': '   '}

        monkeypatch.setattr(round_, 'dating_mail_write', empty)

        made = await _write(SITE, 0)

        assert made == 0
        assert await _names() == []

    async def test_norm_is_counted_in_the_database(self, auth_client, clean):
        """⚠ Норма считается **по базе**, а не в памяти: перезапуск смены держателя
        не должен давать вторую норму за день."""
        await _put('свой', days_ago=0)

        assert await _made_today(SITE, _today(0)) == 1
        assert await _made_today(SITE, '1999-01-01') == 0


class TestOrder:
    """⚠ Уборка — **после** написания, и порядок здесь решает исход неудачи."""

    def test_write_goes_before_sweep(self):
        import inspect

        code = inspect.getsource(dating_mail_round_run)

        assert code.index('_write(site_id') < code.index('_sweep(site_id)'), \
            'уборка идёт раньше написания: не ответит агент — и сайт останется с ' \
            'обрезанным списком, ничего не получив взамен'

    async def test_failed_write_keeps_the_old_ones(self, auth_client, clean,
                                                   monkeypatch):
        """⚠ **Главная проверка порядка.** Агент молчит — старые тексты остаются.

        Ради этого написание и идёт первым: удалять старое имеет смысл, только имея
        новое. Убери мы раньше — один заход с занятым соседом, и рассылке нечем
        писать до завтра.
        """
        from src.dating.dating_mail_agent import DatingMailAgentError

        for n in range(4):
            await _put(f'вчерашний{n}', days_ago=9)

        async def broken(site_id, variables, hint=''):
            raise DatingMailAgentError('браузер соседа занят')

        monkeypatch.setattr(round_, 'dating_mail_write', broken)

        async def only_ours():
            return [{'site_id': SITE, 'schedule': ALWAYS}]

        monkeypatch.setattr(round_, '_sites', only_ours)

        class _Redis:
            def __init__(self):
                self.store = {}

            def get(self, key):
                return self.store.get(key)

            def setex(self, key, seconds, value):
                self.store[key] = value

        monkeypatch.setattr(round_, 'redis_conn_get', lambda: _Redis())

        was = await _settings(auth_client, template_keep='2', template_per_day='3')
        try:
            result = await dating_mail_round_run(0)
        finally:
            await auth_client.put(f'/api/dating/site/{SITE}', json={'settings': was})

        left = await _names()

        assert result['made'] == 0, 'агент молчал, а заготовки появились'
        assert len(left) == 3, ('после неудачного захода список опустел ниже '
                                'дневной нормы')

    async def test_new_ones_replace_the_old(self, auth_client, clean, agent,
                                            monkeypatch):
        """А удачный заход **заменяет**: новые написаны, старые ушли."""
        for n in range(4):
            await _put(f'вчерашний{n}', days_ago=9)

        async def only_ours():
            return [{'site_id': SITE, 'schedule': ALWAYS}]

        monkeypatch.setattr(round_, '_sites', only_ours)

        class _Redis:
            def __init__(self):
                self.store = {}

            def get(self, key):
                return self.store.get(key)

            def setex(self, key, seconds, value):
                self.store[key] = value

        monkeypatch.setattr(round_, 'redis_conn_get', lambda: _Redis())

        was = await _settings(auth_client, template_keep='2', template_per_day='3')
        try:
            result = await dating_mail_round_run(0)
        finally:
            await auth_client.put(f'/api/dating/site/{SITE}', json={'settings': was})

        left = await _names()

        assert result['made'] == 3, 'дневная норма не написалась'
        assert result['gone'] == 4, 'вчерашние не убрались'
        assert len(left) == 3
        assert all(one.startswith('Draft title') for one in left), \
            'в списке остались вчерашние вместо новых'


class TestSitesFilter:
    """Кого круг берёт в работу — проверяется **чтением**, без запуска круга."""

    async def test_only_sites_with_schedule_on(self, auth_client):
        """Отбор идёт по `template_schedule_on`, а не по наличию смен: смены правят
        ползунком, и промежуточные состояния законны."""
        from src.dating.dating_mail_round import _sites

        before = (await auth_client.get(
            f'/api/dating/site/{SITE}/template-schedule')).json()['result']
        try:
            await auth_client.put(f'/api/dating/site/{SITE}/template-schedule-on',
                                  json={'template_schedule_on': False})

            assert SITE not in [one['site_id'] for one in await _sites()]

            await auth_client.put(f'/api/dating/site/{SITE}/template-schedule-on',
                                  json={'template_schedule_on': True})
            # ⚠ Метку «ушёл» снимаем: сайта с выдуманным номером у соседа нет,
            # фоновая сверка честно ставит `is_gone`, а круг такие пропускает.
            await _revive()

            assert SITE in [one['site_id'] for one in await _sites()]
        finally:
            await auth_client.put(
                f'/api/dating/site/{SITE}/template-schedule-on',
                json={'template_schedule_on': before['template_schedule_on']})


class TestWhoIsTaken:
    """Кого круг берёт в работу — с запуском самого круга.

    ⚠⚠ **Список сайтов подменяется, и это не удобство, а защита.**

    Круг обходит **все** сайты с включённым расписанием — включая живые. А делает он
    среди прочего `_sweep`, то есть **удаляет шаблоны**. На живом прогоне так и
    вышло: у сайта, где человек включил расписание, круг снёс пять написанных
    руками текстов и положил на их место три поддельных, которые вернул подменённый
    агент. Восстанавливать пришлось из binlog.

    Подмена агента от этого не спасает: она отвечает за то, что **пишется**, а
    уборка не спрашивает агента вовсе. Значит ограничивать надо не то, что круг
    делает, а то, **над чем** он это делает.
    """

    @pytest.fixture
    async def site(self, auth_client, monkeypatch):
        """Круг видит **только выдуманный сайт** — и ничего больше."""
        await auth_client.put(f'/api/dating/site/{SITE}/template-schedule',
                              json={'schedule': ALWAYS})

        async def only_ours():
            return [{'site_id': SITE, 'schedule': ALWAYS}]

        monkeypatch.setattr(round_, '_sites', only_ours)

        yield SITE

        await auth_client.put(f'/api/dating/site/{SITE}/template-schedule',
                              json={'schedule': []})

    @pytest.fixture
    def clean_mark(self, monkeypatch):
        """Пустая память отметок на каждый тест."""
        class _Redis:
            def __init__(self):
                self.store = {}

            def get(self, key):
                return self.store.get(key)

            def setex(self, key, seconds, value):
                self.store[key] = value

        fake = _Redis()
        monkeypatch.setattr(round_, 'redis_conn_get', lambda: fake)

        return fake

    async def test_site_with_schedule_is_taken(self, site, clean, agent, clean_mark):
        result = await dating_mail_round_run(0)

        assert result['made'] >= 1, 'сайт со сменой пропущен'

    async def test_empty_schedule_is_skipped(self, auth_client, clean, agent,
                                             clean_mark, monkeypatch):
        """Пустое расписание — это «нет», а не «всегда»: правило общее с расписанием
        анкеты и рассылки."""
        async def no_shifts():
            return [{'site_id': SITE, 'schedule': []}]

        monkeypatch.setattr(round_, '_sites', no_shifts)

        assert (await dating_mail_round_run(0))['made'] == 0
        assert not agent, 'агента позвали при пустом расписании'

    async def test_mark_stops_the_second_pass(self, site, clean, agent, clean_mark):
        """Второй заход в тот же срок ничего не делает: отметка на месте."""
        await dating_mail_round_run(0)
        before = len(agent)
        await dating_mail_round_run(0)

        assert len(agent) == before, 'заход пошёл второй раз, не дождавшись срока'

    async def test_mark_is_set_before_work(self, site, clean, agent, clean_mark):
        """⚠ Отметка ставится **до** работы: заготовки пишутся минутами, и
        следующий тик завёл бы вторую пачку поверх первой."""
        await dating_mail_round_run(0)

        assert clean_mark.get(DATING_MAIL_ROUND_KEY.format(site_id=SITE))

    async def test_broken_redis_stops_the_round(self, monkeypatch, site, clean, agent):
        """⚠ Сбой Redis решается в сторону бездействия: лишний заход — это вызовы
        LLM и лишние тексты в списке."""
        def broken():
            raise RuntimeError('redis лежит')

        monkeypatch.setattr(round_, 'redis_conn_get', broken)

        assert (await dating_mail_round_run(0))['made'] == 0
        assert not agent


class TestTitleFromAgent:
    """⚠ Название придумывает **агент**, а не вызывающий.

    Имя должно говорить о самом тексте — «Short and direct», «Curious about his
    hobbies». Знает текст только тот, кто его написал; вызывающий получил бы готовую
    строку и мог бы разве что взять из неё первые слова.

    Прежде имя собиралось по дате и номеру (`Draft 2026-09-09 #1`): три письма одного
    дня отличались только цифрой, и в списке их было не различить.
    """

    async def test_agent_title_becomes_the_name(self, auth_client, clean, monkeypatch):
        async def named(site_id, variables, hint=''):
            return {'title': 'Warm and curious', 'body': 'Hi [[companion_name]]!'}

        monkeypatch.setattr(round_, 'dating_mail_write', named)

        was = await _settings(auth_client, template_per_day='3')
        try:
            await _write(SITE, 0)
        finally:
            await auth_client.put(f'/api/dating/site/{SITE}', json={'settings': was})

        assert all(one == 'Warm and curious' for one in await _names())

    async def test_no_title_falls_back_to_the_date(self, auth_client, clean,
                                                   monkeypatch):
        """⚠ Модель метку теряет — и это не отказ: шаблон без имени в списке не
        найти вовсе, а письмо без шаблона хуже вдвойне. Берём имя по дате."""
        async def unnamed(site_id, variables, hint=''):
            return {'title': '', 'body': 'Hi [[companion_name]]!'}

        monkeypatch.setattr(round_, 'dating_mail_write', unnamed)

        was = await _settings(auth_client, template_per_day='3')
        try:
            await _write(SITE, 0)
        finally:
            await auth_client.put(f'/api/dating/site/{SITE}', json={'settings': was})

        left = await _names()

        assert left, 'без названия заготовка не завелась вовсе'
        assert all(one.startswith('Draft ') for one in left)

    async def test_name_is_cut_to_the_column(self, auth_client, clean, monkeypatch):
        """Длинное имя обрезается: столбец держит 190 знаков, а колонка списка —
        куда меньше."""
        from src.dating.dating_mail import DATING_MAIL_NAME_MAX

        async def wordy(site_id, variables, hint=''):
            return {'title': 'Очень ' * 100, 'body': 'Hi [[companion_name]]!'}

        monkeypatch.setattr(round_, 'dating_mail_write', wordy)

        was = await _settings(auth_client, template_per_day='3')
        try:
            await _write(SITE, 0)
        finally:
            await auth_client.put(f'/api/dating/site/{SITE}', json={'settings': was})

        assert all(len(one) <= DATING_MAIL_NAME_MAX for one in await _names())


class TestAnswerShape:
    """Разбор ответа агента: название и текст приходят одной строкой.

    ⚠ Разделитель — **строка `TITLE:`**, а не JSON: в письме есть переносы строк, и
    упаковка его в JSON на последнем шаге workflow потребовала бы экранирования,
    которого шаблонизатор `%letter%` не делает — первый же перенос порвал бы разбор.
    """

    def test_title_and_body_are_split(self):
        from src.dating.dating_mail_agent import _letter_named

        got = _letter_named('TITLE: Short and direct\n\nHi [[companion_name]],\n\nHow are you?')

        assert got['title'] == 'Short and direct'
        assert got['body'] == 'Hi [[companion_name]],\n\nHow are you?'

    def test_missing_mark_is_not_a_failure(self):
        """Метки нет — текст целиком остаётся телом, а имя пустое."""
        from src.dating.dating_mail_agent import _letter_named

        got = _letter_named('Hi [[companion_name]],\n\nNo title here')

        assert got['title'] == ''
        assert got['body'].startswith('Hi [[companion_name]]')

    def test_quotes_are_stripped(self):
        """LLM берёт название в кавычки при первой возможности."""
        from src.dating.dating_mail_agent import _letter_named

        assert _letter_named('TITLE: "Warm and playful"\n\nТекст')['title'] \
            == 'Warm and playful'

    def test_thinking_is_cut_before_the_mark(self):
        """⚠ Размышления модели снимаются **до** разбора метки: иначе `TITLE:`
        оказывается не в начале, и название теряется вместе с ним."""
        from src.dating.dating_mail_agent import _letter_named

        got = _letter_named('<think>подумаю…</think>TITLE: Short\n\nТекст письма')

        assert got['title'] == 'Short'
        assert got['body'] == 'Текст письма'
