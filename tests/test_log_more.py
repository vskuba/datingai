"""Что журнал научился ловить после разбора живого прода.

Разбор показал пять слепых мест, и каждое стоило реального расследования вслепую:

| Чего не хватало | Во что обошлось |
|-----------------|-----------------|
| размера ответа | «200» у отказа неотличим от «200» у удачи |
| тела отказа | **156 инвайтов** с причиной «сайт ответил 200» — что сказал сайт, неизвестно |
| номера попытки | долбёжку из девяти входов видно только счётом строк глазами |
| владельца запроса к соседу | 3512 строк в сутки без ответа «кому это готовили» |
| причины простоя | пул инвайтов стоял неделю и выглядел работающим |
"""

import pytest

pytestmark = pytest.mark.profile


class TestResponseSize:
    """⚠⚠ Длина ответа: пустое тело при коде 200 — частый тихий отказ.

    ⚠ **Проверяем сборку строки, а не запись в базу**, и это не полумера: сюита в
    журнал запросов не пишет вовсе (`dating_request_off`) — строка от поддельного
    сайта неотличима от живой, а по этому журналу считают, сколько раз мы
    постучались к сайту на самом деле.
    """

    def test_the_length_counts_the_whole_body(self):
        """Длина пишется **всегда**, в отличие от самого тела.

        Ради неё всё и заведено: «200, 43 байта» рядом с «200, 8 КБ» видно
        глазом, без чтения тела и без разбирательства.
        """
        import inspect

        from src.dating import dating_request

        code = inspect.getsource(dating_request.dating_request_write)

        assert "len(body or '')" in code, 'длина ответа снова не пишется'

    def test_a_successful_body_is_not_stored(self):
        """⚠⚠ Тело удачного ответа не храним вовсе, и это не экономия места.

        Там переписка живых людей, списки собеседников, почта: 52 тысячи строк
        в сутки превратили бы журнал в копию базы сайта — с личными данными.
        """
        from src.dating.dating_request import _body_keep

        assert _body_keep('{"Messages": [много личного]}', ok=True) == ''


class TestFailureBody:
    """⚠ У отказа тело хранится: ради него журнал и правили."""

    def test_it_is_stored(self):
        from src.dating.dating_request import _body_keep

        got = _body_keep('{"IsSuccess":false,"Reason":"limit"}', ok=False)

        assert 'IsSuccess' in got, \
            'тело отказа снова не хранится — причину «сайт ответил 200» не разобрать'
        assert 'limit' in got

    def test_secrets_are_cut_out(self):
        """⚠⚠ Секреты вырезаются перед записью.

        Тело отказа входа вполне может нести эхо отправленного логина, а у нас
        уже был день, когда пароли уезжали в журнал открытым текстом.
        """
        from src.dating.dating_request import _body_keep

        got = _body_keep('{"login":"a@b.c","password":"хорошийпароль"}', ok=False)

        assert 'хорошийпароль' not in got, 'пароль уехал в журнал'
        assert '•••' in got, 'вместо пароля должна стоять метка'

    def test_a_long_body_is_cut(self):
        """⚠ Длинный ответ — уже не сообщение, а страница: в ленте её не читают."""
        from src.dating.dating_request import DATING_REQUEST_BODY_MAX, _body_keep

        assert len(_body_keep('я' * 5000, ok=False)) == DATING_REQUEST_BODY_MAX

    def test_the_rules_are_shared_with_the_sql_log(self):
        """⚠ Правила вырезания — **одни** на журнал базы и журнал запросов.

        Две копии регулярок разошлись бы в первый же день, когда список ключей
        пополнят, — и разошлись бы молча: журнал выглядел бы вычищенным.
        """
        import inspect

        from mysql_ import mysql_log

        assert 'logging_secret_clean' in inspect.getsource(mysql_log._clean)

    def test_the_site_api_hands_the_body_over(self):
        """⚠ Решает журнал, а не транспорт: иначе правило повторится в шести местах."""
        import inspect

        from src.dating import dating_site_api

        assert 'body=answer.text' in inspect.getsource(dating_site_api._request)


class TestWhoAskedTheNeighbour:
    """⚠⚠ Запрос к соседу знает, чья это работа."""

    async def test_the_context_travels(self):
        """Работа называет себя — и строка получает анкету с сайтом."""
        from src.dating.dating_request import (dating_request_about,
                                               dating_request_about_get)

        with dating_request_about(profile_id=849, site_id=343):
            assert dating_request_about_get() == (849, 343)

        assert dating_request_about_get() == (0, 0), \
            'контекст не сбросился — чужая работа получит эту анкету'

    def test_the_neighbour_door_reads_it(self):
        """⚠ Читает **дверь**, а не вызывающий: вызывающих полсотни."""
        import inspect

        from src.rhapsody import rhapsody_api

        assert 'dating_request_about_get()' in inspect.getsource(rhapsody_api._note)

    def test_scenario_runs_name_themselves(self):
        """Прогон сценария знает и анкету, и сайт — он их и передаёт."""
        import inspect

        from src.dating import dating_scenario

        assert 'dating_request_about(' in inspect.getsource(
            dating_scenario.dating_scenario_run)

    def test_llm_calls_name_the_site(self):
        """⚠ У вызова агента известен сайт; анкета — не всегда, и ноль честнее.

        Шаблон инвайта пишут на весь пул, а не девушке: приписать его анкете
        значило бы соврать.
        """
        import inspect

        from src.dating import dating_mail_agent

        assert 'dating_request_about(' in inspect.getsource(dating_mail_agent._chat_ask)


class TestIdleIsRecorded:
    """⚠⚠ Простой круга — запись, а не молчание.

    Пул инвайтов на проде застыл на 21 черновике и не набирался неделю. Со
    стороны выглядело как «работает»: круг ходил, чистил ушедших и не добавлял
    ничего. Понять причину можно было только чтением кода.
    """

    async def test_the_first_reason_is_written(self):
        from src.dating.dating_idle import dating_idle_forget, dating_idle_write

        dating_idle_forget('проба')

        assert await dating_idle_write('проба', 'пул пуст', site_id=999_343) is True

    async def test_the_same_reason_is_silent(self):
        """⚠ Пишется **смена** причины, а не каждый заход.

        Круг идёт раз в минуту; пиши он каждый раз, за ночь набежало бы пятьсот
        одинаковых строк, и настоящая беда утонула бы в них.
        """
        from src.dating.dating_idle import dating_idle_forget, dating_idle_write

        dating_idle_forget('проба')
        await dating_idle_write('проба', 'пул пуст', site_id=999_343)

        assert await dating_idle_write('проба', 'пул пуст', site_id=999_343) is False

    async def test_a_new_reason_speaks_again(self):
        from src.dating.dating_idle import dating_idle_forget, dating_idle_write

        dating_idle_forget('проба')
        await dating_idle_write('проба', 'пул пуст', site_id=999_343)

        assert await dating_idle_write('проба', 'норма выбрана',
                                       site_id=999_343) is True

    async def test_going_back_to_work_is_an_event_too(self):
        """⚠ Уход простоя — тоже событие.

        Без него в журнале остаётся вопрос без ответа: чинилось оно само или всё
        ещё стоит.
        """
        from src.dating.dating_idle import dating_idle_forget, dating_idle_write

        dating_idle_forget('проба')
        await dating_idle_write('проба', 'пул пуст', site_id=999_343)

        assert await dating_idle_write('проба', '', site_id=999_343) is True

    def test_the_sender_explains_itself(self):
        """Круг отправки инвайтов пишет причину — тем же кодом, что отвечает странице."""
        import inspect

        from src.dating import dating_invite_send_round

        code = inspect.getsource(dating_invite_send_round.dating_invite_send_round_run)

        assert 'dating_idle_write(' in code
        assert 'dating_invite_send_why(' in code, \
            'круг завёл свою копию правил — она разойдётся с ответом странице'


class TestLoginAttemptNumber:
    """⚠ Номер попытки в строке журнала: долбёжку видно сразу."""

    async def test_the_first_try_has_no_number(self):
        """Приписывать «попытка 1 из 3» обычному входу — красить тревожным то,
        что происходит каждую смену; номер перестал бы значить что-либо."""
        from src.dating.dating_login import _try_text

        assert await _try_text({'site_id': 999_343, 'login_fail_tries': 0}) == 'Вход запущен'

    async def test_a_repeat_says_which_one(self):
        from src.dating.dating_login import _try_text

        text = await _try_text({'site_id': 999_343, 'login_fail_tries': 2})

        assert 'попытка 3 из' in text, text


class TestCardIsNotReReadHmm:
    """⚠ Заполненную карточку заново не читаем.

    Строка «Анкета не прочиталась: у сайта нет сценария анкеты для роли lady»
    висела в журнале на **каждый вход** — 19 раз в сутки — и была враньём про
    беду: карточки заполнены, просто приехали импортом. Сценария чтения у
    Ladadate нет и, судя по журналу, не было никогда.
    """

    def test_an_empty_card_is_read(self):
        from src.dating.dating_login import _card_empty

        assert _card_empty({'profile': '{}'})
        assert _card_empty({'profile': '{"photo": "http://x/1.jpg"}'}), \
            'одного фото мало: имена у наших девушек повторяются'

    def test_a_named_card_is_left_alone(self):
        from src.dating.dating_login import _card_empty

        assert not _card_empty({'profile': '{"name": "Diana"}'})

    def test_the_login_asks_before_reading(self):
        import inspect

        from src.dating import dating_login

        assert '_card_empty(profile)' in inspect.getsource(dating_login.dating_login_run)


async def _last(path: str) -> dict:
    """Последняя строка журнала по этому пути."""
    from mysql_.mysql_ import mysql_get_db_async

    async with mysql_get_db_async() as db:
        await db.execute('SELECT bytes, body FROM dating_request'
                         ' WHERE path = %s ORDER BY id DESC LIMIT 1', (path,))

        return dict((await db.fetchone()) or {})
