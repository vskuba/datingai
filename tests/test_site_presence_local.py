"""Описание сайта читается у себя, а не запрашивается у соседа каждый заход.

⚠⚠ **Счёт, ради которого это сделано** — журнал запросов прода за час:

    /api/browser/site/343    412 обращений
    /api/browser/site/19     343 обращения

Карточку сайта спрашивали четыре петли воркера, каждая по разу **на сайт**:
присутствие раз в 15 секунд, главный цикл держателя и автоответ раз в минуту,
круг восстановления раз в минуту. У Ladadate это триста байт, не менявшихся пять
дней, и читалось из них одно слово — `"http"`.

Хуже цены зависимость: лежит сосед — встаёт **чат**. Не браузер и не сценарии, а
отправка ручкой сайта по нашим cookie, к которой он отношения не имеет.

⚠ **Кеш эту беду не лечит, и это был первый неверный ответ.** Кеш признаёт, что
настройка живёт не там, где её читают. Настройки сайтов у нас и так свои —
`dating_site.settings`, страница «Сайты», правка человеком.
"""

import json

import pytest

from src.dating.dating_site_presence import (dating_site_presence,
                                             dating_site_presence_neighbour)

pytestmark = pytest.mark.profile

# Описание Ladadate, как оно лежит у нас после переезда.
CONFIG = {'base': 'https://example.test',
          'send': {'path': '/chat-send', 'transport': 'http'},
          'answer_language': 'en'}


def _own(value):
    """Заглушка настройки сайта: отдаёт `value` на ключ описания."""

    async def _setting(site_id, key, default=''):
        return value if key == 'presence' else default

    return _setting


def _boom(site_id):
    raise AssertionError(f'пошли к соседу за сайтом #{site_id}, имея своё описание')


class TestOwnSettingWins:
    """Есть своё описание — соседа не спрашивают вовсе."""

    async def test_the_neighbour_is_not_asked(self, monkeypatch):
        """Сосед подменяется взрывом: дойдёт вызов — тест назовёт причину."""
        import src.dating.dating_site_presence as module

        monkeypatch.setattr('src.uvicorn.routers.dating.dating_site.dating_site_setting',
                            _own(json.dumps(CONFIG)))
        monkeypatch.setattr(module, 'dating_site_presence_neighbour', _boom)

        assert (await dating_site_presence(343))['send']['transport'] == 'http'

    async def test_a_broken_setting_is_not_a_config(self, monkeypatch):
        """⚠ Мусор в поле читается как «не настроено», а не роняет петлю.

        Поле правится руками, то есть сломанным бывает по определению. Брось мы
        отсюда — упал бы держатель, а вместе с ним чат всех сайтов сразу.
        """
        monkeypatch.setattr('src.uvicorn.routers.dating.dating_site.dating_site_setting',
                            _own('{сломано}'))

        async def _nothing(site_id):
            return {}

        import src.dating.dating_site_presence as module
        monkeypatch.setattr(module, 'dating_site_presence_neighbour', _nothing)

        assert await dating_site_presence(343) == {}


class TestNeighbourIsTheFallback:
    """⚠ Запасной путь остаётся, и убирать его рано.

    Новый сайт заводят у соседа: пока описание не перенесли, оно единственное,
    чем анкета работает. Сними мы запасной путь — заведение сайта стало бы
    двухходовым, и второй ход забывался бы ровно до первой поломки.
    """

    async def test_without_own_setting_the_neighbour_answers(self, monkeypatch):
        import src.dating.dating_site_presence as module

        monkeypatch.setattr('src.uvicorn.routers.dating.dating_site.dating_site_setting',
                            _own(''))

        async def _neighbour(site_id):
            return {'base': 'https://neighbour.test'}

        monkeypatch.setattr(module, 'dating_site_presence_neighbour', _neighbour)

        assert (await dating_site_presence(343))['base'] == 'https://neighbour.test'

    async def test_a_silent_neighbour_is_emptiness_not_a_failure(self, monkeypatch):
        """Молчание соседа — пустое описание, а не исключение наверх."""
        from src.rhapsody import rhapsody_api

        async def _dead(site_id):
            raise RuntimeError('сосед не отвечает')

        monkeypatch.setattr(rhapsody_api, 'rhapsody_api_site_get', _dead)

        assert await dating_site_presence_neighbour(343) == {}


class TestLoopsDoNotAskTheNeighbour:
    """⚠⚠ Сторож на сами петли: они и давали 412 обращений в час.

    Проверка по исходнику, а не прогоном: поднять держателя с базой, Redis и
    сокетами дороже, чем стоит вопрос, — а вопрос простой и проверяется точно.
    """

    def test_the_holder_reads_our_setting(self):
        import inspect

        from src.dating import dating_chat_hold

        code = inspect.getsource(dating_chat_hold._profiles_ready)

        assert 'dating_site_presence(site_id)' in code, \
            'отбор снова спрашивает карточку у соседа — это три петли держателя'
        assert 'rhapsody_api_site_get' not in code

    def test_the_revive_round_does_not_ask_at_all(self):
        """⚠ Здесь вызов был **мёртвым**: ответ не читался ни разу.

        Полноценный запрос служил проверкой «жив ли сосед» — и лгал: он мог
        лежать, пока присутствие держалось исправно нашим же клиентом.
        """
        import inspect

        from src.dating import dating_revive_site

        code = inspect.getsource(dating_revive_site.dating_revive_site_presence)

        assert 'rhapsody_api_site_get' not in code, \
            'вернулся запрос, результат которого не используется'

    def test_the_site_url_comes_from_our_snapshot(self):
        """Адрес сайта — из снимка `dating_site`, за ним к соседу не ходят.

        ⚠ Join **левый**: строка снимка появляется сверкой, и анкета сайта,
        который ещё не сверяли, иначе выпала бы из работы целиком.
        """
        import inspect

        code = inspect.getsource(
            __import__('src.dating.dating_chat_hold', fromlist=['x'])._profiles_ready)

        assert 'LEFT JOIN dating_site' in code


class TestSettingIsRegistered:
    """Настройка есть в реестре — иначе её не сохранить со страницы «Сайты»."""

    async def test_the_key_is_known(self, auth_client):
        got = (await auth_client.get('/api/dating/site')).json()['result']
        known = {row['key']: row for row in got['known']}

        assert 'presence' in known, 'описание сайта не сохранить: ключа нет в реестре'
        assert known['presence']['default'] == '', \
            'умолчание не пустое — новый сайт считался бы настроенным'

    async def test_it_is_not_the_heartbeat_key(self, auth_client):
        """⚠ Отдельно от `heartbeat`, и это не дубль.

        `heartbeat` — присутствие, переведённое в формат движка, и оно старше.
        Здесь сырое описание, из которого каждый читатель берёт свои ключи.
        """
        got = (await auth_client.get('/api/dating/site')).json()['result']
        keys = {row['key'] for row in got['known']}

        assert {'presence', 'heartbeat'} <= keys
