"""«Фавориты → Диалоги»: галочка «с ответами» — только те, кто хоть раз ответил.

Список показывал **все** пары, с кем письма были, — включая те, где писали только
мы. На живом сайте это 27 пар из 29: девушка отправила два-четыре письма, человек не
ответил ни на одно, и разговора нет — а карточка в списке есть, и такие карточки
вытесняли настоящие разговоры.

## ⚠⚠ То же правило, что у диалогов чата, но с выключателем

У чата оно зовётся «диалог начинается с ответа» (`chat_dialogs`) и включено
**всегда**: ушедшие инвайты видны на своей подвкладке «Доставка». Здесь у правила
есть галочка, потому что вопрос «кому мы писали, а он молчит» задают и самому
списку — но задают редко, поэтому умолчание отсекает.

⚠ **Проверяется по входящим письмам**, а не по длине переписки: у молчащей пары
`letters` как раз два-четыре, и отсев «по короткой переписке» оставил бы её в списке.

## ⚠⚠ Отбор — на сервере, в общем куске SQL

Кусок один на счёт и на список. Поставь условие только списку — пейджер считал бы
страницы по всем парам, и последние выходили бы пустыми. Здесь это и проверяется:
счёт обязан сходиться с длиной, пока страница одна.
"""

import pathlib
import re
import uuid

import pytest

from mysql_.mysql_ import mysql_get_db_async

from src.uvicorn.routers.dating.favorite_mail import favorite_mail_threads

pytestmark = pytest.mark.mail

# Стенд у соседа: заведение анкеты сверяет номер, а круги воркера про него не
# знают — его нет в `site_`.
STAND_SITE = 999_343

ROOT = pathlib.Path(__file__).resolve().parents[1]
BASE = ROOT / 'data/uvicorn/template/admin/dating/profile/site/_base.html'
PAGE = ROOT / 'data/uvicorn/template/admin/dating/profile.html'


@pytest.fixture
async def pairs(auth_client):
    """Две пары: одна ответила, другая молчит.

    ⚠ Обеим нужна звёздочка (`favorite`) — без неё пара в список не идёт вовсе.
    Разница только в письмах:

        ответивший   mail_in (его письмо) + наше ушедшее
        молчащий     только наши ушедшие — их три, и это важно: отсев идёт по
                     входящим, а не по короткой переписке

    ⚠ `state = 'done'`: тем же набором состояний список отбирает избранных
    (`FAVORITE_STATES`), и `fail` в него не входит.
    """
    girl = int((await auth_client.post('/api/dating/profile', json={
        'site_id': STAND_SITE,
        'login': f'ans-{uuid.uuid4().hex[:8]}@example.com',
        'password': 'x'})).json()['result'])

    said = f'said-{uuid.uuid4().hex[:8]}'
    mute = f'mute-{uuid.uuid4().hex[:8]}'

    async with mysql_get_db_async() as db:
        for uid in (said, mute):
            await db.execute(
                'INSERT INTO favorite (profile_id, site_id, uid, state)'
                " VALUES (%s, %s, %s, 'done')", (girl, STAND_SITE, uid))

        # Ответивший: его письмо к нам.
        # ⚠ `message_id` обязателен — номер письма на сайте, `NOT NULL` без умолчания.
        await db.execute(
            'INSERT INTO mail_in (site_id, profile_id, message_id, uid, body,'
            ' date_post) VALUES (%s, %s, %s, %s, %s, NOW() - INTERVAL 10 MINUTE)',
            (STAND_SITE, girl, uuid.uuid4().int % 10 ** 12, said, 'hello'))
        # И наше ему — чтобы пары отличались только его письмом, а не нашими.
        await db.execute(
            'INSERT INTO favorite_mail (site_id, profile_id, uid, name, body,'
            " status, sent_at) VALUES (%s, %s, %s, 'Said', 'hi', 'sent',"
            ' NOW() - INTERVAL 20 MINUTE)', (STAND_SITE, girl, said))

        # Молчащий: три наших письма и ни одного его.
        for step in range(3):
            await db.execute(
                'INSERT INTO favorite_mail (site_id, profile_id, uid, name, body,'
                " status, sent_at) VALUES (%s, %s, %s, 'Mute', 'hi', 'sent',"
                ' NOW() - INTERVAL %s MINUTE)',
                (STAND_SITE, girl, mute, 30 + step))
        await db.connection.commit()

    yield {'girl': girl, 'said': said, 'mute': mute}

    async with mysql_get_db_async() as db:
        await db.execute('DELETE FROM favorite WHERE profile_id = %s', (girl,))
        await db.execute('DELETE FROM mail_in WHERE profile_id = %s', (girl,))
        await db.execute('DELETE FROM favorite_mail WHERE profile_id = %s', (girl,))
        await db.execute('DELETE FROM profile WHERE id = %s', (girl,))
        await db.connection.commit()


async def _uids(pairs, **kw) -> set:
    """Кто попал в список у этой анкеты."""
    got = await favorite_mail_threads(site_id=STAND_SITE,
                                      profile_id=pairs['girl'], user=None, **kw)

    return {row['uid'] for row in got['result']}


class TestTheFilter:
    """Кого показываем и кого нет."""

    async def test_the_silent_one_is_hidden_by_default(self, pairs):
        """⚠ **Умолчание — «с ответами»**, и это проверяется вызовом без довода.

        Пусти умолчание в «показывать всех» — галочка в панели стояла бы поднятой,
        а список приходил бы полным: человек увидел бы молчащих при снятом на вид
        отборе и решил, что галочка не работает.
        """
        got = await _uids(pairs)

        assert pairs['said'] in got, 'ответивший пропал из списка'
        assert pairs['mute'] not in got, 'молчащий показан при умолчании'

    async def test_the_silent_one_shows_when_asked(self, pairs):
        got = await _uids(pairs, answered=0)

        assert {pairs['said'], pairs['mute']} <= got, \
            'со снятой галочкой молчащий всё равно скрыт'

    async def test_the_answered_one_is_never_hidden(self, pairs):
        """Ответивший виден при любом значении: он и есть разговор."""
        assert pairs['said'] in await _uids(pairs, answered=1)
        assert pairs['said'] in await _uids(pairs, answered=0)


class TestItCountsHisLetters:
    """⚠⚠ Отсев по **его письмам**, а не по длине переписки."""

    async def test_the_silent_pair_has_letters_of_ours(self, pairs):
        """У молчащего писем три — и он всё равно скрыт.

        Это и отделяет правило «он ответил» от похожего, но неверного «переписка
        короткая»: наших писем может быть сколько угодно, разговором они её не
        делают.
        """
        got = await favorite_mail_threads(site_id=STAND_SITE,
                                         profile_id=pairs['girl'],
                                         answered=0, user=None)
        mute = next(row for row in got['result'] if row['uid'] == pairs['mute'])

        assert mute['letters'] == 3, mute['letters']
        assert pairs['mute'] not in await _uids(pairs, answered=1)


class TestTheCountMatchesTheList:
    """⚠⚠ Счёт и список — один кусок SQL, иначе последняя страница выходит пустой."""

    async def test_with_the_filter(self, pairs):
        got = await favorite_mail_threads(site_id=STAND_SITE,
                                         profile_id=pairs['girl'],
                                         answered=1, user=None)

        assert got['pages'] == 1, 'пара не уместилась на страницу — проверка не та'
        assert got['total'] == len(got['result']) == 1, \
            f"счёт {got['total']} против списка {len(got['result'])}"

    async def test_without_the_filter(self, pairs):
        got = await favorite_mail_threads(site_id=STAND_SITE,
                                         profile_id=pairs['girl'],
                                         answered=0, user=None)

        assert got['pages'] == 1
        assert got['total'] == len(got['result']) == 2, \
            f"счёт {got['total']} против списка {len(got['result'])}"


class TestTheMarkup:
    """Галочка стоит в шапке разговора и подписана понятно."""

    def _pane(self) -> str:
        text = BASE.read_text(encoding='utf-8')
        found = re.search(r'<div class="fav-pane off" data-fav-pane="talks">.*?'
                          r'(?=<div class="fav-pane off" data-fav-pane="block">)',
                          text, re.S)

        assert found, 'панели «Диалоги» фаворитов не нашлось'

        return found.group(0)

    def test_the_checkbox_is_there(self):
        pane = self._pane()

        assert 'data-slot="fav-answered"' in pane, 'галочки нет'
        assert 'type="checkbox"' in pane

    def test_it_says_with_answers(self):
        visible = re.sub(r'\{#.*?#\}', '', self._pane(), flags=re.S)

        assert 'с ответами' in visible, 'подписи «с ответами» не видно'

    def test_it_stands_in_the_thread_head(self):
        """⚠ В шапке **разговора**, а не над списком слева.

        Вопрос «а это вообще разговор?» возникает при чтении переписки: слева одна
        карточка за другой, и молчание собеседника видно только справа.
        """
        pane = self._pane()
        head = pane[pane.index('data-slot="fav-talk-who"'):
                    pane.index('data-slot="fav-talk-thread"')]

        assert 'data-slot="fav-answered"' in head, \
            'галочка не в шапке колонки разговора'


class TestThePageSide:
    """Страница: умолчание, отбор в запросе, сброс страницы и честная пустота."""

    def _func(self, name: str) -> str:
        page = PAGE.read_text(encoding='utf-8')
        found = re.search(r'function ' + name + r'\(\w*\) \{.*?\n    \}', page, re.S)

        assert found, f'функция {name} не нашлась'

        return found.group(0)

    def test_the_default_is_on(self):
        """⚠⚠ Пустой `localStorage` обязан означать «включено».

        Храни страница «1» как признак включённости — первый заход нового человека
        показывал бы список со снятой галочкой, то есть не то, что задумано. Поэтому
        в хранилище кладётся только **отказ**.
        """
        page = PAGE.read_text(encoding='utf-8')

        assert "getItem(FAV_ANSWERED_KEY) !== '0'" in page, \
            'умолчание галочки задано не через отказ — оно может оказаться снятым'

    def test_the_filter_goes_into_the_request(self):
        """⚠ Отбирает сервер: отбери страница присланное, «с ответами» означало бы
        «те из них, что попали на открытую страницу»."""
        body = self._func('favTalksLoad')

        assert 'answered=' in body, 'отбор не уезжает в запрос'

    def test_toggling_resets_the_page(self):
        """⚠⚠ Страница — в первую, и выбор долой.

        Отбор меняет длину списка: седьмая страница «всех» почти наверняка пуста,
        когда молчащих отсекли. А открытая переписка могла из списка уйти вовсе.
        """
        page = PAGE.read_text(encoding='utf-8')
        found = re.search(r"data-slot=\"fav-answered\"\]'\)\.forEach.*?\}\)\);",
                          page, re.S)

        assert found, 'привязка галочки не нашлась'
        body = found.group(0)

        assert 'favTalksPage = 0' in body, 'страница не сбрасывается'
        assert "favTalkOpen = ''" in body, 'выбор не сбрасывается'

    def test_the_checkbox_is_painted_from_state(self):
        """⚠ Состояние переживает перезагрузку — галочку надо поднять при отрисовке.

        Иначе список отобран, а галочка снята: читается как поломка.
        """
        body = self._func('favTalksShow')

        assert 'favAnsweredPaint()' in body, 'галочка не рисуется по состоянию'

    def test_the_empty_list_explains_the_filter(self):
        """⚠⚠ Пустота под галочкой — **другая** беда, и говорить о ней надо иначе.

        Прежний текст («сюда попадают те, с кем письма уже были») при поднятой
        галочке прямо врёт: письма как раз были, просто все наши. Человек читал бы
        это как «переписок нет вовсе» и пошёл бы искать поломку.
        """
        body = self._func('favTalksPaint')

        assert 'Переписок с ответами нет' in body, \
            'пустота под галочкой объяснена тем же текстом, что и полное отсутствие'
        # ⚠ Пробелы и переносы выкидываем: фраза в коде склеена из строк, и
        # проверка по точному написанию падала бы от переноса, а не от смысла.
        assert 'Снимитегалочку' in re.sub(r'\s+|\+|\'', '', body), \
            'не сказано, чем вернуть молчащих'
