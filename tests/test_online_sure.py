"""Реплика в чат уходит, только когда мы **знаем**, что он в сети.

## ⚠⚠ Зачем понадобилось второе правило рядом с первым

Общее правило отправки — «не знаем, значит не мешаем» (`online_check`,
`test_online_guard.py`): запрет срабатывает там, где человека в свежем снимке
нет, а отсутствие снимка пропускает. Для письма это верно — оно уйдёт в ящик и
дождётся.

Для **реплики в чат** цена незнания другая, и она измерена. 08.11.2026 Lance
закрыл анкету сразу после «hi Marina». У Step2Love разговор живёт открытым
чатом, которого у закрытого нет: отправка отвечает «у анкеты нет открытого
чата», черновик возвращается в пул чистым, круг берёт его через минуту снова.
Снимка «Все онлайн» у сайта в те часы не было вовсе — значит «не знаем», значит
«шлём», — и так **199 попыток за четыре часа** двум анкетам. Конца у этого нет:
закрытая анкета не вернётся.

Отсюда правило владельца: шлём, когда он в сети; не в сети или не знаем —
молчим и ждём. Черновик при этом цел и уйдёт, как только человек появится в
свежем снимке.

⚠ Цена размена названа честно: сайт, у которого ленты «Все онлайн» нет вовсе,
перестаёт отвечать в чат автоматически. Это видно сразу (пул растёт, ответы не
уходят), в отличие от вечного стука, который не видно никак.
"""

import json
import time

import pytest

from src.dating import online
from src.dating.online import ONLINE_FRESH_SEC, online_check, online_sure

pytestmark = pytest.mark.chat

SITE = 343
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
    fake = _FakeRedis()
    monkeypatch.setattr(online, 'redis_conn_get', lambda: fake)

    def put(uids, age_sec=5):
        fake.value = json.dumps({'at': time.time() - age_sec,
                                 'when': '2026-11-08 21:00:00',
                                 'people': [{'uid': one} for one in uids]})

    fake.put = put

    return fake


class TestOnlySureSends:
    """Главное: уверенность — это свежий снимок, в котором он есть."""

    def test_a_man_in_a_fresh_snapshot_gets_the_reply(self, redis):
        redis.put([UID, '999'])

        assert online_sure(SITE, UID) is True

    def test_a_man_out_of_the_snapshot_does_not(self, redis):
        redis.put(['999', '888'])

        assert online_sure(SITE, UID) is False

    def test_no_snapshot_means_no_reply(self, redis):
        """⚠⚠ Здесь правило **обратно** общему, и в этом вся правка.

        `online_check` без снимка отвечает «можно» — иначе молчали бы письма.
        Реплика в чат так не может: без снимка круг стучался к закрывшемуся
        человеку 199 раз за четыре часа и стучался бы вечно.
        """
        redis.value = None

        assert online_check(SITE, UID)['online'] is True, \
            'общее правило изменилось — проверьте, не сломало ли это письма'
        assert online_sure(SITE, UID) is False

    def test_a_stale_snapshot_means_no_reply(self, redis):
        """⚠ Протухший снимок — то же незнание: состав сети меняется быстрее."""
        redis.put([UID], age_sec=ONLINE_FRESH_SEC + 1)

        assert online_sure(SITE, UID) is False

    def test_broken_redis_means_no_reply(self, redis):
        """⚠ И сбой кэша тоже: «не смогли спросить» это не «он в сети»."""
        redis.value = RuntimeError('redis лёг')

        assert online_sure(SITE, UID) is False


class TestTheReplyRoadUsesIt:
    """⚠ Строгую проверку ставим **только** на реплику в чат."""

    def test_the_answer_round_asks_for_certainty(self):
        import inspect

        from src.dating import answer

        code = inspect.getsource(answer.answer_run)

        assert 'online_sure(' in code, \
            'круг ответов снова шлёт по незнанию'
        assert 'online_check(' not in code, \
            'в круге ответов осталась мягкая проверка'

    def test_letters_keep_the_soft_rule(self):
        """⚠⚠ Письма правило **не** меняют: у них нет открытого чата, который
        мог бы не открыться, и ящик дождётся. Поменяй им правило заодно — и
        сайт без ленты перестал бы писать вовсе, молча.
        """
        import inspect

        from src.dating import favorite_mail, mail_reply

        for module in (mail_reply, favorite_mail):
            assert 'online_sure(' not in inspect.getsource(module), \
                f'строгая проверка уехала в письма ({module.__name__})'
