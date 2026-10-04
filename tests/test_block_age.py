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

from src.dating.block import (BLOCK_DENY_DEFAULT, BLOCK_DENY_MAIL_DEFAULT,
                             block_deny)
from src.dating.site_setting import SITE_KNOWN

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

    async def test_the_age_code_is_mail_only(self):
        """⚠⚠ Канал — `mail`, а не «оба».

        Отказ приходит при отправке письма и говорит про письма: человек выставил,
        от женщин какого возраста готов их получать. В чат такой собеседник
        по-прежнему доступен, и гасить им инвайты значило бы запрещать разговор,
        которого никто не запрещал.

        Подтверждено журналом: за всё время «по возрасту» ни разу не пришёл из
        чата, а «он закрылся» — ни разу из почты.
        """
        assert await block_deny(STAND_SITE, AGE_REFUSAL) == 'mail', \
            'отказ по возрасту либо не признан, либо гасит и чат тоже'

    async def test_the_deny_code_covers_both_channels(self):
        """⚠ «Он закрылся» — про девушку, а не про канал: ни реплики, ни письма."""
        assert await block_deny(STAND_SITE, DENY_REFUSAL) == 'all'

    async def test_an_ordinary_refusal_is_not_permanent(self):
        """⚠⚠ Часовой лимит получателя — **не** повод перестать писать навсегда.

        Он исчерпывается всеми, кто пишет этому человеку, и отпускает через час.
        Занеси мы такую пару в «не пишем», мы потеряли бы её из-за чужой
        активности — и молча.
        """
        hourly = ('сайт просит подождать: слишком часто: The user\'s hourly '
                  'incoming message limit has been exceeded '
                  '(ERROR_MESSAGE_USER_HOUR_IN_LIMIT) (→ 403)')

        assert await block_deny(STAND_SITE, hourly) == '', \
            'часовой лимит принят за отказ навсегда'

    async def test_a_plain_error_is_not_permanent(self):
        assert await block_deny(STAND_SITE, 'сайт не отвечает: таймаут') == ''
        assert await block_deny(STAND_SITE, '') == ''


class TestTheMarksAreTwoSettings:
    """⚠⚠ Приметы разведены по каналам — двумя настройками, а не одним списком.

    В одном списке отказ по возрасту гасил бы инвайты: набор чата берёт те же
    строки. Разделение подтверждено журналом, а не выведено из смысла слов.
    """

    def test_each_default_carries_its_own_code(self):
        assert BLOCK_DENY_DEFAULT == 'ERROR_MESSAGE_USER_DENY'
        assert BLOCK_DENY_MAIL_DEFAULT == 'ERROR_MESSAGE_AGE_CRITERIA'

    def test_the_registry_agrees_with_the_code(self):
        """⚠⚠ Умолчаний **два места** у каждой: константа модуля и реестр настроек
        сайта. Разойдись они — страница «Сайты» показывала бы одно, а работа шла бы
        по другому, и никакой ошибки при этом не случилось бы.
        """
        assert SITE_KNOWN['block_deny_mark']['default'] == BLOCK_DENY_DEFAULT
        assert (SITE_KNOWN['block_deny_mail_mark']['default']
                == BLOCK_DENY_MAIL_DEFAULT)


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

    def test_the_favorites_tab_asks_for_mail_only(self):
        """⚠⚠ Вкладка показывает **только отказы почты**.

        Первая редакция показывала весь список, и на «Фаворитах» висели шесть
        сотен пар, закрывшихся в чате: человек пришёл разбираться с доставкой
        писем, а видел чужой канал.

        ⚠ Работу это не меняет — закрывшийся в чате писем тоже не получит. Отбор
        здесь ради чтения.
        """
        page = open('data/uvicorn/template/admin/dating/profile.html',
                    encoding='utf-8').read()

        assert "inviteBlockShow('mail')" in page, 'вкладка снова просит весь список'
        assert "FAV_TABS = ['talk', 'sent', 'talks', 'block']" in page

    def test_the_age_reason_is_named_apart(self):
        """⚠ «Закрылся в почте» про возраст — неправда: он не закрывался, а
        выставил рамки. Человеку эта разница нужна: первое отменяется кнопкой,
        второе не изменится, пока анкете не поправят возраст."""
        page = open('data/uvicorn/template/admin/dating/profile.html',
                    encoding='utf-8').read()

        assert 'ERROR_MESSAGE_AGE_CRITERIA' in page
        assert 'не подходим по возрасту' in page


class TestTheChannelsAreApart:
    """⚠⚠ Отказ по возрасту гасит письма и **не трогает чат**.

    Ради этого и заведён столбец `channel`. Без него набор инвайтов брал бы те же
    строки, и мы запретили бы разговор тому, кто всего лишь не хочет писем от
    женщины этого возраста.
    """

    @pytest.fixture
    async def girl(self, auth_client):
        made = (await auth_client.post('/api/dating/profile', json={
            'site_id': STAND_SITE,
            'login': f'chan-{uuid.uuid4().hex[:8]}@example.com',
            'password': 'x'})).json()['result']

        yield int(made)

        async with mysql_get_db_async() as db:
            await db.execute('DELETE FROM block WHERE profile_id = %s', (int(made),))
            await db.connection.commit()

        await auth_client.delete(f'/api/dating/profile/{made}')

    async def test_a_mail_block_is_invisible_to_chat(self, girl):
        """Набор инвайтов такую пару не видит — значит позовёт как обычно."""
        from src.dating.block import block_add, block_pairs

        uid = f'u{uuid.uuid4().hex[:8]}'
        await block_add(STAND_SITE, girl, uid, 'X', reason=AGE_REFUSAL,
                        source='mail', channel='mail')

        assert (girl, uid) not in await block_pairs(STAND_SITE, 'chat'), \
            'отказ по возрасту гасит инвайты — чат запрещён без причины'

    async def test_a_mail_block_does_stop_letters(self, girl):
        """⚠ Обратная сторона: письма он останавливать обязан."""
        from src.dating.block import block_add, block_pairs

        uid = f'u{uuid.uuid4().hex[:8]}'
        await block_add(STAND_SITE, girl, uid, 'X', reason=AGE_REFUSAL,
                        source='mail', channel='mail')

        assert (girl, uid) in await block_pairs(STAND_SITE), \
            'отказ по возрасту не остановил письма'

    async def test_a_full_block_stops_both(self, girl):
        """«Он закрылся» — про девушку, а не про канал: ни реплики, ни письма."""
        from src.dating.block import block_add, block_pairs

        uid = f'u{uuid.uuid4().hex[:8]}'
        await block_add(STAND_SITE, girl, uid, 'X', reason=DENY_REFUSAL,
                        source='chat', channel='all')

        assert (girl, uid) in await block_pairs(STAND_SITE, 'chat')
        assert (girl, uid) in await block_pairs(STAND_SITE)

    async def test_the_mail_block_keeps_the_invite_draft(self, girl):
        """⚠⚠ И черновик инвайта такой запрет не выметает.

        Запрет чистит пулы, и сноси он заодно готовую реплику — мы потеряли бы то,
        что сайт принял бы.
        """
        from src.dating.block import block_add

        uid = f'u{uuid.uuid4().hex[:8]}'

        async with mysql_get_db_async() as db:
            await db.execute(
                'INSERT INTO invite (site_id, profile_id, uid, name, body, status)'
                " VALUES (%s, %s, %s, 'X', 'hi', 'draft')",
                (STAND_SITE, girl, uid))
            await db.connection.commit()

        await block_add(STAND_SITE, girl, uid, 'X', reason=AGE_REFUSAL,
                        source='mail', channel='mail')

        async with mysql_get_db_async() as db:
            await db.execute(
                'SELECT COUNT(*) AS n FROM invite'
                " WHERE profile_id = %s AND uid = %s AND status = 'draft'",
                (girl, uid))
            left = int(((await db.fetchone()) or {}).get('n') or 0)

            await db.execute('DELETE FROM invite WHERE profile_id = %s', (girl,))
            await db.connection.commit()

        assert left == 1, 'почтовый запрет вымел готовый инвайт'


class TestTheOldRefusalsAreCaughtUp:
    """⚠⚠ Отказы, случившиеся **до** появления приметы, подбираются миграцией.

    Примета заведена сегодня, а отказы сайт давал и раньше. Для тех писем код ещё
    не знал этого слова: письмо осталось черновиком, запрет не завёлся — и вкладка
    «Не пишем» стояла пустой при живом отказе в «Доставке».

    ⚠ Хуже того, черновик оставался рабочим: круг отправки брал его каждый заход,
    получал тот же отказ и клал обратно. Письмо, которое сайт не примет никогда,
    стоило вызова LLM при каждом наборе пула.
    """

    MIGRATION = 'migrations/20260921200000_block_age_backfill.sql'

    def test_the_migration_buries_the_letter_first(self):
        """⚠⚠ Порядок в миграции значащий: сперва `failed`, потом запрет.

        Заведи запрет первым — чистка пулов снесла бы черновик, и отказ исчез бы
        из «Доставки» вместе с ним. Тот же довод, что в `mail_send._deny_check`.
        """
        sql = open(self.MIGRATION, encoding='utf-8').read()

        assert sql.index("SET `status` = 'failed'") < sql.index('INSERT INTO `block`')

    def test_it_takes_only_the_age_refusal(self):
        """⚠⚠ Часовой лимит — **временный**, и запрет по нему был бы потерей.

        Пять входящих в час считаются вместе с письмами чужих агентств: занеси мы
        такую пару в «не пишем», человек пропал бы навсегда из-за чужой
        активности. Черновики таких писем намеренно остаются в пуле.
        """
        sql = open(self.MIGRATION, encoding='utf-8').read()

        assert 'ERROR_MESSAGE_AGE_CRITERIA' in sql
        assert 'HOUR_IN_LIMIT' not in sql.split('-- ─')[-1], \
            'миграция трогает часовой лимит — он временный'

    def test_the_block_is_mail_only(self):
        """Канал `mail`: отказ про письма, чат он не трогает."""
        sql = open(self.MIGRATION, encoding='utf-8').read()

        assert "'mail', 'mail'" in sql, 'запрет заводится не почтовым'

    def test_all_three_pools_are_swept(self):
        """⚠ Отказ приходит на любой из трёх пулов, а запрет один."""
        sql = open(self.MIGRATION, encoding='utf-8').read()

        for table in ('favorite_mail', 'mailing', 'mail_reply'):
            assert f'`{table}`' in sql, f'{table} пропущен'

    def test_it_can_run_twice(self):
        """⚠ Повторный прогон не должен двоить запреты: yoyo накатит миграцию на
        проде, и рестарт не обязан ничего ломать."""
        sql = open(self.MIGRATION, encoding='utf-8').read()

        assert 'NOT EXISTS' in sql
        assert "`status` = 'draft'" in sql
