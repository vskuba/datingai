"""Подсказка карточки: сколько и какой почты анкета уже разослала за 24 часа.

Жёлтый чип карточки («ждёт: …») отвечает на «почему не уходит»; рядом человек
решает другое: **много ли эта анкета уже написала** — одно дело анкета молчит,
другое — у неё за сутки двадцать ушло и её черновик просто в очереди. Числа
кладутся в подсказку чипа, а не на полосу: полоса — про причину, в обрезанную
полосу влезло бы либо одно либо другое.

Считает `send_card_day_sent` — правило то же, что у нормы круга
(`round_sent_day`: `status = 'sent'`, время `sent_at`), только окно скользящие
24 часа, и считается **одним `UNION` по анкетам вернувшейся страницы**, а не
запросом на карточку.

Проверяется в трёх этажах: сам счётчик (три рода, черновики и отказы не в
счёт, вчерашнее не в счёт, чужие анкеты не смешиваются), списки через HTTP
одноразового сервера (`day_sent` доезжает до строк обоих пулов, чьи карточки
носят чип), и страница: подсказка несёт числа и строку «ответов на письма»
не показывает, когда ответов не было.

⚠⚠ **Наружу ничего не уходит:** выдуманный сайт, строк которого нет в таблице
сайтов, — круги воркера его не видят (приём `test_send_rotate.py`).
"""

import re
import shutil
import subprocess
import tempfile
import uuid
from pathlib import Path

import pytest

from mysql_.mysql_ import mysql_get_db_async

from src.dating.send_round import send_card_day_sent

pytestmark = pytest.mark.mail

# Сайт для писем — выдуманный: своей строки в таблице сайтов нет, живая
# отправка и соседние тесты не пересекаются с нашими строками.
SITE = 999_761


async def _gone(profile_id: int, table: str, *, status: str = 'sent',
                minutes: int = 30) -> None:
    """Строка почты анкеты в заданной таблице.

    Args:
        profile_id: от какой анкеты.
        table: `mailing`, `favorite_mail` или `mail_reply`.
        status: `sent`, `failed` или `draft`.
        minutes: сколько минут назад письмо «было»; у не-ушедших `sent_at`
            остаётся NULL — так его пишет отправка.
    """
    uid = uuid.uuid4().hex[:12]
    gone_at = ('NOW() - INTERVAL %s MINUTE' if status == 'sent' else 'NULL')
    tail = [status] + ([minutes] if status == 'sent' else [])

    async with mysql_get_db_async() as db:
        if table == 'mail_reply':
            # Ответ живёт над входящим письмом — без него внешнего ключа
            # не заполнить.
            await db.execute(
                'INSERT INTO mail_in (site_id, profile_id, message_id, uid,'
                ' name, body, date_post) VALUES (%s, %s, %s, %s, \'Her\','
                " 'письмо', NOW() - INTERVAL %s MINUTE)",
                (SITE, profile_id, uuid.uuid4().int % 10 ** 12, uid, minutes))
            await db.execute('SELECT LAST_INSERT_ID() AS id')
            in_id = int((await db.fetchone())['id'])
            await db.execute(
                'INSERT INTO mail_reply (site_id, profile_id, mail_in_id,'
                ' uid, name, body, status, error, sent_at)'
                f" VALUES (%s, %s, %s, %s, 'Him', 'отвечаю', %s, '', {gone_at})",
                (SITE, profile_id, in_id, uid, *tail))
        elif table == 'mailing':
            await db.execute(
                'INSERT INTO mailing (site_id, profile_id, uid, name, body,'
                ' status, error, sent_at)'
                f" VALUES (%s, %s, %s, 'Her', 'рассылаю', %s, '', {gone_at})",
                (SITE, profile_id, uid, *tail))
        else:
            await db.execute(
                'INSERT INTO favorite_mail (site_id, profile_id, uid, name,'
                ' body, status, error, sent_at)'
                f" VALUES (%s, %s, %s, 'Her', 'пристаю', %s, '', {gone_at})",
                (SITE, profile_id, uid, *tail))

        await db.connection.commit()


async def _draft(table: str, profile_id: int) -> int:
    """Черновик в пуле выдуманного сайта — та самая карточка с чипом.

    Returns:
        int: номер письма; список находит его по нему.
    """
    async with mysql_get_db_async() as db:
        await db.execute(
            f'INSERT INTO {table} (site_id, profile_id, uid, name, body,'
            " status, error) VALUES (%s, %s, %s, 'Her', 'Текст письма',"
            " 'draft', '')",
            (SITE, profile_id, uuid.uuid4().hex[:12]))
        await db.connection.commit()
        await db.execute('SELECT LAST_INSERT_ID() AS id')

        return int((await db.fetchone())['id'])


def _row_of(rows: list, letter: int) -> dict:
    """Строка списка по номеру письма."""
    for one in rows:
        if int(one['id']) == int(letter):
            return one

    raise AssertionError(f'письма #{letter} нет в списке: {rows[:2]}')


class TestTheCounterCountsOnlyGone:
    """`send_card_day_sent`: три рода, только ушедшее, только свои анкеты."""

    async def test_three_kinds_land_on_their_own_profile(self, girl_make):
        """Главный случай: свои числа своей анкеты, чужие — чужой.

        У первой — два ушедших рассылки (свежее и 23 часа назад — у границы
        окна, обязано войти), три приставания, один ответ; у второй — только
        рассылка. Черновики, отказы и вчерашнее (за 26 и 30 часов) лежат в
        базах теми же строками и не должны никуда попасть.
        """
        mine = await girl_make('day1')
        theirs = await girl_make('day2')

        for minutes in (40, 23 * 60):
            await _gone(mine, 'mailing', minutes=minutes)
        for minutes in (10, 90, 23 * 60):
            await _gone(mine, 'favorite_mail', minutes=minutes)
        await _gone(mine, 'mail_reply', minutes=15)
        await _gone(theirs, 'mailing', minutes=10)
        await _gone(theirs, 'mailing', minutes=11)

        # Шум, который не смеет попасть в счёт.
        await _gone(mine, 'mailing', status='draft')
        await _gone(mine, 'mailing', status='failed')
        await _gone(mine, 'mailing', minutes=26 * 60)
        await _gone(mine, 'favorite_mail', status='draft')
        await _gone(mine, 'favorite_mail', status='failed')
        await _gone(mine, 'favorite_mail', minutes=40 * 60)
        await _gone(mine, 'mail_reply', status='failed')
        await _gone(mine, 'mail_reply', minutes=30 * 60)

        got = await send_card_day_sent([{'profile_id': mine},
                                        {'profile_id': theirs},
                                        {'profile_id': 0}])

        assert got[mine] == {'mailing': 2, 'favorite_mail': 3,
                             'mail_reply': 1}, got[mine]
        assert got[theirs] == {'mailing': 2, 'favorite_mail': 0,
                               'mail_reply': 0}, 'чужие письма смешались'
        assert 0 not in got, 'пустой номер улетел в запрос'


class TestTheListCarriesTheNumbers:
    """⚠ Путь «список → карточка» — через HTTP одноразового сервера копии:
    подмена сюда не доездает, поле обязана класть сама ручка. Чип рисуется
    только по черновику, поэтому черновик и заводят; `day_sent` лежит на
    строке при любом статусе — решение о показе принимает страница."""

    async def test_the_fav_pool_names_all_three_kinds(self, auth_client,
                                                      girl_make):
        girl = await girl_make('dayh')
        letter = await _draft('favorite_mail', girl)

        await _gone(girl, 'mailing', minutes=20)
        await _gone(girl, 'mailing', minutes=200)
        await _gone(girl, 'favorite_mail', minutes=5)
        await _gone(girl, 'favorite_mail', minutes=600)
        await _gone(girl, 'mail_reply', minutes=45)

        got = await auth_client.get('/api/dating/favorite-mail',
                                    params={'site_id': SITE})
        assert got.status_code == 200, got.text[:200]

        assert _row_of(got.json()['result'], letter)['day_sent'] == {
            'mailing': 2, 'favorite_mail': 2, 'mail_reply': 1}, \
            'числа не доехали до карточки приставания'

    async def test_the_mailing_pool_names_them_too(self, auth_client,
                                                   girl_make):
        """Карточка рассылки носит тот же чип — тот же ответ и у неё."""
        girl = await girl_make('daym')
        letter = await _draft('mailing', girl)

        await _gone(girl, 'mailing', minutes=120)
        await _gone(girl, 'favorite_mail', minutes=15)
        await _gone(girl, 'favorite_mail', minutes=16)

        got = await auth_client.get('/api/dating/mailing',
                                    params={'site_id': SITE})
        assert got.status_code == 200, got.text[:200]

        assert _row_of(got.json()['result'], letter)['day_sent'] == {
            'mailing': 1, 'favorite_mail': 2, 'mail_reply': 0}, \
            'числа не доехали до карточки рассылки'


@pytest.mark.page
class TestTheCardPutsTheNumbersInTheTip:
    """Страница: чип остаётся тем же, а подсказка вырастает числами.

    Функция вырезается из страницы и гоняется под node с подставными `esc` и
    `timeShift` (приём `test_dialogs_paging.py`): проверять её пересказом в
    тесте значило бы стереть не страницу, а саму себя.
    """

    def test_the_tip_grows_numbers_and_keeps_the_silence_out(self):
        node = shutil.which('node')
        if not node:
            pytest.skip('node нет под рукой')

        page = (Path(__file__).resolve().parent.parent
                / 'data/uvicorn/template/admin/dating/profile.html'
                ).read_text()
        found = re.search(r'function mailWaitWhy\(row\) \{.*?function mailCardWait\(row\) \{.*?\n    \}',
                          page, re.S)
        assert found, 'функции карточки не найти на странице'

        probe = """
const esc = (v) => String(v == null ? '' : v)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
const timeShift = (s) => s;
""" + found.group(0) + """

const fail = [];
const title = (html) => html.split('title="')[1].split('">')[0];

let html = mailCardWait({status: 'draft', wait: 'анкета не в сети',
    day_sent: {mailing: 12, favorite_mail: 3, mail_reply: 2}});
if (!html.includes('>ждёт: анкета не в сети</span>'))
    fail.push('полоса сменила текст ради чисел');
if (title(html) !== 'ждёт: анкета не в сети&#10;'
        + 'За 24 ч от анкеты:&#10;рассылка — 12&#10;фаворитам — 3'
        + '&#10;ответов на письма — 2')
    fail.push('подсказка собрана не так: ' + title(html));

html = mailCardWait({status: 'draft', try_at: '2026-01-01 10:30:00',
    day_sent: {mailing: 0, favorite_mail: 0, mail_reply: 0}});
if (!title(html).startsWith('последняя попытка 10:30 — не ушло&#10;'))
    fail.push('числа встали перед прежним текстом: ' + title(html));
if (title(html).includes('ответов'))
    fail.push('тишина показана случившимся событием');
if (!title(html).includes('рассылка — 0'))
    fail.push('нуля в подсказке нет');

if (mailCardWait({status: 'sent',
    day_sent: {mailing: 9, favorite_mail: 9, mail_reply: 9}}) !== '')
    fail.push('чип вырос над ушедшим письмом');

if (title(mailCardWait({status: 'draft', wait: 'w'}))
        .includes('undefined'))
    fail.push('в подсказке влез undefined без данных');

if (fail.length) { console.error(fail.join('; ')); process.exit(1); }
"""

        with tempfile.NamedTemporaryFile('w', suffix='.mjs',
                                         encoding='utf-8') as script:
            script.write(probe)
            script.flush()
            got = subprocess.run([node, script.name], capture_output=True,
                                 text=True, timeout=30)

        assert got.returncode == 0, f'страница врёт в подсказке: {got.stderr}'
