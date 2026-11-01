"""Круг отправки: письма каждого занятия уходят сами, по своему сроку.

⚠⚠ **Ни одно письмо здесь наружу не уходит, и это главное свойство файла.**
Отправка — необратимое действие: письмо приходит живому человеку от имени
девушки. Подменяется `mail_send_one` — единственная функция, которая
ходит на сайт; всё остальное (кого брать, когда, сколько) работает
по-настоящему, с живой базой.

Тот же приём и та же причина, что в `test_mail_send.py`. Проверять здесь надо
именно **решения круга**, а не сам поход: поход проверен там.
"""

import re
import uuid

import pytest

from src.dating import send_round as round_
from src.dating.send_round import send_round_run, send_why
from src.dating.site_setting import SITE_KNOWN

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

    `_today` считает ушедшие сегодня письма **рассылки** — включая оставленные
    другими файлами сюиты: они бегут раньше по алфавиту и убирают за собой только
    свои строки. Наберётся сорок — и круг молча перестаёт слать рассылку, а тесты
    здесь падают на `assert ... == 1` без единого намёка на причину. Так же молчит
    и объяснение простоя: `send_why` честно скажет «норма выбрана».

    Поймано живьём: полный прогон покраснел один раз из трёх, файл поодиночке
    зеленел всегда. Воспроизведено набивкой счётчика до 45 — тогда краснеют сразу
    три теста.

    ⚠ Тесты **про сам потолок** задают его себе обратно: `_max_day` подменяется
    точечно, и видно это прямо в них.
    """
    from mysql_.mysql_ import mysql_get_db_async

    monkeypatch.setattr(round_, '_max_day', _fixed(10 ** 6))

    # ⚠⚠ **И снимает задержку ответа по длине текста.** Умолчание — 30 секунд на
    # десять слов, а входящие письма фикстур стоят временем `NOW()`: любой ответ
    # оказывался бы «ещё не дописанным», и тесты про выключатели, сроки и нормы
    # краснели бы по чужой причине.
    #
    # ⚠ Само правило при этом проверяется — `TestReplyWaitsItsLength` ниже
    # включает коэффициент себе обратно, как это делают тесты про потолок.
    monkeypatch.setattr('src.dating.reply_delay.reply_delay_per10', _fixed(0))

    before = (await auth_client.get(
        f'/api/dating/site/{SITE}/mailing-schedule')).json()['result']

    await auth_client.put(f'/api/dating/site/{SITE}/mailing-schedule',
                          json={'schedule': ALWAYS})

    async with mysql_get_db_async() as db:
        await db.execute('SELECT is_gone FROM site WHERE site_id = %s', (SITE,))
        row = await db.fetchone()
        gone = int((row or {}).get('is_gone') or 0)
        # ⚠ Метку «ушёл» снимаем: сайта с выдуманным номером у соседа нет, фоновая
        # сверка честно её ставит, а круг такие пропускает — писать некому.
        await db.execute(
            'INSERT INTO site (site_id, is_gone, mailing_send_on, reply_send_on)'
            ' VALUES (%s, 0, 1, 1) ON DUPLICATE KEY UPDATE is_gone = 0,'
            ' mailing_send_on = 1, reply_send_on = 1', (SITE,))
        await db.connection.commit()

    yield SITE

    await auth_client.put(f'/api/dating/site/{SITE}/mailing-schedule',
                          json={'schedule': before['schedule']})
    async with mysql_get_db_async() as db:
        # ⚠ И `favorite_mail_send_on` — его включают тесты паузы ниже. Останься
        # он поднятым, стенд остался бы в списке круга для остальных файлов
        # сюиты: ровно тот вид протечки между файлами, от которого этот сайт и
        # выдуман.
        await db.execute('UPDATE site SET is_gone = %s, mailing_send_on = 0,'
                         ' reply_send_on = 0, favorite_mail_send_on = 0'
                         ' WHERE site_id = %s', (gone, SITE))
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

    ⚠ Подменяется `mail_send_one` в модуле круга, а не транспорт: круг
    зовёт её напрямую, и заглушка ниже по стеку оставила бы возможность промаха.
    """
    sent = []

    async def fake(letter, profile, base, password, table='mailing'):
        sent.append({'id': int(letter['id']), 'table': table})

        from src.dating.mail_send import mail_send_mark

        await mail_send_mark(letter, sent=True, table=table)

    monkeypatch.setattr(round_, 'mail_send_one', fake)
    monkeypatch.setattr(round_, '_sent_at', {})
    # ⚠ Остуды нет: очередь спрашивает список остывших получателей
    # (`mail_wait_uids`), а живого Redis у сюиты с хоста нет — его адрес в `.env`
    # внутренний. Про саму остуду есть свой файл (`test_mail_wait.py`); здесь она
    # должна просто не мешать.
    monkeypatch.setattr(round_, 'mail_wait_uids', lambda site_id: [])

    return sent


async def _letter(profile_id: int, table: str = 'mailing', **kw) -> int:
    """Кладёт письмо в пул напрямую: набор требует адресатов в сети.

    ⚠ У ответа обязателен `mail_in_id` — внешний ключ на входящее письмо. Ответ
    без письма, на которое он отвечает, бессмыслен и по схеме невозможен, поэтому
    входящее заводится тут же.

    ⚠⚠ **Адресат по умолчанию у каждого пула свой, и это не опрятность.** Ответ
    тянет за собой входящее письмо того же человека — а рассылка теперь читает ровно
    эту таблицу: ответившему письмом она не уходит (`pool_mail_answered_where`).
    Держи оба пула один `uid`, и фикстура ответа **молча** запирала бы рассылку:
    тест «каждый пул шлёт своё» краснел бы, рассказывая про общий замок, которого
    нет. Разные пулы здесь — разные люди, чем они по смыслу и являются.
    """
    from mysql_.mysql_ import mysql_get_db_async

    uid = kw.get('uid', f'snd-{table}')
    error = kw.get('error', '')

    async with mysql_get_db_async() as db:
        field, value = '', ''
        args = [SITE, profile_id, uid, error]

        if table == 'mail_reply':
            await db.execute(
                'INSERT INTO mail_in (site_id, profile_id, message_id, uid,'
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
            await db.execute('UPDATE site SET mailing_send_on = 0,'
                             ' reply_send_on = 0 WHERE site_id = %s', (SITE,))
            await db.connection.commit()

        assert (await send_round_run(0))['sent'] == 0
        assert post == [], 'письмо ушло при снятом выключателе'

    async def test_mailing_switch_does_not_send_replies(self, auth_client, site,
                                                        profile, post):
        """⚠ Выключатели раздельные: включив рассылку, ответы не включают.

        Это разные занятия — письмо незнакомому и ответ тому, кто написал сам, — и
        решение по ним принимают порознь.
        """
        from mysql_.mysql_ import mysql_get_db_async

        await _letter(profile, 'mail_reply')
        async with mysql_get_db_async() as db:
            await db.execute('UPDATE site SET reply_send_on = 0'
                             ' WHERE site_id = %s', (SITE,))
            await db.connection.commit()

        await send_round_run(0)

        assert post == [], 'ответ ушёл при снятом своём выключателе'


class TestOneAtATime:
    """⚠ Рассылка уходит по одному письму за заход, со своей паузой.

    Пул в полсотни писем, ушедший залпом, — это рассылка робота, чем она и
    является. Пауза делает поток похожим на человека, который сидит и пишет.
    """

    async def test_one_letter_per_pass(self, site, profile, post):
        for _ in range(3):
            await _letter(profile)

        await send_round_run(0)

        assert len(post) == 1, 'за один заход ушло больше одного письма рассылки'

    async def test_second_pass_waits(self, site, profile, post):
        """Второй заход сразу за первым молчит: пауза рассылки ещё не вышла."""
        await _letter(profile)
        await _letter(profile)

        await send_round_run(0)
        await send_round_run(0)

        assert len(post) == 1, 'пауза между письмами не соблюдена'

    async def test_pause_has_no_floor_and_loses_to_options(self):
        """⚠ Предела константой больше нет: темп задан **набором**, а `site_seconds`
        значение вне набора не берёт — значит, «5» в базу не проедет вовсе."""
        values = {int(v) for v, _ in SITE_KNOWN['send_every']['options']}

        assert min(values) >= 30


class TestValuesAreSiteSettings:
    """⚠ Пауза и потолок — настройки сайта на странице, а не числа в коде.

    Хранить их в константах значило бы спорить с человеком, который знает свой
    сайт лучше: поднял паузу на вкладке «Рассылка писем» — и круг ждёт ровно
    столько, сколько подняли, а не сколько когда-то казалось всем сразу. Умолчание же —
    те же значения, что были в константах: незанятый сайт ведёт себя как раньше.
    """

    async def test_default_pause_is_the_old_constant(self, site, profile, post):
        """Не задал сайт паузу — круг ждёт три минуты, прежнюю константу.

        100 секунд — мало (за константы в 30 или 60 письмо ушло бы раньше),
        190 — довольно (за часы письмо промолчало бы и дальше).
        """
        import time

        await _letter(profile)
        await _letter(profile)

        await send_round_run(0)
        assert len(post) == 1, 'первое письмо не ушло'

        round_._sent_at[(SITE, 'mailing')] = time.time() - 100
        assert (await send_round_run(0))['sent'] == 0, \
            'пауза короче умолчания в три минуты'

        round_._sent_at[(SITE, 'mailing')] = time.time() - 190
        assert (await send_round_run(0))['sent'] == 1, \
            'пауза длиннее умолчания в три минуты'

    async def test_pause_is_a_site_setting(self, auth_client, site, profile, post):
        """⚠ Пауза берётся из настроек сайта: поднял минуту — круг ждёт минуту.

        Прошло 70 секунд после прошлого письма — этого не хватило бы умолчанию
        в три минуты (`test_default_pause_is_the_old_constant`) и хватило бы
        минуты из настройки: уходит второе. Молчал бы круг — значит ждёт число
        из кода, а не то, что человек поставил на странице.
        """
        import time

        before = (await auth_client.get(
            f'/api/dating/site/{SITE}')).json()['result']['settings']
        try:
            await auth_client.put(f'/api/dating/site/{SITE}',
                                  json={'settings': {**before, 'send_every': '60'}})
            assert await round_._every(SITE, 'send_every') == 60, \
                'сайт настроил паузу в минуту, круг её не видит'
            await _letter(profile)
            await _letter(profile)

            assert (await send_round_run(0))['sent'] == 1, 'первое письмо не ушло'
            round_._sent_at[(SITE, 'mailing')] = time.time() - 70

            assert (await send_round_run(0))['sent'] == 1, \
                'круг ждёт паузу из кода, а не настроенную сайтом'
            assert len(post) == 2
        finally:
            await auth_client.put(f'/api/dating/site/{SITE}',
                                  json={'settings': before})

    async def test_registry_defaults_are_the_old_constants(self):
        """Умолчания лежат в реестре настроек: их видит страница и берёт круг,
        когда сайт ничего не задавал. Оба числа — ровно те, что жили в константах."""
        assert SITE_KNOWN['send_every']['default'] == '180'
        assert SITE_KNOWN['send_max_day']['default'] == '40'


class TestPoolsPaceIndependently:
    """⚠ Каждое занятие меряется своим сроком: ответы не ждут рассылочной паузы,
    а рассылка — ответы.

    На одном сайте три почтовых занятия, и замок был один на всех: ответы молчали
    под рассылочными тремя минутами, а рассылка стояла под ответными пятью.
    У каждого теперь свои часы.
    """

    async def test_each_ready_pool_sends_its_own(self, site, profile, post):
        """Один заход — по письму из каждого пула, кому пора, не глядя на соседей."""
        mailing = await _letter(profile, 'mailing')
        reply = await _letter(profile, 'mail_reply')

        assert (await send_round_run(0))['sent'] == 2, \
            'пулы ушли общим замком, а не своими часами'
        assert {(one['id'], one['table']) for one in post} == {
            (mailing, 'mailing'), (reply, 'mail_reply')}

    async def test_reply_waits_its_own_five_minutes(self, site, profile, post):
        """Рассылка через три минуты уже вольна, ответ же держат его собственные
        пять: чужая свобода его не выпускает.

        200 секунд: довольно для рассылочных 180 (это видит тест выше), мало для
        ответных 300.
        """
        import time

        await _letter(profile, 'mail_reply')
        second = await _letter(profile, 'mail_reply')

        await send_round_run(0)
        round_._sent_at[(SITE, 'mail_reply')] = time.time() - 200

        assert (await send_round_run(0))['sent'] == 0, \
            'ответ ждал рассылочную паузу, а не свой срок'

        round_._sent_at[(SITE, 'mail_reply')] = time.time() - 310
        assert (await send_round_run(0))['sent'] == 1, \
            'ответ дождался сверх своего срока'
        assert post[-1]['id'] == second


class TestFailedLetterIsLeftAlone:
    """⚠ Письмо с прежней ошибкой круг больше не берёт.

    Неудача оставляет его в пуле с причиной в `error`. Берись круг за него снова,
    один отказ сайта («слишком часто») превратился бы в бесконечный повтор ровно
    тогда, когда сайт просит остановиться. Такое письмо ждёт человека.
    """

    async def test_letter_with_error_is_skipped(self, site, profile, post):
        await _letter(profile, error='сайт просит подождать')

        await send_round_run(0)

        assert post == [], 'круг повторил письмо, на котором уже споткнулся'

    async def test_failure_is_remembered(self, site, profile, post, monkeypatch):
        """Отказ ложится в `error`, а письмо остаётся в пуле — не `failed`."""
        from mysql_.mysql_ import mysql_get_db_async
        from src.dating.mail_send import DatingMailSendError

        async def broken(letter, profile, base, password, table='mailing'):
            raise DatingMailSendError('сайт просит подождать')

        monkeypatch.setattr(round_, 'mail_send_one', broken)
        letter_id = await _letter(profile)

        assert (await send_round_run(0))['failed'] == 1

        async with mysql_get_db_async() as db:
            await db.execute('SELECT status, error FROM mailing WHERE id = %s',
                             (letter_id,))
            row = await db.fetchone()

        assert row['status'] == 'draft', 'письмо помечено ушедшим или провалившимся'
        assert 'подождать' in row['error']


class TestDailyCap:
    """⚠ Норма суток — своя у рассылки: под неё не попадают ни ответы, ни письма
    избранным — у тех пределы свои и стоят они на «Фаворитах».

    Потолок был общий на три пула: рассылка вставала, когда норму съели ответы,
    а на одно письмо отвечают в силу самого ответа, а не по сайтной норме.
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
                        'INSERT INTO mailing (site_id, profile_id, uid, name,'
                        " body, status, sent_at) VALUES (%s, %s, 'cap', 'Тест',"
                        " 'ушло', 'sent', NOW())", (SITE, profile))
                await db.connection.commit()

            await _letter(profile)

            assert (await send_round_run(0))['sent'] == 0
            assert post == [], 'письмо ушло сверх дневного потолка'
        finally:
            await auth_client.put(f'/api/dating/site/{SITE}',
                                  json={'settings': before})

    async def test_norm_covers_mailing_only(self, site, profile, post, monkeypatch):
        """Норма рассылки выбрана целиком — ответы не под ней: человек, который
        написал сам, ждёт ответа, какие бы числа ни были у рассылки за сутки.
        """
        monkeypatch.setattr(round_, '_max_day', _fixed(0))
        monkeypatch.setattr(round_, '_today', _fixed(0))

        await _letter(profile)
        reply = await _letter(profile, 'mail_reply')

        assert (await send_round_run(0))['sent'] == 1, \
            'норма рассылки заглушила ответ'
        assert post == [{'id': reply, 'table': 'mail_reply'}]

    async def test_unknown_count_stops_sending(self, monkeypatch):
        """⚠ Счётчик не прочитался — молчим.

        Непрочитанный счёт означает неизвестный потолок, а лишнее письмо отменить
        нельзя: сомнение решается в сторону молчания.
        """
        from src.dating.send_round import _today

        def broken():
            raise RuntimeError('база лежит')

        # ⚠ Подменяем имя **в модуле общего слоя**, а не в `mysql_`: он
        # импортировал функцию к себе, и правка источника его уже не касается.
        # Тело суточного счёта живёт в `py_modules/round_/round_day.py` — там и
        # имя; `src/dating/round.py` только приносит в него нашу схему.
        from round_ import round_day as day
        monkeypatch.setattr(day, 'mysql_get_db_async', broken)

        assert await _today(SITE) > 100_000


class TestScheduleIsRespected:
    """Шлём в те же часы, когда набираем: письмо в четыре утра выдаёт робота."""

    async def test_outside_the_shift_nothing_goes(self, auth_client, site, profile,
                                                  post):
        await auth_client.put(f'/api/dating/site/{SITE}/mailing-schedule',
                              json={'schedule': [{'from': '00:00', 'to': '00:15'}]})
        await _letter(profile)

        # Двенадцать дня — заведомо вне смены 00:00–00:15.
        await send_round_run(12 - _utc_hour())

        assert post == [], 'письмо ушло вне смены'

    async def test_empty_schedule_means_no(self, auth_client, site, profile, post):
        """⚠ Пустое расписание — «нет», а не «всегда». Иначе сайт без смен слал бы
        круглосуточно, и это была бы первая же беда после включения."""
        await auth_client.put(f'/api/dating/site/{SITE}/mailing-schedule',
                              json={'schedule': []})
        await _letter(profile)

        await send_round_run(0)

        assert post == []


class TestWhyItStands:
    """⚠⚠ Молчащий пул обязан объяснять себя — то же правило, что у инвайтов.

    И у каждого пула теперь свой повод: рассылка говорит свою норму, причём числом,
    а два других пула о ней не обязаны знать вовсе — ответы под ней не стоят.
    """

    async def test_the_switch_off_says_so(self):
        """Выключатели стенда сняты, пока фикстура `site` их не подняла."""
        said = await send_why(SITE, 'mailing', 0)

        assert said == 'отправка рассылки выключена'

    async def test_each_pool_names_itself(self):
        """⚠ Три пула — три выключателя, и путать их нельзя: человек пойдёт снимать
        не тот."""
        assert 'ответов' in await send_why(SITE, 'mail_reply', 0)
        assert 'избранным' in await send_why(SITE, 'favorite_mail', 0)

    async def test_outside_the_shift_says_so(self, site, auth_client):
        await auth_client.put(f'/api/dating/site/{SITE}/mailing-schedule',
                              json={'schedule': [{'from': '00:00', 'to': '00:15'}]})

        # Полдень по местному — заведомо вне смены 00:00–00:15.
        said = await send_why(SITE, 'mailing', 12 - _utc_hour())

        assert 'не смена' in said

    async def test_the_mailing_norm_says_so(self, site, monkeypatch):
        """⚠ Строка обязана назвать норму рассылки числом — иначе молчание
        читается как поломка. И только рассылку: ответы о ней не должны лгать.
        """
        monkeypatch.setattr(round_, '_max_day', _fixed(5))
        monkeypatch.setattr(round_, '_today', _fixed(5))

        said = await send_why(SITE, 'mailing', 0)

        assert 'ушло 5 из 5' in said and 'рассылки' in said

    async def test_other_pools_do_not_mention_the_norm(self, site, profile,
                                                        monkeypatch):
        """Строка про чужую норму в шапке ответов была бы ложью: ответы под
        рассылочной нормой не стоят — и не слышат о ней."""
        monkeypatch.setattr(round_, '_max_day', _fixed(0))
        monkeypatch.setattr(round_, '_today', _fixed(0))
        await _letter(profile, 'mail_reply')

        assert await send_why(SITE, 'mail_reply', 0) == ''

    async def test_an_empty_pool_says_so(self, site):
        assert await send_why(SITE, 'mailing', 0) \
            == 'пул пуст: отправлять нечего'

    async def test_silence_when_there_is_a_letter(self, site, profile):
        """Есть что отправить — сказать нечего."""
        await _letter(profile)

        assert await send_why(SITE, 'mailing', 0) == ''

    async def test_an_unknown_pool_is_silent(self, site):
        """Чужое имя таблицы — не повод объяснять: в SQL оно и не поедет."""
        assert await send_why(SITE, 'dating_users', 0) == ''


def _fixed(value):
    """Заглушка настройки. ⚠ Любые доводы: у `_today` их два, у `_max_day` один."""
    async def _got(*args, **kwargs):
        return value

    return _got


def _utc_hour() -> int:
    """Текущий час UTC — чтобы задать смещение, дающее нужный местный час."""
    import time

    return time.gmtime().tm_hour


class TestReplyWaitsItsLength:
    """⚠⚠ Ответ уходит не раньше, чем его написал бы человек.

    Собеседник видит два времени рядом — своё письмо и наш ответ. Olena ↔ Benoit,
    25.10.2026: четыреста слов через четыре минуты. Столько не печатает никто, и
    разбирать текст для этого не нужно.

    ⚠ Держит это **круг отправки**: набор пула про задержку не знает — ответ
    сочиняется тогда, когда пришло письмо, а ждёт он уже готовым.
    """

    async def _delay(self, monkeypatch, per10: int) -> None:
        """Коэффициент себе обратно: фикстура сайта снимает его для всех прочих."""
        monkeypatch.setattr('src.dating.reply_delay.reply_delay_per10',
                            _fixed(int(per10)))

    async def _long_reply(self, profile_id: int, ago_min: int) -> int:
        """Длинный ответ на письмо заданной давности."""
        from mysql_.mysql_ import mysql_get_db_async

        uid = f'snd-delay-{uuid.uuid4().hex[:8]}'

        async with mysql_get_db_async() as db:
            await db.execute(
                'INSERT INTO mail_in (site_id, profile_id, message_id, uid,'
                " name, body, date_post) VALUES (%s, %s, %s, %s, 'Тест', 'Письмо',"
                ' NOW() - INTERVAL %s MINUTE)',
                (SITE, int(profile_id), int(uuid.uuid4().int % 1_000_000_000),
                 uid, int(ago_min)))
            await db.connection.commit()
            await db.execute('SELECT LAST_INSERT_ID() AS id')
            mail_in_id = int((await db.fetchone())['id'])

            await db.execute(
                'INSERT INTO mail_reply (site_id, profile_id, mail_in_id, uid,'
                ' name, body, status, error)'
                " VALUES (%s, %s, %s, %s, 'Тест', %s, 'draft', '')",
                (SITE, int(profile_id), mail_in_id, uid,
                 ' '.join(['word'] * 300)))
            await db.connection.commit()
            await db.execute('SELECT LAST_INSERT_ID() AS id')

            return int((await db.fetchone())['id'])

    async def test_a_fresh_long_reply_does_not_go(self, site, profile, post,
                                                  monkeypatch):
        """Триста слов при тридцати секундах — четверть часа, и заход уходит ни
        с чем, хотя ответ готов и ошибок у него нет."""
        await self._delay(monkeypatch, 30)
        await self._long_reply(profile, ago_min=0)

        await send_round_run(0)

        assert not post, 'длинный ответ ушёл сразу после входящего письма'

    async def test_an_old_letter_lets_it_go(self, site, profile, post,
                                            monkeypatch):
        """⚠ Прошедшее засчитывается: письмо часовой давности снимает задержку —
        эти минуты человек и потратил бы на чтение и печать."""
        await self._delay(monkeypatch, 30)
        await self._long_reply(profile, ago_min=60)

        await send_round_run(0)

        assert len(post) == 1, 'ответ держат, хотя время на него давно вышло'

    async def test_the_idle_names_the_delay(self, site, profile, monkeypatch):
        """⚠ Пул полон, ошибок нет, отправка включена — и тишина. Без строки
        словами это читается как поломка: так уже было с паузой и остудой."""
        await self._delay(monkeypatch, 30)
        await self._long_reply(profile, ago_min=0)

        said = await send_why(SITE, 'mail_reply', 0)

        assert 'ещё пишется' in said, said


class TestThePauseBelongsToTheGirl:
    """⚠⚠ Пауза между письмами — про анкету, а не про получателя, и держит её
    **этот круг**, а не только набор пула.

    Настройка `favorite_mail_gap` значила «одному человеку не чаще раза в срок».
    Про то, сколько за этот срок напишет одна девушка **разным** людям, правило
    не говорило ничего — и круг, уносящий по письму в минуту, выпускал их подряд.
    Живьём 21 сентября: четыре письма от Елены четверым за восемь минут (18:29,
    18:30, 18:32, 18:37), и сайт ответил отказом.

    ⚠ Проверять надо именно здесь. Черновики лежат готовыми, набор пула их уже
    отпустил, и «одно письмо за срок» решается в последней точке — при выборе,
    что унести следующим.
    """

    async def _favorites_on(self, gap: int, monkeypatch) -> None:
        """Включает пул избранных у сайта и задаёт паузу.

        ⚠⚠ **И заново снимает `is_gone`, хотя фикстура его уже снимала.**

        Сайта с выдуманным номером у соседа нет, и фоновая сверка честно метит
        его «ушёл» — а `send_round._sites()` таких пропускает. Сверка заводится
        открытием любой страницы раздела и ходит по своему сроку, то есть
        случается **посреди** прогона и бьёт по случайному тесту: за шесть
        прогонов покраснели три разных, и каждый файл поодиночке был зелёным.

        Снимаем метку **здесь**, в общей точке всех тестов класса, а не в каждом:
        порознь о ней забывают, и забытый тест краснел бы раз в несколько
        прогонов — то есть выглядел бы плавающим, а не сломанным.
        """
        from mysql_.mysql_ import mysql_get_db_async
        from src.dating import favorite_mail

        # ⚠ Пауза — **промежуток**, и здесь он вырожденный: обе границы равны,
        # то есть жребию нечего бросать. Тестам паузы разброс и не нужен — они
        # про то, держится ли срок вообще; сам жребий стережёт
        # `test_favorite_mail.py`.
        monkeypatch.setattr(favorite_mail, 'favorite_mail_gap_range',
                            _fixed((int(gap), int(gap))))

        async with mysql_get_db_async() as db:
            await db.execute('UPDATE site SET favorite_mail_send_on = 1,'
                             ' is_gone = 0 WHERE site_id = %s', (SITE,))
            await db.connection.commit()

    async def _online(self, profile_id: int) -> None:
        """⚠ Письма избранным уходят только от анкеты в сети — своё правило круга,
        и без него эти тесты молчали бы по чужой причине."""
        from mysql_.mysql_ import mysql_get_db_async

        async with mysql_get_db_async() as db:
            await db.execute("UPDATE profile SET status = 'online' WHERE id = %s",
                             (int(profile_id),))
            await db.connection.commit()

    async def _sent(self, profile_id: int, hours_ago: float) -> None:
        """Её ушедшее письмо — то, от чего пауза отсчитывается."""
        from mysql_.mysql_ import mysql_get_db_async

        async with mysql_get_db_async() as db:
            await db.execute(
                'INSERT INTO favorite_mail'
                ' (site_id, profile_id, uid, name, body, status, sent_at)'
                " VALUES (%s, %s, %s, 'X', 'hi', 'sent',"
                ' NOW() - INTERVAL %s MINUTE)',
                (SITE, int(profile_id), f'u{uuid.uuid4().hex[:8]}',
                 int(hours_ago * 60)))
            await db.connection.commit()

    async def test_a_girl_who_just_wrote_is_skipped(self, site, profile, post,
                                                    monkeypatch):
        """Она написала минуту назад — второе письмо не уходит, хоть и готово."""
        await self._favorites_on(4 * 3600, monkeypatch)
        await self._online(profile)
        await self._sent(profile, 0)
        await _letter(profile, 'favorite_mail')

        await send_round_run(0)

        assert not post, 'письмо ушло вторым подряд от той же анкеты'

    async def test_another_girl_goes_instead(self, site, profile, post,
                                             auth_client, monkeypatch):
        """⚠⚠ И это главное: пауза одной не запирает всех.

        Условие стоит **в запросе**, а не отсевом после выборки. Отсей мы занятую
        девушку после — заход кончился бы ничем, и сайт молчал бы всю паузу, хотя
        писать готовы остальные.
        """
        await self._favorites_on(4 * 3600, monkeypatch)
        other = (await auth_client.post('/api/dating/profile', json={
            'site_id': LIVE_SITE,
            'login': f'snd2-{uuid.uuid4().hex[:8]}@example.com',
            'password': 'x'})).json()['result']

        try:
            await self._online(profile)
            await self._online(other)
            await self._sent(profile, 0)

            busy = await _letter(profile, 'favorite_mail')
            free = await _letter(other, 'favorite_mail')

            await send_round_run(0)

            assert len(post) == 1, 'заход ушёл ни с чем или унёс лишнее'
            assert post[0]['id'] == free, \
                f'круг взял письмо занятой анкеты (#{busy}) вместо свободной'
        finally:
            await auth_client.delete(f'/api/dating/profile/{other}')

    async def test_a_long_silence_lets_her_write(self, site, profile, post,
                                                 monkeypatch):
        await self._favorites_on(4 * 3600, monkeypatch)
        await self._online(profile)
        await self._sent(profile, 30)
        await _letter(profile, 'favorite_mail')

        await send_round_run(0)

        assert len(post) == 1, 'пауза давно вышла, а письмо не ушло'

    async def test_other_pools_are_not_touched(self, site, profile, post,
                                               monkeypatch):
        """⚠ Пауза — настройка избранных, и распоряжаться чужими пулами ей не по
        чину: у рассылки и ответов свои правила и свои сроки.

        Ответ же паузой избранных не задерживается вовсе — что пределы избранных
        считают все её ушедшие письма вместе, держит `test_favorite_mail.py`.
        """
        await self._favorites_on(4 * 3600, monkeypatch)
        await self._online(profile)
        await self._sent(profile, 0)
        mailing = await _letter(profile, 'mailing')

        await send_round_run(0)

        assert len(post) == 1 and post[0]['id'] == mailing, \
            'пауза избранных задержала письмо чужого пула'

    async def test_the_idle_reason_names_the_pause(self, site, profile,
                                                   monkeypatch):
        """⚠ Пул полон, ошибок нет, а отправка молчит — состояние, которое без
        объяснения читается как поломка."""
        await self._favorites_on(4 * 3600, monkeypatch)
        await self._online(profile)
        await self._sent(profile, 0)
        await _letter(profile, 'favorite_mail')

        why = await send_why(SITE, 'favorite_mail', 0)

        assert 'пауза' in why, why


class TestTheFlagsAgree:
    """⚠⚠ Два списка выключателей обязаны сходиться.

    `_POOLS` отвечает «какой пул чем включён», `_sites()` — «куда вообще идти
    за работой». Выражены они порознь намеренно: застава
    `test_site_flags_alive.py` ищет читателя флага **внутри функции**, и
    выведи мы один из другого, имя флага исчезло бы из её поля зрения — а
    именно так однажды тихо перестала работать `chat_answer_on`.

    Цена такой записи — расхождение, и держит его этот сторож: новый пул,
    попавший в `_POOLS` и забытый в `_sites()`, не получил бы ни одного сайта
    и молчал бы без единой ошибки.
    """

    def test_every_pool_switch_opens_a_site(self):
        import inspect

        from src.dating.send_round import _POOLS, _sites

        said = set(re.findall(r"'([a-z_]+_on)'", inspect.getsource(_sites)))

        assert said == {one[0] for one in _POOLS}, (
            'списки выключателей разошлись: пул, которого нет в `_sites`, '
            'не получит ни одного сайта и промолчит без ошибки')


class TestTheQueryCarriesWhatMarksNeed:
    """⚠⚠⚠ Запрос круга обязан выбрать **всё, что читают отметки после
    отправки**. Эта грабля случилась уже **четырежды**.

    Круг берёт письмо фиксированным списком столбцов, а `mail_send_mark` после
    удачной отправки читает из той же строки поля, которых в списке может не
    оказаться. Молчание при этом полное: письмо ушло, запрос не упал, ошибок
    нет — просто отметка легла на `None` и не сделала ничего.

    | когда | чего не выбрали | чем кончилось |
    |-------|-----------------|---------------|
    | 26.09.2026 | `reply_to` | панель числила неотвеченными письма суточной давности |
    | ранее | `message_id` | сосед не узнавал, что реплика дошла |
    | 31.10.2026 | `intention_id`, `gallery_file_id` | письмо ушло, обещание осталось открытым, снимок числился непоказанным |
    | 01.11.2026 | `name` | история легла **в новую пару** у соседа: ключ вышел `207556_207556` вместо `207556_christopher_buchas`, и разговор у агента остался без письма |

    ⚠⚠ Четвёртый случай и показал, чего сторожу не хватало: он перечислял
    столбцы **поимённо**, то есть знал ровно о тех читателях, что были. Новый
    читатель (`_history_write`) добавился мимо списка. Поэтому ниже стоит
    проверка **по соответствию** — она берёт поля из самого читателя и требует
    их от запроса, и следующего читателя поймает сама.

    ⚠ Сторож проверяет **текст запроса**, а не поведение: поймать это живым
    прогоном можно лишь через сутки и случайно — ровно так его и находили.
    """

    def _query(self) -> str:
        import inspect

        from src.dating import send_round

        return inspect.getsource(send_round)

    def test_the_intention_columns_are_selected(self):
        """⚠⚠ `intention_id` закрывает обещание, `gallery_file_id` помнит
        снимок. Без них ушедшее письмо не делает ни того, ни другого — и
        следующее обещание отправит тот же кадр второй раз."""
        code = self._query()

        assert 'm.intention_id' in code and 'm.gallery_file_id' in code, (
            'запрос круга не выбирает столбцы исполнения — отметки лягут на '
            'нули и молча ничего не сделают')

    def test_they_ride_only_for_their_table(self):
        """⚠ У прочих пулов этих колонок нет вовсе: выбери их безусловно, и
        запрос упал бы на `mailing` с «Unknown column»."""
        code = self._query()
        spot = code.find('m.intention_id')
        head = code.rfind('\n', 0, spot - 120)

        assert "table == 'intention_mail'" in code[head:spot + 160], (
            'столбцы исполнения выбираются безусловно — другие пулы упадут')

    def test_every_optional_column_is_in_the_select(self):
        """⚠ Собранные куски обязаны попасть **в сам запрос**: объявить
        переменную и забыть подставить — то же молчание, что и не объявить.
        """
        code = self._query()
        spot = code.find('SELECT m.id, m.site_id')
        query = code[spot:code.find('ORDER BY m.id LIMIT 1', spot)]

        for piece in ('{reply_to}', '{message_id}', '{doing}'):
            assert piece in query, f'кусок {piece} собран, но не подставлен'

    def test_the_query_feeds_every_field_the_history_reads(self):
        """⚠⚠⚠ Главная проверка класса: поля берутся **из самого читателя**.

        Прежние три проверки знают имена столбцов наперёд, и четвёртый случай
        прошёл мимо них именно поэтому — появился новый читатель строки, а в
        список его поля никто не внёс. Эта проверка спрашивает `_history_write`,
        что он читает из `letter`, и требует каждое поле от запроса. Забудется
        столбец у пятого читателя — упадёт здесь, а не через сутки на проде.

        ⚠ Поля читаются из исходника, а не из вызова: подать функции готовый
        словарь (ровно так и делают три теста в `test_mail_send.py`) значит
        проверить её саму, а не дорогу от базы к ней. Молчаливая беда живёт
        именно в этой дороге.
        """
        import inspect
        import re

        from src.dating.mail_send import _history_write

        reader = inspect.getsource(_history_write)
        # ⚠ Берём и `letter.get('x')`, и `letter['x']`: первая форма нынешняя,
        # вторая появится при первой же правке, и сторож её не должен терять.
        fields = set(re.findall(r"letter(?:\.get)?[\(\[]\s*'(\w+)'", reader))

        assert fields, ('сторож не нашёл в `_history_write` ни одного поля '
                        'строки — изменилась форма чтения, проверка ослепла')

        code = self._query()
        spot = code.find('SELECT m.id, m.site_id')
        query = code[spot:code.find('ORDER BY m.id LIMIT 1', spot)]

        # Кусками уезжают столбцы, которых у прочих пулов нет; для нашей сверки
        # важно лишь то, что столбец в запрос попадает хоть каким способом.
        optional = re.findall(r"',\s*m\.(\w+)'", code)

        missing = sorted(f for f in fields
                         if f'm.{f}' not in query and f not in optional)

        assert not missing, (
            f'`_history_write` читает из строки {missing}, а запрос круга их '
            'не выбирает: отметка ляжет на пустоту и промолчит. Так 01.11.2026 '
            'потерялось `name`, и история легла в новую пару у соседа')

