"""Отказ соседа: как он превращается в текст и почему не выбрасывает снимок.

Юнит-тесты без сети: проверяется своя сторона — как читается чужой отказ и что
помнится между запросами. Живого соседа для этого поднимать не нужно, а вот
дождаться от него `409` в нужную секунду прогона нельзя вовсе: пул вкладок у него
общий, и занят он бывает не по расписанию.

Числа в тестах сняты с живого отказа: `предел сессий исчерпан: 25` — это ровно то,
что ответил RhapsodyAi, когда все двадцать пять вкладок его пула оказались заняты.
"""

import time

import pytest

from src.rhapsody import rhapsody_api
from src.rhapsody.rhapsody_api import (_error_text, rhapsody_api_scenario_find,
                                       rhapsody_api_scenario_forget)

pytestmark = pytest.mark.core


class _Response:
    """Ответ соседа настолько, насколько его читает `_error_text`."""

    def __init__(self, status_code: int, payload=None, text: str = ''):
        self.status_code = status_code
        self._payload = payload
        self.text = text

    def json(self):
        if self._payload is None:
            raise ValueError('не JSON')

        return self._payload


class TestErrorText:
    """Отказ соседа — фразой для журнала анкеты, а не кодом и телом ответа."""

    def test_409_says_what_it_means(self):
        """⚠ Ради этого всё и затевалось: «409» не говорит человеку ничего.

        За кодом стоит занятый пул Chromium, общий с задачами самого соседа.
        Панель на это влиять не может, и человек, читающий журнал, должен видеть
        причину, а не число.
        """
        text = _error_text('/api/browser/scenario/278/run',
                           _Response(409, {'detail': 'предел сессий исчерпан: 25'}))

        assert 'браузер соседа занят' in text
        assert 'предел сессий исчерпан: 25' in text

    def test_code_survives_in_the_tail(self):
        """Код остаётся в хвосте: по нему ищут в логах соседа.

        И не только: `rhapsody_api_site_get` отличает «нет такого сайта» от обрыва
        связи по подстроке ` → 404` в тексте. Пропади она — опечатка в номере сайта
        отвечала бы «шлюз не отвечает».
        """
        text = _error_text('/api/browser/site/5', _Response(404, {'detail': 'нет такого'}))

        assert ' → 404' in text

    def test_detail_unwrapped_from_json(self):
        """Берём `detail`, а не тело целиком: фигурные скобки в ленте журнала —
        мусор, за которым не видно самой причины."""
        text = _error_text('/x', _Response(500, {'detail': 'внутри всё сломалось'}))

        assert '{' not in text
        assert 'внутри всё сломалось' in text

    def test_plain_text_body_survives(self):
        """Не JSON — берём текст как есть: отказ бывает и от прокси, у которого
        своего `detail` нет, и молчать о причине хуже, чем показать сырое."""
        text = _error_text('/x', _Response(502, None, text='<html>Bad Gateway</html>'))

        assert 'Bad Gateway' in text

    def test_unknown_code_still_readable(self):
        """Незнакомый код не оставляет строку без объяснения."""
        text = _error_text('/x', _Response(418, {'detail': 'я чайник'}))

        assert 'отказом' in text and 'я чайник' in text


class TestScenarioCache:
    """Найденный сценарий помним: под капотом это выгрузка всего списка соседа."""

    def setup_method(self):
        rhapsody_api_scenario_forget()

    def teardown_method(self):
        rhapsody_api_scenario_forget()

    @pytest.fixture
    def calls(self, monkeypatch):
        """Считает походы к соседу, отвечая одним и тем же списком."""
        counter = {'n': 0}

        async def _fake(method, path, *args, **kwargs):
            counter['n'] += 1

            return {'result': [{'id': 304, 'site_id': 343, 'name': 'чат: список диалогов'}]}

        monkeypatch.setattr(rhapsody_api, '_request', _fake)

        return counter

    async def test_second_lookup_does_not_go_to_neighbour(self, calls):
        """Второй поиск того же — из памяти.

        Список сценариев у соседа выгружается **целиком** ради одного номера, а
        спрашивают его перед каждым прогоном: перед каждым обновлением колонки
        собеседников.
        """
        first = await rhapsody_api_scenario_find(343, 'чат: список диалогов')
        second = await rhapsody_api_scenario_find(343, 'чат: список диалогов')

        assert first == second == {'id': 304, 'site_id': 343, 'name': 'чат: список диалогов'}
        assert calls['n'] == 1, 'второй поиск ушёл к соседу'

    async def test_miss_is_not_remembered(self, calls):
        """⚠ Промах не кэшируется.

        «Такого сценария нет» обычно значит, что его сейчас заводят у соседа.
        Запомни мы пустоту на десять минут — кнопка осталась бы мёртвой после
        того, как её настроили, и чинилось бы это перезапуском панели.
        """
        assert await rhapsody_api_scenario_find(343, 'такого нет') == {}
        assert await rhapsody_api_scenario_find(343, 'такого нет') == {}

        assert calls['n'] == 2, 'пустой ответ запомнился'

    async def test_forget_drops_one_name(self, calls):
        """Забыть — после отказа «его больше нет»: сценарий переименовали или
        удалили, а мы стучимся по старому номеру до конца выдержки."""
        await rhapsody_api_scenario_find(343, 'чат: список диалогов')
        rhapsody_api_scenario_forget(343, 'чат: список диалогов')
        await rhapsody_api_scenario_find(343, 'чат: список диалогов')

        assert calls['n'] == 2

    async def test_other_site_is_its_own_key(self, calls):
        """Ключ — сайт и имя вместе: у двух сайтов бывают одноимённые сценарии, и
        отдать номер чужого значило бы водить браузер не по тому сайту."""
        await rhapsody_api_scenario_find(343, 'чат: список диалогов')
        await rhapsody_api_scenario_find(19, 'чат: список диалогов')

        assert calls['n'] == 2

    async def test_stale_entry_is_refreshed(self, calls, monkeypatch):
        """Выдержка кончилась — спрашиваем заново: сценарий у соседа могли
        переписать, а мы держим его прошлую запись."""
        await rhapsody_api_scenario_find(343, 'чат: список диалогов')

        later = time.monotonic() + rhapsody_api.RHAPSODY_API_SCENARIO_TTL_SEC + 1
        monkeypatch.setattr(rhapsody_api.time, 'monotonic', lambda: later)
        await rhapsody_api_scenario_find(343, 'чат: список диалогов')

        assert calls['n'] == 2


class TestScenarioUnderLabel:
    """Имя под меткой сайта — тот же сценарий этого сайта.

    Индекс имени у соседа один на всю таблицу и без сайта: второму сайту с тем
    же занятием достаётся «имя (Метка)». Точное сравнение с голым именем молча
    глушило кнопку первому владельцу — так Ladadate лишился зелёной кнопки
    «Импортировать профили» в тот день, когда его голые имена ушли под метку,
    чтобы освободить место под строки Luxee.
    """

    def setup_method(self):
        rhapsody_api_scenario_forget()

    def teardown_method(self):
        rhapsody_api_scenario_forget()

    @pytest.fixture
    def labeled(self, monkeypatch):
        """Сосед, у которого имя ушло под метку: 135 «импорт анкет (Ladadate)»."""

        async def _fake(method, path, *args, **kwargs):
            return {'result': [{'id': 135, 'site_id': 343, 'name': 'импорт анкет (Ladadate)'}]}

        monkeypatch.setattr(rhapsody_api, '_request', _fake)

    async def test_labeled_name_is_found_by_the_bare_one(self, labeled):
        """Кнопка смотрит в голые имена — и обязана видеть свой сценарий, когда
        имя уехало под метку сайта."""
        found = await rhapsody_api_scenario_find(343, 'импорт анкет')

        assert found['id'] == 135, 'кнопка покраснела: имя под меткой не узналось'

    async def test_bare_name_of_another_site_does_not_leak(self, labeled):
        """Чужое помеченное имя не притворяется нашим: ряд отсекается по номеру
        сайта до сравнения, и Luxee со своим голым «импорт анкет» здесь ни при
        чём — его строка в чужой ряд не попадёт."""
        assert await rhapsody_api_scenario_find(999344, 'импорт анкет') == {}
