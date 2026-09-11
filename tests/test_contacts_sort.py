"""Собеседницы в карточке анкеты: порядок и страницы.

Проверяется на посеянных данных (`scripts/dating_seed.py`): порядок и потолок
выборки видны только там, где собеседниц сотни. На трёх диалогах любой порядок
выглядит правильным, а страниц не бывает вовсе.

Без посева тесты **пропускаются**, а не падают: сюиту гоняют и по проду, где
тестовых анкет нет и быть не должно. Пропуск говорит, что запустить.
"""

import math

import pytest

pytestmark = pytest.mark.chat
# Сайт посеянных анкет — тот же, что у остальной сюиты.
SITE_ID = 19

# Логин анкеты с сотнями собеседниц. Задан скриптом посева; разойдись эти строки,
# тесты молча начали бы пропускаться.
BIG_LOGIN = 'seed-000-big@example.com'

# Сколько собеседниц на странице. Знает и ручка (`DATING_PROFILE_CONTACTS_PAGE`), но
# сюита ходит по HTTP и в код не заглядывает: число здесь — часть контракта
# страницы, и его расхождение с ручкой поймает `test_page_holds_fifty`.
PAGE = 50


@pytest.fixture
async def big_profile(auth_client):
    """Посеянная анкета с сотнями собеседниц."""
    response = await auth_client.get(f'/api/dating/profile?site_id={SITE_ID}')
    assert response.status_code == 200, response.text[:200]

    row = next((r for r in response.json()['result'] if r['login'] == BIG_LOGIN), None)
    if not row:
        pytest.skip('нет посеянных данных: docker exec dating_uvicorn '
                    'python scripts/dating_seed.py')

    return row


async def contacts_page(client, profile_id: int, page: int) -> dict:
    """Одна страница собеседниц."""
    response = await client.get(f'/api/dating/profile/{profile_id}/card?page={page}')
    assert response.status_code == 200, response.text[:200]

    return response.json()['result']


async def contacts_all(client, profile_id: int) -> list:
    """Все собеседницы, страница за страницей, в том порядке, в каком их отдают.

    Нужен именно сквозной список: свойства порядка («неотвеченные раньше
    отвеченных») проверяются между страницами, а не внутри одной.
    """
    first = await contacts_page(client, profile_id, 1)
    rows = list(first['contacts'])
    for page in range(2, int(first['pages']) + 1):
        rows += (await contacts_page(client, profile_id, page))['contacts']

    return rows


class TestContactsPaging:

    async def test_page_holds_fifty(self, auth_client, big_profile):
        """Первая страница — пятьдесят собеседниц из общего числа, и число сходится
        со счётчиком в списке анкет: врать в двух местах по-разному нельзя."""
        result = await contacts_page(auth_client, big_profile['id'], 1)

        assert len(result['contacts']) == PAGE
        assert result['contacts_total'] == big_profile['contacts']
        assert result['pages'] == math.ceil(result['contacts_total'] / PAGE)
        assert result['page'] == 1

    async def test_second_page_holds_others(self, auth_client, big_profile):
        """На второй странице другие собеседницы, а не те же самые."""
        first = await contacts_page(auth_client, big_profile['id'], 1)
        second = await contacts_page(auth_client, big_profile['id'], 2)

        assert second['page'] == 2
        assert not {c['uid'] for c in first['contacts']} & {c['uid'] for c in second['contacts']}

    async def test_every_contact_shown_once(self, auth_client, big_profile):
        """Листание проходит по всем собеседницам и ни одной не показывает дважды.

        Ловит промах в смещении: `OFFSET` на страницу больше или меньше теряет по
        собеседнице на каждом переходе, и заметить это по одной странице нельзя.
        """
        rows = await contacts_all(auth_client, big_profile['id'])
        uids = [c['uid'] for c in rows]

        assert len(uids) == big_profile['contacts']
        assert len(set(uids)) == len(uids)

    async def test_page_beyond_end_clamped(self, auth_client, big_profile):
        """Страница за концом списка отдаёт последнюю и говорит, какая она.

        Номер приходит со страницы, которая рисовалась до того, как собеседниц
        стало меньше. Пустой ответ на живой анкете выглядел бы как потеря данных.
        """
        result = await contacts_page(auth_client, big_profile['id'], 999)

        assert result['page'] == result['pages']
        assert len(result['contacts']) == PAGE

    async def test_page_below_one_clamped(self, auth_client, big_profile):
        """Нулевая и отрицательная страница — первая, а не отрицательный `OFFSET`."""
        for page in (0, -5):
            result = await contacts_page(auth_client, big_profile['id'], page)

            assert result['page'] == 1

    async def test_messages_read_for_shown_only(self, auth_client, big_profile):
        """У показанных собеседниц есть переписка, и не больше десяти реплик.

        Реплики читаются на показанных — на всех четырёх сотнях это было бы
        четыреста запросов на одно открытие карточки.
        """
        result = await contacts_page(auth_client, big_profile['id'], 3)

        for contact in result['contacts']:
            messages = contact['messages_last']
            assert 0 < len(messages) <= 10
            # Порядок разговора: сверху старое, снизу свежее — как в самом чате.
            assert [m['created_at'] for m in messages] == sorted(m['created_at'] for m in messages)


class TestContactsSort:

    async def test_unanswered_before_answered(self, auth_client, big_profile):
        """Сначала все, кому не ответили, и только потом отвеченные.

        Свойство сквозное: неотвеченная собеседница не имеет права оказаться ниже
        отвеченной ни внутри страницы, ни через страницу.
        """
        rows = await contacts_all(auth_client, big_profile['id'])
        waiting = [index for index, c in enumerate(rows) if c['needs_answer']]

        assert waiting, 'в посеянных данных нет ни одной неотвеченной — проверять нечего'
        assert max(waiting) == len(waiting) - 1, (
            'отвеченная собеседница оказалась выше неотвеченной: '
            f'неотвеченных {len(waiting)}, последняя на месте {max(waiting)}')

    async def test_fresh_first_inside_groups(self, auth_client, big_profile):
        """Внутри группы — по свежести последней реплики, свежие сверху."""
        rows = await contacts_all(auth_client, big_profile['id'])

        for waiting in (True, False):
            group = [c['last_id'] for c in rows if c['needs_answer'] is waiting]

            assert group == sorted(group, reverse=True), (
                f'порядок по свежести сбит в группе needs_answer={waiting}')

    async def test_old_unanswered_on_first_page(self, auth_client, big_profile):
        """Давнее неотвеченное сообщение — на первой странице, а не за потолком.

        Главная беда, ради которой всё затевалось. Пока `needs_answer` считался в
        Python **после** `ORDER BY last_id DESC LIMIT 50`, наверх поднималось лишь
        то неотвеченное, что попало в пятьдесят самых свежих диалогов, — а
        собеседница, которая написала три месяца назад и ответа так и не дождалась,
        не показывалась вовсе.

        Проверяем именно это: на первой странице стоит неотвеченная собеседница,
        которая старше отвеченной, до которой листать ещё несколько страниц. При
        старом порядке такое невозможно — первая страница была ровно пятьдесят
        самых свежих диалогов, и ничего свежее её за ней не оставалось.
        """
        first = await contacts_page(auth_client, big_profile['id'], 1)
        rows = await contacts_all(auth_client, big_profile['id'])

        shown = {c['uid'] for c in first['contacts']}
        answered_later = [c for c in rows if c['uid'] not in shown and not c['needs_answer']]
        assert answered_later, 'отвеченных за первой страницей нет — проверять нечего'

        freshest_answered = max(c['last_id'] for c in answered_later)
        old_waiting = [c for c in first['contacts']
                       if c['needs_answer'] and c['last_id'] < freshest_answered]

        assert old_waiting, (
            'на первой странице нет ни одной неотвеченной собеседницы старше '
            'отвеченных с дальних страниц — потолок выборки снова прячет работу')

    async def test_needs_answer_matches_last_message(self, auth_client, big_profile):
        """Признак совпадает с последней репликой диалога — и считает пометку.

        Ждёт ответа тот, чья последняя реплика входящая **и** не помечена
        отвеченной. Пометка нужна отдельно от направления: агент имеет право
        промолчать, и тогда диалог закрыт, хотя последнее слово осталось за
        собеседником. Считай признак по одному направлению — такие диалоги вечно
        висели бы в работе.
        """
        rows = []
        for page in (1, 3, 8):
            rows += (await contacts_page(auth_client, big_profile['id'], page))['contacts']

        silent = 0
        for contact in rows:
            last = contact['messages_last'][-1]
            waiting = last['direction'] == 'in' and not last['is_answered']

            assert contact['needs_answer'] is waiting, contact['uid']
            if last['direction'] == 'in' and last['is_answered']:
                silent += 1

        assert silent, ('в выборке нет ни одного диалога, закрытого молчанием агента — '
                        'подмену пометки направлением такая проверка не поймает')

    async def test_password_never_leaves(self, auth_client, big_profile):
        """В карточке пароля анкеты нет — ни в профиле, ни рядом с ним."""
        response = await auth_client.get(f"/api/dating/profile/{big_profile['id']}/card")

        assert response.status_code == 200
        assert 'seed-password' not in response.text
