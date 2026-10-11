"""Точка «в сети» у строки разговора — одно правило на оба списка.

Списков два и стоят они на соседних подвкладках: «Чат → Диалоги» и «Фавориты →
Диалоги». Вопрос у них один — с кем говорим и здесь ли он сейчас, — а точка была
только у первого: во втором человек видел те же лица без всякого признака и
читал это как «тут про онлайн не знают».

## ⚠⚠ Почему это не две правки, а одна

Признак складывается из двух слагаемых: ленты онлайна (она про сайт и обновляется
своим кругом) и запасного поля `contact.is_online` — для сайта, у которого ленты
нет вовсе. Посчитай его каждый список сам, и расхождение будет молчаливым: один и
тот же человек выйдет зелёным в «Чате» и серым в «Фаворитах», а объяснить разницу
нечем — списки соседние, данные те же.

Поэтому здесь проверяется **не точка, а общность правила**: обе дороги спрашивают
`talkOnline`, и слагаемые приезжают в обе ручки.

## ⚠ Перерисовка — третья дорога, и о ней забывают

Лента онлайна приходит позже списка, и точки доводит `talkDotsPaint`. Он ходит по
DOM, а не по данным, — значит запасное слагаемое обязано лежать в самой строке
(`data-online-own`), иначе перерисовка знает половину правила и гасит точку,
зажжённую полем. На сайте с лентой это незаметно (поле там пустое), на сайте без
ленты перерисовка не случается вовсе: два правила, которые нигде не встречаются.
"""

import pathlib
import re
import uuid

import pytest

from mysql_.mysql_ import mysql_get_db_async

from src.uvicorn.routers.dating.favorite_mail import favorite_mail_threads

pytestmark = pytest.mark.mail

STAND_SITE = 999_343

ROOT = pathlib.Path(__file__).resolve().parents[1]
PAGE = ROOT / 'data/uvicorn/template/admin/dating/profile.html'


@pytest.fixture
async def talk(auth_client):
    """Пара с перепиской: он написал, мы ответили, звёздочка стоит.

    ⚠ Карточка в `contact` — **с поднятым** `is_online`: именно она и проверяет
    запасное слагаемое. Без неё поле приезжало бы ложным у всех, и тест не
    отличал бы «правило работает» от «правило не спрашивали».
    """
    girl = int((await auth_client.post('/api/dating/profile', json={
        'site_id': STAND_SITE,
        'login': f'dot-{uuid.uuid4().hex[:8]}@example.com',
        'password': 'x'})).json()['result'])
    uid = f'dot-{uuid.uuid4().hex[:8]}'

    async with mysql_get_db_async() as db:
        await db.execute(
            'INSERT INTO favorite (profile_id, site_id, uid, state)'
            " VALUES (%s, %s, %s, 'done')", (girl, STAND_SITE, uid))
        await db.execute(
            'INSERT INTO mail_in (site_id, profile_id, message_id, uid, body,'
            ' date_post) VALUES (%s, %s, %s, %s, %s, NOW() - INTERVAL 10 MINUTE)',
            (STAND_SITE, girl, uuid.uuid4().int % 10 ** 12, uid, 'hello'))
        await db.execute(
            'INSERT INTO contact (profile_id, uid, name, photo, age, city,'
            " is_online) VALUES (%s, %s, 'Henry', '', 0, '', 1)", (girl, uid))
        await db.connection.commit()

    yield {'girl': girl, 'uid': uid}

    async with mysql_get_db_async() as db:
        await db.execute('DELETE FROM favorite WHERE profile_id = %s', (girl,))
        await db.execute('DELETE FROM mail_in WHERE profile_id = %s', (girl,))
        await db.execute('DELETE FROM contact WHERE profile_id = %s', (girl,))
        await db.execute('DELETE FROM profile WHERE id = %s', (girl,))
        await db.connection.commit()


def _page() -> str:
    return PAGE.read_text(encoding='utf-8')


def _body(name: str) -> str:
    """Тело функции страницы — от объявления до следующего на том же отступе."""
    text = _page()
    found = re.search(rf'\n    function {name}\(.*?\n    \}}\n', text, re.S)

    assert found, f'функции {name} на странице нет'

    return found.group(0)


class TestTheHandleSendsTheField:
    """⚠ Слагаемые правила приезжают в **обе** ручки, иначе оно разъедется."""

    async def test_threads_tell_who_is_online(self, talk):
        got = await favorite_mail_threads(site_id=STAND_SITE,
                                          profile_id=talk['girl'],
                                          user=None)
        row = next(one for one in got['result'] if one['uid'] == talk['uid'])

        assert row['is_online'] is True, \
            '«Фавориты → Диалоги» не знают, в сети ли собеседник'

    async def test_it_is_a_bool_even_without_a_card(self, talk):
        """⚠ У пары без карточки в `contact` поле приезжает `None`.

        Страница ждёт два состояния, а не три: `null` в `!!row.is_online` даёт
        ложь молча, но то же `null` в `data-online-own` строки — строку
        `"null"`, то есть **истину**. Приводим к `bool` на сервере, пока
        состояний ещё два.
        """
        async with mysql_get_db_async() as db:
            await db.execute('DELETE FROM contact WHERE profile_id = %s',
                             (talk['girl'],))
            await db.connection.commit()

        got = await favorite_mail_threads(site_id=STAND_SITE,
                                          profile_id=talk['girl'],
                                          user=None)
        row = next(one for one in got['result'] if one['uid'] == talk['uid'])

        assert row['is_online'] is False, 'поле приехало не булевым'


class TestOneRuleForBothLists:
    """⚠⚠ Главное: правило одно, и обе дороги спрашивают именно его."""

    def test_both_lists_are_named_in_one_place(self):
        """Списки перечислены списком, а не проверяются по месту."""
        found = re.search(r'const TALK_DOT_SLOTS = \[(.*?)\];', _page())

        assert found, 'списка слотов с точками нет'
        assert "'dialogs'" in found.group(1), 'диалоги чата выпали из списка'
        assert "'fav-talks-list'" in found.group(1), \
            'диалоги фаворитов выпали из списка — точки перестанут догонять ленту'

    def test_the_chat_list_asks_the_rule(self):
        assert 'talkOnline(c)' in _body('chatContacts'), \
            'диалоги чата снова считают «в сети» своей строкой'

    def test_the_favorites_list_asks_the_rule(self):
        assert 'talkOnline(one)' in _body('favTalksPaint'), \
            'диалоги фаворитов считают «в сети» мимо общего правила'

    def test_the_rule_lives_in_one_function(self):
        """⚠⚠ Сложение слагаемых написано **один раз**.

        Сторож смотрит на выражение, а не на имя: вторая копия появляется не
        переименованием, а строкой `peopleOnlineHas(...) || ...is_online` в
        соседнем списке — ровно так расходятся правила, которые «и так
        одинаковые».
        """
        text = _page()
        twice = re.findall(r'peopleOnlineHas\([^)]*\)\s*\|\|', text)

        assert len(twice) == 1, \
            f'сложение «лента или своё поле» написано {len(twice)} раз(а)'
        assert 'peopleOnlineHas(row.uid) || !!row.is_online' in _body('talkOnline')


class TestTheRepaintKnowsTheWholeRule:
    """⚠⚠ Перерисовка ходит по DOM — значит правило должно быть в строке."""

    def test_the_row_carries_the_fallback(self):
        """Запасное слагаемое кладётся в строку обоих списков."""
        assert 'talkDotMark(c)' in _body('chatContacts'), \
            'строка диалога чата не несёт запасного признака'
        assert 'talkDotMark(one)' in _body('favTalksPaint'), \
            'строка диалога фаворитов не несёт запасного признака'

    def test_the_repaint_uses_the_same_rule(self):
        """⚠ Не `peopleOnlineHas` напрямую: это была бы половина правила."""
        body = _body('talkDotsPaint')

        assert 'talkOnline({' in body, \
            'перерисовка считает «в сети» мимо общего правила'
        assert 'dataset.onlineOwn' in body, \
            'перерисовка не видит запасного признака и погасит точку'

    def test_the_favorites_row_can_be_found_by_uid(self):
        """⚠ Перерисовка ищет строки по `data-uid`, а щелчок идёт по `data-index`.

        Номер в строке нужен обоим, и у фаворитов его не было вовсе: список
        остался бы с точками той минуты, когда его собрали.
        """
        body = _body('favTalksPaint')

        assert 'data-uid=' in body, 'строке фаворитов нечем найтись при перерисовке'
        assert 'data-index=' in body, 'щелчок по строке фаворитов сломан'
