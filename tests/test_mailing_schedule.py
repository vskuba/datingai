"""Расписание массовой рассылки: когда с сайта уходят письма.

⚠ **Оно про сайт, а не про анкету** — и это главное, что тут проверяется.
Расписание анкеты (`dating_profile.schedule`, `test_schedule.py`) отвечает на
вопрос «когда ей быть в сети»; здесь вопрос другой, и ответ на него один на все
учётки сайта.

Хранение и правка идут по HTTP, в живую панель с живой базой: проверять хранение
с поддельной базой значило бы проверять подделку.

⚠ **Номера сайтов выдуманные**, как и в `test_dating_site.py`: своей таблицы
сайтов здесь нет, `site_id` — просто ключ в чужую базу. Живое расписание задеть
невозможно в принципе, а зависеть от поднятого соседа ради ключа незачем.

Разбор и проверка смен — общие с расписанием анкеты (`dating_schedule.py`), и
второй раз они здесь не проверяются: там своя сюита. Здесь — что ручка ими
пользуется, что хранится это отдельно от настроек и что правит только админ.
"""

import pytest

pytestmark = pytest.mark.mail
# Номера, под которыми сюита пишет своё. Те же, что у настроек сайта: сайта с
# такими номерами у соседа нет.
SITE = 999_777
SITE_OTHER = 999_778

ДВЕ = [{'from': '09:00', 'to': '12:00'}, {'from': '18:00', 'to': '21:30'}]


@pytest.fixture
async def kept(auth_client):
    """Возвращает расписание обоих тестовых номеров таким, каким оно было.

    Что завела, то и убрала — правило одно на всю сюиту. Флаг исполнения тоже:
    оставленный включённым, он однажды заставил бы круг рассылки гнать письма по
    расписанию, которого никто не заводил.
    """
    before = {site: await _got(auth_client, site) for site in (SITE, SITE_OTHER)}

    yield

    for site, was in before.items():
        await auth_client.put(f'/api/dating/site/{site}/mailing-schedule',
                              json={'schedule': was['schedule']})
        await auth_client.put(f'/api/dating/site/{site}/mailing-schedule-on',
                              json={'mailing_schedule_on': was['mailing_schedule_on']})


class TestMailingSchedule:
    """Завести, прочитать, убрать."""

    async def test_saved_schedule_comes_back(self, auth_client, kept):
        await auth_client.put(f'/api/dating/site/{SITE}/mailing-schedule',
                              json={'schedule': ДВЕ})

        assert (await _got(auth_client, SITE))['schedule'] == ДВЕ

    async def test_save_normalises_and_sorts(self, auth_client, kept):
        """Время приходит как придётся, а хранится приведённым и по порядку.

        Разбирает его тот же `dating_schedule.py`, что и расписание анкеты: одна
        забытая ведущая ноль ломала бы сравнение строк, на котором держится
        порядок.
        """
        await auth_client.put(f'/api/dating/site/{SITE}/mailing-schedule',
                              json={'schedule': [{'from': '18:00', 'to': '21:30'},
                                                 {'from': '9:0', 'to': '12:00'}]})

        assert (await _got(auth_client, SITE))['schedule'] == ДВЕ

    async def test_schedule_can_be_cleared(self, auth_client, kept):
        """Пустое расписание — законное состояние («шлём руками»), а не ошибка."""
        await auth_client.put(f'/api/dating/site/{SITE}/mailing-schedule',
                              json={'schedule': ДВЕ})
        await auth_client.put(f'/api/dating/site/{SITE}/mailing-schedule',
                              json={'schedule': []})

        assert (await _got(auth_client, SITE))['schedule'] == []

    async def test_unknown_site_is_empty_not_404(self, auth_client):
        """Сайт, которому рассылку не настраивали, — законное состояние: строка
        появляется при первом сохранении."""
        answer = await auth_client.get('/api/dating/site/999999/mailing-schedule')

        assert answer.status_code == 200
        # ⚠ Поля сверяем по одному, а не словарь целиком: ответ прирастает
        # (`mailing_send_on` добавился к расписанию), и равенство ломалось бы на
        # каждом новом поле, ничего не проверяя по существу.
        row = answer.json()['result']

        assert row['site_id'] == 999999
        assert row['schedule'] == []
        assert row['mailing_schedule_on'] is False

    async def test_sites_keep_their_own(self, auth_client, kept):
        """Расписание одного сайта не видно у другого: ключ — его номер."""
        await auth_client.put(f'/api/dating/site/{SITE}/mailing-schedule',
                              json={'schedule': ДВЕ})

        assert (await _got(auth_client, SITE_OTHER))['schedule'] == []


class TestMailingScheduleIsChecked:
    """Проверка та же, что у расписания анкеты, и ручка ею пользуется."""

    async def test_overlap_is_refused(self, auth_client, kept):
        """⚠ Смены внахлёст отклоняются, а не правятся молча — иначе рассылка
        ушла бы дважды в один час."""
        answer = await auth_client.put(
            f'/api/dating/site/{SITE}/mailing-schedule',
            json={'schedule': [{'from': '10:00', 'to': '15:00'},
                               {'from': '14:00', 'to': '20:00'}]})

        assert answer.status_code == 422
        assert 'налезают' in answer.json()['detail']

    async def test_backwards_is_refused(self, auth_client, kept):
        answer = await auth_client.put(
            f'/api/dating/site/{SITE}/mailing-schedule',
            json={'schedule': [{'from': '15:00', 'to': '10:00'}]})

        assert answer.status_code == 422

    async def test_too_many_is_refused(self, auth_client, kept):
        """Предел смен — общий с анкетой, и он смысловой: больше четырёх означало
        бы «шлём почти всегда»."""
        answer = await auth_client.put(
            f'/api/dating/site/{SITE}/mailing-schedule',
            json={'schedule': [{'from': f'{h:02d}:00', 'to': f'{h + 1:02d}:00'}
                               for h in (1, 3, 5, 7, 9)]})

        assert answer.status_code == 422
        assert 'не больше' in answer.json()['detail']

    async def test_refused_schedule_is_not_stored(self, auth_client, kept):
        """Отказ не должен затирать то, что лежало: человек правит ползунком, и
        промежуточное состояние не повод терять сохранённое."""
        await auth_client.put(f'/api/dating/site/{SITE}/mailing-schedule',
                              json={'schedule': ДВЕ})
        await auth_client.put(f'/api/dating/site/{SITE}/mailing-schedule',
                              json={'schedule': [{'from': '10:00', 'to': '15:00'},
                                                 {'from': '14:00', 'to': '20:00'}]})

        assert (await _got(auth_client, SITE))['schedule'] == ДВЕ


class TestMailingScheduleOn:
    """Выключатель — отдельно от самих смен."""

    async def test_flag_is_saved_apart_from_shifts(self, auth_client, kept):
        """⚠ Отдельная ручка, и это не формальность: смены правят ползунком, и
        промежуточные состояния законны. Исполняй мы «по факту наличия», рассылка
        уходила бы на каждое движение ручки."""
        await auth_client.put(f'/api/dating/site/{SITE}/mailing-schedule-on',
                              json={'mailing_schedule_on': True})
        await auth_client.put(f'/api/dating/site/{SITE}/mailing-schedule',
                              json={'schedule': ДВЕ})

        got = await _got(auth_client, SITE)

        assert got['mailing_schedule_on'] is True, 'смены сбросили флаг'
        assert got['schedule'] == ДВЕ

    async def test_shifts_survive_the_flag(self, auth_client, kept):
        await auth_client.put(f'/api/dating/site/{SITE}/mailing-schedule',
                              json={'schedule': ДВЕ})
        await auth_client.put(f'/api/dating/site/{SITE}/mailing-schedule-on',
                              json={'mailing_schedule_on': True})

        assert (await _got(auth_client, SITE))['schedule'] == ДВЕ, 'флаг стёр смены'


class TestMailingScheduleAndSettings:
    """⚠ Самое важное: расписание живёт **не в `settings`**.

    Настройки сайта переписываются целиком — страница «Сайты» шлёт то, что на ней,
    и ключа `mailing_schedule` она не знает. Лежи расписание там, первое же
    сохранение любой соседней настройки стёрло бы его молча.
    """

    async def test_saving_settings_keeps_the_schedule(self, auth_client, kept):
        await auth_client.put(f'/api/dating/site/{SITE}/mailing-schedule',
                              json={'schedule': ДВЕ})
        await auth_client.put(f'/api/dating/site/{SITE}/mailing-schedule-on',
                              json={'mailing_schedule_on': True})

        before = (await auth_client.get(
            f'/api/dating/site/{SITE}')).json()['result']['settings']
        try:
            await auth_client.put(f'/api/dating/site/{SITE}',
                                  json={'settings': {'тест_ключ': '30'}})
            got = await _got(auth_client, SITE)
        finally:
            await auth_client.put(f'/api/dating/site/{SITE}',
                                  json={'settings': before})

        assert got['schedule'] == ДВЕ, 'сохранение настроек стёрло расписание'
        assert got['mailing_schedule_on'] is True, 'сохранение настроек сбросило флаг'

    async def test_schedule_does_not_leak_into_settings(self, auth_client, kept):
        """И обратно: расписание не должно проступать в наборе настроек — там его
        никто не ждёт, а страница «Сайты» показала бы его полем ввода."""
        await auth_client.put(f'/api/dating/site/{SITE}/mailing-schedule',
                              json={'schedule': ДВЕ})

        settings = (await auth_client.get(
            f'/api/dating/site/{SITE}')).json()['result']['settings']

        assert 'mailing_schedule' not in settings
        assert 'mailing_schedule_on' not in settings


class TestMailingScheduleRights:
    """Правка — админская: рассылка уходит от всех анкет сайта разом."""

    async def test_operator_can_read(self, dating_client):
        """Чтение открыто всем вошедшим: оператор работает на этой вкладке, и
        прятать от него «когда уходит рассылка» значило бы звать админа на каждый
        вопрос."""
        answer = await dating_client.get(f'/api/dating/site/{SITE}/mailing-schedule')

        assert answer.status_code == 200

    async def test_operator_cannot_write(self, dating_client):
        answer = await dating_client.put(f'/api/dating/site/{SITE}/mailing-schedule',
                                         json={'schedule': []})

        assert answer.status_code == 403

    async def test_operator_cannot_switch(self, dating_client):
        answer = await dating_client.put(
            f'/api/dating/site/{SITE}/mailing-schedule-on',
            json={'mailing_schedule_on': True})

        assert answer.status_code == 403

    async def test_closed_without_login(self, client):
        answer = await client.get(f'/api/dating/site/{SITE}/mailing-schedule')

        assert answer.status_code in (401, 307)


async def _got(auth_client, site_id: int) -> dict:
    answer = (await auth_client.get(
        f'/api/dating/site/{site_id}/mailing-schedule')).json()['result']

    return {'schedule': answer['schedule'],
            'mailing_schedule_on': answer['mailing_schedule_on']}
