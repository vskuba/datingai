"""Обвязка кругов воркера: сон до дедлайна, пояс каждым кругом, отказ не роняет смену.

## ⚠⚠ Зачем

Через `round_loop` идут **пятнадцать кругов** держателя сокетов — весь автоответ,
вся почта, присутствие, инвайты. Раньше каждый круг крутили руками, одними и теми же
четырьмя строками; их и свели сюда, чтобы копии не разъезжались. Цена поломки
соответствующая: сломайся обвязка — молча встанет всё сразу, а в панели это выглядит
как «ничего не происходит», без единой строки об ошибке.

Тестов у неё не было ни одного.

## Что проверяется

| Обещание | Почему важно |
|----------|--------------|
| сперва сон, потом работа | смена только что поднялась, тяжёлое в первую секунду незачем |
| сон обрезан остатком до дедлайна | иначе смена кончится позже, а за ней стоит следующая |
| отказ круга — в журнал, смена продолжается | у соседа бывает перезапуск; уронить смену дороже круга молчания |
| пояс перечитывается **каждым** кругом | `timezone` правят на ходу, запомненный держал бы старое расписание шесть часов |

## ⚠ Время подделано, а не выжидается

Сюита не может спать секундами: пятнадцать кругов по тику — это минуты на один
тест. Поэтому `asyncio.sleep` подменён — он копит, **сколько** просили спать, и
возвращает управление сразу. Проверяем именно запрошенные сроки: это и есть
поведение, а не то, сколько реально прошло.

⚠ `time.monotonic` подменён тоже, и вместе они дают управляемый ход времени: каждый
сон двигает часы ровно на столько, сколько просили. Без этого цикл `while` либо
кончился бы сразу, либо не кончился никогда.
"""

import asyncio

import pytest

from src.dating import round as round_mod

pytestmark = pytest.mark.core


@pytest.fixture
def clock(monkeypatch):
    """Управляемое время: сон не ждёт, а двигает часы и копит запрошенные сроки.

    Returns:
        dict: `naps` — сроки, которые просили проспать; `now` — текущие часы.
    """
    state = {'naps': [], 'now': 0.0}

    async def _sleep(seconds):
        state['naps'].append(float(seconds))
        state['now'] += float(seconds)

    monkeypatch.setattr(round_mod.asyncio, 'sleep', _sleep)
    monkeypatch.setattr(round_mod.time, 'monotonic', lambda: state['now'])

    return state


class TestTheSleepComesFirst:
    """⚠ Порядок: сон, потом работа. Смену только что подняли."""

    async def test_the_body_waits_for_the_first_tick(self, clock):
        order = []

        async def _body():
            order.append(('работа', clock['now']))

        await round_mod.round_loop(deadline=10, tick_sec=10, name='проба',
                                   body=_body)

        assert clock['naps'], 'круг не спал вовсе — работа началась сразу'
        assert order and order[0][1] > 0, 'работа пошла до первого сна'


class TestTheSleepIsCappedByTheDeadline:
    """⚠⚠ Сон обрезан остатком: иначе смена кончается позже, чем должна."""

    async def test_a_tick_bigger_than_the_rest_is_trimmed(self, clock):
        """Тик 60, а до дедлайна 5 — спать надо 5, не 60."""

        async def _body():
            pass

        await round_mod.round_loop(deadline=5, tick_sec=60, name='проба',
                                   body=_body)

        assert clock['naps'][0] == 5, (
            f'проспали {clock["naps"][0]} вместо остатка 5 — смена кончится позже')

    async def test_the_loop_stops_at_the_deadline(self, clock):
        """Круг не уходит за дедлайн: за ним в очереди следующая смена."""
        runs = []

        async def _body():
            runs.append(clock['now'])

        await round_mod.round_loop(deadline=10, tick_sec=3, name='проба',
                                   body=_body)

        assert runs, 'круг не отработал ни разу'
        assert clock['now'] >= 10, 'цикл вышел раньше дедлайна'
        assert all(one <= 10 for one in runs), 'работа шла после дедлайна'

    async def test_the_sleep_is_never_below_a_second(self, clock):
        """⚠ Нижняя граница — секунда: остаток бывает долей, и без неё цикл
        крутился бы вплотную, съедая процессор на пустых заходах."""

        async def _body():
            pass

        await round_mod.round_loop(deadline=0.2, tick_sec=60, name='проба',
                                   body=_body)

        assert all(one >= 1.0 for one in clock['naps']), \
            f'спали меньше секунды: {clock["naps"]}'


class TestAFailingRoundDoesNotKillTheShift:
    """⚠⚠ Отказ круга — строка в журнал, а не конец смены.

    Держатель стоит часами; за это время у соседа бывает и перезапуск, и окно
    техработ. Уронить смену случайной `KeyError` из разбора чужого ответа дороже,
    чем круг молчания до следующего тика.
    """

    async def test_the_loop_survives_and_keeps_going(self, clock):
        runs = []

        async def _body():
            runs.append(1)
            raise KeyError('сосед ответил не тем')

        await round_mod.round_loop(deadline=9, tick_sec=3, name='проба',
                                   body=_body)

        assert len(runs) >= 2, 'после первого отказа круг больше не заходил'

    async def test_the_failure_goes_to_the_journal_with_frames(self, clock,
                                                              monkeypatch):
        """⚠ Именно `logger_exception`, а не текст: «'NoneType' object has no
        attribute 'get'» без кадров не говорит, кто вернул не то."""
        said = []

        def _exception(message, exc=None):
            said.append((message, exc))

        monkeypatch.setattr(round_mod, 'logger_exception', _exception)

        async def _body():
            raise ValueError('беда')

        await round_mod.round_loop(deadline=3, tick_sec=3, name='почта',
                                   body=_body)

        assert said, 'отказ круга не попал в журнал'
        message, exc = said[0]
        assert 'почта' in message, f'в журнале нет имени круга: {message}'
        assert isinstance(exc, ValueError), 'исключение не передано — кадров не будет'

    async def test_a_cancel_is_not_swallowed(self, clock):
        """⚠⚠ Остановку смены глотать нельзя: `CancelledError` — не отказ круга, а
        приказ закончиться. Проглоти его — и воркер не выключится вовсе."""

        async def _body():
            raise asyncio.CancelledError()

        with pytest.raises(asyncio.CancelledError):
            await round_mod.round_loop(deadline=9, tick_sec=3, name='проба',
                                       body=_body)


class TestTheTimezoneIsReadEveryRound:
    """⚠⚠ Пояс перечитывается каждым кругом, а не запоминается.

    Настройку `timezone` правят на ходу. Запомни её один раз — и расписание анкет
    поедет по старому поясу до конца смены, то есть шесть часов.
    """

    async def test_the_setting_is_asked_on_every_pass(self, clock, monkeypatch):
        asked = []

        async def _setting_get(key, default=''):
            asked.append(key)

            return '+3'

        monkeypatch.setattr(round_mod, 'setting_get', _setting_get)

        runs = []

        async def _body(offset):
            runs.append(offset)

        await round_mod.round_offset_loop(deadline=9, tick_sec=3, name='проба',
                                         body=_body)

        assert len(runs) >= 2, 'круг зашёл меньше двух раз — сравнивать нечего'
        assert asked.count('timezone') == len(runs), (
            f'пояс спросили {asked.count("timezone")} раз на {len(runs)} заходов — '
            'значит он запомнен')

    async def test_the_offset_reaches_the_body(self, clock, monkeypatch):
        """Смещение доезжает числом, а не строкой настройки."""

        async def _setting_get(key, default=''):
            return '+5'

        monkeypatch.setattr(round_mod, 'setting_get', _setting_get)

        got = []

        async def _body(offset):
            got.append(offset)

        await round_mod.round_offset_loop(deadline=3, tick_sec=3, name='проба',
                                         body=_body)

        assert got == [5], f'в круг уехало {got}, а не смещение 5'

    async def test_a_changed_setting_is_seen_without_restart(self, clock,
                                                            monkeypatch):
        """⚠⚠ Главное: правку настройки видно со следующего круга.

        Тот самый случай, ради которого пояс и не запоминается.
        """
        values = ['+0', '+3', '+3']

        async def _setting_get(key, default=''):
            return values.pop(0) if values else '+3'

        monkeypatch.setattr(round_mod, 'setting_get', _setting_get)

        got = []

        async def _body(offset):
            got.append(offset)

        await round_mod.round_offset_loop(deadline=9, tick_sec=3, name='проба',
                                         body=_body)

        assert got[0] == 0 and 3 in got[1:], (
            f'смена настройки не доехала до круга: {got}')
