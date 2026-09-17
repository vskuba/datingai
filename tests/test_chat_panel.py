"""Переписка из панели агентства: разбор строк, запись и границы импорта.

Разбор — чистый юнит: строки панели заранее известны (сняты с живой страницы), и
проверять их против живого сайта значило бы зависеть от того, кто кому написал
сегодня.

Ручка зовётся в процессе с поддельным соседом (`rhapsody_fake.py`): она гонит
сценарий, а сюита наружу не ходит (`testing.md`, §1.2).
"""

import uuid

import pytest

from mysql_.mysql_ import mysql_get_db_async
from fastapi import HTTPException

from src.dating.dating_chat_panel import (DATING_CHAT_PANEL_PREFIX,
                                          dating_chat_panel_rows)
from src.dating import dating_chat_panel
from src.uvicorn.routers.dating import dating_chat
from src.dating.dating_chat_panel import DATING_CHAT_PANEL_DAYS_MAX as DATING_CHAT_DAYS_MAX
from src.uvicorn.routers.dating.dating_chat import (dating_chat_import,
                                                    dating_chat_ready)

pytestmark = pytest.mark.chat

# Наши анкеты: номер на сайте → номер у нас.
PROFILES = {'643694': 849, '486221': 853}

# Строка панели — ровно в том виде, в каком её отдаёт сценарий. Снята с живой
# страницы `userchatmessages`, поэтому и поля, и склейка «номер + имя» настоящие.
ROW = {'номер': '36594474', 'когда': '2026-09-01 13:11:28',
       'от_номер': '181685', 'от': '181685 Semen Gorbunkov',
       'кому_номер': '643694', 'кому': '643694 Diana',
       'текст': 'helo', 'текст_подсказка': 'helo'}


def _extract(*rows):
    return {'сообщения': list(rows)}


def _row(**over):
    return {**ROW, **over}


class TestDirection:
    """⚠ Своя сторона узнаётся по `site_uid`, а не по цвету ячейки."""

    def test_message_to_our_profile_is_incoming(self):
        rows = dating_chat_panel_rows(_extract(ROW), PROFILES)

        assert len(rows) == 1
        assert rows[0]['profile_id'] == 849
        assert rows[0]['direction'] == 'in'
        assert rows[0]['sender_uid'] == '181685'
        assert rows[0]['sender_name'] == 'Semen Gorbunkov'

    def test_message_from_our_profile_is_outgoing(self):
        """⚠ У исходящего `sender_uid` — тоже собеседник, а не автор.

        Название столбца обманчиво, но по нему собран индекс диалога и
        группировка карточки: положи мы туда автора, исходящие уехали бы в
        отдельный «диалог с самим собой».
        """
        rows = dating_chat_panel_rows(_extract(_row(
            **{'от_номер': '643694', 'от': '643694 Diana',
               'кому_номер': '181685', 'кому': '181685 Semen Gorbunkov'})), PROFILES)

        assert rows[0]['direction'] == 'out'
        assert rows[0]['sender_uid'] == '181685', 'в исходящем записан автор вместо собеседника'
        assert rows[0]['profile_id'] == 849

    def test_row_about_other_profiles_is_skipped(self):
        """Панель агентства показывает всех, а заводим мы у себя не всех.

        Пропуск — не ошибка разбора: приписать чужую переписку своей анкете было
        бы куда хуже.
        """
        rows = dating_chat_panel_rows(_extract(_row(
            **{'кому_номер': '999999', 'кому': '999999 Чужая'})), PROFILES)

        assert rows == []

    def test_both_sides_ours_counts_as_incoming(self):
        """Обе стороны наши — считаем входящим: получателю оно и пришло."""
        rows = dating_chat_panel_rows(_extract(_row(
            **{'от_номер': '486221', 'от': '486221 Elena'})), PROFILES)

        assert rows[0]['direction'] == 'in' and rows[0]['profile_id'] == 849


class TestFields:
    """Что кладём в строку."""

    def test_message_id_gets_a_prefix(self):
        """⚠ Приставка обязательна: `message_uid` общий с сокетом.

        У сокета там ULID кадра, здесь числовой номер панели; без приставки они
        однажды сойдутся, и сообщение потеряется как «повтор».
        """
        rows = dating_chat_panel_rows(_extract(ROW), PROFILES)

        assert rows[0]['message_uid'] == DATING_CHAT_PANEL_PREFIX + '36594474'

    def test_name_keeps_its_spaces(self):
        """Имя режется по номеру, а не по первому пробелу: «Semen Gorbunkov» иначе
        потерял бы половину."""
        rows = dating_chat_panel_rows(_extract(ROW), PROFILES)

        assert rows[0]['sender_name'] == 'Semen Gorbunkov'

    def test_longer_of_text_and_title_wins(self):
        """⚠ Сайт кладёт полную строку в подсказку, а в теле она может быть
        обрезана вёрсткой. Какая длиннее, заранее не известно."""
        long_text = 'полный текст, который в теле обрезан'
        rows = dating_chat_panel_rows(_extract(_row(
            **{'текст': 'полный текст…', 'текст_подсказка': long_text})), PROFILES)

        assert rows[0]['text'] == long_text

    def test_time_is_taken_as_is(self):
        """Панель подписывает колонку «Date UTC» — перевод был бы догадкой."""
        rows = dating_chat_panel_rows(_extract(ROW), PROFILES)

        assert rows[0]['sent_at'] == '2026-09-01 13:11:28'

    def test_unreadable_time_does_not_lose_the_message(self):
        """Формат даты сменился — сообщение всё равно наше. Запись ляжет со
        временем импорта: потерять её хуже, чем показать не тем часом."""
        rows = dating_chat_panel_rows(_extract(_row(**{'когда': 'вчера вечером'})), PROFILES)

        assert len(rows) == 1 and rows[0]['sent_at'] == ''

    def test_uid_falls_back_to_the_cell(self):
        """Ссылки в ячейке может не оказаться — номер тогда берётся из текста."""
        rows = dating_chat_panel_rows(_extract(_row(**{'от_номер': ''})), PROFILES)

        assert rows[0]['sender_uid'] == '181685'


class TestRefuses:
    """Чего разбор не пропускает."""

    def test_row_without_id_is_skipped(self):
        """Без номера сообщения нечем ловить повторы: каждый прогон плодил бы копии."""
        assert dating_chat_panel_rows(_extract(_row(**{'номер': ''})), PROFILES) == []

    def test_empty_text_is_skipped(self):
        """Пустая строка в ленте неотличима от сбоя разбора."""
        assert dating_chat_panel_rows(_extract(
            _row(**{'текст': '', 'текст_подсказка': ''})), PROFILES) == []

    def test_repeat_inside_one_batch_is_dropped(self):
        """Один номер — одна запись, даже если панель показала строку дважды."""
        rows = dating_chat_panel_rows(_extract(ROW, dict(ROW)), PROFILES)

        assert len(rows) == 1

    def test_junk_extract_is_empty(self):
        for extract in ({}, {'сообщения': None}, {'сообщения': ['строка']}, None):
            assert dating_chat_panel_rows(extract, PROFILES) == []


class TestHandler:
    """Ручка импорта: что она делает до прогона и как отказывает."""

    async def test_ready_says_no_without_a_scenario(self, rhapsody):
        rhapsody.forget_scenarios()

        result = (await dating_chat_ready(343, user=None))['result']

        assert result['ready'] is False and 'сценар' in result['why'].lower()

    async def test_ready_says_no_without_a_site(self, rhapsody):
        result = (await dating_chat_ready(user=None))['result']

        assert result['ready'] is False
        assert not rhapsody.calls, 'к соседу пошли, не зная сайта'

    async def test_busy_neighbour_is_not_ready_either(self, rhapsody):
        """Отказ соседа гасит кнопку, но подсказка другая: чинить человеку разное."""
        rhapsody.refuse(409, 'предел сессий исчерпан: 25')

        result = (await dating_chat_ready(343, user=None))['result']

        assert result['ready'] is False and 'браузер соседа занят' in result['why']

    async def test_no_site_is_422(self, rhapsody):
        with pytest.raises(HTTPException) as refusal:
            await dating_chat_import(user=None)

        assert refusal.value.status_code == 422

    @pytest.mark.parametrize('days', [0, -1, DATING_CHAT_DAYS_MAX + 1])
    async def test_bad_range_is_422(self, rhapsody, monkeypatch, days):
        """⚠ Предел диапазона — не придирка.

        Панель отдаёт период целиком, одной страницей и без постраничности:
        попроси у неё год — получишь либо таймаут браузера, либо страницу, которую
        нечем прокрутить.
        """
        with pytest.raises(HTTPException) as refusal:
            await dating_chat_import(343, days=days, user=None)

        assert refusal.value.status_code == 422

    async def test_site_without_profiles_is_422(self, rhapsody, monkeypatch):
        """⚠ Анкеты спрашиваем **до** прогона.

        Без них разбирать нечего, а прогон это сорок секунд живого браузера и
        вкладка в общем пуле соседа — платить их за ответ, который некуда
        положить, незачем.
        """
        monkeypatch.setattr(dating_chat_panel, 'dating_chat_panel_profiles', _profiles({}))

        with pytest.raises(HTTPException) as refusal:
            await dating_chat_import(343, user=None)

        assert refusal.value.status_code == 422
        assert not rhapsody.calls, 'сценарий погнали, не имея анкет'

    async def test_broken_scenario_is_an_answer_not_a_crash(self, rhapsody, monkeypatch):
        """Сценарий встал — 200 со `status: error`.

        Правило то же, что у импорта анкет: сайт мог сменить вёрстку или не
        пустить в панель, и код ответа говорит про наш запрос, а не про то, что
        вышло у него.
        """
        _by_scenario(monkeypatch)
        monkeypatch.setattr(dating_chat_panel, 'dating_chat_panel_profiles', _profiles(PROFILES))
        monkeypatch.setattr(dating_chat_panel, 'dating_scenario_run',
                            _run({'status': 'error', 'step_no': 7, 'error': 'нет таблицы'}))

        answer = (await dating_chat_import(343, user=None))['result']

        assert answer['status'] == 'error' and 'шаге 7' in answer['error']
        assert answer['added'] == 0

    async def test_range_reaches_the_scenario(self, rhapsody, monkeypatch):
        """Диапазон уезжает подстановками в том формате, который принимает панель."""
        seen = {}
        _by_scenario(monkeypatch)
        monkeypatch.setattr(dating_chat_panel, 'dating_chat_panel_profiles', _profiles(PROFILES))
        monkeypatch.setattr(dating_chat_panel, 'dating_scenario_run', _run({'status': 'ok'}, seen))
        monkeypatch.setattr(dating_chat_panel, 'dating_chat_panel_save',
                            _saved({'added': 0, 'same': 0, 'contacts': 0}))

        await dating_chat_import(343, days=7, user=None)

        assert seen['variables']['account_id'] == '0', 'спросили одну анкету вместо всех'
        for key in ('date_from', 'date_to'):
            value = seen['variables'][key]
            assert len(value) == 10 and value[2] == '.' and value[5] == '.', value


class TestImportedAnswerThreshold:
    """⚠ Привезённое отвечается **только свежее** — самая дорогая ошибка здесь.

    Прежде `source = 'panel'` исключался целиком, и это было верно, пока импорт шёл
    кнопкой. Теперь у сайта без сокета панель — единственный источник входящих
    (`dating.md`, §5.9.1), и запрет означал бы «не отвечать вовсе». Вместо запрета
    порог по возрасту.

    ⚠ **Проверяем отбор на живой базе, а не наличие строки в исходнике.** Прежний
    тест искал `"m.source <> 'panel'" in code` — и не заметил, как правило
    сменилось: строка осталась, смысл стал другим. Такой тест хуже, чем никакого:
    он создаёт уверенность, не давая проверки.
    """

    @pytest.fixture
    async def dialogue(self, rhapsody):
        """Одноразовая анкета со своей перепиской.

        ⚠ Своя, а не живая: сторож в `conftest` не даст писать в переписку
        существующих — и правильно, так уже намусорили десятью «привет».

        Фикстура `rhapsody` обязательна: заведение анкеты сверяет сайт у соседа, и
        без заглушки тест пошёл бы к нему по-настоящему.
        """
        from src.uvicorn.routers.dating.dating_profile import (DatingProfileSave,
                                                               dating_profile_create,
                                                               dating_profile_delete)

        answer = await dating_profile_create(
            # Сайт из списка поддельного соседа: заведение сверяет его у соседа, и
            # выдуманного номера тот не знает. ⚠ Берём **стенд**, а не рабочий
            # сайт: анкета сюиты не должна попадать в круги воркера
            # (`test_mail_in.py`, `STAND_SITE`).
            DatingProfileSave(site_id=999_343, login=f'panel-{uuid.uuid4().hex[:8]}',
                              password='x'), user=None)
        profile_id = int(answer['result'])

        yield profile_id

        try:
            await dating_profile_delete(profile_id, user=None)
        except Exception:
            pass

    async def _put(self, profile_id: int, source: str, age_hours: int) -> None:
        """Кладёт входящее заданного возраста и источника."""
        async with mysql_get_db_async() as db:
            await db.execute(
                'INSERT INTO message'
                ' (profile_id, sender_uid, sender_name, text, message_uid,'
                '  direction, is_read, source, created_at)'
                " VALUES (%s, '77', 'Он', 'вопрос', %s, 'in', 1, %s,"
                '         NOW() - INTERVAL %s HOUR)',
                (profile_id, f'probe-{uuid.uuid4().hex[:12]}', source, int(age_hours)))
            await db.connection.commit()

    async def test_fresh_panel_message_is_answered(self, dialogue):
        """Свежее привезённое отвечается: иначе у сайта без сокета не отвечалось бы
        ничего."""
        from src.dating.dating_answer import _waiting_find

        await self._put(dialogue, 'panel', age_hours=2)

        assert len(await _waiting_find(dialogue, 5, [], False)) == 1

    async def test_old_panel_message_is_not_answered(self, dialogue):
        """⚠ Старое привезённое — нет. Импорт за 60 дней остаётся доступным кнопкой,
        и без порога первый же такой прогон дал бы агенту десятки поводов написать
        по старым письмам, живым людям."""
        from src.dating.dating_answer import _waiting_find

        await self._put(dialogue, 'panel', age_hours=48)

        assert await _waiting_find(dialogue, 5, [], False) == []

    async def test_import_does_not_hide_a_fresh_message(self, dialogue):
        """⚠ Привезённая старая реплика не заслоняет свежую.

        Отбор берёт **последнюю** реплику диалога. Пока порядок считался по
        `MAX(id)`, импорт ломал его: он кладёт недельную историю сегодня, то есть с
        наибольшими `id`, и «последней» становилась привезённая старая реплика — а
        свежее неотвеченное сообщение оставалось без ответа.

        Порядок вставки здесь и воспроизводит эту ловушку: сперва свежее, потом
        старое, как оно и происходит при импорте после сокета.
        """
        from src.dating.dating_answer import _waiting_find

        await self._put(dialogue, 'socket', age_hours=2)
        await self._put(dialogue, 'panel', age_hours=50)

        rows = await _waiting_find(dialogue, 5, [], False)

        assert len(rows) == 1, 'свежее сообщение заслонено привезённым старым'

    async def test_socket_message_has_no_threshold(self, dialogue):
        """Порог только для привезённого: поймавшееся сокетом старое сообщение
        осталось без ответа не потому, что оно старое, а потому что мы не ответили —
        и ответить всё ещё надо."""
        from src.dating.dating_answer import _waiting_find

        await self._put(dialogue, 'socket', age_hours=48)

        assert len(await _waiting_find(dialogue, 5, [], False)) == 1

    async def test_imported_rows_are_marked_read(self):
        """`is_read = 1` у привезённых: импорт это догон журнала, а не доставка.

        Пометь мы недельную историю непрочитанной — панель зажглась бы счётчиками
        по всем диалогам разом, и человек не отличил бы новое от привезённого.
        """
        import inspect

        from src.dating import dating_chat_panel

        code = inspect.getsource(dating_chat_panel.dating_chat_panel_save)

        assert "1, 'panel'" in code


def _by_scenario(monkeypatch) -> None:
    """Заставляет импорт идти сценарием, а не ручкой панели.

    ⚠ Чем забирать, решает настройка `admin_url` живого сайта (`dating.md`,
    §5.9.1), и у Ladadate она заполнена — тесты сценария молча ушли бы на ручку и
    попытались постучаться наружу. Путь здесь **условие теста**, а не обстановка:
    сценарный путь остаётся у сайтов без панели, и проверять его надо явно.
    """
    async def _no_panel(site_id):
        return False

    monkeypatch.setattr(dating_chat_panel, '_panel_ready', _no_panel)


def _profiles(rows: dict):
    async def _all(site_id):
        return dict(rows)

    return _all


def _run(result: dict, seen: dict = None):
    async def _go(scenario, site_id, profile_id=0, session_id='', variables=None,
                  secrets=None, keep_session=True):
        if seen is not None:
            seen['variables'] = dict(variables or {})

        return dict(result)

    return _go


def _saved(result: dict):
    async def _save(rows):
        return dict(result)

    return _save
