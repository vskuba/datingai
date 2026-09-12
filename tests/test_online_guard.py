"""⚠ Не писать тому, кого нет в сети — на обоих путях сразу.

Сообщение тому, кого на сайте нет, либо отклоняется, либо уходит в пустоту. У
сценария оно вдобавок стоит десятков секунд живого браузера и вкладки в общем пуле
соседа, а у автоответа — ещё и денег на LLM за ответ, который некому отправить.

Проверяется механика, а не сегодняшнее состояние сайта: кто в сети прямо сейчас —
не наше дело и в тестах не участвует. Снимок подменяется.
"""

import json
import time

import pytest

from src.dating import dating_online
from src.dating.dating_online import DATING_ONLINE_FRESH_SEC, dating_online_check

pytestmark = pytest.mark.chat
SITE = 343

# Номера выдуманные: тесту всё равно, а живую анкету трогать нельзя даже в тех
# ветках, где отправка отказывает, — ветку легко переписать так, что она пройдёт.
PROFILE_ID = 999_849
UID = '999181685'


class _FakeRedis:
    """Redis настолько, насколько его читает проверка."""

    def __init__(self, value=None):
        self.value = value

    def get(self, key):
        if isinstance(self.value, Exception):
            raise self.value

        return self.value


@pytest.fixture
def redis(monkeypatch):
    """Подменяет Redis. Значение ставит сам тест — через `put`."""
    fake = _FakeRedis()
    monkeypatch.setattr(dating_online, 'redis_conn_get', lambda: fake)

    def put(uids, age_sec=5):
        fake.value = json.dumps({'at': time.time() - age_sec,
                                 'when': '2026-09-02 10:00:00',
                                 'people': [{'uid': u} for u in uids]})

    fake.put = put

    return fake


class TestOnlineCheck:
    """Что считается «в сети»."""

    def test_contact_in_the_snapshot_is_online(self, redis):
        redis.put([UID, '999'])

        state = dating_online_check(SITE, UID)

        assert state == {'online': True, 'known': True, 'age_sec': 5}

    def test_contact_out_of_the_snapshot_is_offline(self, redis):
        """Сама принадлежность списку и означает «в сети» — он для этого и снят."""
        redis.put(['999', '888'])

        state = dating_online_check(SITE, UID)

        assert state['online'] is False and state['known'] is True


class TestUnknownDoesNotBlock:
    """⚠ Не знаем — не мешаем. Запрет только там, где мы **знаем**, что человека нет."""

    def test_no_snapshot_allows_sending(self, redis):
        """Снимка нет у сайта без ленты «Все онлайн» (у Goldenbride её нет вовсе) и
        в первые минуты после перезапуска. Молчать в этих случаях значило бы тихо
        выключить автоответ на весь сайт, и никто бы не понял почему."""
        redis.value = None

        state = dating_online_check(SITE, UID)

        assert state['online'] is True, 'без снимка отправка запрещена'
        assert state['known'] is False, 'незнание выдано за знание'

    def test_stale_snapshot_allows_sending(self, redis):
        """Состав сети за пять минут меняется: запрещать по старому снимку —
        угадывать. Пропускаем, отметив, что уверенности нет."""
        redis.put(['999'], age_sec=DATING_ONLINE_FRESH_SEC + 1)

        state = dating_online_check(SITE, UID)

        assert state['online'] is True and state['known'] is False

    def test_broken_redis_allows_sending(self, redis):
        """Сбой кэша не должен останавливать работу: он здесь ускорение, а не
        источник правды."""
        redis.value = RuntimeError('redis лёг')

        assert dating_online_check(SITE, UID)['online'] is True

    def test_junk_snapshot_allows_sending(self, redis):
        for value in ('не json', '[]', '{}', '{"at": 1}'):
            redis.value = value

            assert dating_online_check(SITE, UID)['online'] is True, value

    def test_no_site_allows_sending(self, redis):
        """Анкета без сайта — состояние ошибочное, но не повод молчать здесь: об
        этом скажет тот, кто сайт и спрашивает."""
        redis.put([])

        assert dating_online_check(0, UID)['online'] is True


class TestGuardStandsInBothPaths:
    """⚠ Запрет стоит **до** дорогой работы, а не после неё.

    Проверяется порядок, а не факт: отказать можно и после прогона сценария —
    только он к тому времени уже отнял сорок секунд браузера, а агент — деньги за
    ответ, который некому отправить.
    """

    async def test_send_refuses_before_running_the_scenario(self, monkeypatch, redis):
        """Отправка не гонит сценарий, если собеседника нет в сети."""
        from src.dating import dating_send

        redis.put(['999'])
        runs = []

        async def _run(*args, **kwargs):
            runs.append(args)

            return {'status': 'ok'}

        monkeypatch.setattr(dating_send, 'dating_scenario_run', _run)

        result = await dating_send.dating_send_message(
            {'id': PROFILE_ID, 'site_id': SITE, 'login': 'x'},
            {'send': {'transport': 'scenario'}}, UID, 'привет')

        assert result['ok'] is False
        assert result['offline'] is True, 'отказ выдан за ошибку, а это состояние'
        assert not runs, 'сценарий погнали ради сообщения, которое некому доставить'

    async def test_answer_does_not_ask_the_agent_when_offline(self, monkeypatch, redis):
        """Автоответ не спрашивает агента, если собеседника нет в сети.

        ⚠ Проверка стоит до `_agent_ask`: ответ LLM стоит денег, а отправить его
        будет некому. Прежде порядок был обратный — спрашивали, получали ответ и
        только потом узнавали от сайта, что собеседник вышел.
        """
        from src.dating import dating_answer

        redis.put(['999'])
        asked, sent = [], []

        async def _waiting(profile_id, limit, only, lead):
            return [{'sender_uid': UID, 'sender_name': 'Semen',
                     'text': 'вопрос', 'direction': 'in'}]

        async def _ask(agent_id, question, language, profile=None):
            asked.append(question)

            return ('ответ', '')

        async def _send(profile, presence, uid, text):
            sent.append(text)

            return {'ok': True, 'error': '', 'offline': False, 'response': '',
                    'message_uid': 'x'}

        async def _log(*args, **kwargs):
            return None

        monkeypatch.setattr(dating_answer, '_waiting_find', _waiting)
        monkeypatch.setattr(dating_answer, '_agent_ask', _ask)
        monkeypatch.setattr(dating_answer, 'dating_send_message', _send)
        monkeypatch.setattr(dating_answer, 'dating_log_write', _log)
        monkeypatch.setattr(dating_answer, '_tried_mark', _log)

        result = await dating_answer.dating_answer_run(
            {'id': PROFILE_ID, 'site_id': SITE, 'role': 'lady'}, {}, agent_id=1, limit=5)

        assert not asked, 'агента спросили ради ответа, который некому отправить'
        assert not sent
        assert result['offline'] == 1, 'пропуск не посчитан'

    async def test_answer_works_when_online(self, monkeypatch, redis):
        """Обратная сторона: в сети — спрашиваем и отправляем.

        Без этой проверки запрет мог бы оказаться безусловным, и автоответ молча
        перестал бы работать вовсе.
        """
        from src.dating import dating_answer

        redis.put([UID])
        asked, sent = [], []

        async def _waiting(profile_id, limit, only, lead):
            return [{'sender_uid': UID, 'sender_name': 'Semen',
                     'text': 'вопрос', 'direction': 'in'}]

        async def _ask(agent_id, question, language, profile=None):
            asked.append(question)

            return ('ответ', '')

        async def _send(profile, presence, uid, text):
            sent.append(text)

            return {'ok': True, 'error': '', 'offline': False, 'response': '',
                    'message_uid': 'x'}

        async def _log(*args, **kwargs):
            return None

        monkeypatch.setattr(dating_answer, '_waiting_find', _waiting)
        monkeypatch.setattr(dating_answer, '_agent_ask', _ask)
        monkeypatch.setattr(dating_answer, 'dating_send_message', _send)
        monkeypatch.setattr(dating_answer, 'dating_log_write', _log)
        monkeypatch.setattr(dating_answer, '_tried_mark', _log)
        monkeypatch.setattr(dating_answer, '_answered_mark', _log, raising=False)

        result = await dating_answer.dating_answer_run(
            {'id': PROFILE_ID, 'site_id': SITE, 'role': 'lady'}, {}, agent_id=1, limit=5)

        assert asked == ['вопрос']
        assert sent == ['ответ']
        assert result['sent'] == 1


class TestKeyMatchesTheSnapshot:
    """⚠ Ключ снимка написан в двух местах, и разъехаться им нельзя."""

    def test_key_is_the_same_as_people_writes(self):
        """`dating_online` читает то, что кладёт `dating_people`. Второе написание
        ключа — единственное, чем эти два модуля связаны, и молчаливое расхождение
        выключило бы проверку целиком: снимка «не нашлось» значит «не знаем», то
        есть разрешено всегда."""
        from src.uvicorn.routers.dating.dating_people import DATING_PEOPLE_SITE_KEY

        assert dating_online.DATING_ONLINE_KEY == \
            DATING_PEOPLE_SITE_KEY.format(site_id='{site_id}', kind='online')


class TestOnlineShow:
    """Второй порог — для показа точки, а не для решения об отправке.

    ⚠ **Порога два не по недосмотру.** «Слать ли письмо сейчас» — решение с
    последствием, и пятиминутный снимок для него уже сомнителен. «Был ли человек на
    сайте недавно» — сведение к размышлению, и десятиминутной давности оно не
    портит.
    """

    def test_show_threshold_is_longer(self):
        from src.dating.dating_online import (DATING_ONLINE_FRESH_SEC,
                                              DATING_ONLINE_SHOW_SEC)

        assert DATING_ONLINE_SHOW_SEC > DATING_ONLINE_FRESH_SEC

    def test_no_snapshot_is_not_online(self, monkeypatch):
        """⚠ Для показа «не знаем» — это **не** «в сети», в отличие от отправки.

        Отправка при незнании пропускает: запрещать по незнанию нельзя, иначе
        анкета молчала бы всякий раз, когда лента не снята. Показ наоборот молчит:
        зелёная точка у того, о ком мы ничего не знаем, — прямая неправда.
        """
        from src.dating import dating_online

        monkeypatch.setattr(dating_online, '_snapshot', lambda site_id: None)

        show = dating_online.dating_online_show(1, 'x')
        send = dating_online.dating_online_check(1, 'x')

        assert show['online'] is False and show['known'] is False
        assert send['online'] is True and send['known'] is False
