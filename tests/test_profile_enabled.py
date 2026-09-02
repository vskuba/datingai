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

from src.dating import dating_online
from src.uvicorn.routers.dating.dating_profile import (DatingProfileSave,
                                                       dating_profile_create,
                                                       dating_profile_delete,
                                                       dating_profile_list,
                                                       dating_profile_row)
from src.uvicorn.routers.dating.dating_profile_action import (DatingProfileAuto,
                                                              DatingProfileEnabled,
                                                              dating_profile_action_auto,
                                                              dating_profile_action_enabled)

SITE = 343
UID = '181685'


class _FakeRedis:
    """Redis настолько, насколько его читает проверка онлайна."""

    def __init__(self):
        self.value = None

    def get(self, key):
        return self.value


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
    monkeypatch.setattr(dating_online, 'redis_conn_get', lambda: fake)

    return fake


class TestSendRefusesWhenDisabled:
    """Отправка: запрет стоит раньше всего остального."""

    async def test_disabled_profile_does_not_send(self, monkeypatch, online):
        """⚠ Сценарий не гоняется: он стоит десятков секунд живого браузера и
        вкладки в общем пуле соседа."""
        from src.dating import dating_send

        гоняли = []

        async def _run(*args, **kwargs):
            гоняли.append(args)

            return {'status': 'ok'}

        monkeypatch.setattr(dating_send, 'dating_scenario_run', _run)

        result = await dating_send.dating_send_message(
            {'id': 849, 'site_id': SITE, 'login': 'x', 'enabled': 0},
            {'send': {'transport': 'scenario'}}, UID, 'привет')

        assert result['ok'] is False
        assert 'выключена' in result['error']
        assert not гоняли, 'сценарий погнали от выключенной анкеты'

    async def test_disabled_is_not_offline(self, monkeypatch, online):
        """⚠ `offline` здесь **False**, и это не мелочь.

        `offline: True` значит «собеседник вернётся, и тогда отправим» — автоответ
        по нему оставляет сообщение неотвеченным и придёт снова. Выключенная анкета
        не вернётся сама: её включает человек. Смешай их — и панель обещала бы
        отправку, которой не будет.
        """
        from src.dating import dating_send

        result = await dating_send.dating_send_message(
            {'id': 849, 'site_id': SITE, 'enabled': 0},
            {'send': {'transport': 'scenario'}}, UID, 'привет')

        assert result['offline'] is False

    async def test_missing_column_means_enabled(self, monkeypatch, online):
        """Строка без `enabled` читается как «ведём».

        Колонка появилась при 64 работающих анкетах. Умолчание `False` остановило
        бы разом всех, у кого строка пришла из старого снимка или чужого SELECT.
        """
        from src.dating import dating_send

        гоняли = []

        async def _run(*args, **kwargs):
            гоняли.append(args)

            return {'status': 'ok', 'extract': {}}

        async def _var(site_id, name):
            return {'chat_companion_url_pattern': 'https://site/chat#{ID}',
                    'login_url': 'https://site'}.get(name, '')

        async def _find(site_id, name):
            return {'id': 1, 'name': name}

        async def _setting(site_id, key):
            return 'пароль' if key == 'api_password' else ''

        monkeypatch.setattr(dating_send, 'dating_scenario_run', _run)
        monkeypatch.setattr(dating_send, 'rhapsody_api_site_variable', _var)
        monkeypatch.setattr(dating_send, 'rhapsody_api_scenario_find', _find)
        monkeypatch.setattr(dating_send, 'dating_site_setting', _setting)

        # Ключа `enabled` в словаре нет вовсе — как в строке до миграции.
        result = await dating_send.dating_send_message(
            {'id': 849, 'site_id': SITE, 'login': 'x'},
            {'send': {'transport': 'scenario'}}, UID, 'привет')

        assert result['ok'] is True, 'анкета без колонки сочтена выключенной'
        assert гоняли, 'сценарий не погнали'


class TestAutoRoundSkipsDisabled:
    """Круг воркера: не платим за агента ради анкеты, которая всё равно молчит."""

    @pytest.fixture
    def spied(self, monkeypatch):
        """Круг с подменёнными дорогими шагами. Возвращает, до чего дошло."""
        from src.dating import dating_auto

        seen = {'agents': [], 'answers': []}

        async def _agent(profile):
            seen['agents'].append(int(profile['id']))

            return 1

        async def _answer(profile, presence, agent_id, limit):
            seen['answers'].append(int(profile['id']))

            return {'asked': 1, 'sent': 1, 'silent': 0, 'offline': 0, 'errors': []}

        monkeypatch.setattr(dating_auto, '_agent_id', _agent)
        monkeypatch.setattr(dating_auto, 'dating_answer_run', _answer)

        return seen

    async def test_disabled_profile_is_skipped(self, spied):
        """⚠ Раньше `_agent_id`: поход к соседу за агентом стоит запроса, а ответ
        LLM — денег. Отправка откажет и сама, но к тому времени уже потрачено."""
        from src.dating.dating_auto import dating_auto_round

        await dating_auto_round([{'profile': {'id': 1, 'enabled': 0, 'auto_answer': 1},
                                  'presence': {}}])

        assert spied['agents'] == [], 'за агентом сходили ради выключенной анкеты'
        assert spied['answers'] == []

    async def test_enabled_without_auto_answer_is_skipped(self, spied):
        """Вторая ось: анкету ведут руками. Включена, но агент за неё не отвечает."""
        from src.dating.dating_auto import dating_auto_round

        await dating_auto_round([{'profile': {'id': 2, 'enabled': 1, 'auto_answer': 0},
                                  'presence': {}}])

        assert spied['answers'] == []

    async def test_both_on_works(self, spied):
        """Обратная сторона: включена и отвечает — круг идёт.

        Без этой проверки запрет мог бы оказаться безусловным, и автоответ молча
        перестал бы работать вовсе.
        """
        from src.dating.dating_auto import dating_auto_round

        result = await dating_auto_round([
            {'profile': {'id': 3, 'enabled': 1, 'auto_answer': 1}, 'presence': {}}])

        assert spied['answers'] == [3]
        assert result['sent'] == 1

    async def test_disabled_does_not_hide_the_others(self, spied):
        """Выключенная анкета в списке не останавливает круг: пропускаем её, а не
        выходим. Смена держателя отдаёт все анкеты сразу, и одна отставленная не
        должна лишать ответов остальных."""
        from src.dating.dating_auto import dating_auto_round

        await dating_auto_round([
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
        answer = await dating_profile_create(
            DatingProfileSave(site_id=SITE, login=f'enabled-{uuid.uuid4().hex[:10]}',
                              password='secret', note='тест выключателя'),
            user=None)
        profile_id = int(answer['result'])

        yield profile_id

        try:
            await dating_profile_delete(profile_id, user=None)
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

    async def test_flag_reaches_the_list(self, rhapsody, profile):
        """Список рисует значок выключенной — значит флаг должен в нём быть.

        ⚠ Без него, глядя на сотню строк, человек не понимает, почему часть из них
        молчит, и лезет искать поломку.
        """
        await _set(profile, False)
        rows = (await dating_profile_list(site_id=SITE, user=None))['result']
        found = next(row for row in rows if int(row['id']) == profile)

        assert found['enabled'] is False

    async def test_two_switches_are_independent(self, profile):
        """⚠ Выключатель анкеты не трогает автоответ, и наоборот.

        Их путают, но случаи разные: анкету ведут руками — включена, автоответ
        снят. Задень один флаг другим, и это состояние стало бы недостижимым.
        """
        await dating_profile_action_auto(
            profile, DatingProfileAuto(auto_answer=False), request=None, user=None)
        await _set(profile, False)
        await _set(profile, True)

        row = await dating_profile_row(profile)

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
    await dating_profile_action_enabled(
        profile_id, DatingProfileEnabled(enabled=enabled), user=None)


async def _enabled(profile_id: int) -> bool:
    return bool((await dating_profile_row(profile_id))['enabled'])
