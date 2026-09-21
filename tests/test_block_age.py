"""Отказ «не подходите по возрасту» — такой же навсегда, как «он закрылся».

## ⚠⚠ Что чинится

Сайт отвечает на попытку написать письмо:

    403 "You do not match the criteria of age to send messages to this user
         (ERROR_MESSAGE_AGE_CRITERIA)"

Человек выставил, от женщин какого возраста готов получать письма, и эта девушка
в рамки не попала. Для панели это был обычный отказ: письмо оставалось с причиной,
круг избранных возвращался к нему следующим заходом и снова платил вызовом LLM за
письмо, которое сайт не примет.

## ⚠ Это про пару, а не про человека

Отказ говорит «**ты** не подходишь»: другая наша девушка, чей возраст в рамки
попадает, ему напишет. Поэтому код ложится в ту же таблицу пар, что и «он
закрылся», и ничего в её устройстве не меняет.

## ⚠ Примета — настройкой, а не константой

Коды придумывает сайт. Оба наших работают на одном движке, и его коды стоят
умолчанием; сайт на другом скажет это иначе, и примета правится на странице
«Сайты».
"""

import inspect
import uuid

import pytest

from mysql_.mysql_ import mysql_get_db_async

from src.dating.block import BLOCK_DENY_DEFAULT, block_deny

pytestmark = pytest.mark.mail

STAND_SITE = 999_343

AGE_REFUSAL = ('сайт не дал разрешения на отправку: сайт просит подождать: '
               'слишком часто: You do not match the criteria of age to send '
               'messages to this user (ERROR_MESSAGE_AGE_CRITERIA) '
               '(/messages/check-send/635295 → 403)')

DENY_REFUSAL = ('{"IsSuccess":false,"Reason":"ERROR_MESSAGE_USER_DENY",'
                '"Message":"User ignore messages from you"}')


class TestTheAgeRefusalIsPermanent:
    """⚠⚠ Главное: по такому отказу пара попадает в «не пишем»."""

    async def test_the_age_code_is_recognised(self):
        assert await block_deny(STAND_SITE, AGE_REFUSAL), \
            'отказ по возрасту не признан стойким — круг будет платить за него вечно'

    async def test_the_old_code_still_works(self):
        """⚠ Обратная сторона: добавив вторую примету, нельзя потерять первую."""
        assert await block_deny(STAND_SITE, DENY_REFUSAL)

    async def test_an_ordinary_refusal_is_not_permanent(self):
        """⚠⚠ Часовой лимит получателя — **не** повод перестать писать навсегда.

        Он исчерпывается всеми, кто пишет этому человеку, и отпускает через час.
        Занеси мы такую пару в «не пишем», мы потеряли бы её из-за чужой
        активности — и молча.
        """
        hourly = ('сайт просит подождать: слишком часто: The user\'s hourly '
                  'incoming message limit has been exceeded '
                  '(ERROR_MESSAGE_USER_HOUR_IN_LIMIT) (→ 403)')

        assert not await block_deny(STAND_SITE, hourly), \
            'часовой лимит принят за отказ навсегда'

    async def test_a_plain_error_is_not_permanent(self):
        assert not await block_deny(STAND_SITE, 'сайт не отвечает: таймаут')
        assert not await block_deny(STAND_SITE, '')


class TestBothMarksAreTheDefault:
    """⚠ Умолчание несёт оба кода — иначе настройку пришлось бы править руками
    на каждом сайте."""

    def test_the_default_carries_both(self):
        assert 'ERROR_MESSAGE_USER_DENY' in BLOCK_DENY_DEFAULT
        assert 'ERROR_MESSAGE_AGE_CRITERIA' in BLOCK_DENY_DEFAULT

    def test_the_registry_agrees_with_the_code(self):
        """⚠⚠ Умолчаний **два места**: константа модуля и реестр настроек сайта.

        Разойдись они — страница «Сайты» показывала бы одно, а работа шла бы по
        другому, и никакой ошибки при этом не случилось бы.
        """
        from src.uvicorn.routers.dating.site_ import SITE_KNOWN

        assert SITE_KNOWN['block_deny_mark']['default'] == BLOCK_DENY_DEFAULT


class TestThePoolSkipsThem:
    """⚠⚠ Ради этого всё и затевалось: такие пары не попадают в пул фаворитов."""

    @pytest.fixture
    async def pair(self, auth_client):
        """Анкета и запрещённая пара к ней."""
        made = (await auth_client.post('/api/dating/profile', json={
            'site_id': STAND_SITE,
            'login': f'blockage-{uuid.uuid4().hex[:8]}@example.com',
            'password': 'x'})).json()['result']
        uid = f'u{uuid.uuid4().hex[:8]}'

        yield int(made), uid

        async with mysql_get_db_async() as db:
            await db.execute('DELETE FROM block WHERE profile_id = %s', (int(made),))
            await db.connection.commit()

        await auth_client.delete(f'/api/dating/profile/{made}')

    async def test_the_selection_excludes_blocked_pairs(self, pair):
        """⚠ Условием **в самом запросе**, а не отсевом после: список уходит в
        потолок захода, и запрещённые пары съедали бы места живых."""
        code = inspect.getsource(
            __import__('src.dating.favorite_mail', fromlist=['_quiet'])._quiet)

        assert 'FROM block b' in code, \
            'набор избранных снова не смотрит в «не пишем»'
        assert 'b.profile_id = f.profile_id AND b.uid = f.uid' in code


class TestTheTabShowsThem:
    """⚠ Подвкладка «Не пишем» есть и на «Фаворитах», а не только на «Чате»."""

    def test_the_favorites_tab_has_the_pane(self):
        page = open('data/uvicorn/template/admin/dating/profile/site/_base.html',
                    encoding='utf-8').read()

        assert 'data-fav-tab="block"' in page, 'подвкладки нет'
        assert 'data-fav-pane="block"' in page, 'панели нет'

    def test_both_panes_share_one_list(self):
        """⚠⚠ Своей копии данных у вкладки фаворитов нет: запрет ложится на пару,
        а не на канал, и две таблицы разошлись бы в первый же день."""
        page = open('data/uvicorn/template/admin/dating/profile.html',
                    encoding='utf-8').read()

        assert "BLOCK_SLOTS = ['invite-block', 'fav-block']" in page
        assert "FAV_TABS = ['talk', 'sent', 'talks', 'block']" in page

    def test_the_age_reason_is_named_apart(self):
        """⚠ «Закрылся в почте» про возраст — неправда: он не закрывался, а
        выставил рамки. Человеку эта разница нужна: первое отменяется кнопкой,
        второе не изменится, пока анкете не поправят возраст."""
        page = open('data/uvicorn/template/admin/dating/profile.html',
                    encoding='utf-8').read()

        assert 'ERROR_MESSAGE_AGE_CRITERIA' in page
        assert 'не подходим по возрасту' in page
