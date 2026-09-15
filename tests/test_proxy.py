"""Выход анкеты: свой IP на все двери.

⚠ **Зачем это стеречь.** Сайт считает учётки по внешнему адресу: с одного IP он
пускает ограниченное число девушек, дальше отвергает вход. Правило «у анкеты свой
выход, и он один и тот же во всех дверях» держится не одним местом, а пятью
(HTTP сайта, присутствие, сокет, вкладка браузера, кабинет), и расходятся такие
правила молча.

К соседу и наружу сюита не ходит: реестр подменяется, а проверяется наша сторона
— кэш, сборка адреса и та ловушка с паролем, на которой мы уже споткнулись.
"""

import pytest

from datetime_.datetime_ import datetime_utc_now
from http_.http_pool import http_pool_transport
from http_.http_proxy import http_proxy_hide, http_proxy_url, http_proxy_ws_url

from src.dating import dating_proxy
from src.dating.dating_proxy import (dating_proxy_url, DatingProxyError,
                                     DATING_PROXY_FRESH_SEC)

pytestmark = pytest.mark.profile


@pytest.fixture(autouse=True)
def proxy_on(monkeypatch):
    """Главный выключатель выходов включён на всё, что здесь проверяется.

    ⚠ Он выключен по умолчанию (`proxy_enabled` у сайта), и это правильно: пока
    каналы не куплены, механизма быть не должно. Но здесь стережётся сама
    механика — кэш, раздача, сборка адреса, — и она имеет смысл только при
    включённом выключателе. Сам выключатель проверяется отдельно, в
    `test_proxy_switch.py`.
    """
    async def _on(site_id):
        return True

    monkeypatch.setattr(dating_proxy, 'dating_proxy_enabled', _on)


class TestProxyUrl:
    """Сборка адреса. Здесь ошибаются молча, и стоит это часов отладки."""

    def test_password_is_escaped(self):
        """`@` и `:` в пароле экранируются — иначе часть пароля станет хостом.

        У провайдеров прокси такие пароли — обычное дело, и `p@ss` без
        экранирования даёт «прокси не отвечает»: разбор URL уводит соединение не
        туда, а причину ищут где угодно, только не в пароле.
        """
        url = http_proxy_url('socks5', '10.0.0.1', 1080, 'user', 'p@ss:word')

        assert url == 'socks5://user:p%40ss%3Aword@10.0.0.1:1080'

    def test_websocket_form_decodes_back(self):
        """⚠ Для сокета пароль раскодируется обратно — и это не косметика.

        `websockets` шлёт userinfo в `Proxy-Authorization` **как есть**, без
        раскодирования, а httpx — раскодирует. Один адрес поэтому работает в HTTP
        и получает 407 в сокете, причём ровно на паролях со спецсимволами.
        Проверено живьём на стенде.
        """
        url = http_proxy_url('http', 'host', 8899, 'stand', 'p@ss:word')

        assert http_proxy_ws_url(url) == 'http://stand:p@ss:word@host:8899'

    def test_password_never_shows(self):
        """Показываемая форма пароль прячет: она едет в журнал и на страницу."""
        url = http_proxy_url('http', 'host', 3128, 'user', 'secret')

        assert 'secret' not in http_proxy_hide(url)
        assert http_proxy_hide(url) == 'http://user:•••@host:3128'

    def test_unknown_scheme_refused(self):
        """Схема не из списка — отказ сразу, а не «соединение не устанавливается»."""
        with pytest.raises(ValueError):
            http_proxy_url('ftp', 'host', 21)


class TestProxyTransport:
    """Транспорт на выход: соединение, открытое через один прокси, не отдаётся другому."""

    async def test_transport_is_own_per_exit(self):
        """⚠ Разные выходы — разные транспорты, один и тот же — общий.

        Общий транспорт на всех означал бы, что запрос анкеты уходит по уже
        открытому соединению **чужого** выхода: сайт увидел бы ту же учётку с
        другого адреса — ровно то, от чего прокси и заводился.
        """
        direct = http_pool_transport()
        first = http_pool_transport('http://one:1')
        second = http_pool_transport('http://two:2')

        assert first is not direct
        assert first is not second
        assert http_pool_transport('http://one:1') is first


class TestProxyCache:
    """Кэш адреса у анкеты: без него присутствие зависело бы от соседа."""

    async def test_fresh_cache_does_not_ask_neighbour(self, monkeypatch):
        """Свежий кэш отдаётся как есть: сосед не спрашивается вовсе.

        Присутствие бьёт каждые полминуты по всем анкетам — поход к соседу на
        каждый удар означал бы, что его перезапуск гасит «в сети» всей установки.
        """
        asked = []

        async def _claim(profile):
            asked.append(profile)
            raise AssertionError('соседа спрашивать было не нужно')

        monkeypatch.setattr(dating_proxy, 'dating_proxy_take', _claim)

        from datetime import timedelta
        fresh = datetime_utc_now().replace(tzinfo=None) - timedelta(seconds=DATING_PROXY_FRESH_SEC // 2)

        url = await dating_proxy_url({'id': 1, 'proxy_url': 'http://a:1', 'proxy_at': fresh})

        assert url == 'http://a:1'
        assert not asked

    async def test_stale_cache_is_refreshed(self, monkeypatch):
        """Протухший кэш перечитывается: пароль канала мог смениться."""
        async def _claim(profile):
            return {'id': 7, 'url': 'http://new:2', 'check_ip': '1.2.3.4'}

        monkeypatch.setattr(dating_proxy, 'dating_proxy_take', _claim)

        from datetime import timedelta
        old = datetime_utc_now().replace(tzinfo=None) - timedelta(seconds=DATING_PROXY_FRESH_SEC * 2)

        url = await dating_proxy_url({'id': 1, 'proxy_url': 'http://old:1', 'proxy_at': old})

        assert url == 'http://new:2'

    async def test_neighbour_silent_keeps_old_exit(self, monkeypatch):
        """⚠ Сосед **молчит** (обрыв связи) — идём прежним выходом.

        Про наше место он ничего не сказал, и оно, скорее всего, на месте.
        Прямой выход здесь был бы худшим исходом: он не виден ничем, пока сайт не
        закроет учётку, а прежний прокси за нами закреплён — сайт видел нас
        именно с него.

        ⚠ Код `0` — это «не дозвонились». Ответ соседа разбирается иначе, см.
        соседний тест.
        """
        from src.rhapsody.rhapsody_api import RhapsodyError

        async def _claim(profile):
            raise RhapsodyError('RhapsodyAi недоступен', code=0)

        monkeypatch.setattr(dating_proxy, 'dating_proxy_take', _claim)

        url = await dating_proxy_url({'id': 1, 'proxy_url': 'http://old:1', 'proxy_at': None})

        assert url == 'http://old:1'

    async def test_lost_exit_drops_the_cache(self, monkeypatch):
        """⚠ Список пришёл, а места в нём нет — прежний выход забываем.

        Разница с прошлым тестом по существу: там связь оборвалась и про выходы
        ничего не известно, здесь список получен — значит нашего выхода в нём
        больше нет (удалили, выключили, не прошёл проверку). Держаться за кэш
        тут значит ходить через мёртвый прокси до скончания века, а причина
        выглядела бы как беда сайта.

        Поймано живьём: прокси удалили у соседа, а две анкеты продолжали держать
        его адрес.
        """
        forgotten = []

        async def _take(profile):
            raise DatingProxyError('мест нет: годных выходов 0')

        async def _forget(profile_id):
            forgotten.append(profile_id)

        monkeypatch.setattr(dating_proxy, 'dating_proxy_take', _take)
        monkeypatch.setattr(dating_proxy, 'dating_proxy_forget', _forget)

        profile = {'id': 5, 'proxy_url': 'http://dead:1', 'proxy_at': None}

        assert await dating_proxy_url(profile) == ''
        assert forgotten == [5], 'кэш мёртвого выхода не почищен'
        assert profile['proxy_url'] == '', 'строка анкеты осталась с мёртвым адресом'

    async def test_no_exit_at_all_is_empty(self, monkeypatch):
        """Выхода нет и занять не вышло — пустая строка, а не выдуманный адрес."""
        async def _claim(profile):
            raise DatingProxyError('мест нет')

        monkeypatch.setattr(dating_proxy, 'dating_proxy_take', _claim)

        assert await dating_proxy_url({'id': 1, 'proxy_url': '', 'proxy_at': None}) == ''


class TestProxyPick:
    """Раздача: кому какой выход. ⚠ Это **наше** знание, а не соседа.

    Реестр у него отвечает «какие выходы есть и какие живые»; сколько анкет
    допустимо на адрес, знает только сайт знакомств — а про сайты знакомств там
    не слыхали. Здесь и стережём.
    """

    async def test_counts_by_seen_ip_not_by_proxy(self, monkeypatch):
        """⚠⚠ Считаем по увиденному адресу, а не по номеру выхода.

        Два прокси одного пула выходят **одним** IP. По номерам они выглядят как
        два места, а сайт видит одно — и предел «одна анкета на адрес» с ними
        молча превращался бы в две.
        """
        picked = []

        async def _fake(db, rows, seats, profile_id):
            picked.append((rows, seats))
            return rows[0]

        rows = [{'id': 1, 'url': 'http://a:1', 'check_ip': '5.5.5.5'},
                {'id': 2, 'url': 'http://b:1', 'check_ip': '5.5.5.5'}]

        # Своих считает база: подменяем её ответ «одна анкета уже сидит на 5.5.5.5».
        from src.dating.dating_proxy import _pick

        class _Db:
            async def execute(self, *args):
                self._rows = [{'proxy_ip': '5.5.5.5', 'n': 1}]

            async def fetchall(self):
                return self._rows

        with pytest.raises(DatingProxyError, match='мест нет'):
            await _pick(_Db(), rows, 1, 99)

    async def test_spreads_across_addresses(self):
        """Берём адрес, за которым сидит меньше всех: анкеты ложатся слоем.

        Иначе первый выход набивался бы до предела, а остальные простаивали —
        и падение одного канала уносило бы сразу всех, кто на нём.
        """
        from src.dating.dating_proxy import _pick

        rows = [{'id': 1, 'url': 'http://a:1', 'check_ip': '1.1.1.1'},
                {'id': 2, 'url': 'http://b:1', 'check_ip': '2.2.2.2'}]

        class _Db:
            async def execute(self, *args):
                self._rows = [{'proxy_ip': '1.1.1.1', 'n': 1}]

            async def fetchall(self):
                return self._rows

        got = await _pick(_Db(), rows, 3, 99)

        assert got['check_ip'] == '2.2.2.2', 'выбрали занятый адрес вместо свободного'

    async def test_refusal_says_what_to_do(self):
        """Мест нет — причина с числами и что делать.

        «Свободных выходов нет» одинаково звучит и когда их не завели, и когда
        предел сайта стоит единицей при десяти анкетах. Разбирать это по журналу
        — полчаса.
        """
        from src.dating.dating_proxy import _pick

        class _Db:
            async def execute(self, *args):
                self._rows = [{'proxy_ip': '1.1.1.1', 'n': 2}]

            async def fetchall(self):
                return self._rows

        with pytest.raises(DatingProxyError) as got:
            await _pick(_Db(), [{'id': 1, 'url': 'u', 'check_ip': '1.1.1.1'}], 2, 99)

        assert 'предел сайта' in str(got.value)
        assert 'Анкет на один внешний IP' in str(got.value)


class TestProxyKeepsOldExit:
    """Прежний выход у анкеты: когда сохраняем, а когда переселяем.

    ⚠ Правило просил заказчик словами: «когда анкета логинится, ей нужно
    подставлять прокси, который у неё уже был; но если она не попадает в лимиты —
    выдавать новый». Оба конца и стережём.
    """

    async def test_old_exit_kept_while_it_fits(self):
        """Влезает в предел — остаётся прежний, сколько бы ни было свободных.

        Сайт помнит, с какого адреса анкета входила: переезд для него
        подозрительнее паузы.
        """
        from src.dating.dating_proxy import _fits

        class _Db:
            async def execute(self, sql, args=None):
                # Первым спрашивают своё время, потом — сколько старших на адресе.
                self._row = ({'proxy_at': 'вчера'} if 'SELECT proxy_at' in sql
                             else {'n': 0})

            async def fetchone(self):
                return self._row

        assert await _fits(_Db(), {'check_ip': '1.1.1.1'}, 1, 5) is True

    async def test_old_exit_dropped_when_over_the_limit(self):
        """Предел снизили либо кто-то сел раньше — уступаем и едем на новый."""
        from src.dating.dating_proxy import _fits

        class _Db:
            async def execute(self, sql, args=None):
                self._row = ({'proxy_at': 'сегодня'} if 'SELECT proxy_at' in sql
                             else {'n': 1})

            async def fetchone(self):
                return self._row

        assert await _fits(_Db(), {'check_ip': '1.1.1.1'}, 1, 5) is False

    async def test_seniority_breaks_the_tie(self):
        """⚠⚠ Старшинство разрывает симметрию, иначе анкеты меняются местами вечно.

        Две анкеты на адресе с пределом в одну: считай мы «сколько всего»,
        обе увидели бы «занято 2 ≥ 1», обе пошли бы за новым выходом — и так по
        кругу. Считаем **тех, кто занял раньше**: первая остаётся, вторая едет.
        """
        from src.dating.dating_proxy import _fits

        asked = []

        class _Db:
            async def execute(self, sql, args=None):
                asked.append(sql)
                self._row = ({'proxy_at': 'т'} if 'SELECT proxy_at' in sql else {'n': 0})

            async def fetchone(self):
                return self._row

        await _fits(_Db(), {'check_ip': '1.1.1.1'}, 1, 5)

        assert any('proxy_at <=' in sql for sql in asked), \
            'старшинство не учтено — анкеты будут меняться выходами по кругу'

    async def test_no_ip_never_fits(self):
        """Выход без увиденного адреса не годится: считать его не с чем."""
        from src.dating.dating_proxy import _fits

        assert await _fits(None, {'check_ip': ''}, 5, 1) is False


class TestProxyIpMoved:
    """⚠ Канал сменил внешний адрес — анкетам его подтягивают.

    Резидентные и мобильные прокси меняют IP сами: строка подключения та же,
    наружу смотрит другой адрес. Без подтягивания предел «анкет на IP» считался
    бы по адресу, которого больше нет, — и две анкеты, съехавшие на один новый
    IP, выглядели бы как сидящие на разных. Защита, ради которой всё делалось,
    молча перестала бы работать.
    """

    async def test_only_ip_is_touched(self):
        """Обновляется **только** `proxy_ip`.

        ⚠ `proxy_at` трогать нельзя: по нему считается старшинство при перегрузе
        адреса (`_fits`), и сдвиг сделал бы всех «младшими» разом — анкеты начали
        бы уступать друг другу выход по кругу.
        """
        from src.dating.dating_proxy_round import _ip_sync

        asked = []

        class _Db:
            rowcount = 0
            connection = type('C', (), {'commit': staticmethod(lambda: _noop())})()

            async def execute(self, sql, args=None):
                asked.append(sql)

        async def _noop():
            return None

        # Подменяем соединение: проверяем текст запроса, а не работу базы.
        import src.dating.dating_proxy_round as mod
        from contextlib import asynccontextmanager

        @asynccontextmanager
        async def _db():
            yield _Db()

        mod.mysql_get_db_async = _db
        await _ip_sync([{'id': 7, 'ok': True, 'ip': '9.9.9.9'}])

        assert any('SET proxy_ip' in sql for sql in asked)
        assert not any('proxy_at' in sql for sql in asked), \
            'сдвинули время занятия — старшинство поедет, и анкеты начнут меняться выходами'

    async def test_dead_and_addressless_are_skipped(self):
        """Мёртвый выход и выход без адреса анкетам ничего не подтягивают.

        У мёртвого адреса нет вовсе, и записать пустую строку значило бы стереть
        анкете тот адрес, с которого она **сейчас** ходит.
        """
        from src.dating.dating_proxy_round import _ip_sync
        import src.dating.dating_proxy_round as mod
        from contextlib import asynccontextmanager

        asked = []

        class _Db:
            rowcount = 0
            connection = type('C', (), {'commit': staticmethod(lambda: _ok())})()

            async def execute(self, sql, args=None):
                asked.append(sql)

        async def _ok():
            return None

        @asynccontextmanager
        async def _db():
            yield _Db()

        mod.mysql_get_db_async = _db
        await _ip_sync([{'id': 1, 'ok': False, 'ip': ''},
                        {'id': 2, 'ok': True, 'ip': ''}])

        assert not [sql for sql in asked if 'UPDATE' in sql], \
            'записали пустой адрес — анкета потеряла тот, с которого ходит'
