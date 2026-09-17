"""Чёрный список: кому эта анкета больше не пишет.

⚠ **Написано по живому отказу.** Инвайт от Nadezhda748852 к DDominic909801:

    200 {"IsSuccess":false,"Reason":"ERROR_MESSAGE_USER_DENY",
         "Message":"User ignore messages from you"}

Собеседник закрылся от анкеты. До этой правки отказ был обычным: реплика
оставалась в пуле с причиной, круг через сутки готовил следующую — ценой вызова
LLM, — и так до бесконечности.

Проверяется здесь то, что легко сломать незаметно:

* **примета отказа читается из настройки сайта**, а не зашита в код;
* **запрет — про пару**: другая наша девушка звать его вправе;
* **черновики уходят сразу** — иначе круг отправки честно попробует каждый;
* **ответ на его письмо остаётся**: запрет отвечает на «писать ли первыми».
"""

import uuid

import pytest

from mysql_.mysql_ import mysql_get_db_async

from src.dating import block
from src.dating.block import (BLOCK_DENY_DEFAULT,
                                     BLOCK_POOLS, block_add,
                                     block_deny, block_drop,
                                     block_pairs, block_rows)
from src.dating.invite import _cards as invite_cards
from src.dating.mailing import _pairs as mailing_pairs

pytestmark = pytest.mark.chat

STAND_SITE = 999_343
UID = 'zz-block'

# Ответ сайта целиком — как он и приходит.
DENY = ('сайт ответил 200: {"IsSuccess":false,"Reason":"ERROR_MESSAGE_USER_DENY",'
        '"Message":"User ignore messages from you"}')


@pytest.fixture
async def girl(auth_client):
    """Анкета стенда. Убирает за собой запреты и пулы."""
    profile_id = int((await auth_client.post('/api/dating/profile', json={
        'site_id': STAND_SITE,
        'login': f'blk-{uuid.uuid4().hex[:8]}@example.com',
        'password': 'x'})).json()['result'])

    yield profile_id

    async with mysql_get_db_async() as db:
        await db.execute('DELETE FROM block WHERE profile_id = %s', (profile_id,))
        for table in BLOCK_POOLS:
            await db.execute(f'DELETE FROM {table} WHERE profile_id = %s', (profile_id,))
        await db.execute('DELETE FROM profile WHERE id = %s', (profile_id,))
        await db.connection.commit()


class TestDeny:
    """Что считается отказом «он закрылся»."""

    async def test_the_site_code_is_recognised(self):
        assert await block_deny(STAND_SITE, DENY) is True

    async def test_an_ordinary_refusal_is_not(self):
        """⚠ Обычный отказ — временный, и запрет по нему был бы потерей человека."""
        assert await block_deny(
            STAND_SITE, 'сайт ответил 200: {"IsSuccess":false,'
                        '"Reason":"ERROR_TOO_MANY_REQUESTS"}') is False

    async def test_an_empty_answer_is_not(self):
        assert await block_deny(STAND_SITE, '') is False

    async def test_the_mark_comes_from_the_site_setting(self, monkeypatch):
        """⚠ Примету придумывает сайт: коды отказа — его словарь, не наш.

        Умолчание тут лишь потому, что оба наших сайта на одном движке.
        """
        async def _said(site_id, key, default=''):
            assert key == block.BLOCK_DENY_SETTING

            return 'HE_IS_DONE_WITH_YOU'

        import src.uvicorn.routers.dating.site_ as site_router
        monkeypatch.setattr(site_router, 'site_setting', _said)

        assert await block_deny(STAND_SITE, DENY) is False
        assert await block_deny(STAND_SITE, 'oops HE_IS_DONE_WITH_YOU') is True

    def test_the_default_is_the_code_we_saw_live(self):
        assert BLOCK_DENY_DEFAULT == 'ERROR_MESSAGE_USER_DENY'


class TestAdd:
    """Что происходит, когда запрет заносят."""

    async def test_the_pair_is_remembered(self, girl):
        assert await block_add(STAND_SITE, girl, UID, 'Dominic', DENY) is True
        assert (girl, UID) in await block_pairs(STAND_SITE)

    async def test_the_second_time_is_the_same_record(self, girl):
        await block_add(STAND_SITE, girl, UID, 'Dominic', DENY)

        assert await block_add(STAND_SITE, girl, UID, 'Dominic', DENY) is False
        assert (await block_rows(STAND_SITE, girl))['total'] == 1

    async def test_drafts_of_the_pair_are_swept(self, girl):
        """⚠ Иначе круг отправки честно попробует каждый и получит тот же отказ:
        запрет откладывал бы работу вместо того, чтобы её отменить."""
        for table in BLOCK_POOLS:
            await _draft(table, girl, UID)

        await block_add(STAND_SITE, girl, UID, 'Dominic', DENY)

        for table in BLOCK_POOLS:
            assert await _count(table, girl) == 0, f'{table}: черновик остался'

    async def test_what_already_went_is_not_touched(self, girl):
        """Ушедшее — история, и она правдива: письмо он получил до того, как закрылся."""
        await _draft('mailing', girl, UID, status='sent')

        await block_add(STAND_SITE, girl, UID, 'Dominic', DENY)

        assert await _count('mailing', girl) == 1

    async def test_replies_are_not_a_pool_here(self):
        """⚠ Ответ на его письмо уходит и после запрета: он написал сам.

        Запрет отвечает на вопрос «писать ли **первыми**», а входящее письмо этот
        вопрос снимает. Молчать в ответ на прямое обращение хуже.
        """
        assert 'mail_reply' not in BLOCK_POOLS


class TestTheRowHasBothFaces:
    """⚠ Строка рисует **обе стороны** тем же видом, что «Доставка» и «Диалоги».

    Тому виду (`talkSide`) нужны три поля на сторону: имя, лицо, номер на сайте.
    Лица девушки ручка сперва не отдавала вовсе — та оставалась буквой в кружке,
    хотя её фотография лежала в той же строке запроса.
    """

    async def test_the_girl_comes_with_her_face(self, girl, auth_client):
        await _profile_card(girl, name='Диана', photo='https://site/di.jpg',
                            site_uid='643694')
        await block_add(STAND_SITE, girl, UID, 'Dominic', DENY)

        one = (await block_rows(STAND_SITE, girl))['rows'][0]

        assert one['profile_name'] == 'Диана'
        assert one['profile_photo'] == 'https://site/di.jpg'
        assert one['profile_uid'] == '643694'

    async def test_a_profile_without_a_card_is_not_a_failure(self, girl):
        """Карточки с сайта может не быть — тогда имя из логина, лицо пустое."""
        await block_add(STAND_SITE, girl, UID, 'Dominic', DENY)

        one = (await block_rows(STAND_SITE, girl))['rows'][0]

        assert one['profile_name'] and one['profile_photo'] == ''


class TestPairsObeyIt:
    """Наборы пулов запрет соблюдают — и оба одинаково."""

    GIRLS = [{'id': 849, 'login': 'a@example.com'}, {'id': 850, 'login': 'b@example.com'}]
    PEOPLE = [{'uid': '111', 'name': 'Brian'}]
    TEMPLATES = [{'id': 7}]

    def test_invites_skip_the_blocked_girl(self):
        """⚠ Запрет отнимает **анкету**, а не человека: вторая его не знает."""
        got = list(invite_cards(self.GIRLS, self.PEOPLE, set(), set(), {(849, '111')}))

        assert [girl['id'] for girl, _ in got] == [850]

    def test_invites_skip_him_when_everyone_is_blocked(self):
        blocked = {(girl['id'], '111') for girl in self.GIRLS}

        assert list(invite_cards(self.GIRLS, self.PEOPLE, set(), set(), blocked)) == []

    def test_mailing_skips_the_blocked_girl(self):
        got = list(mailing_pairs(self.GIRLS, self.PEOPLE, self.TEMPLATES, set(),
                                 {(849, '111')}))

        assert [girl['id'] for girl, _, _ in got] == [850]

    def test_mailing_skips_him_when_everyone_is_blocked(self):
        blocked = {(girl['id'], '111') for girl in self.GIRLS}

        assert list(mailing_pairs(self.GIRLS, self.PEOPLE, self.TEMPLATES, set(),
                                  blocked)) == []

    def test_no_blocks_changes_nothing(self):
        """Умолчание — пустой набор: старые вызовы работают как работали."""
        assert len(list(invite_cards(self.GIRLS, self.PEOPLE, set(), set()))) == 1
        assert len(list(mailing_pairs(self.GIRLS, self.PEOPLE, self.TEMPLATES,
                                      set()))) == 1


class TestFavoriteMailObeysIt:
    """У писем избранным запрет стоит условием **в самом запросе**."""

    def test_the_query_asks_about_the_block(self):
        import inspect

        from src.dating import favorite_mail

        code = inspect.getsource(favorite_mail._quiet)

        assert 'block' in code, 'затихшие разговоры не смотрят на запрет'


class TestThePageOfTheList:
    """⚠ Список копится сам и не убывает: запрет не истекает.

    Каждый отказ сайта добавляет строку, и отдавать его целиком значит однажды
    отдать всё подряд — тем медленнее, чем дольше панель работает.
    """

    async def test_the_count_is_the_whole_list(self, girl):
        for n in range(3):
            await block_add(STAND_SITE, girl, f'{UID}-{n}', 'Dominic', DENY)

        got = await block_rows(STAND_SITE, girl, page=1, per=2)

        assert got['total'] == 3 and got['pages'] == 2
        assert len(got['rows']) == 2

    async def test_the_second_page_is_other_rows(self, girl):
        for n in range(3):
            await block_add(STAND_SITE, girl, f'{UID}-{n}', 'Dominic', DENY)

        first = await block_rows(STAND_SITE, girl, page=1, per=2)
        second = await block_rows(STAND_SITE, girl, page=2, per=2)

        assert {one['id'] for one in first['rows']} \
            & {one['id'] for one in second['rows']} == set()

    async def test_a_page_past_the_end_comes_back(self, girl):
        """⚠ Правим, а не отвергаем: запрет могли снять с последней страницы."""
        await block_add(STAND_SITE, girl, UID, 'Dominic', DENY)

        got = await block_rows(STAND_SITE, girl, page=99, per=2)

        assert got['page'] == got['pages'] and got['rows']

    async def test_an_empty_list_is_one_page(self, girl):
        got = await block_rows(STAND_SITE, girl)

        assert got == {'rows': [], 'page': 1, 'pages': 1, 'total': 0}


class TestDrop:
    """Снять запрет может только человек."""

    async def test_it_goes_away(self, girl):
        await block_add(STAND_SITE, girl, UID, 'Dominic', DENY)
        one = (await block_rows(STAND_SITE, girl))['rows'][0]

        assert await block_drop(int(one['id'])) is True
        assert await block_pairs(STAND_SITE) == set() \
            or (girl, UID) not in await block_pairs(STAND_SITE)

    async def test_dropping_what_is_gone_says_so(self):
        assert await block_drop(999_999_999) is False


async def _profile_card(profile_id: int, name: str, photo: str,
                        site_uid: str) -> None:
    """Кладёт анкету с сайта: из неё берутся имя, лицо и номер девушки."""
    import json

    async with mysql_get_db_async() as db:
        await db.execute('UPDATE profile SET profile = %s WHERE id = %s',
                         (json.dumps({'name': name, 'photo': photo,
                                      'site_uid': site_uid}, ensure_ascii=False),
                          int(profile_id)))
        await db.connection.commit()


async def _draft(table: str, profile_id: int, uid: str, status: str = 'draft') -> None:
    """Черновик в пуле. ⚠ У инвайтов нет `mail_id`, у писем он есть — но пустой
    столбец допустим, и тест не про него."""
    async with mysql_get_db_async() as db:
        await db.execute(
            f'INSERT INTO {table} (site_id, profile_id, uid, name, body, status)'
            ' VALUES (%s, %s, %s, %s, %s, %s)',
            (STAND_SITE, int(profile_id), uid, 'Dominic', 'привет', status))
        await db.connection.commit()


async def _count(table: str, profile_id: int) -> int:
    async with mysql_get_db_async() as db:
        await db.execute(f'SELECT COUNT(*) AS n FROM {table} WHERE profile_id = %s',
                         (int(profile_id),))

        return int((await db.fetchone())['n'])
