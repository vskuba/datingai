"""Четыре круга, у которых не было ни одного теста: присутствие, лента, панель, анкета.

## ⚠⚠ Зачем отдельным файлом

`heartbeat_round`, `online_round`, `panel_round` и `profile_page` не упоминались в
сюите **ни разу** — ни по имени, ни через ручку. Это не мелкие вспомогательные
модули: первый отвечает за «анкета видна в сети» (её видит собеседник), второй — за
ленту, «на которой стоит половина панели», третий держит сессию агентства круглые
сутки, четвёртый читает анкету девушки разбором страницы.

Проверяется здесь не работа кругов целиком — она требует сайта, соседа и браузера, —
а их **обещания**: сроки, память между заходами, сторона, в которую решается
сомнение, и совпадение расчётов, которые обязаны совпадать.

## ⚠ Почему обещания, а не поведение

Круг присутствия ходит на живой сайт, круг панели поднимает вкладку Chromium у
соседа. Живого HTTP в сюите не бывает (`testing.md`, §1.2), а заглушка на весь
обход означала бы, что проверяем заглушку. Зато у каждого круга есть решения,
которые принимаются **до** всякого выхода наружу, и ошибка в них стоит дорого:
пропущенный удар — «анкета не в сети», лишний вход — вкладка Chromium.
"""

import time

import pytest

from src.dating import (heartbeat_round, online_round, panel_round,
                        profile_page, schedule_round)
from src.dating import round as engine

pytestmark = pytest.mark.profile


class TestTheLocalMinutesAgreeBetweenRounds:
    """⚠⚠ Два круга считают местные минуты, и они обязаны считать одинаково.

    Расписание решает, **когда входить**, присутствие — **когда быть в сети**.
    Разойдись расчёты, и анкета входила бы по одним часам, а держалась в сети по
    другим: в панели это выглядит как «расписание не работает», причём по-разному у
    разных сайтов.
    """

    @pytest.mark.parametrize('offset', [-11, -3, 0, 2, 3, 5, 12])
    def test_both_rounds_give_the_same_minute(self, offset):
        assert (heartbeat_round.round_minutes(offset)
                == schedule_round.round_minutes(offset)), \
            f'на поясе {offset:+} круги разошлись в местном времени'

    def test_the_offset_shifts_by_whole_hours(self):
        """Пояс +3 отличается от +0 ровно на 180 минут по кругу суток."""
        base = heartbeat_round.round_minutes(0)
        shifted = heartbeat_round.round_minutes(3)

        assert (shifted - base) % (24 * 60) == 180, \
            f'сдвиг {shifted - base} минут вместо 180'

    def test_the_minute_is_inside_a_day(self):
        """⚠ Значение — минуты **внутри** суток: расписание сверяет его со сменой."""
        for offset in (-12, 0, 14):
            got = heartbeat_round.round_minutes(offset)

            assert 0 <= got < 24 * 60, f'пояс {offset:+} дал {got} минут'


class TestTheHeartbeatRemembersItsPasses:
    """Память круга присутствия: первый заход — всегда пора, дальше по сроку.

    ⚠ Память здесь **в процессе**, а не в Redis, и это осознанно: смена держателя
    перезапускается раз в час, и лишний удар после перезапуска дешевле лишнего
    чтения каждым заходом.
    """

    @pytest.fixture(autouse=True)
    def clean(self):
        """⚠ Память модульная и общая: без очистки тесты влияли бы друг на друга."""
        heartbeat_round._beaten.clear()
        yield
        heartbeat_round._beaten.clear()

    def test_the_first_pass_is_always_due(self):
        assert heartbeat_round._due(4242, every=60) is True, \
            'по сайту не били ни разу, а круг решил, что рано'

    def test_right_after_a_pass_it_is_not_due(self):
        heartbeat_round._mark(4242)

        assert heartbeat_round._due(4242, every=60) is False, \
            'ударили только что, а круг собрался снова'

    def test_it_is_due_again_once_the_term_has_passed(self, monkeypatch):
        heartbeat_round._mark(4242)
        later = time.time() + 61
        monkeypatch.setattr(heartbeat_round.time, 'time', lambda: later)

        assert heartbeat_round._due(4242, every=60) is True, \
            'срок вышел, а круг всё ещё молчит'

    def test_sites_are_remembered_apart(self):
        """⚠ Отметка на сайт, а не общая: у сайтов свои сроки присутствия."""
        heartbeat_round._mark(11)

        assert heartbeat_round._due(11, every=60) is False
        assert heartbeat_round._due(22, every=60) is True, \
            'отметка одного сайта погасила удары по другому'


class TestThePanelDoubtsTowardsSilence:
    """⚠⚠ У круга панели цена ошибок несимметрична, и сомнение решается молчанием.

    Лишний заход может кончиться входом сценарием, то есть вкладкой Chromium у
    соседа; пропущенный — задержкой в пять минут. Поэтому сбой Redis обязан читаться
    как «не пора», а не как «пора».
    """

    def test_a_broken_redis_means_not_due(self, monkeypatch):
        def _boom():
            raise RuntimeError('Redis лёг')

        monkeypatch.setattr(engine, 'redis_conn_get', _boom)

        assert engine.round_slot_due(panel_round.PANEL_ROUND_KEY, 4242,
                            'панели') is False, \
            'Redis недоступен, а круг решил идти — это вкладка Chromium на ровном месте'

    def test_a_broken_redis_does_not_raise_on_mark(self, monkeypatch):
        """⚠ И отметка не бросает: упади она — круг оборвался бы после работы,
        уже сходив к соседу, и следующий заход повторил бы вход заново."""
        def _boom():
            raise RuntimeError('Redis лёг')

        monkeypatch.setattr(engine, 'redis_conn_get', _boom)

        engine.round_slot_mark(panel_round.PANEL_ROUND_KEY, 4242, 300, 'панели')

    def test_the_term_of_the_mark_is_the_setting(self, monkeypatch):
        """⚠⚠ Срок отметки **и есть** настройка: Redis гасит её сам.

        Второго места, где срок сравнивают с часами, здесь нет намеренно — оно
        разошлось бы с настройкой на первой же правке.
        """
        said = {}

        class _Fake:
            def setex(self, key, term, value):
                said['key'], said['term'] = key, term

            def get(self, key):
                return None

        monkeypatch.setattr(engine, 'redis_conn_get', lambda: _Fake())
        engine.round_slot_mark(panel_round.PANEL_ROUND_KEY, 4242, 300, 'панели')

        assert said['term'] == 300, f'срок отметки {said.get("term")} вместо 300'
        assert '4242' in said['key'], f'ключ не про этот сайт: {said.get("key")}'

    def test_a_zero_term_is_lifted_to_a_second(self, monkeypatch):
        """⚠ Ноль Redis не принимает — отметка не поставилась бы вовсе, и круг
        ходил бы каждым тиком."""
        said = {}

        class _Fake:
            def setex(self, key, term, value):
                said['term'] = term

            def get(self, key):
                return None

        monkeypatch.setattr(engine, 'redis_conn_get', lambda: _Fake())
        engine.round_slot_mark(panel_round.PANEL_ROUND_KEY, 4242, 0, 'панели')

        assert said['term'] >= 1, 'срок отметки нулевой — Redis её не примет'


class TestThePanelRoundIgnoresTheSchedule:
    """⚠⚠ Расписания у круга панели нет, и это решение, а не упущение.

    Панель — наша страница агентства, и вопрос к ней один: жива ли сессия. Жива она
    должна быть **всегда**, в том числе в три ночи, когда придёт круг импорта
    переписки. Появись здесь смены — импорт ночью упёрся бы в мёртвую сессию.
    """

    def test_the_module_does_not_consult_shifts(self):
        import inspect

        code = inspect.getsource(panel_round)

        for word in ('schedule_now', 'schedule_parse', 'mailing_schedule'):
            assert word not in code, \
                f'круг панели начал спрашивать расписание ({word}) — сессия умрёт к ночи'


class TestTheOnlineRoundHasNoTermOfItsOwn:
    """⚠⚠ Частота ленты — та же настройка, что у страницы, а не своя.

    Круг не приносит нового способа снимать список: он нажимает на кнопку вместо
    человека. Заведи он свой срок — два числа отвечали бы на один вопрос, и объяснить
    расхождение было бы нечем.
    """

    def test_the_freshness_is_decided_by_the_page_side(self):
        import inspect

        code = inspect.getsource(online_round.online_round_run)

        assert 'people_snap_due' in code, \
            'круг перестал спрашивать выдержку у той же стороны, что и страница'
        assert 'people_snap_start' in code, \
            'круг заводит съём в обход общей точки'

    def test_there_is_no_own_term_constant(self):
        """⚠ У круга своя только частота тика; выдержку снимка он не объявляет.

        Проверяется по **именам констант модуля**, а не поиском в тексте: имя
        настройки стоит в его докстринге объяснением, и поиск по тексту падал бы
        на прозе, а не на заведённом числе.
        """
        terms = [one for one in dir(online_round)
                 if one.isupper() and ('EVERY' in one or 'FRESH' in one
                                       or 'AGE' in one)]

        assert terms == [], \
            f'у круга завелась своя выдержка ({terms}) — второе число на один вопрос'

    def test_the_tick_is_the_only_own_number(self):
        """Тик — своё дело круга: как часто **смотреть**, а не как часто снимать."""
        assert online_round.ONLINE_ROUND_TICK_SEC >= 1

    async def test_one_bad_site_does_not_stop_the_others(self, monkeypatch):
        """⚠ Обход продолжается: у соседа бывает занят пул, у сайта — вёрстка."""
        seen = []

        async def _sites():
            return [1, 2, 3]

        async def _due(site_id):
            if int(site_id) == 2:
                raise RuntimeError('пул занят')

            return True

        async def _start(site_id):
            seen.append(int(site_id))

            return True

        monkeypatch.setattr(online_round, '_sites', _sites)

        import src.uvicorn.routers.dating.people as people

        monkeypatch.setattr(people, 'people_snap_due', _due)
        monkeypatch.setattr(people, 'people_snap_start', _start)

        got = await online_round.online_round_run()

        assert seen == [1, 3], f'обход оборвался на сломанном сайте: {seen}'
        assert got['checked'] == 3 and got['started'] == 2


class TestTheProfilePageParsing:
    """⚠⚠ Анкета девушки читается разбором страницы — у женской учётки JSON нет.

    Кабинет девушки собран на GWT-RPC и отдаёт бинарный поток: `LadyStats` отвечает
    `{"success":true}` без единого поля, а `ManStats` под женской учёткой — 401.
    Значит поля приезжают шагами `extract`, то есть **строками из вёрстки**, и весь
    риск здесь в разборе: вёрстка сайта меняется без предупреждения.
    """

    def test_the_stub_pixel_is_not_taken_for_a_photo(self):
        """⚠⚠ Главная ловушка: заглушка ленивой загрузки не пустая.

        Пока карусель не долистали, в `src` лежит прозрачный пиксель
        `data:image/gif;base64,…`. «Первая непустая строка» взяла бы именно его — и
        в карточке оказалась бы дырка вместо лица.
        """
        got = profile_page._first_real(
            'data:image/gif;base64,R0lGODlh\nhttps://site/photo/1.jpg')

        assert got == 'https://site/photo/1.jpg', \
            f'взяли заглушку вместо фотографии: {got}'

    def test_the_first_real_line_wins(self):
        """Селектор ловит всю карусель, а в карточке нужна одна — главная."""
        got = profile_page._first_real('  \n\nПервая\nВторая\n')

        assert got == 'Первая'

    def test_nothing_real_means_empty(self):
        """⚠ Одни заглушки — пусто, а не заглушка: пустое поле честнее дырки."""
        assert profile_page._first_real('data:image/gif;base64,AAA') == ''
        assert profile_page._first_real('   \n  \n') == ''

    def test_a_relative_photo_becomes_full(self):
        """Страница отдаёт и относительный адрес, и полный."""
        got = profile_page._photo_full('/photo/1.jpg', {'base': 'https://site/'})

        assert got == 'https://site/photo/1.jpg', got

    def test_a_full_photo_is_left_alone(self):
        """⚠ Полный адрес не склеивается второй раз — вышло бы `https://site/https://…`."""
        got = profile_page._photo_full('https://site/photo/1.jpg',
                                       {'base': 'https://site/'})

        assert got == 'https://site/photo/1.jpg', got

    def test_the_id_keeps_only_digits(self):
        """⚠ Сайт печатает «ID: 1430119», а в карточке нужен номер."""
        card = profile_page._profile_build(
            {'id': 'ID: 1430119', 'age': '43 года', 'name': 'Renata',
             'city': 'Kharkiv', 'about': 'текст', 'photo': ''},
            {'login': '999'}, {})

        assert card['id'] == '1430119', card['id']
        assert card['age'] == 43, card['age']

    def test_a_missing_id_falls_back_to_the_login(self):
        """⚠⚠ Номера на странице нет — берём логин: карточка без номера не сошьётся
        с фактами, а ключ фактов собирается из номера и имени."""
        card = profile_page._profile_build(
            {'id': '', 'age': '', 'name': 'Diana', 'city': '', 'about': '',
             'photo': ''},
            {'login': '643694'}, {})

        assert card['id'] == '643694', card['id']

    def test_an_unreadable_age_becomes_zero(self):
        """⚠ Возраст числом: строка «нет данных» дала бы `ValueError` на сравнении."""
        card = profile_page._profile_build(
            {'id': '1', 'age': 'нет данных', 'name': '', 'city': '', 'about': '',
             'photo': ''},
            {'login': '1'}, {})

        assert card['age'] == 0

    def test_every_declared_field_is_in_the_card(self):
        """⚠ Карточка несёт все объявленные поля, даже пустые: страница читает их
        по имени, и отсутствующий ключ там — `undefined` вместо прочерка."""
        card = profile_page._profile_build({}, {'login': '7'}, {})

        for field in profile_page.PROFILE_PAGE_FIELDS:
            assert field in card, f'поле `{field}` пропало из карточки'
