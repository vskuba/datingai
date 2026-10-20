"""Круг шаблонов: заготовки пишутся сами каждой анкете, и старые уходят сами.

⚠ **Наружу сюита не ходит** (`testing.md`): написание заготовки — это вызов LLM у
соседа. Поэтому подменяется `mail_write` — единственная дорогая часть; всё
остальное работает по-настоящему, с живой базой.

⚠ **Сайт и анкеты выдуманные.** Своей таблицы сайтов здесь нет, `site_id` — просто
ключ, и живые шаблоны задеть невозможно в принципе. Это здесь важнее обычного:
круг **удаляет шаблоны**, и промах в одну цифру стоил бы чужих текстов.

## Что здесь ловится

**Анкета.** С 2026-10-16 круг пишет **каждой включённой анкете своей рукой**, и
норма из настройки действует на анкету, а не на сайт: два списка в сутки не должны
съесть норму друг друга.

**Имя.** Заготовку не зовут — её считают: `template_1`, `template_2`, `template_3`.
Владелицу строки показывает аватарка в списке, а не буква в имени. Ответ агента —
только текст, и в имя он не просится вовсе.

**Порядок.** Уборка идёт после написания. Обратный порядок неверен по счёту: свежая
заготовка, написанная в этом же заходе, попадала бы в «самые свежие», которые уборка
бережёт, — и вытесняла бы оттуда вчерашнюю, оставляя список без запаса.

**Предохранитель.** Уборка никогда не оставляет анкету без текстов: самые свежие
**каждой** числом в дневную норму не трогаются даже старыми. Без него первое же
включение расписания опустошало бы списки — у живого сайта пять шаблонов недельной
давности при сроке в двое суток.

**Дневная норма.** Считается по базе, а не в памяти: **по строкам самой анкеты** за
сегодня, и перезапуск смены держателя не должен давать вторую норму за день.
"""

import json
import uuid

import pytest

from src.dating import mail_round as round_
from round_ import round_slot as slot
from round_.round_slot import round_slot_key
from src.dating import round as engine
from src.dating.mail_round import (MAIL_ROUND_KEY,
                                          mail_round_run, _made_today,
                                          _sweep, _write)

pytestmark = pytest.mark.mail

# Выдуманный сайт: у соседа его нет, живые шаблоны задеть невозможно.
SITE = 999_777

# Смена во все сутки — чтобы проверка «идёт ли смена» не зависела от часа прогона.
# ⚠ **`24:00`, а не `23:45`.** Смена задумана «во все сутки», чтобы проверка
# «идёт ли смена» не зависела от часа прогона, — но `23:45` оставляла дыру в
# четверть часа, и сюита честно краснела каждый вечер между 23:45 и полуночью.
# Поймано живьём: двадцать три теста в трёх файлах разом, и все по этой причине.
#
# `24:00` — законный конец суток и здесь, и на проде: правая граница смены
# строгая (`from <= minutes < to`), так что 1440 включает и последнюю минуту.
ALWAYS = [{'from': '00:00', 'to': '24:00'}]


@pytest.fixture
async def clean():
    """Пустые списки шаблонов и анкет выдуманного сайта до и после теста.

    ⚠ Убираем **и до**: прошлый прогон мог упасть на середине, и его заготовки
    испортили бы счёт этому.

    ⚠ **Анкеты тоже.** С 2026-10-16 норма считается на анкету, и чужая строка
    `profile` с прошлого теста была бы чужой нормой в этом.

    ⚠⚠ **И реестр переменных — он сайту нужен.** С 20.10.2026 круг проверяет
    заготовку тем же `mail_check`, что и правка руками, а тот отвергает метку,
    которой нет в реестре. Сайт без единой переменной — условие, в котором
    годной заготовки с меткой не бывает вовсе: у живого сайта реестр есть, и
    стенд обязан его повторять, иначе проверяется небывалое.
    """
    from mysql_.mysql_ import mysql_get_db_async

    async def wipe():
        async with mysql_get_db_async() as db:
            await db.execute('DELETE FROM mail WHERE site_id = %s', (SITE,))
            await db.execute('DELETE FROM profile WHERE site_id = %s', (SITE,))
            await db.execute('DELETE FROM mail_var WHERE site_id = %s', (SITE,))
            await db.connection.commit()

    async def registry():
        async with mysql_get_db_async() as db:
            for name, source in (('girl_name', 'profile'),
                                 ('companion_name', 'companion'),
                                 ('question_to_companion', 'live')):
                await db.execute(
                    'INSERT INTO mail_var (site_id, name, source, title)'
                    ' VALUES (%s, %s, %s, %s)', (SITE, name, source, name))
            await db.connection.commit()

    await wipe()
    await registry()

    yield

    await wipe()


@pytest.fixture
def agent(monkeypatch):
    """Подменяет агента: считает вызовы вместо походов к LLM.

    ⚠ Возвращает **только текст**, как настоящий: с 2026-10-17 ответ агента
    имени не несёт вовсе. Имя строит круг из ключа владелицы — заглушка это
    честно переживает.
    """
    calls = []

    async def fake(site_id, variables, girl=None):
        calls.append(int(site_id))

        return f'Hi [[companion_name]]! Draft #{len(calls)}'

    monkeypatch.setattr(round_, 'mail_write', fake)

    return calls


async def _girl(name: str, enabled: int = 1) -> int:
    """Заводит выдуманную анкету и возвращает её номер.

    Имя ложится в JSON-колонку `profile` — оно нужно анкете для её карточки;
    заготовку круга оно больше не зовёт (имя только считает номер).
    """
    from mysql_.mysql_ import mysql_get_db_async

    async with mysql_get_db_async() as db:
        await db.execute(
            'INSERT INTO profile (site_id, login, password, note, role, enabled,'
            ' profile) VALUES (%s, %s, %s, %s, %s, %s, %s)',
            (SITE, f'{uuid.uuid4().hex[:8]}@example.com', 'x', 'сюита', 'lady',
             int(enabled), json.dumps({'name': name}, ensure_ascii=False)))
        await db.connection.commit()
        await db.execute('SELECT LAST_INSERT_ID() AS id')

        return int((await db.fetchone())['id'])


async def _put(name: str, days_ago: int = 0, enabled: int = 1,
               profile_id: int = None) -> int:
    """Кладёт шаблон с заданным возрастом (и владелицей). Возвращает номер."""
    from mysql_.mysql_ import mysql_get_db_async

    async with mysql_get_db_async() as db:
        await db.execute(
            'INSERT INTO mail (site_id, profile_id, name, body, enabled, created_at)'
            ' VALUES (%s, %s, %s, %s, %s, NOW() - INTERVAL %s DAY)',
            (SITE, int(profile_id) if profile_id else None,
             f'{name}-{uuid.uuid4().hex[:6]}', 'Текст', int(enabled), int(days_ago)))
        await db.connection.commit()
        await db.execute('SELECT LAST_INSERT_ID() AS id')

        return int((await db.fetchone())['id'])


async def _names(gid: int = None) -> list[str]:
    """Что осталось в списке (или у одной анкеты), свежие сверху."""
    from mysql_.mysql_ import mysql_get_db_async

    sql = ('SELECT name FROM mail'
           ' WHERE site_id = %s'
           + (' AND profile_id = %s' if gid else '')
           + ' ORDER BY created_at DESC, id DESC')

    async with mysql_get_db_async() as db:
        await db.execute(sql, (SITE, gid) if gid else (SITE,))

        return [str(row['name']) for row in await db.fetchall() or []]


async def _revive():
    """Снимает у выдуманного сайта метку «ушёл»: фоновая сверка ставит её честно —
    такого сайта у соседа нет, — а круг ушедшие пропускает."""
    from mysql_.mysql_ import mysql_get_db_async

    async with mysql_get_db_async() as db:
        await db.execute('UPDATE site SET is_gone = 0 WHERE site_id = %s', (SITE,))
        await db.connection.commit()


async def _settings(auth_client, **values):
    """Ставит настройки выдуманного сайта, возвращает прежние — для отката."""
    was = (await auth_client.get(f'/api/dating/site/{SITE}')).json()['result']['settings']
    await auth_client.put(f'/api/dating/site/{SITE}',
                          json={'settings': {**was, **values}})

    return was


class TestSweep:
    """Уборка старых заготовок — каждой анкете своей рукой."""

    async def test_old_are_gone(self, auth_client, clean):
        """Старше срока — уходят."""
        gid = await _girl('Елена')
        await _put('старый', days_ago=5, profile_id=gid)
        await _put('старый2', days_ago=4, profile_id=gid)
        # Свежих кладём столько, чтобы предохранитель занялся ими, а не старыми.
        for n in range(3):
            await _put(f'свежий{n}', days_ago=0, profile_id=gid)

        was = await _settings(auth_client, template_keep='2', template_per_day='3')
        try:
            gone = await _sweep(SITE)
        finally:
            await auth_client.put(f'/api/dating/site/{SITE}', json={'settings': was})

        assert gone == 2, 'старые не убрались'
        assert not [one for one in await _names() if one.startswith('старый')]

    async def test_fresh_stay(self, auth_client, clean):
        """Моложе срока — остаются, даже если их много."""
        gid = await _girl('Елена')
        for n in range(5):
            await _put(f'свежий{n}', profile_id=gid)

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
        gid = await _girl('Елена')
        for n in range(5):
            await _put(f'древний{n}', days_ago=10, profile_id=gid)

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

        ⚠ Числа берутся **из набора настройки** (`3`, а не `2`): `site_seconds`
        значение вне набора отвергает и подставляет умолчание — тест на «двух в
        сутки» проверял бы нашу выдумку о том, что настройке можно задать что угодно.
        """
        gid = await _girl('Елена')
        await _put('давно', days_ago=30, profile_id=gid)
        await _put('позавчера', days_ago=9, profile_id=gid)
        await _put('вчера', days_ago=8, profile_id=gid)
        await _put('сегодня-старое', days_ago=7, profile_id=gid)

        was = await _settings(auth_client, template_keep='2', template_per_day='3')
        try:
            gone = await _sweep(SITE)
        finally:
            await auth_client.put(f'/api/dating/site/{SITE}', json={'settings': was})

        left = await _names()

        assert gone == 1, 'убрали не одного самого старого'
        assert len(left) == 3
        assert not any(one.startswith('давно') for one in left), 'сберегли не тех'

    async def test_reserve_is_each_ones_own(self, auth_client, clean):
        """⚠ **Запас свой у каждой.** Один общий `LIMIT` на весь сайт выбрал бы
        норму строк одной анкеты и оставил бы без запаса другую, чьи тексты чуть
        старее: `PARTITION BY profile_id` считает норму каждой своей.

        Уобщённый предохранитель здесь краснел бы числом: два списка дали бы четыре
        удалённые строки вместо двух.
        """
        anna = await _girl('Анна')
        visa = await _girl('Вика')
        for n in range(3):
            await _put(f'доля{n}', days_ago=30, profile_id=anna)
            await _put(f'юля{n}', days_ago=30, profile_id=visa)

        was = await _settings(auth_client, template_keep='2', template_per_day='2')
        try:
            gone = await _sweep(SITE)
        finally:
            await auth_client.put(f'/api/dating/site/{SITE}', json={'settings': was})

        assert gone == 2, 'уборка съела чужую норму: запас должен быть у каждой свой'
        assert len(await _names(anna)) == 2
        assert len(await _names(visa)) == 2

    async def test_zero_means_never(self, auth_client, clean):
        """«Не удалять» — не трогаем ничего, как бы старо ни было."""
        gid = await _girl('Елена')
        for n in range(4):
            await _put(f'древний{n}', days_ago=100, profile_id=gid)

        was = await _settings(auth_client, template_keep='0')
        try:
            gone = await _sweep(SITE)
        finally:
            await auth_client.put(f'/api/dating/site/{SITE}', json={'settings': was})

        assert gone == 0
        assert len(await _names()) == 4

    async def test_letters_survive(self, auth_client, clean):
        """⚠ Готовые письма уборка не уносит: у `mailing.mail_id` внешний
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
                'INSERT INTO mailing'
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
    """Написание дневной нормы **каждой включённой анкете**."""

    async def test_daily_norm_at_once(self, auth_client, clean, agent):
        """Заход пишет **всю** норму анкеты, а не по одной."""
        await _girl('Елена')
        was = await _settings(auth_client, template_per_day='3')
        try:
            made = await _write(SITE, 0)
        finally:
            await auth_client.put(f'/api/dating/site/{SITE}', json={'settings': was})

        assert made == 3
        assert len(agent) == 3, 'агента позвали не столько раз'
        assert set(await _names()) == {'template_1', 'template_2', 'template_3'}, \
            'имя заготовки не считает место в её норме'

    async def test_every_girl_gets_her_own_norm(self, auth_client, clean, agent):
        """⚠ **Норма на анкету, а не на сайт.** Две анкеты при норме «по две»
        получают четыре заготовки, а не две на двоих.

        Прежний счёт на весь сайт здесь краснеет сразу: первая анкета съедала бы
        норму второй, и та оставалась бы без текстов вовсе.
        """
        first = await _girl('Дина')
        second = await _girl('Зоя')

        was = await _settings(auth_client, template_per_day='2')
        try:
            made = await _write(SITE, 0)
        finally:
            await auth_client.put(f'/api/dating/site/{SITE}', json={'settings': was})

        assert made == 4, 'норма оказалась общей на сайт'
        assert sorted(await _names(first)) == sorted(await _names(second)) \
            == ['template_1', 'template_2'], 'норма одной легла на счёт другой'

    async def test_namesakes_share_the_name_apart(self, auth_client, clean, agent):
        """Две однофамилицы — не беда: имя у обеих одно и то же (`template_1`), а
        строки различаются по `profile_id`. Номер — место в ЕЁ норме, а не в чужой."""
        first = await _girl('Ирина')
        second = await _girl('Ирина')

        was = await _settings(auth_client, template_per_day='1')
        try:
            made = await _write(SITE, 0)
        finally:
            await auth_client.put(f'/api/dating/site/{SITE}', json={'settings': was})

        assert made == 2
        assert sorted(await _names()) == ['template_1', 'template_1']
        assert len(await _names(first)) == 1
        assert len(await _names(second)) == 1

    async def test_disabled_girl_is_skipped(self, auth_client, clean, agent):
        """Снятая анкета не ждёт заготовок: круг пишет живым, а не всем подряд."""
        gid = await _girl('Нина', enabled=0)

        was = await _settings(auth_client, template_per_day='3')
        try:
            made = await _write(SITE, 0)
        finally:
            await auth_client.put(f'/api/dating/site/{SITE}', json={'settings': was})

        assert made == 0
        assert await _names(gid) == []

    async def test_second_pass_does_nothing(self, auth_client, clean, agent):
        """⚠ Норма — про сутки, и повторный заход в тот же день молчит."""
        await _girl('Елена')
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
        gid = await _girl('Елена')
        await _put('уже-есть', days_ago=0, profile_id=gid)

        was = await _settings(auth_client, template_per_day='3')
        try:
            made = await _write(SITE, 0)
        finally:
            await auth_client.put(f'/api/dating/site/{SITE}', json={'settings': was})

        assert made == 2, 'дневная норма посчитана без уже заведённых'
        assert 'template_1' not in await _names(gid), \
            'нумерация не продолжилась за уже заведённым'

    async def test_new_template_is_enabled(self, auth_client, clean, agent):
        """⚠ Заготовка ложится **включённой** — иначе цепочка не замыкается:
        рассылка берёт только включённые шаблоны своей анкеты.

        Безопасно это потому, что круга отправки нет: текст доходит до пула, а из
        пула письма уходят по одному, кнопкой.
        """
        from mysql_.mysql_ import mysql_get_db_async

        await _girl('Елена')
        was = await _settings(auth_client, template_per_day='3')
        try:
            await _write(SITE, 0)
        finally:
            await auth_client.put(f'/api/dating/site/{SITE}', json={'settings': was})

        async with mysql_get_db_async() as db:
            await db.execute('SELECT enabled FROM mail WHERE site_id = %s', (SITE,))
            rows = await db.fetchall()

        assert all(int(row['enabled']) for row in rows), 'заготовка пришла выключенной'

    async def test_agent_refusal_stops_the_pass(self, auth_client, clean, monkeypatch):
        """Отказ агента обрывает заход целиком, а не одну заготовку: сосед занят
        для всех одинаково, а недостающие доберутся через час."""
        from src.dating.mail_agent import DatingMailAgentError

        gid = await _girl('Елена')
        calls = []

        async def broken(site_id, variables, girl=None):
            calls.append(1)
            raise DatingMailAgentError('браузер соседа занят')

        monkeypatch.setattr(round_, 'mail_write', broken)

        was = await _settings(auth_client, template_per_day='3')
        try:
            made = await _write(SITE, 0)
        finally:
            await auth_client.put(f'/api/dating/site/{SITE}', json={'settings': was})

        assert made == 0
        assert len(calls) == 1, 'после отказа продолжили дёргать агента'
        assert await _names(gid) == [], 'обрванный заход наполовину оставил след'

    async def test_empty_answer_is_not_saved(self, auth_client, clean, monkeypatch):
        """Агент ответил пусто — пустой шаблон в список не кладём."""
        async def empty(site_id, variables, girl=None):
            return ''

        await _girl('Вера')
        monkeypatch.setattr(round_, 'mail_write', empty)

        made = await _write(SITE, 0)

        assert made == 0
        assert await _names() == []

    async def test_norm_is_counted_in_the_database(self, auth_client, clean):
        """⚠ Норма считается **по базе своей анкеты**, а не в памяти: перезапуск
        смены держателя не должен давать вторую норму за день — и чужие строки
        её норму не съедают."""
        gid = await _girl('Елена')
        other = await _girl('Инна')
        await _put('свой', days_ago=0, profile_id=gid)
        await _put('чужой', days_ago=0, profile_id=other)

        assert await _made_today(SITE, gid, 0) == 1, \
            'норма посчитана по всему сайту, а не по анкете'
        # ⚠ метка лежит в UTC, сутки местные: без сдвига на пояс строка ночи
        # (21:00–23:59 UTC) осталась бы вчерашним местным днём, и норму писалось
        # бы заново каждый час. Проверка в тёмном окне живая, вне него — тоже
        # истина, но ровно потому, что сдвиг нечего было показать.
        assert await _made_today(SITE, gid, 3) == 1, \
            'сутки считаются без часового пояса анкеты'


class TestOrder:
    """⚠ Уборка — **после** написания, и порядок здесь решает исход неудачи."""

    def test_write_goes_before_sweep(self):
        import inspect

        code = inspect.getsource(round_._work)

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
        from src.dating.mail_agent import DatingMailAgentError

        gid = await _girl('Ольга')
        for n in range(4):
            await _put(f'вчерашний{n}', days_ago=9, profile_id=gid)

        async def broken(site_id, variables, girl=None):
            raise DatingMailAgentError('браузер соседа занят')

        monkeypatch.setattr(round_, 'mail_write', broken)

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

        # ⚠ Redis подменяется **в модуле слоя**: отметка захода переехала в

        # `py_modules/round_/round_slot.py`, а `src/dating/round.py` её лишь

        # ре-экспортирует. Подмена в двери переставила бы её ссылку, оставив код

        # слоя при своей, — и тест зеленел бы, ничего не проверяя.

        monkeypatch.setattr(slot, 'redis_conn_get', lambda: _Redis())

        was = await _settings(auth_client, template_keep='2', template_per_day='3')
        try:
            result = await mail_round_run(0)
        finally:
            await auth_client.put(f'/api/dating/site/{SITE}', json={'settings': was})

        left = await _names()

        assert result['made'] == 0, 'агент молчал, а заготовки появились'
        assert len(left) == 3, ('после неудачного захода список опустел ниже '
                                'дневной нормы')

    async def test_new_ones_replace_the_old(self, auth_client, clean, agent,
                                            monkeypatch):
        """А удачный заход **заменяет**: новые написаны, старые ушли, и новые
        зовутся числом — своим собственным номером, а не тем, что выдумал агент."""
        gid = await _girl('Ольга')
        for n in range(4):
            await _put(f'вчерашний{n}', days_ago=9, profile_id=gid)

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

        # ⚠ Redis подменяется **в модуле слоя**: отметка захода переехала в

        # `py_modules/round_/round_slot.py`, а `src/dating/round.py` её лишь

        # ре-экспортирует. Подмена в двери переставила бы её ссылку, оставив код

        # слоя при своей, — и тест зеленел бы, ничего не проверяя.

        monkeypatch.setattr(slot, 'redis_conn_get', lambda: _Redis())

        was = await _settings(auth_client, template_keep='2', template_per_day='3')
        try:
            result = await mail_round_run(0)
        finally:
            await auth_client.put(f'/api/dating/site/{SITE}', json={'settings': was})

        left = await _names()

        assert result['made'] == 3, 'дневная норма не написалась'
        assert result['gone'] == 4, 'вчерашние не убрались'
        assert set(left) == {'template_1', 'template_2',
                             'template_3'}, 'в списке остались чужие имена'


class TestSitesFilter:
    """Кого круг берёт в работу — проверяется **чтением**, без запуска круга."""

    async def test_only_sites_with_schedule_on(self, auth_client):
        """Отбор идёт по `template_schedule_on`, а не по наличию смен: смены правят
        ползунком, и промежуточные состояния законны."""
        from src.dating.mail_round import _sites

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
        # ⚠ Redis подменяется **в модуле слоя**: отметка захода переехала в
        # `py_modules/round_/round_slot.py`, а `src/dating/round.py` её лишь
        # ре-экспортирует. Подмена в двери переставила бы её ссылку, оставив код
        # слоя при своей, — и тест зеленел бы, ничего не проверяя.
        monkeypatch.setattr(slot, 'redis_conn_get', lambda: fake)

        return fake

    async def test_site_with_schedule_is_taken(self, site, clean, agent, clean_mark):
        await _girl('Аня')
        result = await mail_round_run(0)

        assert result['made'] >= 1, 'сайт со сменой пропущен'

    async def test_empty_schedule_is_skipped(self, auth_client, clean, agent,
                                             clean_mark, monkeypatch):
        """Пустое расписание — это «нет», а не «всегда»: правило общее с расписанием
        анкеты и рассылки."""
        async def no_shifts():
            return [{'site_id': SITE, 'schedule': []}]

        monkeypatch.setattr(round_, '_sites', no_shifts)

        assert (await mail_round_run(0))['made'] == 0
        assert not agent, 'агента позвали при пустом расписании'

    async def test_mark_stops_the_second_pass(self, site, clean, agent, clean_mark):
        """Второй заход в тот же срок ничего не делает: отметка на месте."""
        await _girl('Аня')
        await mail_round_run(0)
        before = len(agent)
        await mail_round_run(0)

        assert len(agent) == before, 'заход пошёл второй раз, не дождавшись срока'

    async def test_mark_is_set_before_work(self, site, clean, agent, clean_mark):
        """⚠ Отметка ставится **до** работы: заготовки пишутся минутами, и
        следующий тик завёл бы вторую пачку поверх первой."""
        await mail_round_run(0)

        assert clean_mark.get(round_slot_key(MAIL_ROUND_KEY, SITE))

    async def test_broken_redis_stops_the_round(self, monkeypatch, site, clean, agent):
        """⚠ Сбой Redis решается в сторону бездействия: лишний заход — это вызовы
        LLM и лишние тексты в списке."""
        def broken():
            raise RuntimeError('redis лежит')

        # ⚠ Redis подменяется **в модуле слоя**: отметка захода переехала в

        # `py_modules/round_/round_slot.py`, а `src/dating/round.py` её лишь

        # ре-экспортирует. Подмена в двери переставила бы её ссылку, оставив код

        # слоя при своей, — и тест зеленел бы, ничего не проверяя.

        monkeypatch.setattr(slot, 'redis_conn_get', broken)

        assert (await mail_round_run(0))['made'] == 0
        assert not agent


class TestNameCounts:
    """⚠ Имя заготовки только считает место в норме — и не зовёт владелицу.

    Прежнее правило (с 2026-10-16) звало анкету (`elena_template_2`) — и в списке
    однофамилиц имена не различали ровно ничего, только сбивали: владелицу
    показывает аватарка строки. С 2026-10-17 ответ агента и вовсе только текст:
    в имя не просится ни одна его строчка.
    """

    async def test_response_text_is_all_the_round_counts(self, auth_client, clean,
                                                         monkeypatch):
        """Ответ — только текст: имя остаётся числом, каким бы ни был ответ."""
        gid = await _girl('Соня')

        async def named(site_id, variables, girl=None):
            return 'Hi [[companion_name]]!'

        monkeypatch.setattr(round_, 'mail_write', named)

        was = await _settings(auth_client, template_per_day='1')
        try:
            await _write(SITE, 0)
        finally:
            await auth_client.put(f'/api/dating/site/{SITE}', json={'settings': was})

        assert await _names(gid) == ['template_1']


class TestRoundState:
    """Что круг сделает дальше — отвечает **он сам**, а не страница.

    ⚠⚠ Заведено по живому разбору 20.10.2026. Заготовки удалили сразу после
    захода, и час до следующего читался как поломка: список пуст, к соседу
    ничего не идёт, и никакого способа узнать, что круг просто держит паузу.
    Разбор занял час и кончился тем, что всё работало.

    ⚠ Сторожим главное — что считает **сервер теми же выражениями**, какими
    работает круг. Повтори страница этот расчёт, он разошёлся бы с кругом на
    первой правке: грабля, которая здесь уже случалась дважды (причина пустого
    пула и значок потолка инициативы).
    """

    async def test_the_state_comes_from_the_round_itself(self):
        """Счёт суток, смену и срок берём у круга, а не считаем заново."""
        import inspect

        from src.dating.mail_round import mail_round_state

        code = inspect.getsource(mail_round_state)

        for mark in ('round_sites(', 'schedule_now(', 'round_slot_left(',
                     'round_day_count('):
            assert mark in code, f'состояние считает своё, а не круговое: {mark}'

    async def test_schedule_off_is_not_a_breakage(self, auth_client, clean):
        """Расписание выключено — это `on: False`, а не отказ и не пустота.

        Отбор тот же самый, которым круг берёт сайты в работу (`_sites`), и
        «сайта в списке нет» значит ровно «не работаем», а не «что-то сломалось».
        """
        from src.dating.mail_round import mail_round_state

        before = (await auth_client.get(
            f'/api/dating/site/{SITE}/template-schedule')).json()['result']
        try:
            await auth_client.put(f'/api/dating/site/{SITE}/template-schedule-on',
                                  json={'template_schedule_on': False})

            got = await mail_round_state(SITE)

            assert got['on'] is False
            assert got['made'] == 0 and got['norm'] == 0
        finally:
            await auth_client.put(
                f'/api/dating/site/{SITE}/template-schedule-on',
                json={'template_schedule_on': before['template_schedule_on']})

    async def test_the_norm_counts_every_enabled_girl(self, auth_client, clean):
        """Норма сайта — дневная норма анкеты, умноженная на число включённых.

        ⚠ Круг обходит **каждую своей рукой**, и норма сайта без этого множителя
        показывала бы три там, где он напишет пятьдесят семь.
        """
        from src.dating.mail_round import mail_round_state

        await _girl('Аня')
        await _girl('Вера')
        await _girl('Зоя', enabled=0)
        await _revive()

        was = await _settings(auth_client, template_per_day='3')
        try:
            await auth_client.put(f'/api/dating/site/{SITE}/template-schedule-on',
                                  json={'template_schedule_on': True})
            await _revive()

            got = await mail_round_state(SITE)

            # Выключенная в норму не идёт: круг её не берёт вовсе.
            assert got['norm'] == 6, got
        finally:
            await auth_client.put(f'/api/dating/site/{SITE}', json={'settings': was})

    async def test_the_handle_carries_the_state(self, auth_client, clean):
        """Состояние приезжает **тем же ответом**, что расписание: страница
        рисует их одной строкой, и второй запрос ради трёх чисел был бы лишним."""
        got = (await auth_client.get(
            f'/api/dating/site/{SITE}/template-schedule')).json()['result']

        assert 'round' in got, 'ручка расписания больше не несёт состояние круга'
        for key in ('on', 'shift', 'next_in', 'made', 'norm'):
            assert key in got['round'], f'в состоянии нет {key}'


class TestTheRoundChecksTheDraft:
    """⚠⚠ Круг проверяет заготовку **тем же `mail_check`**, что и правка руками.

    Писал он прямым `INSERT`, то есть мимо проверки, — и это стоило двум анкетам
    всей рассылки. 20.10.2026 агент выдумал метку `[[recipient_name]]` вместо
    `companion_name`; круг записал, а пул по такому шаблону не собирается вовсе
    («не заполнено — recipient_name»), и письма от этих анкет просто перестали
    готовиться.

    ⚠ Судья у соседа на это не опора: у него предел проб, и после него он
    отдаёт что есть. Проверка нужна **на нашей стороне**, у самой записи.
    """

    async def test_an_invented_mark_is_not_saved(self, auth_client, clean,
                                                 monkeypatch):
        """Метки нет в реестре — заготовка не ложится в базу."""
        gid = await _girl('Ника')

        async def invented(site_id, variables, girl=None):
            return 'Good evening, [[recipient_name]]. How was your day?'

        monkeypatch.setattr(round_, 'mail_write', invented)

        was = await _settings(auth_client, template_per_day='2')
        try:
            made = await _write(SITE, 0)
        finally:
            await auth_client.put(f'/api/dating/site/{SITE}', json={'settings': was})

        assert made == 0, 'негодная заготовка всё-таки записана'
        assert await _names(gid) == [], 'в списке анкеты появился брак'

    async def test_a_good_draft_still_passes(self, auth_client, clean, monkeypatch):
        """⚠ Проверка не должна отвергать годное: метка из реестра проходит.

        Сторож нужен в паре с первым — иначе «ничего не сохраняем» выглядело бы
        как исправная проверка.
        """
        gid = await _girl('Злата')

        async def fine(site_id, variables, girl=None):
            return 'Hello! [[question_to_companion]]'

        monkeypatch.setattr(round_, 'mail_write', fine)

        was = await _settings(auth_client, template_per_day='1')
        try:
            made = await _write(SITE, 0)
        finally:
            await auth_client.put(f'/api/dating/site/{SITE}', json={'settings': was})

        assert made == 1, 'годная заготовка не сохранилась'
        assert await _names(gid) == ['template_1']


class TestTheNormLivesInOnePlace:
    """⚠⚠ Норма заготовок правится **у расписания шаблонов**, а не в двух местах.

    Поле стояло и на «Настройках сайтов», и рядом со списком шаблонов — одно
    значение с двумя полями. Это два места, где его правят, и вечный вопрос,
    какое главное; расходятся такие пары молча.

    ⚠ Запись из реестра при этом **не убрана**: `site_seconds` берёт оттуда
    умолчание, а `_number_check` — границы. Снятая запись сняла бы и проверку,
    и значение стало бы любым.
    """

    def test_the_setting_is_hidden_from_the_settings_page(self):
        """Признак `hidden` стоит, а `kind` остался числовым."""
        from src.dating.site_setting import SITE_KNOWN

        spec = SITE_KNOWN['template_per_day']

        assert spec.get('hidden') is True, 'норма вернулась на страницу настроек'
        assert spec['kind'] == 'number', 'поле перестало быть числовым'
        assert spec['min'] == 1, 'у нормы пропала нижняя граница'

    async def test_the_registry_still_carries_it(self, auth_client):
        """⚠ Странице ключ всё равно отдаётся — с пометкой.

        Не отдай мы его вовсе, страница сочла бы ключ чужим и показала в «Своих
        настройках»: он не исчез бы, а переехал на соседнюю вкладку.
        """
        known = (await auth_client.get('/api/dating/site')).json()['result']['known']
        mine = {row['key']: row for row in known}

        assert 'template_per_day' in mine, 'ключ пропал из реестра страницы'
        assert mine['template_per_day']['hidden'] is True
        # Соседняя настройка рядом — рисуется как обычно.
        assert mine['template_every']['hidden'] is False
