"""Дверь к чату агента: `chat_talk`, `chat_ask`, `chat_delivered`.

Сосед пишет обмен в свою таблицу **внутри прогона** workflow — до того, как
панель попробует отправить реплику. Три функции здесь единственный путь к
этому обмену: `chat_talk` отдаёт ответ и номер строки, `chat_ask` — тонкая
обёртка для тех, кому номер не нужен (почта, галерея), а `chat_delivered`
отмечает, что реплика реально ушла собеседнику.

⚠ **Живой HTTP запрещён** (`testing.md`, §1.2): подменяется не транспорт
httpx и не поддельный сосед (`rhapsody_fake.py`), а приватная `_request` того
же модуля — тело всех трёх функций интересует именно то, как они разбирают
то, что `_request` вернула или бросила, а не то, как строится запрос (это
уже проверяет `test_rhapsody_link.py`).

Прежде эти три функции в сюите не выполнялись вовсе: звонящие (`answer.py` и
другие) подменяли `rhapsody_api_chat_ask` целиком, и тело `_request` → разбор
ответа не исполнялось ни разу. Ключевая гарантия задачи — «отказ отметки
доставки не роняет вызывающего» — держалась только на чтении кода.
"""

import pytest

from src.rhapsody import rhapsody_api
from src.rhapsody.rhapsody_api import (RhapsodyError, rhapsody_api_chat_ask,
                                       rhapsody_api_chat_delivered,
                                       rhapsody_api_chat_talk)

pytestmark = pytest.mark.core


@pytest.fixture
def requested(monkeypatch):
    """Подменяет `_request`, отдавая по одному заготовленному ответу на вызов.

    Очередь, а не функция по коду пути: некоторым тестам нужно различить
    первый и второй поход (хотя здесь все вызовы — ровно один на тест), а
    хранить и то и другое проще списком вызовов и списком ответов.
    """
    calls = []
    answers = []

    async def _fake(method, path, payload=None, timeout=None):
        calls.append({'method': method, 'path': path, 'payload': payload})
        answer = answers.pop(0)
        if isinstance(answer, Exception):
            raise answer

        return answer

    monkeypatch.setattr(rhapsody_api, '_request', _fake)

    return {'calls': calls, 'answers': answers}


class TestChatTalk:
    """`rhapsody_api_chat_talk` — текст и номер обмена разом."""

    async def test_text_and_number_come_through(self, requested):
        requested['answers'].append({'result': 'привет', 'message_id': 777})

        got = await rhapsody_api_chat_talk(7, 'вопрос')

        assert got == {'text': 'привет', 'message_id': 777, 'intentions': {}}

    async def test_the_question_and_agent_travel_in_the_payload(self, requested):
        """⚠ Таймаут агента и номер агента — не на словах, а в самом запросе."""
        requested['answers'].append({'result': '', 'message_id': 0})

        await rhapsody_api_chat_talk(7, 'вопрос', {'companion_name': 'gerard'})

        sent = requested['calls'][0]
        assert sent['path'] == '/chat/ask'
        assert sent['payload']['agent_id'] == 7
        assert sent['payload']['question'] == 'вопрос'
        assert sent['payload']['variables']['companion_name'] == 'gerard'
        assert sent['payload']['variables']['chat_timeout'] == \
            rhapsody_api.RHAPSODY_API_CHAT_TIMEOUT

    async def test_silence_is_an_empty_string_not_an_error(self, requested):
        """Агент может промолчать — прогон ушёл в «Тишину», и это законный ответ."""
        requested['answers'].append({'result': '', 'message_id': 0})

        got = await rhapsody_api_chat_talk(7, 'вопрос')

        assert got == {'text': '', 'message_id': 0, 'intentions': {}}

    @pytest.mark.parametrize('raw', [None, '', 'не число', [], {}])
    async def test_a_missing_or_unreadable_number_is_zero(self, requested, raw):
        """⚠⚠ Сердце находки 1: `message_id` бывает каким угодно, кроме числа.

        `companion_session` — чужая таблица, и сосед отдаёт то, что в ней
        лежит на этот момент. `None`, пустая строка, не-число — всё это
        законные значения столбца, а не повод уронить чат одной необработанной
        `ValueError`/`TypeError`.
        """
        requested['answers'].append({'result': 'ответ', 'message_id': raw})

        got = await rhapsody_api_chat_talk(7, 'вопрос')

        assert got == {'text': 'ответ', 'message_id': 0, 'intentions': {}}

    async def test_the_key_is_simply_absent(self, requested):
        """Соседний случай «ключа нет вовсе» — не то же самое, что `None`,
        но разбор (`data.get('message_id') or 0`) не должен его отличать."""
        requested['answers'].append({'result': 'ответ'})

        got = await rhapsody_api_chat_talk(7, 'вопрос')

        assert got == {'text': 'ответ', 'message_id': 0, 'intentions': {}}

    async def test_intentions_come_through_with_their_context(self, requested):
        """⚠⚠ Контракт — объект «метка → контекст», а не список меток.

        Контекст собирает тот же прогон, что нашёл намерение: письму нужен его
        текст и приметы снимка, звонку — время. Списком меток исполнителю
        пришлось бы добывать это заново, по пересказу вместо первоисточника.
        """
        requested['answers'].append(
            {'result': 'ок', 'message_id': 1,
             'intentions': {'she_promised_send_photo': {'letter': 'hi',
                                                        'tags': 'outdoor'}}})

        got = await rhapsody_api_chat_talk(1, 'вопрос')

        assert got['intentions'] == {'she_promised_send_photo':
                                     {'letter': 'hi', 'tags': 'outdoor'}}

    async def test_no_intentions_key_is_an_empty_object(self, requested):
        """⚠ Ключ отдаётся всегда: «пусто» и «ключа нет» зовущий сольёт в одно,
        и пропажа стала бы неотличима от штатной тишины."""
        requested['answers'].append({'result': 'ок', 'message_id': 1})

        got = await rhapsody_api_chat_talk(1, 'вопрос')

        assert got['intentions'] == {}

    async def test_a_list_is_no_longer_understood(self, requested):
        """⚠⚠ Старая форма (список меток) **не понимается**, и это намеренно.

        Отсюда порядок выкладки: сперва наше чтение, потом запись у соседа.
        Прими мы и список — расхождение сторон прошло бы незамеченным, и
        намерения приезжали бы без контекста неизвестно сколько.
        """
        requested['answers'].append(
            {'result': 'ок', 'message_id': 1,
             'intentions': ['she_promised_send_photo']})

        got = await rhapsody_api_chat_talk(1, 'вопрос')

        assert got['intentions'] == {}

    async def test_a_strange_intentions_shape_is_an_empty_object(self, requested):
        requested['answers'].append(
            {'result': 'ок', 'message_id': 1, 'intentions': 'не объект'})

        got = await rhapsody_api_chat_talk(1, 'вопрос')

        assert got['intentions'] == {}

    async def test_blank_marks_are_dropped_and_a_bad_context_is_emptied(
            self, requested):
        """⚠ Метка без имени выбрасывается, а контекст не той формы становится
        пустым — но саму метку не теряет: форму кладёт сосед, и сменить её он
        может раньше, чем мы узнаем."""
        requested['answers'].append(
            {'result': 'ок', 'message_id': 1,
             'intentions': {'  promised_photo  ': {'letter': 'hi'},
                            '': {'letter': 'ничей'},
                            'promised_call': 'не объект'}})

        got = await rhapsody_api_chat_talk(1, 'вопрос')

        assert got['intentions'] == {'promised_photo': {'letter': 'hi'},
                                     'promised_call': {}}

    async def test_numeric_string_is_accepted(self, requested):
        """Сосед вправе отдать номер строкой — `int()` должен его принять."""
        requested['answers'].append({'result': 'ответ', 'message_id': '777'})

        got = await rhapsody_api_chat_talk(7, 'вопрос')

        assert got == {'text': 'ответ', 'message_id': 777, 'intentions': {}}

    async def test_refusal_is_not_swallowed(self, requested):
        """⚠ В отличие от «доставки», отказ в самом вопросе **не гасится**: тут
        нечего отменять, реплика ещё не ушла."""
        requested['answers'].append(RhapsodyError('браузер соседа занят', code=409))

        with pytest.raises(RhapsodyError):
            await rhapsody_api_chat_talk(7, 'вопрос')


class TestChatAsk:
    """`rhapsody_api_chat_ask` — на этой строке держатся `mail_agent.py` и
    `gallery_vision.py`: обёртка обязана продолжать отдавать **строку**."""

    async def test_it_still_returns_a_plain_string(self, requested):
        requested['answers'].append({'result': 'письмо для адресата', 'message_id': 42})

        got = await rhapsody_api_chat_ask(7, 'вопрос')

        assert got == 'письмо для адресата'
        assert isinstance(got, str)

    async def test_the_message_id_is_not_leaked_into_the_string(self, requested):
        """⚠⚠ Обёртка режет словарь до текста — номер обмена звонящему через
        `chat_ask` вообще не виден: почтовым агентам и галерее он не нужен, и
        получить его здесь означало бы завести вторую, не ту дверь."""
        requested['answers'].append({'result': 'текст', 'message_id': 999})

        got = await rhapsody_api_chat_ask(7, 'вопрос')

        assert got == 'текст'

    async def test_silence_is_still_an_empty_string(self, requested):
        requested['answers'].append({'result': '', 'message_id': 0})

        assert await rhapsody_api_chat_ask(7, 'вопрос') == ''


class TestChatDelivered:
    """`rhapsody_api_chat_delivered` — отметка не имеет права ронять отправку:
    реплика к этому моменту уже ушла живому человеку."""

    async def test_marked_one_is_true(self, requested):
        requested['answers'].append({'result': {'marked': 1}})

        assert await rhapsody_api_chat_delivered(777) is True

    async def test_marked_zero_is_false(self, requested):
        """Уже отмеченная строка — обычный исход, не беда: ручка соседа
        идемпотентна."""
        requested['answers'].append({'result': {'marked': 0}})

        assert await rhapsody_api_chat_delivered(777) is False

    @pytest.mark.parametrize('number', [0, -1, -777])
    async def test_zero_or_negative_does_not_call_the_neighbour(self, requested, number):
        """⚠ Ноль — «отмечать нечего» (агент смолчал, `chat_talk` его и не дал);
        отрицательный — такого номера не бывает вовсе. Оба не стоят похода
        наружу."""
        got = await rhapsody_api_chat_delivered(number)

        assert got is False
        assert requested['calls'] == [], 'ушли к соседу за ничего не значащим номером'

    async def test_the_number_travels_in_the_payload(self, requested):
        requested['answers'].append({'result': {'marked': 1}})

        await rhapsody_api_chat_delivered(777)

        sent = requested['calls'][0]
        assert sent['path'] == '/chat/message/delivered'
        assert sent['payload'] == {'message_id': 777}

    async def test_a_refusal_from_the_neighbour_is_false_not_raised(self, requested):
        """⚠⚠ Главная гарантия задачи. Реплика уже ушла собеседнику: бросить
        исключение значило бы отменить то, что отменить нельзя. Вместо этого —
        `False` и строка в журнале."""
        requested['answers'].append(RhapsodyError('браузер соседа занят', code=409))

        got = await rhapsody_api_chat_delivered(777)

        assert got is False

    async def test_a_broken_link_is_false_not_raised(self, requested):
        """То же самое для обрыва связи — `_request` бросает `RhapsodyError`
        и на отказ по коду, и на то, что соседа вовсе нет; отличать их здесь
        не нужно, важен исход."""
        requested['answers'].append(RhapsodyError('RhapsodyAi недоступен: connect timeout'))

        got = await rhapsody_api_chat_delivered(777)

        assert got is False

    @pytest.mark.parametrize('result', [[], 'ok', 1, True, ['marked']])
    async def test_a_non_dict_result_is_false_not_raised(self, requested, result):
        """⚠⚠ Находка 2. Разбор ответа стоял вне `try`: ответь сосед кодом 200
        с `result`, который не словарь, `.get('marked')` бросал
        `AttributeError`, и он уходил наверх — ровно там, где реплика уже
        недоступна для отмены."""
        requested['answers'].append({'result': result})

        got = await rhapsody_api_chat_delivered(777)

        assert got is False

    async def test_a_missing_result_key_is_false_not_raised(self, requested):
        """Соседний случай — `result` нет вовсе, а не `None` и не словарь:
        `data.get('result') or {}` подставляет `{}`, и `.get('marked')`
        работает как обычно."""
        requested['answers'].append({})

        assert await rhapsody_api_chat_delivered(777) is False
