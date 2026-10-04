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
`site_.settings`, страница «Сайты», правка человеком.
"""

import json

import pytest

from src.dating.site_presence import (site_presence,
                                             site_presence_neighbour)

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
        import src.dating.site_presence as module

        monkeypatch.setattr('src.dating.site_presence.site_setting',
                            _own(json.dumps(CONFIG)))
        monkeypatch.setattr(module, 'site_presence_neighbour', _boom)

        assert (await site_presence(343))['send']['transport'] == 'http'

    async def test_a_broken_setting_is_not_a_config(self, monkeypatch):
        """⚠ Мусор в поле читается как «не настроено», а не роняет петлю.

        Поле правится руками, то есть сломанным бывает по определению. Брось мы
        отсюда — упал бы держатель, а вместе с ним чат всех сайтов сразу.
        """
        monkeypatch.setattr('src.dating.site_presence.site_setting',
                            _own('{сломано}'))

        async def _nothing(site_id):
            return {}

        import src.dating.site_presence as module
        monkeypatch.setattr(module, 'site_presence_neighbour', _nothing)

        assert await site_presence(343) == {}


class TestNeighbourIsTheFallback:
    """⚠ Запасной путь остаётся, и убирать его рано.

    Новый сайт заводят у соседа: пока описание не перенесли, оно единственное,
    чем анкета работает. Сними мы запасной путь — заведение сайта стало бы
    двухходовым, и второй ход забывался бы ровно до первой поломки.
    """

    async def test_without_own_setting_the_neighbour_answers(self, monkeypatch):
        import src.dating.site_presence as module

        monkeypatch.setattr('src.dating.site_presence.site_setting',
                            _own(''))

        async def _neighbour(site_id):
            return {'base': 'https://neighbour.test'}

        monkeypatch.setattr(module, 'site_presence_neighbour', _neighbour)

        assert (await site_presence(343))['base'] == 'https://neighbour.test'

    async def test_a_silent_neighbour_is_emptiness_not_a_failure(self, monkeypatch):
        """Молчание соседа — пустое описание, а не исключение наверх."""
        from src.rhapsody import rhapsody_api

        async def _dead(site_id):
            raise RuntimeError('сосед не отвечает')

        monkeypatch.setattr(rhapsody_api, 'rhapsody_api_site_get', _dead)

        assert await site_presence_neighbour(343) == {}


class TestLoopsDoNotAskTheNeighbour:
    """⚠⚠ Сторож на сами петли: они и давали 412 обращений в час.

    Проверка по исходнику, а не прогоном: поднять держателя с базой, Redis и
    сокетами дороже, чем стоит вопрос, — а вопрос простой и проверяется точно.
    """

    def test_the_holder_reads_our_setting(self):
        import inspect

        from src.dating import chat_hold

        code = inspect.getsource(chat_hold._profiles_ready)

        assert 'site_presence(site_id)' in code, \
            'отбор снова спрашивает карточку у соседа — это три петли держателя'
        assert 'rhapsody_api_site_get' not in code

    def test_the_revive_round_does_not_ask_at_all(self):
        """⚠ Здесь вызов был **мёртвым**: ответ не читался ни разу.

        Полноценный запрос служил проверкой «жив ли сосед» — и лгал: он мог
        лежать, пока присутствие держалось исправно нашим же клиентом.
        """
        import inspect

        from src.dating import revive_site

        code = inspect.getsource(revive_site.revive_site_presence)

        assert 'rhapsody_api_site_get' not in code, \
            'вернулся запрос, результат которого не используется'

    def test_the_profile_handles_do_not_ask_either(self):
        """⚠⚠ Ручки раздела — тоже, и это была последняя дверь.

        `profile_site` ходила к соседу живьём, а её читают девять ручек.
        Дороже всех обходилась `/probe`: её дёргает **открытая страница «Анкеты»**
        раз в две минуты, и карточка приезжала ради одного `presence`.

        Хуже цены была зависимость: `RhapsodyError` превращался там в `502`, то
        есть лежащий сосед отнимал отправку сообщения и проверку входа — работу,
        которая идёт ручкой самого сайта по нашим cookie.
        """
        import inspect

        from src.uvicorn.routers.dating import profile_

        code = inspect.getsource(profile_.profile_site)

        assert 'site_presence' in code, \
            'ручки раздела снова спрашивают карточку у соседа'
        assert 'rhapsody_api_site_get' not in code

    def test_the_site_url_comes_from_our_snapshot(self):
        """Адрес сайта — из снимка `site_`, за ним к соседу не ходят.

        ⚠ Join **левый**: строка снимка появляется сверкой, и анкета сайта,
        который ещё не сверяли, иначе выпала бы из работы целиком.
        """
        import inspect

        code = inspect.getsource(
            __import__('src.dating.chat_hold', fromlist=['x'])._profiles_ready)

        assert 'LEFT JOIN site' in code


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


class TestStoredValueIsValidJson:
    """⚠⚠ Значение в базе обязано разбираться — проверяем сами, а не на глаз.

    **Живая беда, стоившая двух выкладок.** Миграция переезда положила описание
    строкой, экранировав только одинарные кавычки. В строковом литерале MySQL
    обратный слэш — **тоже** escape-символ, а у Ladadate внутри JSON стоит

        "ok_mark": "\\"IsSuccess\\":true"

    При вставке `\\"` стало `"`, и строка порвалась на 211-м знаке.

    ⚠ **Молча.** Накат прошёл без ошибок — для MySQL это законная строка, — а
    проверка «ПОСЛЕ» смотрела `JSON_TYPE` и `LIKE '%"send"%'`: битая строка
    остаётся строкой и подстроку содержит. Код же читал пустоту и честно уходил к
    соседу, так что единственной приметой был неизменившийся ритм его запросов.

    Отсюда правило: значение проверяется **разбором**, и здесь, и в миграции
    (`JSON_VALID`), а пишется hex-литералом — он escape-последовательностей не
    знает вовсе.
    """

    async def test_every_stored_description_parses(self):
        """Все описания в базе стенда разбираются в словарь."""
        import json

        from mysql_.mysql_ import mysql_get_db_async

        async with mysql_get_db_async() as db:
            await db.execute(
                'SELECT site_id, JSON_UNQUOTE(JSON_EXTRACT(settings, %s)) AS raw'
                '  FROM site'
                ' WHERE JSON_EXTRACT(settings, %s) IS NOT NULL', ('$.presence',) * 2)
            rows = await db.fetchall()

        for row in rows or []:
            raw = row['raw']
            if not raw or raw == 'null':
                continue

            try:
                got = json.loads(raw)
            except ValueError as e:
                raise AssertionError(
                    f"описание сайта #{row['site_id']} не разбирается: {e}. "
                    f'Похоже на экранирование в миграции: пиши значение '
                    f'hex-литералом, `CONVERT(X\'…\' USING utf8mb4)`') from e

            assert isinstance(got, dict), \
                f"описание сайта #{row['site_id']} — не словарь, а {type(got).__name__}"

    def test_the_migration_checks_itself(self):
        """⚠ Миграция обязана проверять **разбор**, а не тип и подстроку.

        `JSON_TYPE` вернёт `STRING` и битой строке, `LIKE '%"send"%'` найдёт в ней
        подстроку — обе проверки прошлую беду пропустили. `JSON_VALID` ловит.
        """
        import pathlib

        text = pathlib.Path(
            'migrations/20260916180000_dating_site_presence_fix.sql').read_text(
                encoding='utf-8')

        assert 'JSON_VALID' in text, 'миграция снова не проверяет разбор'
        assert "CONVERT(X'" in text, 'значение снова пишется строковым литералом'
