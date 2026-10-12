"""Админ-панель агентства: ссылка в шапке и состояние её сессии.

⚠ **Наружу сюита не ходит** (`testing.md`): проверка панели — это запрос на чужой
сайт, а вход в неё — прогон сценария у соседа. Подменяется поэтому ровно граница:
движок присутствия (`http_heartbeat_check`) и дверь к соседу. Под проверкой
остаётся то, ради чего модуль написан, — как собирается адрес проверки, что
считается живой сессией, когда заводится вход и что видит страница.

Живьём проверено вручную: вход сценарием «вход в панель» под Ladadate, cookie
сняты (`SID`), страница `/partner/agency/profile` под ними открывается, проверка
отвечает `online` за 0,2 секунды.
"""

import pytest

from src.dating import panel
from src.dating.panel import (panel_name, panel_root,
                                     panel_show, panel_check,
                                     panel_keep, _split)
from src.dating.panel_fetch import panel_fetch_table

pytestmark = pytest.mark.core

# Номер выдуманный: своей таблицы сайтов здесь нет, а живые настройки задевать
# нельзя. Тот же приём и тот же диапазон, что у настроек сайта.
SITE = 999_781

PANEL = 'https://panel.test/partner/agency/profile'

# Кусок живой страницы панели: две строки журнала чата. ⚠ Заголовок таблицы здесь
# не для полноты — на нём и проверяется, что разбор берёт только `tbody`.
PAGE = """
<table class="table table-bordered">
  <thead class="thead-light"><tr><th>ID</th><th>Date UTC</th><th>From</th>
    <th>To</th><th>Body</th></tr></thead>
  <tbody>
    <tr>
      <td>37641085</td>
      <td>2026-09-02 19:12:09</td>
      <td style=""><a href="/partner/agency/userprofileview/181685">181685</a> Semen Gorbunkov</td>
      <td style="color:red"><a href="/partner/agency/userprofileview/643694">643694</a> Diana</td>
      <td><p style="width:250px;" title="I am too">I am too</p></td>
    </tr>
    <tr>
      <td>37639038</td>
      <td>2026-09-02 19:09:08</td>
      <td style="color:red"><a href="/partner/agency/userprofileview/643694">643694</a> Diana</td>
      <td style=""><a href="/partner/agency/userprofileview/181685">181685</a> Semen Gorbunkov</td>
      <td><p style="width:250px;" title="pretty chill honestly. just watching a series">pretty chill hon…</p></td>
    </tr>
  </tbody>
</table>
"""


@pytest.fixture
async def panel_site(auth_client):
    """Сайт с заданной панелью. Что завела, то и убрала."""
    before = ((await auth_client.get(f'/api/dating/site/{SITE}')).json()
              ['result']['settings'])

    await auth_client.put(f'/api/dating/site/{SITE}',
                          json={'settings': {'admin_url': PANEL,
                                             'admin_mark': 'Ladies profiles',
                                             'admin_every': '300'}})
    # ⚠ Состояние панели чистим **до** теста, а не только после. Оно живёт в
    # колонках сайта, а не в настройках, и записанное соседним тестом означало бы
    # порядок прогона как условие: «ещё не спрашивали» проходило бы в одиночку и
    # падало в сюите.
    await _panel_wipe()

    yield SITE

    await auth_client.put(f'/api/dating/site/{SITE}', json={'settings': before})
    await _panel_wipe()


class TestProbeUrl:
    """⚠ Адрес проверки: косая черта в конце решает всё.

    Движок склеивает адрес как `base + '/' + path`, поэтому целый адрес в `base`
    давал бы лишнюю черту — а Ladadate отвечает на неё `301` на адрес без неё, и с
    `no_redirect` это читается как «панель не признала вход». Так и вышло на
    первом заходе: вход проходил, сессия жила, точка горела красным.
    """

    @pytest.mark.parametrize('url, base, path', [
        ('https://panel.test/partner/agency/profile', 'https://panel.test',
         'partner/agency/profile'),
        # Черта в конце снимается — ровно тот случай, что и ловил.
        ('https://panel.test/partner/agency/', 'https://panel.test', 'partner/agency'),
        # Голый хост: пути нет вовсе, и выдумывать его нечем.
        ('https://panel.test', 'https://panel.test', ''),
    ])
    def test_url_splits_into_root_and_path(self, url, base, path):
        assert _split(url) == (base, path)

    async def test_config_asks_the_page_after_login(self, panel_site):
        """Шаг проверки идёт на ту же страницу, что стоит в настройке."""
        config = await panel._config(SITE)
        step = config['alive'][0]

        assert config['base'] == 'https://panel.test'
        assert step['path'] == 'partner/agency/profile'
        assert step['ok_mark'] == 'Ladies profiles'
        # ⚠ Без этого панель, уводящая гостя на форму, выглядит живой всегда.
        assert step['no_redirect'] is True


class TestName:
    """Подпись ссылки — хост, и только он."""

    @pytest.mark.parametrize('url, name', [
        ('https://panel.test/partner/agency/profile', 'panel.test'),
        ('https://panel.test/', 'panel.test'),
        ('panel.test', 'panel.test'),
    ])
    def test_name_is_the_host(self, url, name):
        assert panel_name(url) == name


class TestShow:
    """Что видит шапка страницы."""

    async def test_no_url_means_no_panel(self, auth_client):
        """⚠ Адрес — он же выключатель: пусто значит «панели у сайта нет».

        Пустая ссылка в шапке обещала бы то, чего некому исполнить.
        """
        got = await panel_show(999_782)

        assert got['on'] is False
        assert got['url'] == ''

    async def test_panel_shows_its_link_and_name(self, panel_site):
        got = await panel_show(SITE)

        assert got['on'] is True
        assert got['url'] == PANEL
        assert got['name'] == 'panel.test'

    async def test_unknown_is_not_offline(self, panel_site):
        """⚠ Пустое состояние — «ещё не спрашивали», а не «вход потерян».

        Сведи их в одно, и панель, настроенная минуту назад, выглядела бы
        сломанной до первого захода круга.
        """
        assert (await panel_show(SITE))['status'] == ''


class TestCheck:
    """Проверка сессии: что пишется сайту."""

    async def test_without_cookies_it_is_offline_without_asking(self, panel_site,
                                                               monkeypatch):
        """⚠ Не входили — `offline`, и чужой сервер при этом не трогаем.

        Спрашивать панель, зная, что предъявить нечего, значит платить запросом за
        ответ, который известен заранее.
        """
        called = []

        async def _never(*args, **kwargs):
            called.append(1)

            return {'status': 'online', 'error': '', 'ms': 0}

        monkeypatch.setattr(panel, 'http_heartbeat_check', _never)

        got = await panel_check(SITE)

        assert got['status'] == 'offline'
        assert not called, 'спросили панель, хотя предъявить нечего'

    async def test_answer_is_saved_to_the_site(self, panel_site, monkeypatch):
        """Ответ панели ложится в строку сайта — из неё его и читает шапка."""
        monkeypatch.setattr(panel, '_row',
                            _row_with({'panel_state': _state(), 'panel_status': '',
                                       'panel_error': ''}))
        monkeypatch.setattr(panel, 'http_heartbeat_check',
                            _answer({'status': 'online', 'error': '', 'ms': 12}))

        await panel_check(SITE)
        # ⚠ Подмены снимаем до показа: `panel_show` читает ту же строку
        # сайта, и с подменённой он показал бы не записанное, а подставленное.
        monkeypatch.undo()

        assert (await panel_show(SITE))['status'] == 'online'

    async def test_success_wipes_the_old_error(self, panel_site, monkeypatch):
        """⚠ Удача стирает прежний отказ: строка живёт до следующей удачи.

        Иначе панель, починившаяся сама, показывала бы вчерашнюю причину рядом с
        зелёной точкой.
        """
        monkeypatch.setattr(panel, '_row',
                            _row_with({'panel_state': _state()}))
        monkeypatch.setattr(panel, 'http_heartbeat_check',
                            _answer({'status': 'error', 'error': 'сеть легла', 'ms': 0}))
        await panel_check(SITE)

        monkeypatch.setattr(panel, 'http_heartbeat_check',
                            _answer({'status': 'online', 'error': '', 'ms': 8}))
        await panel_check(SITE)
        # Подмены снимаем до показа — он читает ту же строку сайта.
        monkeypatch.undo()

        assert (await panel_show(SITE))['status'] == 'online'
        assert (await panel_show(SITE))['error'] == ''


class TestKeep:
    """Когда заводится вход сценарием, а когда нет."""

    async def test_error_does_not_start_the_browser(self, panel_site, monkeypatch):
        """⚠ `error` — «не достучались», и браузер соседа из-за этого не гоняем.

        Вкладка Chromium стоит секунд и памяти, а чужая сетевая икота проходит
        сама: круг придёт снова через `admin_every`.
        """
        monkeypatch.setattr(panel, '_row', _row_with({'panel_state': _state()}))
        monkeypatch.setattr(panel, 'http_heartbeat_check',
                            _answer({'status': 'error', 'error': 'таймаут', 'ms': 0}))
        monkeypatch.setattr(panel, 'panel_login', _login_forbidden())

        got = await panel_keep(SITE)

        assert got['status'] == 'error'
        assert got['logged_in'] is False

    async def test_offline_starts_the_login(self, panel_site, monkeypatch):
        """`offline` значит «панель нас не признала» — это и есть повод войти."""
        logins = []

        async def _login(site_id):
            logins.append(int(site_id))

            return {'status': 'ok', 'error': ''}

        monkeypatch.setattr(panel, '_row', _row_with({'panel_state': _state()}))
        monkeypatch.setattr(panel, 'http_heartbeat_check',
                            _answer({'status': 'offline', 'error': 'не признала', 'ms': 3}))
        monkeypatch.setattr(panel, 'panel_login', _login)

        got = await panel_keep(SITE)

        assert logins == [SITE]
        assert got['logged_in'] is True

    async def test_login_is_checked_again(self, panel_site, monkeypatch):
        """⚠ После входа спрашиваем панель ещё раз, и это не перестраховка.

        Сценарий отчитывается о **своих** шагах, а не о том, признала ли панель
        сессию. У анкет это уже ловило неверный пароль: сценарий проходил,
        состояние приходило пустым, а карточка выглядела рабочей.
        """
        answers = iter([{'status': 'offline', 'error': 'не признала', 'ms': 1},
                        {'status': 'online', 'error': '', 'ms': 2}])

        async def _check(*args, **kwargs):
            return next(answers)

        monkeypatch.setattr(panel, '_row', _row_with({'panel_state': _state()}))
        monkeypatch.setattr(panel, 'http_heartbeat_check', _check)
        monkeypatch.setattr(panel, 'panel_login',
                            lambda site_id: _ok({'status': 'ok', 'error': ''}))

        got = await panel_keep(SITE)

        assert got['status'] == 'online'


class TestRoot:
    """Корень панели: от него строятся адреса её страниц."""

    @pytest.mark.parametrize('url, root', [
        # Страница внутри панели: корень — то, что рядом с ней.
        ('https://panel.test/partner/agency/profile', 'https://panel.test/partner/agency'),
        # ⚠ Черта в конце значит «это и есть папка»: срежь мы последний кусок и
        # здесь, все таблицы искались бы этажом выше.
        ('https://panel.test/partner/agency/', 'https://panel.test/partner/agency'),
        ('https://panel.test', 'https://panel.test'),
    ])
    def test_root_is_the_folder_of_the_page(self, url, root):
        assert panel_root(url) == root


class TestTable:
    """Разбор таблицы панели: своим парсером, без библиотек."""

    def test_rows_come_from_tbody_only(self):
        """⚠ Заголовок таблицы не строка данных.

        Возьми разбор все `tr`, и «ID / Date UTC / From» приехало бы первым
        сообщением — неизвестно от кого и неизвестно когда.
        """
        rows = panel_fetch_table(PAGE)

        assert len(rows) == 2
        assert rows[0][0]['text'] == '37641085'

    def test_cell_keeps_both_link_and_text(self):
        """Панель пишет в ячейку «181685 Semen Gorbunkov»: номер ссылкой, имя рядом."""
        cell = panel_fetch_table(PAGE)[0][2]

        assert cell['link'] == '181685'
        assert cell['text'] == '181685 Semen Gorbunkov'

    def test_full_text_comes_from_the_title(self):
        """⚠ Полный текст лежит в подсказке: в самой ячейке он обрезан шириной."""
        cell = panel_fetch_table(PAGE)[1][4]

        assert cell['title'] == 'pretty chill honestly. just watching a series'

    def test_cell_keys_are_latin(self):
        """⚠ Ячейки — **наше** изобретение, и ключи у них латиницей.

        Кириллица в ключе требует переключения раскладки в `grep` и в поиске
        редактора, а похожие начертания (`с`/`c`, `о`/`o`) дают ключи, различимые
        только байтами (`code_rules.md`, §1.2). Поля справа от `_cells` — другое
        дело: их имена задал сценарий у соседа.
        """
        cell = panel_fetch_table(PAGE)[0][0]

        assert set(cell) == {'text', 'link', 'title'}

    def test_cells_are_named_as_the_scenario_named_them(self):
        """⚠ Имена полей те же, что у сценария: разбор строк общий на оба пути.

        Разойдись они — и забор ручкой однажды стал бы читать таблицу иначе, чем
        забор сценарием, а заметить это было бы нечем.

        ⚠ Потому эти имена и остались русскими, когда ключи ячеек стали
        латинскими: они **не наши**. Перевод делает `_cells`, в одном месте.
        """
        from src.dating.chat_panel import _cells

        one = _cells(panel_fetch_table(PAGE))[0]

        assert one['номер'] == '37641085'
        assert one['когда'] == '2026-09-02 19:12:09'
        assert one['от_номер'] == '181685'
        assert one['кому_номер'] == '643694'
        assert one['текст_подсказка'] == 'I am too'

    def test_rows_turn_into_messages(self):
        """Сквозная проверка: таблица → записи `dating_message`.

        ⚠ Здесь и видно, ради чего имена общие: `chat_panel_rows` не знает,
        откуда пришли строки, и разбирает их одинаково.
        """
        from src.dating.chat_panel import _cells, chat_panel_rows

        got = chat_panel_rows({'сообщения': _cells(panel_fetch_table(PAGE))},
                                     {'643694': 77})

        assert [one['direction'] for one in got] == ['in', 'out']
        # ⚠ Собеседник, а не автор: так же устроен сокет, и на этом держится
        # группировка диалогов.
        assert {one['sender_uid'] for one in got} == {'181685'}
        assert got[0]['message_uid'] == 'panel:37641085'


class TestTransport:
    """Чем забираем переписку: ручкой панели или сценарием."""

    async def test_site_with_a_panel_goes_by_the_handle(self, panel_site):
        from src.dating.chat_panel import _panel_ready

        assert await _panel_ready(SITE) is True

    async def test_site_without_a_panel_keeps_the_scenario(self):
        """⚠ Не запасной путь, а выбор по настройке: без адреса панели — сценарий."""
        from src.dating.chat_panel import _panel_ready

        assert await _panel_ready(999_782) is False


class TestHandle:
    """Ручка шапки: только читает."""

    async def test_handle_answers_with_the_saved_state(self, auth_client, panel_site):
        got = (await auth_client.get(f'/api/dating/site/{SITE}/panel')).json()['result']

        assert got['site_id'] == SITE
        assert got['on'] is True
        assert got['name'] == 'panel.test'

    async def test_site_without_panel_says_so(self, auth_client):
        got = (await auth_client.get('/api/dating/site/999782/panel')).json()['result']

        assert got['on'] is False


async def _panel_wipe() -> None:
    """Стирает состояние панели тестового сайта: колонки, а не настройки."""
    from mysql_.mysql_ import mysql_get_db_async

    async with mysql_get_db_async() as db:
        await db.execute("UPDATE site SET panel_state = NULL, panel_status = '',"
                         "       panel_checked_at = NULL, panel_login_at = NULL,"
                         "       panel_error = '' WHERE site_id = %s", (SITE,))
        await db.connection.commit()


# ── Подмены ──────────────────────────────────────────────────────────────────

def _state() -> str:
    """Снятое состояние браузера с cookie нужного хоста."""
    return ('{"cookies": [{"name": "SID", "value": "s1", "domain": "panel.test"}],'
            ' "origins": []}')


def _row_with(fields: dict):
    """Подменённая строка сайта: те поля, что нужны проверке."""
    async def _row(site_id):
        return {'panel_state': None, 'panel_status': '', 'panel_checked_at': None,
                'panel_login_at': None, 'panel_error': '', **fields}

    return _row


def _answer(result: dict):
    async def _check(*args, **kwargs):
        return dict(result)

    return _check


def _login_forbidden():
    async def _login(site_id):
        raise AssertionError('завели вход сценарием там, где его быть не должно')

    return _login


async def _ok(result: dict) -> dict:
    return result
