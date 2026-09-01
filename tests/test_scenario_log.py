"""Запись прогона сценария: что попадает в журнал прогонов.

Юнит-тесты без сети и базы: и прогон, и запись подменяются. Гонять настоящий
нельзя — сорок секунд живого браузера и вход на сайт под анкетой; дождаться
настоящего отказа тоже нельзя, пул вкладок у соседа занят не по расписанию.

Проверяется ровно то, ради чего модуль и заведён: **след остаётся от каждого
прогона**, включая тот, который не начался.

⚠ Прогон даёт **две** записи: `sent` перед отправкой и исход после. Поэтому тесты
смотрят на последнюю (`written[-1]`) — она про итог, — а парность проверяется
отдельно.
"""

import pytest

from src.dating import dating_scenario
from src.dating.dating_scenario import dating_scenario_run
from src.rhapsody.rhapsody_api import RhapsodyError

SCENARIO = {'id': 278, 'name': 'избранные собеседники: список'}
SITE = 343
PROFILE = 849


@pytest.fixture
def written(monkeypatch):
    """Ловит записи вместо базы и подменяет прогон по требованию теста."""
    rows = []

    async def _write(site_id, profile_id, scenario_id, name, status, step_no, error, ms):
        rows.append({'site_id': site_id, 'profile_id': profile_id,
                     'scenario_id': scenario_id, 'name': name, 'status': status,
                     'step_no': step_no, 'error': error, 'ms': ms})

    monkeypatch.setattr(dating_scenario, '_write', _write)

    return rows


def _run(returns=None, fails=''):
    """Подменённый прогон у соседа: отдаёт ответ или бросает отказ связи."""
    async def _call(scenario_id, session_id, variables, secrets, keep_session=True):
        if fails:
            raise RhapsodyError(fails)

        return dict(returns or {'status': 'ok', 'extract': {}})

    return _call


class TestRunIsRecorded:
    """След остаётся от каждого прогона."""

    async def test_success_is_written(self, written, monkeypatch):
        monkeypatch.setattr(dating_scenario, 'rhapsody_api_scenario_run', _run())

        await dating_scenario_run(SCENARIO, SITE, PROFILE)

        assert [row['status'] for row in written] == ['sent', 'ok']
        assert written[-1]['site_id'] == SITE
        assert written[-1]['profile_id'] == PROFILE

    async def test_sent_is_written_before_the_run(self, written, monkeypatch):
        """⚠ Отметка «запрос ушёл» пишется **до** прогона.

        Прогон идёт десятки секунд, а с очередью и минуты. Без этой строки человек,
        открывший «Журналы» в это время, видел бы тишину и не отличил бы «ничего не
        запускалось» от «запущено и ещё работает».
        """
        sent = []

        async def _slow(scenario_id, session_id, variables, secrets, keep_session=True):
            # К этому моменту отметка обязана уже лежать в журнале.
            sent.append([row['status'] for row in written])

            return {'status': 'ok'}

        monkeypatch.setattr(dating_scenario, 'rhapsody_api_scenario_run', _slow)

        await dating_scenario_run(SCENARIO, SITE, PROFILE)

        assert sent == [['sent']], 'отметка об отправке не попала в журнал до прогона'

    async def test_sent_has_no_duration(self, written, monkeypatch):
        """У отметки об отправке длительности нет и быть не может: она пишется
        тогда, когда мерить ещё нечего."""
        monkeypatch.setattr(dating_scenario, 'rhapsody_api_scenario_run', _run())

        await dating_scenario_run(SCENARIO, SITE, PROFILE)

        assert written[0]['ms'] == 0
        assert written[0]['step_no'] == 0

    async def test_failed_scenario_keeps_the_step(self, written, monkeypatch):
        """Номер шага отвечает на «где именно встало», не открывая сценарий у
        соседа. Ради него журнал наполовину и заводится."""
        monkeypatch.setattr(dating_scenario, 'rhapsody_api_scenario_run',
                            _run({'status': 'error', 'step_no': 7, 'error': 'нет кнопки'}))

        await dating_scenario_run(SCENARIO, SITE, PROFILE)

        assert written[-1]['status'] == 'error'
        assert written[-1]['step_no'] == 7
        assert 'нет кнопки' in written[-1]['error']

    async def test_unreachable_neighbour_is_written_too(self, written, monkeypatch):
        """⚠ Прогон, который не начался, — тоже прогон.

        Сосед не ответил, вкладка не открывалась, сайт ничего не видел. Не запиши
        мы это — в журнале осталась бы дыра ровно там, где человек ищет причину
        молчания панели.
        """
        monkeypatch.setattr(dating_scenario, 'rhapsody_api_scenario_run',
                            _run(fails='браузер соседа занят'))

        with pytest.raises(RhapsodyError):
            await dating_scenario_run(SCENARIO, SITE, PROFILE)

        assert [row['status'] for row in written] == ['sent', 'error']
        assert written[-1]['step_no'] == 0, 'сценарий не начинался — шага у него нет'
        assert 'браузер соседа занят' in written[-1]['error']

    async def test_refusal_is_passed_on(self, written, monkeypatch):
        """Записали и пробросили: что делать с отказом — 502 или прежний снимок из
        кэша — решает вызывающий, а не журнал."""
        monkeypatch.setattr(dating_scenario, 'rhapsody_api_scenario_run',
                            _run(fails='сосед не отвечает'))

        with pytest.raises(RhapsodyError, match='сосед не отвечает'):
            await dating_scenario_run(SCENARIO, SITE, PROFILE)

    async def test_answer_is_returned_as_is(self, written, monkeypatch):
        """Ответ соседа проходит насквозь: журнал ничего в нём не меняет, иначе
        разбор карточек получал бы не то, что прислал сайт."""
        monkeypatch.setattr(dating_scenario, 'rhapsody_api_scenario_run',
                            _run({'status': 'ok', 'extract': {'избранные': [1, 2]}}))

        answer = await dating_scenario_run(SCENARIO, SITE, PROFILE)

        assert answer['extract'] == {'избранные': [1, 2]}


class TestRunDetails:
    """Что именно записывается."""

    async def test_name_is_kept_beside_the_number(self, written, monkeypatch):
        """Имя сценария хранится рядом с номером: у соседа его могут переименовать
        или удалить, и тогда номер перестанет что-либо значить."""
        monkeypatch.setattr(dating_scenario, 'rhapsody_api_scenario_run', _run())

        await dating_scenario_run(SCENARIO, SITE, PROFILE)

        assert written[-1]['name'] == SCENARIO['name']
        assert written[-1]['scenario_id'] == SCENARIO['id']

    async def test_run_without_profile(self, written, monkeypatch):
        """Импорт и выгрузка идут по сайту целиком — анкеты у них нет.

        Приписать такой прогон одной из привезённых анкет было бы враньём, а
        потерять его — потерять единственный след импорта.
        """
        monkeypatch.setattr(dating_scenario, 'rhapsody_api_scenario_run', _run())

        await dating_scenario_run({'id': 5, 'name': 'импорт анкет'}, SITE)

        assert written[-1]['profile_id'] == 0
        assert written[-1]['site_id'] == SITE

    async def test_duration_is_measured(self, written, monkeypatch):
        """Длительность — вторая половина смысла: «вход стал идти вдвое дольше»
        видно только рядом лежащими замерами."""
        monkeypatch.setattr(dating_scenario, 'rhapsody_api_scenario_run', _run())

        await dating_scenario_run(SCENARIO, SITE, PROFILE)

        assert written[-1]['ms'] >= 0

    async def test_long_name_is_cut(self, written, monkeypatch):
        """Длиннее колонки — режем здесь: иначе длинное имя роняло бы вставку, и
        прогон пропал бы из журнала целиком."""
        monkeypatch.setattr(dating_scenario, 'rhapsody_api_scenario_run', _run())

        await dating_scenario_run({'id': 1, 'name': 'и' * 500}, SITE)

        assert len(written[-1]['name']) == dating_scenario.DATING_SCENARIO_NAME_MAX
