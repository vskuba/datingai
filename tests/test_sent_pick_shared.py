"""Отбор именами — один на три журнала доставки: инвайты, письма, письма избранным.

Нажатие по имени в строке журнала отбирает по нему: «всё, что уходило от этой
девушки», «всё, что уходило ему». Завелось это у инвайтов, где журнал растёт на
реплику в минуту, и оттуда просилось к соседям — вопрос у трёх журналов один.

## ⚠⚠ Почему это один сторож, а не три

Отбор — не разметка, а **правило**: два поля запроса, условие в SQL, и оно нужно и
счёту, и выборке. Спрашивай каждый журнал по-своему — получится шесть написаний
одного условия, и первым отвалится `strip()` у номера: «отобрать по Семёну»
превратится в «ничего не нашлось», молча. Поэтому здесь проверяется, что все три
ручки отвечают **одинаково**, и одним и тем же кодом (`pool_pick_where`).

## ⚠ Отбор считает сервер, а не браузер

Отдай ручка «последние триста», а страница отбери среди них — «все письма Семёну»
означало бы «те из них, что попали на открытую страницу». Поэтому сторож смотрит на
`total`: он обязан сужаться вместе с выборкой, иначе пейджер обещает страницы, на
которых ничего нет.
"""

import uuid

import pytest

from mysql_.mysql_ import mysql_get_db_async

pytestmark = pytest.mark.mail

STAND_SITE = 999_343

# Три журнала и их ручки. ⚠ У инвайтов отбор живёт в общей ручке пула
# (`?status_=delivery`), у писем — в своей `/delivery`: журналы писем сливают две
# таблицы, и слить их страницами может только своя ручка.
JOURNALS = (
    ('инвайты', '/api/dating/invite', {'status_': 'delivery'}),
    ('письма', '/api/dating/mailing/delivery', {}),
    ('избранные', '/api/dating/favorite-mail/delivery', {}),
)


@pytest.fixture
async def journal(auth_client):
    """Две анкеты, два собеседника, и у каждой пары — по строке в каждом журнале.

    ⚠ Оба собеседника заведены в `favorite`: журнал избранных берёт **только пары из
    звёздочек**, и без этого отбор там проверялся бы на пустоте.

    ## ⚠⚠ Уборка идёт `finally`, а не после `yield`

    Обычный фикстур с `yield` при падении **в наборе данных** до `yield` не доходит —
    и уборка не случается вовсе. Здесь это уже случилось: `uq_mailing_once` отверг
    вставку, строки остались, и **пять чужих тестов** в соседних файлах покраснели:
    они сверяют `total` журнала сайта с точным числом, а журнал у стенда общий.

    Диагноз при этом выглядел как «сломался отбор»: падали проверки страниц у писем,
    избранных и инвайтов разом. Поэтому уборка здесь безусловная.
    """
    girls = []
    for _ in range(2):
        girls.append(int((await auth_client.post('/api/dating/profile', json={
            'site_id': STAND_SITE,
            'login': f'pick-{uuid.uuid4().hex[:8]}@example.com',
            'password': 'x'})).json()['result']))

    tag = uuid.uuid4().hex[:6]
    men = (f'pk1-{tag}', f'pk2-{tag}')

    mails = []

    try:
        await _fill(girls, men, mails, tag)

        yield {'girls': girls, 'men': men, 'tag': tag, 'mails': mails}
    finally:
        await _wipe(girls, mails)


class TestAllThreeJournalsFilterByGirl:
    """⚠ «Всё, что уходило от этой девушки» — во всех трёх журналах одинаково."""

    async def test_the_rows_are_hers_only(self, auth_client, journal):
        for name, path, extra in JOURNALS:
            got = await _ask(auth_client, path, extra,
                             profile_id=journal['girls'][0])
            mine = _mine(got, journal['tag'])

            assert mine, f'{name}: по анкете не нашлось ничего'
            assert {row['profile_id'] for row in mine} == {journal['girls'][0]}, \
                f'{name}: в отбор по анкете попали чужие строки'

    async def test_the_other_girl_is_gone(self, auth_client, journal):
        for name, path, extra in JOURNALS:
            got = await _ask(auth_client, path, extra,
                             profile_id=journal['girls'][0])

            assert journal['girls'][1] not in [row['profile_id']
                                               for row in got['result']], \
                f'{name}: вторая анкета осталась в отборе'


class TestAllThreeJournalsFilterByCompanion:
    """⚠ «Всё, что уходило ему» — тоже во всех трёх.

    ⚠⚠ Вопрос **про человека, а не про пару**: писать ему могли обе девушки, и обе
    строки обязаны остаться. Сузь отбор до пары — и «что мы вообще ему писали»
    перестало бы отвечаться, а это главный вопрос к журналу.
    """

    async def test_the_rows_are_his_only(self, auth_client, journal):
        for name, path, extra in JOURNALS:
            got = await _ask(auth_client, path, extra, uid=journal['men'][0])
            mine = _mine(got, journal['tag'])

            assert mine, f'{name}: по собеседнику не нашлось ничего'
            assert {row['uid'] for row in mine} == {journal['men'][0]}, \
                f'{name}: в отбор по собеседнику попали чужие строки'

    async def test_both_girls_stay(self, auth_client, journal):
        for name, path, extra in JOURNALS:
            got = await _ask(auth_client, path, extra, uid=journal['men'][0])
            mine = _mine(got, journal['tag'])

            assert {row['profile_id'] for row in mine} == set(journal['girls']), \
                f'{name}: отбор по человеку сузился до одной анкеты'


class TestBothSidesAtOnce:
    """⚠ Стороны порознь и обе разом: «от Дианы и ему» — законный вопрос."""

    async def test_one_row_left(self, auth_client, journal):
        for name, path, extra in JOURNALS:
            got = await _ask(auth_client, path, extra,
                             profile_id=journal['girls'][1],
                             uid=journal['men'][1])
            mine = _mine(got, journal['tag'])

            assert len(mine) == 1, f'{name}: осталось {len(mine)} строк вместо одной'
            assert mine[0]['profile_id'] == journal['girls'][1]
            assert mine[0]['uid'] == journal['men'][1]


class TestTheCountShrinksToo:
    """⚠⚠ Считает отбор **сервер**: `total` обязан сужаться вместе с выборкой.

    Иначе пейджер обещает страницы, на которых ничего нет, — а человек, дойдя до
    третьей, решит, что журнал сломался.
    """

    async def test_the_total_follows_the_filter(self, auth_client, journal):
        for name, path, extra in JOURNALS:
            whole = await _ask(auth_client, path, extra)
            one = await _ask(auth_client, path, extra,
                             profile_id=journal['girls'][0],
                             uid=journal['men'][0])

            assert one['total'] < whole['total'], \
                f"{name}: отбор не сузил счёт ({one['total']} из {whole['total']})"

    async def test_the_pages_follow_it(self, auth_client, journal):
        """⚠ И число страниц: оно считается по тому же `total`."""
        for name, path, extra in JOURNALS:
            one = await _ask(auth_client, path, extra, per=1,
                             profile_id=journal['girls'][0],
                             uid=journal['men'][0])

            assert one['pages'] == one['total'], \
                f'{name}: страниц {one["pages"]} при счёте {one["total"]} по одной'


class TestUnknownNamesGiveNothing:
    """⚠ Небывшее имя — пустой журнал, а не полный.

    Забытый отбор в счёте или в выборке проявился бы ровно здесь: строки есть, а
    отбирать было нечего.
    """

    async def test_an_unknown_companion(self, auth_client, journal):
        for name, path, extra in JOURNALS:
            got = await _ask(auth_client, path, extra, uid='нет-такого')

            assert _mine(got, journal['tag']) == [], \
                f'{name}: отбор по небывшему имени отдал строки'


class TestOneRuleForAllSix:
    """⚠⚠⚠ Условие отбора — **одна функция** на шесть мест.

    Шесть: три журнала, и в каждом счёт и выборка. У журналов писем это ещё и две
    таблицы — своя и `mail_reply`, — то есть своих написаний было бы вдвое больше.
    Разойдись счёт с выборкой, последняя страница выходила бы пустой.
    """

    def test_no_handler_writes_it_itself(self):
        import inspect

        from src.uvicorn.routers.dating import favorite_mail as fav
        from src.uvicorn.routers.dating import invite as inv
        from src.uvicorn.routers.dating import mailing as mail

        for what, code in (('инвайты', inspect.getsource(inv.invite_list)),
                           ('письма', inspect.getsource(mail.mailing_delivery)),
                           ('избранные',
                            inspect.getsource(fav.favorite_mail_delivery))):
            assert 'pool_pick_where' in code or 'mail_reply_delivery_scope' in code, \
                f'{what}: отбор снова написан своим SQL'
            assert '.uid = %s' not in code, \
                f'{what}: условие отбора вернулось в ручку своей копией'

    def test_the_number_is_stripped(self):
        """⚠ Номер собеседника `strip`-ается: он приходит из адреса, и пробел в нём
        превратил бы «отобрать по Семёну» в «ничего не нашлось»."""
        from src.dating.pool_pair import pool_pick_where

        assert pool_pick_where('m', 0, ' 803656 ')[1] == ['803656']

    def test_empty_means_all(self):
        """⚠ Ноль и пустая строка означают «все», а не «никого»: без отбора журнал
        полон — он отвечает на «что вообще уходило с сайта»."""
        from src.dating.pool_pair import pool_pick_where

        assert pool_pick_where('m', 0, '') == ('', [])
        assert pool_pick_where('m', 0, '   ') == ('', [])


class TestThePageHasOnePicker:
    """⚠⚠ На странице отбор тоже один — `sentPicker`, а не три набора переменных.

    Своя копия у каждого журнала — это девяносто строк трижды: состояние, доводы
    запроса, полоса чипсов, обработчики. Расходятся такие копии на первой правке.
    """

    def test_the_picker_is_declared_once(self):
        assert self._page().count('function sentPicker(') == 1, \
            'выкройка отбора снова в нескольких местах'

    def test_all_three_journals_take_it(self):
        page = self._page()

        for what in ('inviteSentPick = sentPicker(',
                     'favSentPick = sentPicker(',
                     'mailSentPick = sentPicker('):
            assert what in page, f'журнал держит свой отбор: нет {what}'

    def test_every_journal_paints_its_strip(self):
        page = self._page()

        for what in ('invite-sent-filter', 'fav-sent-filter', 'mail-sent-filter'):
            assert f"paint('{what}')" in page, f'полоса {what} не рисуется'

    def test_the_name_click_does_not_open_the_window(self):
        """⚠⚠ У двух журналов писем **вся строка** открывает окно записи, и нажатие
        по имени обязано гасить всплытие. Без этого оно делало бы две вещи разом:
        отбирало список и открывало окно поверх него."""
        import re

        page = self._page()
        body = re.search(r'one\.bind = \(box\) => \{.*?\n        \};',
                         page, re.S).group(0)

        assert 'stopPropagation' in body, \
            'нажатие по имени всплывает до строки — откроется и окно записи'

    def test_the_strips_are_in_the_template(self):
        import pathlib

        base = (pathlib.Path(__file__).resolve().parents[1]
                / 'data/uvicorn/template/admin/dating/profile/site/_base.html'
                ).read_text(encoding='utf-8')

        for what in ('invite-sent-filter', 'fav-sent-filter', 'mail-sent-filter'):
            assert f'data-slot="{what}"' in base, f'полосы {what} нет в разметке'

    def _page(self) -> str:
        import pathlib

        return (pathlib.Path(__file__).resolve().parents[1]
                / 'data/uvicorn/template/admin/dating/profile.html'
                ).read_text(encoding='utf-8')


# ── Приватное ────────────────────────────────────────────────────────────────
#
# ⚠ Помощники — **под проверками**, как в `test_invite_delivery.py`: правило проекта
# «приватное ниже публичного» действует и в сюите. Читают файл сверху, и начинать
# чтение с механики набора данных значит откладывать то, ради чего файл открыли.

async def _fill(girls: list, men: tuple, mails: list, tag: str) -> None:
    """Набор данных журнала. ⚠ Отдельной функцией, чтобы уборка стояла `finally`."""
    async with mysql_get_db_async() as db:
        # ⚠⚠ Шаблон рассылки — **свой у каждой девушки**, и это не прихоть теста:
        # ключ `uq_mailing_once` держит правило «один шаблон уходит человеку ровно
        # раз, кем бы ни была девушка» (два одинаковых письма от двух незнакомок
        # выдают агентство). Один шаблон на обеих здесь просто не вставился бы.
        for step in range(2):
            await db.execute(
                'INSERT INTO mail (site_id, name, body, enabled)'
                " VALUES (%s, %s, 'Привет!', 1)",
                (STAND_SITE, f'tpl{step}-{tag}'))
            await db.execute('SELECT LAST_INSERT_ID() AS id')
            mails.append(int((await db.fetchone())['id']))

        for girl, mail_id in zip(girls, mails):
            for uid in men:
                await db.execute(
                    'INSERT INTO favorite (site_id, profile_id, uid, state)'
                    " VALUES (%s, %s, %s, 'done')", (STAND_SITE, girl, uid))
                await db.execute(
                    'INSERT INTO invite (site_id, profile_id, uid, name, body,'
                    " status, sent_at) VALUES (%s, %s, %s, 'Man', 'зову',"
                    " 'sent', NOW())", (STAND_SITE, girl, uid))
                await db.execute(
                    'INSERT INTO mailing (site_id, profile_id, mail_id, uid, name,'
                    " body, status, sent_at) VALUES (%s, %s, %s, %s, 'Man',"
                    " 'письмо', 'sent', NOW())",
                    (STAND_SITE, girl, mail_id, uid))
                await db.execute(
                    'INSERT INTO favorite_mail (site_id, profile_id, uid, name,'
                    " body, status, sent_at) VALUES (%s, %s, %s, 'Man',"
                    " 'пристаю', 'sent', NOW())", (STAND_SITE, girl, uid))
        await db.connection.commit()


async def _wipe(girls: list, mails: list) -> None:
    """Уборка за собой — всего, что успело завестись."""
    async with mysql_get_db_async() as db:
        for girl in girls:
            for table in ('invite', 'mailing', 'favorite_mail', 'favorite'):
                await db.execute(f'DELETE FROM {table} WHERE profile_id = %s',
                                 (girl,))
            await db.execute('DELETE FROM profile WHERE id = %s', (girl,))
        for mail_id in mails:
            await db.execute('DELETE FROM mail WHERE id = %s', (mail_id,))
        await db.connection.commit()


async def _ask(client, path, extra, **pick) -> dict:
    params = {'site_id': STAND_SITE, 'per': 100, **extra, **pick}

    return (await client.get(path, params=params)).json()


def _mine(got: dict, tag: str) -> list:
    """Только наши строки: журнал сайта общий, и в нём лежит чужое."""
    return [row for row in got['result'] if tag in str(row['uid'])]
