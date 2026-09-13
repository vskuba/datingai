"""Круг отправки: письма и ответы уходят сами, по одному.

⚠⚠ **Ни одно письмо здесь наружу не уходит, и это главное свойство файла.**
Отправка — необратимое действие: письмо приходит живому человеку от имени
девушки. Подменяется `dating_mail_send_one` — единственная функция, которая
ходит на сайт; всё остальное (кого брать, когда, сколько) работает
по-настоящему, с живой базой.

Тот же приём и та же причина, что в `test_mail_send.py`. Проверять здесь надо
именно **решения круга**, а не сам поход: поход проверен там.
"""

import uuid

import pytest

from src.dating import dating_send_round as round_
from src.dating.dating_send_round import dating_send_round_run, dating_send_why

pytestmark = pytest.mark.mail
# Выдуманный сайт: у соседа его нет, живую отправку задеть невозможно.
SITE = 999_777

# Сайт для анкеты: заведение сверяет номер у поддельного соседа.
LIVE_SITE = 343

# Смена во все сутки — чтобы проверки не зависели от часа прогона.
# ⚠ **`24:00`, а не `23:45`.** Смена задумана «во все сутки», чтобы проверка
# «идёт ли смена» не зависела от часа прогона, — но `23:45` оставлял дыру в
# четверть часа, и сюита честно краснела каждый вечер между 23:45 и полуночью.
# Поймано живьём: двадцать три теста в трёх файлах разом, и все по этой причине.
#
# `24:00` — законный конец суток и здесь, и на проде: правая граница смены
# строгая (`from <= minutes < to`), так что 1440 включает и последнюю минуту.
ALWAYS = [{'from': '00:00', 'to': '24:00'}]


@pytest.fixture
async def site(auth_client, monkeypatch):
    """Сайт с включёнными выключателями отправки. Убирает за собой всё.

    ⚠⚠ **И снимает дневной потолок, иначе тесты зависят от соседей.**

    `_today` считает **все** письма стенда, ушедшие сегодня, — включая оставленные
    другими файлами сюиты: они бегут раньше по алфавиту и убирают за собой только
    свои строки. Наберётся сорок — и круг молча перестаёт отправлять, а тесты здесь
    падают на `assert ... == 1` без единого намёка на причину. Так же молчит и
    объяснение простоя: `dating_send_why` честно скажет «норма выбрана».

    Поймано живьём: полный прогон покраснел один раз из трёх, файл поодиночке
    зеленел всегда. Воспроизведено набивкой счётчика до 45 — тогда краснеют сразу
    три теста.

    ⚠ Тесты **про сам потолок** задают его себе обратно: `_max_day` подменяется
    точечно, и видно это прямо в них.
    """
    from mysql_.mysql_ import mysql_get_db_async

    monkeypatch.setattr(round_, '_max_day', _fixed(10 ** 6))

    before = (await auth_client.get(
        f'/api/dating/site/{SITE}/mailing-schedule')).json()['result']

    await auth_client.put(f'/api/dating/site/{SITE}/mailing-schedule',
                          json={'schedule': ALWAYS})

    async with mysql_get_db_async() as db:
        await db.execute('SELECT is_gone FROM dating_site WHERE site_id = %s', (SITE,))
        row = await db.fetchone()
        gone = int((row or {}).get('is_gone') or 0)
        # ⚠ Метку «ушёл» снимаем: сайта с выдуманным номером у соседа нет, фоновая
        # сверка честно её ставит, а круг такие пропускает — писать некому.
        await db.execute(
            'INSERT INTO dating_site (site_id, is_gone, mailing_send_on, reply_send_on)'
            ' VALUES (%s, 0, 1, 1) ON DUPLICATE KEY UPDATE is_gone = 0,'
            ' mailing_send_on = 1, reply_send_on = 1', (SITE,))
        await db.connection.commit()

    yield SITE

    await auth_client.put(f'/api/dating/site/{SITE}/mailing-schedule',
                          json={'schedule': before['schedule']})
    async with mysql_get_db_async() as db:
        await db.execute('UPDATE dating_site SET is_gone = %s, mailing_send_on = 0,'
                         ' reply_send_on = 0 WHERE site_id = %s', (gone, SITE))
        await db.connection.commit()


@pytest.fixture
async def profile(auth_client):
    """Анкета-отправитель. Каскад убирает за ней письма и ответы."""
    made = (await auth_client.post('/api/dating/profile', json={
        'site_id': LIVE_SITE, 'login': f'snd-{uuid.uuid4().hex[:8]}@example.com',
        'password': 'x'})).json()['result']

    yield made

    await auth_client.delete(f'/api/dating/profile/{made}')


@pytest.fixture
def post(monkeypatch):
    """Подменяет **поход на сайт**: считает письма вместо отправки.

    ⚠ Подменяется `dating_mail_send_one` в модуле круга, а не транспорт: круг
    зовёт её напрямую, и заглушка ниже по стеку оставила бы возможность промаха.
    """
    sent = []

    async def fake(letter, profile, base, password, table='dating_mailing'):
        sent.append({'id': int(letter['id']), 'table': table})

        from src.dating.dating_mail_send import dating_mail_send_mark

        await dating_mail_send_mark(letter, sent=True, table=table)

    monkeypatch.setattr(round_, 'dating_mail_send_one', fake)
    monkeypatch.setattr(round_, '_sent_at', {})

    return sent


async def _letter(profile_id: int, table: str = 'dating_mailing', **kw) -> int:
    """Кладёт письмо в пул напрямую: набор требует адресатов в сети.

    ⚠ У ответа обязателен `mail_in_id` — внешний ключ на входящее письмо. Ответ
    без письма, на которое он отвечает, бессмыслен и по схеме невозможен, поэтому
    входящее заводится тут же.
    """
    from mysql_.mysql_ import mysql_get_db_async

    uid = kw.get('uid', 'snd-uid')
    error = kw.get('error', '')

    async with mysql_get_db_async() as db:
        field, value = '', ''
        args = [SITE, profile_id, uid, error]

        if table == 'dating_mail_reply':
            await db.execute(
                'INSERT INTO dating_mail_in (site_id, profile_id, message_id, uid,'
                " name, body, date_post) VALUES (%s, %s, %s, %s, 'Тест', 'Письмо',"
                ' NOW())',
                (SITE, profile_id, int(uuid.uuid4().int % 1_000_000_000), uid))
            await db.connection.commit()
            await db.execute('SELECT LAST_INSERT_ID() AS id')
            field, value = ', mail_in_id', ', %s'
            args.append(int((await db.fetchone())['id']))

        await db.execute(
            f'INSERT INTO {table} (site_id, profile_id, uid, name, body, status,'
            f' error{field})'
            f" VALUES (%s, %s, %s, 'Тест', 'Текст письма', 'draft', %s{value})",
            tuple(args))
        await db.connection.commit()
        await db.execute('SELECT LAST_INSERT_ID() AS id')

        return int((await db.fetchone())['id'])


class TestSwitchesRule:
    """⚠ Выключатель — единственное согласие на отправку.

    «Готовим» и «шлём» это разные решения: набранный пул согласием на отправку не
    является. Проверка стоит первой, потому что цена ошибки здесь наивысшая —
    письмо живому человеку отменить нельзя.
    """

    async def test_nothing_goes_while_switch_is_off(self, auth_client, site,
                                                    profile, post):
        """Выключатели сняты — не уходит ничего, сколько бы ни лежало в пуле."""
        from mysql_.mysql_ import mysql_get_db_async

        await _letter(profile)
        async with mysql_get_db_async() as db:
            await db.execute('UPDATE dating_site SET mailing_send_on = 0,'
                             ' reply_send_on = 0 WHERE site_id = %s', (SITE,))
            await db.connection.commit()

        assert (await dating_send_round_run(0))['sent'] == 0
        assert post == [], 'письмо ушло при снятом выключателе'

    async def test_mailing_switch_does_not_send_replies(self, auth_client, site,
                                                        profile, post):
        """⚠ Выключатели раздельные: включив рассылку, ответы не включают.

        Это разные занятия — письмо незнакомому и ответ тому, кто написал сам, — и
        решение по ним принимают порознь.
        """
        from mysql_.mysql_ import mysql_get_db_async

        await _letter(profile, 'dating_mail_reply')
        async with mysql_get_db_async() as db:
            await db.execute('UPDATE dating_site SET reply_send_on = 0'
                             ' WHERE site_id = %s', (SITE,))
            await db.connection.commit()

        await dating_send_round_run(0)

        assert post == [], 'ответ ушёл при снятом своём выключателе'


class TestOneAtATime:
    """⚠ По одному письму за заход, с паузой.

    Пул в полсотни писем, ушедший залпом, — это рассылка робота, чем он и
    является. Пауза делает поток похожим на человека, который сидит и пишет.
    """

    async def test_one_letter_per_pass(self, site, profile, post):
        for _ in range(3):
            await _letter(profile)

        await dating_send_round_run(0)

        assert len(post) == 1, 'за один заход ушло больше одного письма'

    async def test_second_pass_waits(self, site, profile, post):
        """Второй заход сразу за первым молчит: пауза ещё не вышла."""
        await _letter(profile)
        await _letter(profile)

        await dating_send_round_run(0)
        await dating_send_round_run(0)

        assert len(post) == 1, 'пауза между письмами не соблюдена'

    async def test_pause_has_a_floor(self):
        """⚠ Нижний предел поверх настройки: «5» в поле превратило бы смену в
        очередь из сотни писем в час — то, от чего пауза и заведена."""
        from src.dating.dating_send_round import DATING_SEND_EVERY_MIN

        assert DATING_SEND_EVERY_MIN >= 30


class TestRepliesGoFirst:
    """⚠ Ответы вперёд писем: их ждёт живой человек, написавший первым."""

    async def test_reply_wins_over_mailing(self, site, profile, post):
        await _letter(profile, 'dating_mailing')
        await _letter(profile, 'dating_mail_reply')

        await dating_send_round_run(0)

        assert post and post[0]['table'] == 'dating_mail_reply'


class TestFailedLetterIsLeftAlone:
    """⚠ Письмо с прежней ошибкой круг больше не берёт.

    Неудача оставляет его в пуле с причиной в `error`. Берись круг за него снова,
    один отказ сайта («слишком часто») превратился бы в бесконечный повтор ровно
    тогда, когда сайт просит остановиться. Такое письмо ждёт человека.
    """

    async def test_letter_with_error_is_skipped(self, site, profile, post):
        await _letter(profile, error='сайт просит подождать')

        await dating_send_round_run(0)

        assert post == [], 'круг повторил письмо, на котором уже споткнулся'

    async def test_failure_is_remembered(self, site, profile, post, monkeypatch):
        """Отказ ложится в `error`, а письмо остаётся в пуле — не `failed`."""
        from mysql_.mysql_ import mysql_get_db_async
        from src.dating.dating_mail_send import DatingMailSendError

        async def broken(letter, profile, base, password, table='dating_mailing'):
            raise DatingMailSendError('сайт просит подождать')

        monkeypatch.setattr(round_, 'dating_mail_send_one', broken)
        letter_id = await _letter(profile)

        assert (await dating_send_round_run(0))['failed'] == 1

        async with mysql_get_db_async() as db:
            await db.execute('SELECT status, error FROM dating_mailing WHERE id = %s',
                             (letter_id,))
            row = await db.fetchone()

        assert row['status'] == 'draft', 'письмо помечено ушедшим или провалившимся'
        assert 'подождать' in row['error']


class TestDailyCap:
    """⚠ Потолок в сутки — общий на рассылку и ответы.

    Считает их получатель, а не наши таблицы: раздельные потолки означали бы вдвое
    больше писем от одной девушки.
    """

    async def test_cap_stops_sending(self, auth_client, site, profile, post,
                                     monkeypatch):
        from mysql_.mysql_ import mysql_get_db_async

        # ⚠ Потолок возвращаем себе: фикстура `post` снимает его для всех прочих
        # тестов файла (см. там же, почему). Здесь он и есть предмет проверки.
        monkeypatch.setattr(round_, '_max_day', _fixed(10))

        before = (await auth_client.get(
            f'/api/dating/site/{SITE}')).json()['result']['settings']
        try:
            await auth_client.put(f'/api/dating/site/{SITE}',
                                  json={'settings': {**before, 'send_max_day': '10'}})
            # Десять уже ушло сегодня — потолок выбран.
            async with mysql_get_db_async() as db:
                for _ in range(10):
                    await db.execute(
                        'INSERT INTO dating_mailing (site_id, profile_id, uid, name,'
                        " body, status, sent_at) VALUES (%s, %s, 'cap', 'Тест',"
                        " 'ушло', 'sent', NOW())", (SITE, profile))
                await db.connection.commit()

            await _letter(profile)

            assert (await dating_send_round_run(0))['sent'] == 0
            assert post == [], 'письмо ушло сверх дневного потолка'
        finally:
            await auth_client.put(f'/api/dating/site/{SITE}',
                                  json={'settings': before})

    async def test_unknown_count_stops_sending(self, monkeypatch):
        """⚠ Счётчик не прочитался — молчим.

        Непрочитанный счёт означает неизвестный потолок, а лишнее письмо отменить
        нельзя: сомнение решается в сторону молчания.
        """
        from src.dating.dating_send_round import _today

        def broken():
            raise RuntimeError('база лежит')

        # ⚠ Подменяем имя **в модуле круга**, а не в `mysql_`: он импортировал
        # функцию к себе, и правка источника его уже не касается.
        monkeypatch.setattr(round_, 'mysql_get_db_async', broken)

        assert await _today(SITE) > 100_000


class TestScheduleIsRespected:
    """Шлём в те же часы, когда набираем: письмо в четыре утра выдаёт робота."""

    async def test_outside_the_shift_nothing_goes(self, auth_client, site, profile,
                                                  post):
        await auth_client.put(f'/api/dating/site/{SITE}/mailing-schedule',
                              json={'schedule': [{'from': '00:00', 'to': '00:15'}]})
        await _letter(profile)

        # Двенадцать дня — заведомо вне смены 00:00–00:15.
        await dating_send_round_run(12 - _utc_hour())

        assert post == [], 'письмо ушло вне смены'

    async def test_empty_schedule_means_no(self, auth_client, site, profile, post):
        """⚠ Пустое расписание — «нет», а не «всегда». Иначе сайт без смен слал бы
        круглосуточно, и это была бы первая же беда после включения."""
        await auth_client.put(f'/api/dating/site/{SITE}/mailing-schedule',
                              json={'schedule': []})
        await _letter(profile)

        await dating_send_round_run(0)

        assert post == []


class TestWhyItStands:
    """⚠⚠ Молчащий пул обязан объяснять себя — то же правило, что у инвайтов.

    У писем оно острее: дневной потолок **общий** на три пула, и встать рассылка
    может оттого, что норму съели ответы. Числа при этом лежат в разных таблицах, и
    сложить их человеку негде.
    """

    async def test_the_switch_off_says_so(self):
        """Выключатели стенда сняты, пока фикстура `site` их не подняла."""
        said = await dating_send_why(SITE, 'dating_mailing', 0)

        assert said == 'отправка рассылки выключена'

    async def test_each_pool_names_itself(self):
        """⚠ Три пула — три выключателя, и путать их нельзя: человек пойдёт снимать
        не тот."""
        assert 'ответов' in await dating_send_why(SITE, 'dating_mail_reply', 0)
        assert 'избранным' in await dating_send_why(SITE, 'dating_favorite_mail', 0)

    async def test_outside_the_shift_says_so(self, site, auth_client):
        await auth_client.put(f'/api/dating/site/{SITE}/mailing-schedule',
                              json={'schedule': [{'from': '00:00', 'to': '00:15'}]})

        # Полдень по местному — заведомо вне смены 00:00–00:15.
        said = await dating_send_why(SITE, 'dating_mailing', 12 - _utc_hour())

        assert 'не смена' in said

    async def test_the_day_cap_says_it_is_shared(self, site, monkeypatch):
        """⚠ Строка обязана сказать, что потолок **общий**: встала рассылка, а
        выбрали норму ответы — искать причину человек пойдёт не туда."""
        import src.dating.dating_send_round as round_

        monkeypatch.setattr(round_, '_max_day', _fixed(5))
        monkeypatch.setattr(round_, '_today', _fixed(5))

        said = await dating_send_why(SITE, 'dating_mailing', 0)

        assert 'ушло 5 из 5' in said and 'общая' in said

    async def test_an_empty_pool_says_so(self, site):
        assert await dating_send_why(SITE, 'dating_mailing', 0) \
            == 'пул пуст: отправлять нечего'

    async def test_silence_when_there_is_a_letter(self, site, profile):
        """Есть что отправить — сказать нечего."""
        await _letter(profile)

        assert await dating_send_why(SITE, 'dating_mailing', 0) == ''

    async def test_an_unknown_pool_is_silent(self, site):
        """Чужое имя таблицы — не повод объяснять: в SQL оно и не поедет."""
        assert await dating_send_why(SITE, 'dating_users', 0) == ''


def _fixed(value):
    """Заглушка настройки. ⚠ Любые доводы: у `_today` их два, у `_max_day` один."""
    async def _got(*args, **kwargs):
        return value

    return _got


def _utc_hour() -> int:
    """Текущий час UTC — чтобы задать смещение, дающее нужный местный час."""
    import time

    return time.gmtime().tm_hour
