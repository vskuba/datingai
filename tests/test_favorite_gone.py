"""Звезда погасла на сайте — пара уходит в `gone`, а не живёт у нас вечно.

## ⚠⚠ Живая беда: Оксана писала Джону, которого у неё в избранных нет

Прод, 28 сентября. В пуле «Фавориты → Приставание» висело письмо
`Oksana413636 → John849892`, а в виджете «Мои фавориты» у Оксаны Джона не было.
Разобрано по строкам:

    favorite id=76  profile_id=48  uid=849892
    state=already   reason=site_list   created_at=2026-09-13 11:02:14

`reason = site_list` значит, что строку завёл `favorite_sync`: **13 сентября Джон
действительно был у неё в избранных**. Потом звезда на сайте погасла, а строка
осталась — `favorite_sync` только добавлял и ничего не убирал. Отбор писем
(`favorite_mail._quiet`) читает **нашу** таблицу, а не сайт, и с тех пор пара
считалась законной.

Сверка живых списков в тот день: у тринадцати анкет **22 лишние пары**, все с
`reason = site_list` и все заведены 13 сентября. Двое из них висели у многих сразу
(`808675` у шести девушек, `157454` у пяти) — такие пропадают из всех списков разом,
то есть учётки убрал сам сайт.

## ⚠⚠ Почему не убирали — и почему теперь можно

Довод был записан прямо в `favorite_sync`: «снятая на сайте звезда и не пришедший
список выглядят отсюда одинаково». И это было правдой: `favorite_rows` отвечал
пустым списком **и** когда избранных нет, **и** когда ответ пришёл без списка.
Вычистить всех по кривому ответу дорого, поэтому не чистили никого.

Чинится в том порядке, в каком беда и сложилась:

1. **Список либо пришёл, либо нет.** Ответ без `Users` — отказ
   (`DatingFavoriteError`), а `[]` теперь значит ровно «сайт говорит: избранных
   нет». Оба зовущих отказ уже ловят: круг звёздочек уходит к следующей анкете,
   сверка молчит и работает по тому, что знает.
2. **Пропавших гасим** — `state = 'gone'`.

## ⚠ Гасим, а не удаляем, и это не вкусовщина

`DELETE` унёс бы и состояние (`tries`, `error`), и повод (`reason`), и историю:
журнал доставки делит ответы между «Фаворитами» и «Письмами» по наличию строки.
`gone` же не входит в `FAVORITE_STATES`, а по нему отбирают все шесть мест — пул
писем, лента «Избранные онлайн», счётчики вкладок, — и перестают видеть пару сами,
без единой правки в них.

## ⚠⚠ Возврат обязателен, иначе лекарство хуже болезни

Сайт может моргнуть и отдать пустой список; тогда погаснут все. Поэтому тот, кто
снова появился в списке, **зажигается обратно** (`already`): один пропущенный заход
приставания — вся цена такой осечки. Не будь возврата, моргание сайта стоило бы нам
всех избранных навсегда.
"""

import uuid

import pytest

from mysql_.mysql_ import mysql_get_db_async

from src.dating.favorite import (DatingFavoriteError, FAVORITE_ALREADY,
                                        FAVORITE_DONE, FAVORITE_FAIL,
                                        FAVORITE_GONE, FAVORITE_STATES)

pytestmark = pytest.mark.mail

# ⚠ **Свой номер стенда, а не общий `999_343`.** Здесь заводятся строки
# `favorite_mail`, а журнал доставки считает их **по сайту целиком**
# (`test_favorite_sent_paging`: «семь строк» — весь журнал стенда). Одно наше
# ушедшее письмо на общем номере превращало его в восемь, и падал чужой тест.
STAND_SITE = 999_348


def _uid() -> str:
    """Номер собеседника, не занятый ничьей живой строкой."""
    return f'gone-{uuid.uuid4().hex[:8]}'


async def _girl(status: str = 'offline') -> int:
    """Анкета сюиты: метка `@example.com` уносит её уборкой после прогона."""
    async with mysql_get_db_async() as db:
        await db.execute(
            'INSERT INTO profile (site_id, login, password, enabled, status)'
            ' VALUES (%s, %s, %s, 1, %s)',
            (STAND_SITE, f'favgone-{uuid.uuid4().hex[:8]}@example.com', 'x',
             status))
        await db.connection.commit()

        return int(db.lastrowid)


async def _star(girl: int, uid: str, state: str = FAVORITE_ALREADY) -> None:
    async with mysql_get_db_async() as db:
        await db.execute(
            'INSERT INTO favorite (site_id, profile_id, uid, state, reason)'
            " VALUES (%s, %s, %s, %s, 'site_list')",
            (STAND_SITE, girl, uid, state))
        await db.connection.commit()


async def _state(girl: int, uid: str) -> str:
    """Состояние пары в нашей таблице. Пусто — строки нет вовсе."""
    async with mysql_get_db_async() as db:
        await db.execute('SELECT state FROM favorite'
                         ' WHERE profile_id = %s AND uid = %s', (girl, uid))
        row = await db.fetchone()

    return str((row or {}).get('state') or '')


def _answer(*uids: str):
    """Ответ сайта со списком избранных — тех, кого назвали."""
    async def call(*args, **kwargs):
        return {'Users': [{'AccountId': one, 'Name': 'Someone'} for one in uids]}

    return call


async def _sync(monkeypatch, girl: int, *uids: str) -> int:
    """Сверка одной анкеты против названного списка сайта."""
    from src.dating import favorite

    monkeypatch.setattr(favorite, 'site_api_call', _answer(*uids))

    return await favorite.favorite_sync({'id': girl, 'site_id': STAND_SITE},
                                        'base', 'pw')


class TestTheListEitherCameOrDidNot:
    """⚠⚠ Первая половина починки: пустой список и отсутствие списка — разное.

    Пока оба отвечали `[]`, чистить по нему было нельзя вовсе: один кривой ответ
    сайта унёс бы все звёзды разом.
    """

    async def test_a_body_without_the_list_is_a_refusal(self, monkeypatch):
        """Ответ без `Users` — отказ, а не «избранных нет».

        ⚠ Оба зовущих отказ уже ловят и ведут себя верно: круг звёздочек идёт к
        следующей анкете (жать звезду наугад значило бы гасить чужую), сверка
        молчит и работает по тому, что уже знает.
        """
        from src.dating import favorite

        async def no_list(*args, **kwargs):
            return {'Error': 'что-то пошло не так'}

        monkeypatch.setattr(favorite, 'site_api_call', no_list)

        with pytest.raises(DatingFavoriteError):
            await favorite.favorite_rows({'id': 1}, 'base', 'pw')

    async def test_an_empty_list_is_an_answer(self, monkeypatch):
        """`Users: []` — законный ответ: у анкеты нет избранных.

        Живой случай: анкета #50 на проде отвечает двенадцатью байтами, и это не
        поломка.
        """
        from src.dating import favorite

        monkeypatch.setattr(favorite, 'site_api_call', _answer())

        assert await favorite.favorite_rows({'id': 1}, 'base', 'pw') == []

    async def test_uids_refuse_the_same_way(self, monkeypatch):
        """⚠ Тот же разбор у номеров: ими живёт круг звёздочек."""
        from src.dating import favorite

        async def no_list(*args, **kwargs):
            return {}

        monkeypatch.setattr(favorite, 'site_api_call', no_list)

        with pytest.raises(DatingFavoriteError):
            await favorite.favorite_uids({'id': 1}, 'base', 'pw')

    async def test_uids_read_an_empty_list(self, monkeypatch):
        from src.dating import favorite

        monkeypatch.setattr(favorite, 'site_api_call', _answer())

        assert await favorite.favorite_uids({'id': 1}, 'base', 'pw') == set()


class TestTheVanishedGoOut:
    """Сверка гасит тех, кого в списке сайта больше нет."""

    async def test_the_one_missing_from_the_list_turns_gone(self, monkeypatch):
        """Та самая пара «Оксана — Джон»: звезды на сайте нет, строка гаснет."""
        girl, john = await _girl(), _uid()
        await _star(girl, john)

        await _sync(monkeypatch, girl, _uid())

        assert await _state(girl, john) == FAVORITE_GONE, \
            'пара осталась избранной — приставание снова напишет Джону'

    async def test_the_one_still_listed_stays(self, monkeypatch):
        """Кто в списке — того не трогаем: сверка не чистка."""
        girl, star = await _girl(), _uid()
        await _star(girl, star)

        await _sync(monkeypatch, girl, star)

        assert await _state(girl, star) == FAVORITE_ALREADY

    async def test_a_failed_star_is_left_alone(self, monkeypatch):
        """⚠ `fail` гасить не надо: он и так не избранный.

        Гасим только тех, кто **считался** избранным (`FAVORITE_STATES`). У `fail`
        свой смысл — «нажать не вышло», — и свой счётчик заходов; перепиши мы его,
        круг звёздочек потерял бы `tries` и пошёл бы к человеку заново.
        """
        girl, broken = await _girl(), _uid()
        await _star(girl, broken, FAVORITE_FAIL)

        await _sync(monkeypatch, girl)

        assert await _state(girl, broken) == FAVORITE_FAIL

    async def test_another_girls_row_is_not_touched(self, monkeypatch):
        """⚠ Гаснет **пара**, а не человек: у соседки он может гореть законно.

        Список избранных — её собственный, и сверка одной анкеты ничего не знает
        о списках остальных.
        """
        mine, hers, man = await _girl(), await _girl(), _uid()
        await _star(mine, man)
        await _star(hers, man)

        await _sync(monkeypatch, mine)

        assert await _state(mine, man) == FAVORITE_GONE
        assert await _state(hers, man) == FAVORITE_ALREADY, \
            'сверка одной анкеты погасила звезду другой'

    async def test_a_refusal_leaves_everything_alone(self, monkeypatch):
        """Списка не было — не знаем ничего, и трогать нечего.

        ⚠ Ради этого и разведены «пусто» с «не пришло»: молчание сайта не повод
        забыть избранных.
        """
        from src.dating import favorite

        girl, star = await _girl(), _uid()
        await _star(girl, star)

        async def no_list(*args, **kwargs):
            return {'Error': 'сайт молчит'}

        monkeypatch.setattr(favorite, 'site_api_call', no_list)
        await favorite.favorite_sync({'id': girl, 'site_id': STAND_SITE},
                                     'base', 'pw')

        assert await _state(girl, star) == FAVORITE_ALREADY

    async def test_a_returned_star_lights_again(self, monkeypatch):
        """⚠⚠ Вернулся в список — снова избранный.

        Без этого одно моргание сайта (пустой список на секунду) стоило бы нам
        всех избранных навсегда: погашенных сверка больше никогда не сочла бы
        новыми.
        """
        girl, man = await _girl(), _uid()
        await _star(girl, man)

        await _sync(monkeypatch, girl)
        assert await _state(girl, man) == FAVORITE_GONE

        await _sync(monkeypatch, girl, man)

        assert await _state(girl, man) == FAVORITE_ALREADY, \
            'погашенная пара не возвращается — моргание сайта стало приговором'

    async def test_the_gone_pair_loses_its_draft(self, monkeypatch):
        """Черновик приставания уходит вместе со звездой.

        Письмо избранному пишется потому, что он избранный; с погасшей звездой
        слать его нечего. Тот же жест, что у кнопки «убрать из фаворитов»
        (`favorite_forget`) и у запрета (`block_add`).
        """
        girl, man = await _girl(), _uid()
        await _star(girl, man)

        async with mysql_get_db_async() as db:
            await db.execute(
                'INSERT INTO favorite_mail'
                ' (site_id, profile_id, uid, body, status)'
                " VALUES (%s, %s, %s, 'привет', 'draft')",
                (STAND_SITE, girl, man))
            # ⚠ Ушедшее — журнал, и журнал отвечает за уже случившееся.
            await db.execute(
                'INSERT INTO favorite_mail'
                ' (site_id, profile_id, uid, body, status, sent_at)'
                " VALUES (%s, %s, %s, 'ушло', 'sent', NOW())",
                (STAND_SITE, girl, man))
            await db.connection.commit()

        await _sync(monkeypatch, girl)

        async with mysql_get_db_async() as db:
            await db.execute('SELECT status FROM favorite_mail'
                             ' WHERE profile_id = %s AND uid = %s', (girl, man))
            left = [str(row['status']) for row in await db.fetchall() or []]
            # Прибираем за собой сразу: журнал доставки считает строки по сайту,
            # и ушедшее письмо стенда переживает нас до конца прогона.
            await db.execute('DELETE FROM favorite_mail WHERE profile_id = %s',
                             (girl,))
            await db.connection.commit()

        assert left == ['sent'], \
            f'ожидали только ушедшее письмо, осталось {left}'


class TestThePoolStopsWriting:
    """Что даёт `gone` тем шести местам, которые о нём не знают."""

    def test_gone_is_not_a_favorite_state(self):
        """⚠ В этом и весь приём: набор состояний не тронут, и все шесть мест,
        которые им отбирают, перестают видеть пару сами."""
        assert FAVORITE_GONE not in FAVORITE_STATES
        assert FAVORITE_STATES == (FAVORITE_DONE, FAVORITE_ALREADY)

    async def test_the_pool_does_not_pick_a_gone_pair(self):
        """Отбор писем избранным обходит погашенную пару стороной.

        ⚠ Проверяется через сам отбор (`_quiet`), а не чтением его текста: правило
        «кого считаем избранным» должно совпадать с тем, что видит круг.
        """
        from src.dating.favorite_mail import _quiet

        girl, man = await _girl('online'), _uid()
        await _star(girl, man, FAVORITE_ALREADY)

        picked = await _quiet(STAND_SITE, girl)
        assert [str(one['uid']) for one in picked] == [man], \
            'избранная пара не попала в отбор — сторож проверяет не то'

        async with mysql_get_db_async() as db:
            await db.execute('UPDATE favorite SET state = %s'
                             ' WHERE profile_id = %s AND uid = %s',
                             (FAVORITE_GONE, girl, man))
            await db.connection.commit()

        assert await _quiet(STAND_SITE, girl) == [], \
            'погашенной паре всё ещё пишут письма'


class TestTheFullPoolStillSyncs:
    """⚠⚠ Дверь, запиравшаяся изнутри: полный пул не давал себя почистить.

    Сверка гасит пропавших и уносит их черновики, но жила **внутри набора** — то
    есть за проверкой «есть ли место в пуле»:

        room = await _pool_max(site_id) - pool
        if room <= 0:
            return None

    Выходило кольцо: стухший черновик держит место → места нет → набор не зовётся
    → сверка не идёт → черновик не убирается. И единственный, кто из этого кольца
    выходил, — **круг отправки**: полный пул он как раз разбирает, то есть слал
    письмо тому, кто давно не избранный.

    Найдено на проде сразу после выкладки `gone`: пул был полон, и за пятнадцать
    минут не погасла ни одна из 22 пар. Гасили разовым прогоном сверки руками.
    """

    async def test_the_round_syncs_before_counting_room(self, monkeypatch):
        """Пул полон — набор не идёт, а сверка идёт."""
        from src.dating import favorite_mail_round

        synced = []

        async def sync(site_id, profile_id=0):
            synced.append(int(site_id))

        async def nothing(site_id):
            return None

        async def full(site_id):
            return 10 ** 6

        async def cap(site_id):
            return 20

        async def never(*args, **kwargs):
            raise AssertionError('набор пошёл при полном пуле')

        monkeypatch.setattr(favorite_mail_round, 'favorite_mail_stars_sync', sync)
        monkeypatch.setattr(favorite_mail_round, 'pool_sweep', nothing)
        monkeypatch.setattr(favorite_mail_round, 'favorite_mail_pool_left', full)
        monkeypatch.setattr(favorite_mail_round, '_pool_max', cap)
        monkeypatch.setattr(favorite_mail_round, 'favorite_mail_make', never)

        got = await favorite_mail_round._work({'site_id': STAND_SITE}, 0)

        assert got is None, 'при полном пуле круг всё-таки набирал письма'
        assert synced == [STAND_SITE], \
            'сверка не прошла — пул снова заперт своим же мусором'

    def test_the_gate_is_still_the_gate(self):
        """⚠ Свой вызов у круга **не отменяет срока**: сверка спрашивает его сама.

        Иначе круг, будимый свежей лентой каждые три минуты, вернул бы те самые
        157 запросов в час, ради которых срок и заводили
        (`test_favorite_sync_every`).
        """
        import inspect

        from src.dating import favorite_mail

        code = inspect.getsource(favorite_mail.favorite_mail_stars_sync)

        assert 'if profile_id or _sync_due(site_id):' in code
