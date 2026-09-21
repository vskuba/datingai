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

from src.dating import online
from src.dating.online import ONLINE_FRESH_SEC, online_check

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
    monkeypatch.setattr(online, 'redis_conn_get', lambda: fake)

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

        state = online_check(SITE, UID)

        assert state == {'online': True, 'known': True, 'age_sec': 5}

    def test_contact_out_of_the_snapshot_is_offline(self, redis):
        """Сама принадлежность списку и означает «в сети» — он для этого и снят."""
        redis.put(['999', '888'])

        state = online_check(SITE, UID)

        assert state['online'] is False and state['known'] is True


class TestUnknownDoesNotBlock:
    """⚠ Не знаем — не мешаем. Запрет только там, где мы **знаем**, что человека нет."""

    def test_no_snapshot_allows_sending(self, redis):
        """Снимка нет у сайта без ленты «Все онлайн» (у Goldenbride её нет вовсе) и
        в первые минуты после перезапуска. Молчать в этих случаях значило бы тихо
        выключить автоответ на весь сайт, и никто бы не понял почему."""
        redis.value = None

        state = online_check(SITE, UID)

        assert state['online'] is True, 'без снимка отправка запрещена'
        assert state['known'] is False, 'незнание выдано за знание'

    def test_stale_snapshot_allows_sending(self, redis):
        """Состав сети за пять минут меняется: запрещать по старому снимку —
        угадывать. Пропускаем, отметив, что уверенности нет."""
        redis.put(['999'], age_sec=ONLINE_FRESH_SEC + 1)

        state = online_check(SITE, UID)

        assert state['online'] is True and state['known'] is False

    def test_broken_redis_allows_sending(self, redis):
        """Сбой кэша не должен останавливать работу: он здесь ускорение, а не
        источник правды."""
        redis.value = RuntimeError('redis лёг')

        assert online_check(SITE, UID)['online'] is True

    def test_junk_snapshot_allows_sending(self, redis):
        for value in ('не json', '[]', '{}', '{"at": 1}'):
            redis.value = value

            assert online_check(SITE, UID)['online'] is True, value

    def test_no_site_allows_sending(self, redis):
        """Анкета без сайта — состояние ошибочное, но не повод молчать здесь: об
        этом скажет тот, кто сайт и спрашивает."""
        redis.put([])

        assert online_check(0, UID)['online'] is True


class TestGuardStandsInBothPaths:
    """⚠ Запрет стоит **до** дорогой работы, а не после неё.

    Проверяется порядок, а не факт: отказать можно и после прогона сценария —
    только он к тому времени уже отнял сорок секунд браузера, а агент — деньги за
    ответ, который некому отправить.
    """

    async def test_send_refuses_before_running_the_scenario(self, monkeypatch, redis):
        """Отправка не гонит сценарий, если собеседника нет в сети."""
        from src.dating import send

        redis.put(['999'])
        runs = []

        async def _run(*args, **kwargs):
            runs.append(args)

            return {'status': 'ok'}

        monkeypatch.setattr(send, 'scenario_run', _run)

        result = await send.send_message(
            {'id': PROFILE_ID, 'site_id': SITE, 'login': 'x'},
            {'send': {'transport': 'scenario'}}, UID, 'привет')

        assert result['ok'] is False
        assert result['offline'] is True, 'отказ выдан за ошибку, а это состояние'
        assert not runs, 'сценарий погнали ради сообщения, которое некому доставить'

    async def test_answer_writes_but_does_not_send_when_offline(self, monkeypatch, redis):
        """Нет в сети — ответ **пишется**, но не уходит.

        ## ⚠⚠ Правило переменилось, и нарочно

        Прежде проверка стояла **до** `_agent_ask`: нет собеседника — не платим за
        LLM. Экономно, но у этого была цена. В панели пусто: «ответ готовится» не
        отличалось от «ничего не происходит». А вернувшийся собеседник ждал ещё
        круг и десяток секунд на сочинение.

        Теперь текст пишется сразу и лежит черновиком (`chat_reply`), а наружу не
        уходит ничего. Деньги на LLM тратятся ровно раз — при следующем заходе
        готовый текст берут из пула, а не сочиняют снова
        (`test_chat_reply_draft.py`).

        ⚠ Что осталось прежним и проверяется здесь же: **отправки нет**. Сайт либо
        отклонит сообщение, либо примет его в пустоту.
        """
        from src.dating import answer

        redis.put(['999'])
        asked, sent = [], []

        # ⚠ Подпись повторяет живую: порога свежести в отборе больше нет.
        async def _waiting(profile_id, limit, only, lead):
            # ⚠ `id` — повод написать: по нему ищется и кладётся черновик
            # ответа (`chat_reply.message_id`).
            return [{'id': 1, 'sender_uid': UID, 'sender_name': 'Semen',
                     'text': 'вопрос', 'direction': 'in'}]

        async def _ask(agent_id, question, language, profile=None,
                   history='', uid='', anyway=False, name=''):
            asked.append(question)

            return ('ответ', '')

        async def _send(profile, presence, uid, text):
            sent.append(text)

            return {'ok': True, 'error': '', 'offline': False, 'response': '',
                    'message_uid': 'x'}

        async def _log(*args, **kwargs):
            return None

        monkeypatch.setattr(answer, '_waiting_find', _waiting)
        monkeypatch.setattr(answer, '_agent_ask', _ask)
        monkeypatch.setattr(answer, 'send_message', _send)
        monkeypatch.setattr(answer, 'log_write', _log)
        monkeypatch.setattr(answer, '_tried_mark', _log)

        result = await answer.answer_run(
            {'id': PROFILE_ID, 'site_id': SITE, 'role': 'lady'}, {}, agent_id=1, limit=5)

        assert asked, 'агента не спросили — черновику взяться неоткуда'
        assert not sent, 'ответ ушёл человеку, которого нет в сети'
        assert result['drafted'] == 1, 'ответ не лёг в пул'
        assert result['offline'] == 1, 'пропуск отправки не посчитан'

    async def test_answer_works_when_online(self, monkeypatch, redis):
        """Обратная сторона: в сети — спрашиваем и отправляем.

        Без этой проверки запрет мог бы оказаться безусловным, и автоответ молча
        перестал бы работать вовсе.
        """
        from src.dating import answer

        redis.put([UID])
        asked, sent = [], []

        # ⚠ Подпись повторяет живую: порога свежести в отборе больше нет.
        async def _waiting(profile_id, limit, only, lead):
            # ⚠ `id` — повод написать: по нему ищется и кладётся черновик
            # ответа (`chat_reply.message_id`).
            return [{'id': 1, 'sender_uid': UID, 'sender_name': 'Semen',
                     'text': 'вопрос', 'direction': 'in'}]

        async def _ask(agent_id, question, language, profile=None,
                   history='', uid='', anyway=False, name=''):
            asked.append(question)

            return ('ответ', '')

        async def _send(profile, presence, uid, text):
            sent.append(text)

            return {'ok': True, 'error': '', 'offline': False, 'response': '',
                    'message_uid': 'x'}

        async def _log(*args, **kwargs):
            return None

        monkeypatch.setattr(answer, '_waiting_find', _waiting)
        monkeypatch.setattr(answer, '_agent_ask', _ask)
        monkeypatch.setattr(answer, 'send_message', _send)
        monkeypatch.setattr(answer, 'log_write', _log)
        monkeypatch.setattr(answer, '_tried_mark', _log)
        monkeypatch.setattr(answer, '_answered_mark', _log, raising=False)

        result = await answer.answer_run(
            {'id': PROFILE_ID, 'site_id': SITE, 'role': 'lady'}, {}, agent_id=1, limit=5)

        assert asked == ['вопрос']
        assert sent == ['ответ']
        assert result['sent'] == 1


class TestKeyMatchesTheSnapshot:
    """⚠ Ключ снимка написан в двух местах, и разъехаться им нельзя."""

    def test_key_is_the_same_as_people_writes(self):
        """`online` читает то, что кладёт `people`. Второе написание
        ключа — единственное, чем эти два модуля связаны, и молчаливое расхождение
        выключило бы проверку целиком: снимка «не нашлось» значит «не знаем», то
        есть разрешено всегда."""
        from src.uvicorn.routers.dating.people import PEOPLE_SITE_KEY

        assert online.ONLINE_KEY == \
            PEOPLE_SITE_KEY.format(site_id='{site_id}', kind='online')


class TestOnlineShow:
    """Второй порог — для показа точки, а не для решения об отправке.

    ⚠ **Порога два не по недосмотру.** «Слать ли письмо сейчас» — решение с
    последствием, и пятиминутный снимок для него уже сомнителен. «Был ли человек на
    сайте недавно» — сведение к размышлению, и десятиминутной давности оно не
    портит.
    """

    def test_show_threshold_is_longer(self):
        from src.dating.online import (ONLINE_FRESH_SEC,
                                              ONLINE_SHOW_SEC)

        assert ONLINE_SHOW_SEC > ONLINE_FRESH_SEC

    def test_no_snapshot_is_not_online(self, monkeypatch):
        """⚠ Для показа «не знаем» — это **не** «в сети», в отличие от отправки.

        Отправка при незнании пропускает: запрещать по незнанию нельзя, иначе
        анкета молчала бы всякий раз, когда лента не снята. Показ наоборот молчит:
        зелёная точка у того, о ком мы ничего не знаем, — прямая неправда.
        """
        from src.dating import online

        monkeypatch.setattr(online, '_snapshot', lambda site_id: None)

        show = online.online_show(1, 'x')
        send = online.online_check(1, 'x')

        assert show['online'] is False and show['known'] is False
        assert send['online'] is True and send['known'] is False
