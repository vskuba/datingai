"""⚠ Выключенная анкета не пишет никому и ничем.

Два выключателя, и они про разное. `enabled` — «ведём ли анкету вовсе»;
`auto_answer` — «отвечает ли за неё агент». Случаи не совпадают: анкету ведут
руками (включена, автоответ снят) и анкету отставляют совсем (выключена). Тесты
держат обе оси раздельно — свести их в один флаг значило бы отнять первый случай.

Хранение проверяется по HTTP, в живую панель с живой базой. Запреты — вызовом
функций с подменёнными дорогими шагами: важно, что до них **не доходит**, а не что
они вернули.
"""

import json
import time
import uuid

import pytest
from fastapi import HTTPException

from src.dating import online as online_module
from src.uvicorn.routers.dating.profile_ import (DatingProfileSave,
                                                       profile_create,
                                                       profile_delete,
                                                       profile_list,
                                                       profile_row)
from src.uvicorn.routers.dating.profile_action import (DatingProfileAuto,
                                                              DatingProfileEnabled,
                                                              profile_action_auto,
                                                              profile_action_enabled)

pytestmark = pytest.mark.profile

SITE = 343

# ⚠ **Номера выдуманные, и это не придирка.**
#
# Сюита писала в живую базу живой анкеты: тест «строка без колонки читается как
# ведём» подменял прогон сценария, но не запись отправленного — и каждый прогон
# сюиты добавлял в переписку настоящей анкеты ещё одно «привет». К десятому
# прогону человек увидел десять приветов в диалоге и принял это за поломку
# автоответа.
#
# Отсюда два правила, и оба нужны: номер анкеты — заведомо не существующий, а
# запись в переписку подменяется (`saved` ниже). Первого мало — строка легла бы с
# чужим номером и просто мусорила; второго мало — подмену легко забыть.
PROFILE_ID = 999_849
UID = '999181685'


@pytest.fixture
def saved(monkeypatch):
    """Перехватывает запись отправленного в переписку.

    ⚠ **Обязательна для любого теста, где отправка проходит успешно.** Без неё
    `send_message` пишет в живую таблицу `dating_message` — и переписка
    настоящей анкеты обрастает тестовыми репликами. Так и вышло: десять прогонов
    сюиты — десять «привет» в живом диалоге.

    Заодно это делает проверку точнее: видно, **что** записалось, а не только что
    отправка вернула `ok`.
    """
    from src.dating import send

    rows = []

    async def _save(profile_id, to_uid, text, message_uid, source='socket'):
        rows.append((int(profile_id), str(to_uid), str(text)))

    async def _mark(profile_id, to_uid):
        return None

    monkeypatch.setattr(send, '_message_save', _save)
    monkeypatch.setattr(send, '_answered_mark', _mark)

    return rows


@pytest.fixture
def online(monkeypatch):
    """Собеседник в сети: иначе запрет онлайна сработает раньше и проверит не то.

    ⚠ Без этой подмены тест «выключенная не отправляет» проходил бы и со снятым
    запретом `enabled` — просто по другой причине. Ловушка настоящая: первый
    вариант теста так и зеленел.
    """
    fake = _FakeRedis()
    fake.value = json.dumps({'at': time.time(), 'when': '',
                             'people': [{'uid': UID}]})
    monkeypatch.setattr(online_module, 'redis_conn_get', lambda: fake)

    return fake


class TestSendRefusesWhenDisabled:
    """Отправка: запрет стоит раньше всего остального."""

    async def test_disabled_profile_does_not_send(self, monkeypatch, online):
        """⚠ Сценарий не гоняется: он стоит десятков секунд живого браузера и
        вкладки в общем пуле соседа."""
        from src.dating import send

        runs = []

        async def _run(*args, **kwargs):
            runs.append(args)

            return {'status': 'ok'}

        monkeypatch.setattr(send, 'scenario_run', _run)

        result = await send.send_message(
            {'id': PROFILE_ID, 'site_id': SITE, 'login': 'x', 'enabled': 0},
            {'send': {'transport': 'scenario'}}, UID, 'привет')

        assert result['ok'] is False
        assert 'выключена' in result['error']
        assert not runs, 'сценарий погнали от выключенной анкеты'

    async def test_disabled_is_not_offline(self, monkeypatch, online):
        """⚠ `offline` здесь **False**, и это не мелочь.

        `offline: True` значит «собеседник вернётся, и тогда отправим» — автоответ
        по нему оставляет сообщение неотвеченным и придёт снова. Выключенная анкета
        не вернётся сама: её включает человек. Смешай их — и панель обещала бы
        отправку, которой не будет.
        """
        from src.dating import send

        result = await send.send_message(
            {'id': PROFILE_ID, 'site_id': SITE, 'enabled': 0},
            {'send': {'transport': 'scenario'}}, UID, 'привет')

        assert result['offline'] is False

    async def test_missing_column_means_enabled(self, monkeypatch, online, saved):
        """Строка без `enabled` читается как «ведём».

        Колонка появилась при 64 работающих анкетах. Умолчание `False` остановило
        бы разом всех, у кого строка пришла из старого снимка или чужого SELECT.
        """
        from src.dating import send

        runs = []

        async def _run(*args, **kwargs):
            runs.append(args)

            return {'status': 'ok', 'extract': {}}

        async def _var(site_id, name):
            return {'chat_companion_url_pattern': 'https://site/chat#{ID}',
                    'login_url': 'https://site'}.get(name, '')

        async def _find(site_id, name):
            return {'id': 1, 'name': name}

        async def _setting(site_id, key):
            return 'пароль' if key == 'api_password' else ''

        monkeypatch.setattr(send, 'scenario_run', _run)
        monkeypatch.setattr(send, 'rhapsody_api_site_variable', _var)
        monkeypatch.setattr(send, 'rhapsody_api_scenario_find', _find)
        monkeypatch.setattr(send, 'site_setting', _setting)

        # Ключа `enabled` в словаре нет вовсе — как в строке до миграции.
        result = await send.send_message(
            {'id': PROFILE_ID, 'site_id': SITE, 'login': 'x'},
            {'send': {'transport': 'scenario'}}, UID, 'привет')

        assert result['ok'] is True, 'анкета без колонки сочтена выключенной'
        assert runs, 'сценарий не погнали'
        assert saved == [(PROFILE_ID, UID, 'привет')], 'отправленное не записалось'


class TestNoDoubleSend:
    """⚠ Тот же текст тому же собеседнику подряд не отправляется.

    Смысл в последней реплике диалога: если она наша и с этим же текстом, значит мы
    это уже сказали, а собеседник ещё не ответил. Повтор ему ничего не добавит, а в
    диалоге выглядит поломкой — так и вышло: человек увидел десять «привет» подряд
    и решил, что сломался автоответ.

    База здесь подменена: проверяется решение, а не чтение строки.
    """

    @pytest.fixture
    def dialogue(self, monkeypatch):
        """Последняя реплика диалога. Тест ставит её через `put`."""
        from src.dating import send

        state = {'last': None}

        class _Cursor:
            async def execute(self, *args, **kwargs):
                return None

            async def fetchone(self):
                return state['last']

            @property
            def connection(self):
                return self

            async def commit(self):
                return None

        class _Db:
            async def __aenter__(self):
                return _Cursor()

            async def __aexit__(self, *args):
                return False

        monkeypatch.setattr(send, 'mysql_get_db_async', lambda: _Db())
        state['put'] = lambda text, direction='out': state.__setitem__(
            'last', {'text': text, 'direction': direction})

        return state

    async def _send(self, text='привет'):
        from src.dating import send

        return await send.send_message(
            {'id': PROFILE_ID, 'site_id': SITE, 'login': 'x', 'enabled': 1},
            {'send': {'transport': 'scenario'}}, UID, text)

    async def test_same_text_twice_is_refused(self, dialogue, online, monkeypatch):
        from src.dating import send

        runs = []
        monkeypatch.setattr(send, 'scenario_run',
                            lambda *a, **k: runs.append(a))
        dialogue['put']('привет')

        result = await self._send('привет')

        assert result['ok'] is False
        assert 'уже отправлено' in result['error']
        assert not runs, 'сценарий погнали ради повтора'

    @pytest.mark.parametrize('last, sending, duplicate', [
        ('привет', 'привет', True),
        ('Привет', '  привет ', True),          # регистр и пробелы не спасают
        ('привет', 'привет!', False),           # другой текст — другое сообщение
        ('привет', 'как дела?', False),
    ])
    async def test_comparison_is_by_meaning(self, dialogue, last, sending, duplicate):
        """Сравнение по сути, а не побуквенно: агент возвращает тот же текст с
        другим пробелом или регистром, а для собеседника это одно сообщение.

        ⚠ Проверяем **саму функцию**, а не путь отправки целиком. Первый вариант
        теста шёл через `send_message` и зеленел даже с побуквенным
        сравнением: отказ приходил дальше по пути, по другой причине, а тест этого
        не различал. Поймано проверкой на излом.
        """
        from src.dating.send import _said_already

        dialogue['put'](last)

        assert await _said_already(PROFILE_ID, UID, sending) is duplicate

    async def test_other_text_goes_through(self, dialogue, online, saved, monkeypatch):
        """Обратная сторона: другой текст отправляется.

        Без этой проверки запрет мог бы оказаться безусловным, и отправка молча
        перестала бы работать вовсе.
        """
        _scenario_ok(monkeypatch)
        dialogue['put']('привет')

        result = await self._send('как дела?')

        assert result['ok'] is True, 'новое сообщение не ушло'

    async def test_their_message_does_not_block_us(self, dialogue, online, saved,
                                                   monkeypatch):
        """⚠ Последняя реплика **их** — отправляем, даже если текст тот же.

        «привет» в ответ на «привет» — законный разговор. Запрет только на наш
        повтор поверх собственного слова.
        """
        _scenario_ok(monkeypatch)
        dialogue['put']('привет', direction='in')

        result = await self._send('привет')

        assert result['ok'] is True, 'их сообщение приняли за наше'

    async def test_empty_dialogue_goes_through(self, dialogue, online, saved, monkeypatch):
        """Первое сообщение в переписке: сравнивать не с чем."""
        _scenario_ok(monkeypatch)

        assert (await self._send('привет'))['ok'] is True

    async def test_broken_database_does_not_block(self, online, saved, monkeypatch):
        """⚠ Сбой базы даёт «отправляем», а не «молчим».

        Молча не отправить хуже, чем отправить второй раз: потерянный ответ человек
        не увидит вовсе, а дубль хотя бы заметен.
        """
        from src.dating import send

        def _broken():
            raise RuntimeError('база недоступна')

        monkeypatch.setattr(send, 'mysql_get_db_async', _broken)
        _scenario_ok(monkeypatch)

        assert (await self._send('привет'))['ok'] is True


class _FakeRedis:
    """Redis настолько, насколько его читает проверка онлайна."""

    def __init__(self):
        self.value = None

    def get(self, key):
        return self.value


def _scenario_ok(monkeypatch):
    """Подменяет всё, что нужно удачному прогону сценария: сосед, настройки, прогон.

    ⚠ Наружу при этом не ходит ничего — ни к RhapsodyAi, ни на сайт. Сторож сети в
    `conftest` это и проверяет.
    """
    from src.dating import send

    async def _run(*args, **kwargs):
        return {'status': 'ok', 'extract': {}}

    async def _var(site_id, name):
        return {'chat_companion_url_pattern': 'https://site/chat#{ID}',
                'login_url': 'https://site'}.get(name, '')

    async def _find(site_id, name):
        return {'id': 1, 'name': name}

    async def _setting(site_id, key):
        return 'пароль' if key == 'api_password' else ''

    monkeypatch.setattr(send, 'scenario_run', _run)
    monkeypatch.setattr(send, 'rhapsody_api_site_variable', _var)
    monkeypatch.setattr(send, 'rhapsody_api_scenario_find', _find)
    monkeypatch.setattr(send, 'site_setting', _setting)


class TestAutoRoundSkipsDisabled:
    """Круг воркера: не платим за агента ради анкеты, которая всё равно молчит."""

    @pytest.fixture
    def spied(self, monkeypatch):
        """Круг с подменёнными дорогими шагами. Возвращает, до чего дошло.

        ⚠ Выключатель сайта поднят подменой: здесь проверяется флаг **анкеты**, а
        анкеты в этих тестах заводятся без `site_id` вовсе — настоящий `_answer_on`
        спросил бы про сайт «0» и отказал бы всем, то есть тесты зеленели бы по
        причине, к их вопросу не относящейся. Сам выключатель проверяется в
        `tests/test_auto_agent_id.py`, `TestTheSiteSwitch`.
        """
        from src.dating import auto

        seen = {'agents': [], 'answers': []}

        async def _agent(profile):
            seen['agents'].append(int(profile['id']))

            return 1

        async def _answer(profile, presence, agent_id, limit):
            seen['answers'].append(int(profile['id']))

            return {'asked': 1, 'sent': 1, 'silent': 0, 'offline': 0, 'errors': []}

        async def _answer_on(site_id):
            return True

        monkeypatch.setattr(auto, '_agent_id', _agent)
        monkeypatch.setattr(auto, 'answer_run', _answer)
        monkeypatch.setattr(auto, '_answer_on', _answer_on)

        return seen

    async def test_disabled_profile_is_skipped(self, spied):
        """⚠ Раньше `_agent_id`: поход к соседу за агентом стоит запроса, а ответ
        LLM — денег. Отправка откажет и сама, но к тому времени уже потрачено."""
        from src.dating.auto import auto_round

        await auto_round([{'profile': {'id': 1, 'enabled': 0, 'auto_answer': 1},
                                  'presence': {}}])

        assert spied['agents'] == [], 'за агентом сходили ради выключенной анкеты'
        assert spied['answers'] == []

    async def test_the_profile_flag_is_not_read_anymore(self):
        """⚠⚠ Флаг анкеты `auto_answer` больше не читается — выключатель один.

        Он был вторым: галочку из интерфейса убрали при переезде выключателя на
        сайт (`site_.chat_answer_on`, «отправка ответов» на вкладке «Диалоги»), а
        проверка в круге осталась — и в комментариях страниц при этом было
        записано «круг его больше не читает».

        Стоило это живого молчания: на проде у двенадцати анкет из тринадцати
        столбец остался в нуле, и агент не отвечал никому при поднятом
        выключателе сайта — а поправить нечем, галочки в панели нет.
        """
        import inspect

        from src.dating.auto import auto_round

        code = inspect.getsource(auto_round)

        assert "profile.get('auto_answer')" not in code, \
            'флаг анкеты снова читается — выключателей опять два'

    async def test_a_profile_without_the_flag_still_answers(self, spied):
        """И работает это на деле: анкета с нулём в столбце отвечает."""
        from src.dating.auto import auto_round

        await auto_round([{'profile': {'id': 2, 'enabled': 1, 'auto_answer': 0},
                           'presence': {}}])

        assert spied['answers'], 'анкету снова режет флаг, которого нет в панели'


    async def test_both_on_works(self, spied):
        """Обратная сторона: включена и отвечает — круг идёт.

        Без этой проверки запрет мог бы оказаться безусловным, и автоответ молча
        перестал бы работать вовсе.
        """
        from src.dating.auto import auto_round

        result = await auto_round([
            {'profile': {'id': 3, 'enabled': 1, 'auto_answer': 1}, 'presence': {}}])

        assert spied['answers'] == [3]
        assert result['sent'] == 1

    async def test_disabled_does_not_hide_the_others(self, spied):
        """Выключенная анкета в списке не останавливает круг: пропускаем её, а не
        выходим. Смена держателя отдаёт все анкеты сразу, и одна отставленная не
        должна лишать ответов остальных."""
        from src.dating.auto import auto_round

        await auto_round([
            {'profile': {'id': 1, 'enabled': 0, 'auto_answer': 1}, 'presence': {}},
            {'profile': {'id': 2, 'enabled': 1, 'auto_answer': 1}, 'presence': {}}])

        assert spied['answers'] == [2]


class TestEnabledEndpoint:
    """Ручка выключателя: **база настоящая**, сосед поддельный.

    ⚠ Ручки зовутся в процессе, а не по HTTP: заведение анкеты сверяет сайт у
    соседа, и по HTTP это дало бы два запроса к RhapsodyAi на каждую тестовую
    учётку — то есть сюиту, падающую от его перезапуска. То же правило, что в
    `test_profile_crud.py`.
    """

    @pytest.fixture
    async def profile(self, rhapsody):
        """Одноразовая анкета в живой базе. Чужую трогать нельзя: выключение
        остановило бы работу."""
        answer = await profile_create(
            DatingProfileSave(site_id=SITE, login=f'enabled-{uuid.uuid4().hex[:10]}',
                              password='secret', note='тест выключателя'),
            user=None)
        profile_id = int(answer['result'])

        yield profile_id

        try:
            await profile_delete(profile_id, user=None)
        except HTTPException:
            pass

    async def test_new_profile_is_enabled(self, profile):
        """Заведённая анкета работает сразу: её и заводят, чтобы вести."""
        assert await _enabled(profile) is True

    async def test_toggle_is_stored(self, profile):
        await _set(profile, False)

        assert await _enabled(profile) is False

        await _set(profile, True)

        assert await _enabled(profile) is True

    async def test_disabling_drops_the_watch(self, profile):
        """⚠ Выключение анкеты снимает и присмотр автомата (`keep_online`).

        Снятая галочка значит «перестань водить её сама», а круг восстановления
        про галочку не знает: он берёт всех с `keep_online = 1` и анкету без
        расписания считает дежурящей круглосуточно (`revive_db._on_duty`). Поймано
        живьём дважды за день: на выключенных анкетах Ladadate «вход под анкетой»
        шёл круглые сутки, а под выключенной анкетой Step2Love круг ленты входил
        на сайт каждые две минуты. Та же болезнь и то же лечение, что у снятия
        исполнения расписания (`test_schedule.py`).
        """
        from mysql_.mysql_ import mysql_get_db_async

        async with mysql_get_db_async() as db:
            await db.execute('UPDATE profile SET keep_online = 1 WHERE id = %s',
                             (profile,))
            await db.connection.commit()

        await _set(profile, False)

        assert await _keep_online(profile) == 0, \
            'анкету выключили, а присмотр остался — круг вернёт её в сеть'

    async def test_flag_reaches_the_list(self, rhapsody, profile):
        """Список рисует значок выключенной — значит флаг должен в нём быть.

        ⚠ Без него, глядя на сотню строк, человек не понимает, почему часть из них
        молчит, и лезет искать поломку.
        """
        await _set(profile, False)
        rows = (await profile_list(site_id=SITE, user=None))['result']
        found = next(row for row in rows if int(row['id']) == profile)

        assert found['enabled'] is False

    async def test_two_switches_are_independent(self, profile):
        """⚠ Выключатель анкеты не трогает автоответ, и наоборот.

        Их путают, но случаи разные: анкету ведут руками — включена, автоответ
        снят. Задень один флаг другим, и это состояние стало бы недостижимым.
        """
        await profile_action_auto(
            profile, DatingProfileAuto(auto_answer=False), request=None, user=None)
        await _set(profile, False)
        await _set(profile, True)

        row = await profile_row(profile)

        assert bool(row['enabled']) is True
        assert bool(row['auto_answer']) is False, 'включение анкеты вернуло автоответ'

    async def test_unknown_profile_is_404(self):
        """404, а не молчаливое ничего: правка несуществующей анкеты — ошибка
        вызывающего, и знать о ней он должен."""
        with pytest.raises(HTTPException) as refusal:
            await _set(99_999_999, False)

        assert refusal.value.status_code == 404

    async def test_closed_without_login(self, client):
        """То, что решается до входа в обработчик, — по HTTP, как и вся сюита."""
        answer = await client.put('/api/dating/profile/1/enabled',
                                  json={'enabled': False})

        assert answer.status_code == 401


async def _set(profile_id: int, enabled: bool) -> None:
    await profile_action_enabled(
        profile_id, DatingProfileEnabled(enabled=enabled), user=None)


async def _enabled(profile_id: int) -> bool:
    return bool((await profile_row(profile_id))['enabled'])


async def _keep_online(profile_id: int) -> int:
    """Присмотр автомата из живой базы: роутер его правит, не показывая на странице."""
    from mysql_.mysql_ import mysql_get_db_async

    async with mysql_get_db_async() as db:
        await db.execute('SELECT keep_online FROM profile WHERE id = %s', (profile_id,))
        row = await db.fetchone()

    return int(row['keep_online'])
