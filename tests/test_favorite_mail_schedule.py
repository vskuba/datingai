"""Расписание писем избранным: когда круг их сочиняет.

⚠ **Оно про «когда сочиняем», а не «когда отправляем».** Часы отправки — общие
на всю почту сайта (`mailing_schedule`, `test_mailing_schedule.py`), и вопроса
здесь два, потому что разное и дорого: сочинение стоит вызовов LLM и наружу не
пишет, отправка не стоит ничего и уходит живому человеку.

⚠⚠ **Своей галочки у этих смен нет.** Работает занятие или нет, говорит
`favorite_mail_make_on` — та же, что и раньше. У рассылки эту роль играет
`mailing_schedule_on`, у инвайтов `invite_schedule_on`: имя другое, смысл тот
же, и вторая галочка рядом отвечала бы на один вопрос дважды.

Хранение и правка идут по HTTP, в живую панель с живой базой: проверять хранение
с поддельной базой значило бы проверять подделку.

⚠ **Номера сайтов выдуманные**: своей таблицы сайтов здесь нет, `site_id` —
просто ключ в чужую базу, и живое расписание задеть невозможно.
"""

import pytest

pytestmark = pytest.mark.mail

SITE = 999_777
SITE_OTHER = 999_778

TWO = [{'from': '09:00', 'to': '12:00'}, {'from': '18:00', 'to': '21:30'}]


@pytest.fixture
async def kept(auth_client):
    """Возвращает смены обоих номеров такими, какими они были.

    ⚠ И галочку генерации: оставленная поднятой, она заставила бы круг сочинять
    письма по расписанию, которого никто не заводил, — а каждое письмо это вызов
    LLM.
    """
    before = {site: await _got(auth_client, site) for site in (SITE, SITE_OTHER)}

    yield

    for site, was in before.items():
        await auth_client.put(f'/api/dating/site/{site}/favorite-mail-schedule',
                              json={'schedule': was['schedule']})
        await auth_client.put(f'/api/dating/site/{site}/favorite-mail-make-on',
                              json={'favorite_mail_make_on':
                                    was['favorite_mail_make_on']})


class TestTheScheduleIsKept:
    """Завести, прочитать, убрать."""

    async def test_saved_schedule_comes_back(self, auth_client, kept):
        await auth_client.put(f'/api/dating/site/{SITE}/favorite-mail-schedule',
                              json={'schedule': TWO})

        assert (await _got(auth_client, SITE))['schedule'] == TWO

    async def test_save_normalises_and_sorts(self, auth_client, kept):
        """Время приходит как придётся, а хранится приведённым и по порядку.

        Разбирает его тот же `schedule.py`, что и расписание анкеты: правил
        здесь своих нет и быть не должно.
        """
        await auth_client.put(f'/api/dating/site/{SITE}/favorite-mail-schedule',
                              json={'schedule': [{'from': '18:00', 'to': '21:30'},
                                                 {'from': '9:0', 'to': '12:00'}]})

        assert (await _got(auth_client, SITE))['schedule'] == TWO

    async def test_schedule_can_be_cleared(self, auth_client, kept):
        """⚠ Пустое — законное состояние, и значит оно «не сочиняем».

        Не «сочиняем всегда»: правило одно на проект. Второе прочтение означало
        бы, что человек, стерев все смены, получает круглосуточную работу — то
        есть ровно обратное тому, что он сделал.
        """
        await auth_client.put(f'/api/dating/site/{SITE}/favorite-mail-schedule',
                              json={'schedule': TWO})
        await auth_client.put(f'/api/dating/site/{SITE}/favorite-mail-schedule',
                              json={'schedule': []})

        assert (await _got(auth_client, SITE))['schedule'] == []

    async def test_sites_keep_their_own(self, auth_client, kept):
        """Смены одного сайта не видны у другого: ключ — его номер."""
        await auth_client.put(f'/api/dating/site/{SITE}/favorite-mail-schedule',
                              json={'schedule': TWO})

        assert (await _got(auth_client, SITE_OTHER))['schedule'] == []

    async def test_unknown_site_is_empty_not_404(self, auth_client):
        """Сайт, которому ничего не настраивали, — законное состояние."""
        answer = await auth_client.get('/api/dating/site/999999/favorite-mail')

        assert answer.status_code == 200
        row = answer.json()['result']

        assert row['schedule'] == []
        assert row['favorite_mail_make_on'] is False


class TestTheScheduleIsChecked:
    """⚠⚠ Проверка та же, что у рассылки и инвайтов, и ручка ею пользуется.

    Это не формальность: у расписания шаблонов проверки **не было** — код звал
    только разбор, — и негодные смены оно принимало молча, сохраняя разобранную
    половину. Нашлось это, когда три одинаковые записи свели к одной.
    """

    async def test_overlap_is_refused(self, auth_client, kept):
        """⚠ Смены внахлёст отклоняются, а не правятся молча."""
        answer = await auth_client.put(
            f'/api/dating/site/{SITE}/favorite-mail-schedule',
            json={'schedule': [{'from': '10:00', 'to': '15:00'},
                               {'from': '14:00', 'to': '20:00'}]})

        assert answer.status_code == 422
        assert 'налезают' in answer.json()['detail']

    async def test_a_refused_schedule_changes_nothing(self, auth_client, kept):
        """Отказ обязан быть полным: сохранись половина, человек увидел бы
        расписание, которого не задавал, под сообщением об ошибке."""
        await auth_client.put(f'/api/dating/site/{SITE}/favorite-mail-schedule',
                              json={'schedule': TWO})
        await auth_client.put(
            f'/api/dating/site/{SITE}/favorite-mail-schedule',
            json={'schedule': [{'from': '10:00', 'to': '15:00'},
                               {'from': '14:00', 'to': '20:00'}]})

        assert (await _got(auth_client, SITE))['schedule'] == TWO


class TestOneDoorForEverySchedule:
    """⚠⚠ Все расписания сайта пишутся **одной** дорогой.

    Их четыре — рассылка, инвайты, шаблоны, письма избранным, — и раньше каждое
    имело свой `INSERT … ON DUPLICATE KEY UPDATE` с собственной проверкой (или,
    как у шаблонов, без неё). Шестнадцать таких записей на файл означали
    шестнадцать мест, куда правка доезжает не целиком.

    Сторож проверяет не красоту, а то самое свойство: имя столбца берётся из
    белого списка, и мимо него записи нет.
    """

    def test_every_schedule_column_is_listed(self):
        from src.uvicorn.routers.dating.site_switch import SITE_SCHEDULE_COLUMNS

        for column in ('mailing_schedule', 'invite_schedule',
                       'template_schedule', 'favorite_mail_schedule'):
            assert column in SITE_SCHEDULE_COLUMNS, \
                f'{column} пишется мимо общей дороги'

    def test_a_column_outside_the_list_is_refused(self):
        """⚠ Имя столбца уезжает в SQL текстом — параметром оно не бывает.

        Белый список здесь не формальность, а то единственное, что отделяет
        подстановку имени от дыры.
        """
        import asyncio

        from src.uvicorn.routers.dating.site_switch import site_schedule_put

        with pytest.raises(AssertionError):
            asyncio.get_event_loop_policy().new_event_loop().run_until_complete(
                site_schedule_put(SITE, 'profile', []))

    def test_the_handlers_do_not_write_by_hand(self):
        """Ни одна ручка не пишет в `site` своим запросом — только помощниками."""
        import inspect

        from src.uvicorn.routers.dating import site_switch

        code = inspect.getsource(site_switch)
        # Две записи — в самих помощниках; всё, что сверх, это вернувшаяся копия.
        assert code.count('ON DUPLICATE KEY UPDATE') == 3, \
            'в файле снова пишут в `site` мимо общих помощников'


async def _got(auth_client, site_id: int) -> dict:
    """Настройки писем избранным сайта: смены, галочки и пределы — одним ответом.

    ⚠ Отдельной ручки под смены нет намеренно: подвкладка рисует их и выключатели
    одной полосой, и два запроса дали бы два момента времени — человек увидел бы
    зелёную смену под снятой галочкой.
    """
    answer = await auth_client.get(f'/api/dating/site/{site_id}/favorite-mail')

    return answer.json()['result']
