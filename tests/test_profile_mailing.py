"""⚠ Рассылка писем: флаг есть, исполнителя нет.

Третий выключатель анкеты, и он про своё. `enabled` — «ведём ли анкету вовсе»,
`auto_answer` — «отвечает ли за неё агент в чате», `mailing_on` — «шлём ли от неё
рассылку писем». Случаи не совпадают: анкета отвечает в чате, но не рассылает;
анкету ведут руками, а рассылка стоит.

⚠ **Круга рассылки пока не существует.** Флаг только хранит решение человека —
исполнителя напишет отдельная задача. Поэтому здесь проверяется хранение и
независимость от соседних флагов, а не поведение: сторожить отсутствие поведения
нечем.

База настоящая, сосед поддельный. Ручки зовутся в процессе, а не по HTTP:
заведение анкеты сверяет сайт у соседа, и по HTTP это дало бы два запроса к
RhapsodyAi на каждую тестовую учётку — то есть сюиту, падающую от его перезапуска.
То же правило, что в `test_profile_crud.py` и `test_profile_enabled.py`.
"""

import uuid

import pytest
from fastapi import HTTPException

from src.uvicorn.routers.dating.dating_profile import (DatingProfileSave,
                                                       dating_profile_create,
                                                       dating_profile_delete,
                                                       dating_profile_list,
                                                       dating_profile_row)
from src.uvicorn.routers.dating.dating_profile_action import (
    DatingProfileAuto, DatingProfileEnabled, DatingProfileMailing,
    dating_profile_action_auto, dating_profile_action_enabled,
    dating_profile_action_mailing)
from src.uvicorn.routers.dating.dating_profile_view import dating_profile_view_card

pytestmark = pytest.mark.mail
SITE = 343


@pytest.fixture
async def profile(rhapsody):
    """Одноразовая анкета в живой базе.

    ⚠ Чужую трогать нельзя: щёлкая её флагами, сюита меняла бы работу настоящей
    учётки — и человек искал бы поломку там, где её нет.
    """
    answer = await dating_profile_create(
        DatingProfileSave(site_id=SITE, login=f'mailing-{uuid.uuid4().hex[:10]}',
                          password='secret', note='тест рассылки'),
        user=None)
    profile_id = int(answer['result'])

    yield profile_id

    try:
        await dating_profile_delete(profile_id, user=None)
    except HTTPException:
        pass


class TestMailingEndpoint:
    """Ручка рассылки: хранение и независимость от соседних флагов."""

    async def test_new_profile_has_mailing_off(self, profile):
        """⚠ Умолчание — «не рассылаем», в отличие от `enabled` («ведём»).

        Показать включённой галочку у функции, которой нет, значит соврать:
        человек решит, что рассылка идёт.
        """
        assert await _mailing(profile) is False

    async def test_toggle_is_stored(self, profile):
        await _set(profile, True)

        assert await _mailing(profile) is True

        await _set(profile, False)

        assert await _mailing(profile) is False

    async def test_mailing_is_independent(self, profile):
        """⚠ Три флага про разное, и ни один не задевает соседей.

        Состояния «ведём руками, рассылка идёт» и «отвечаем в чате, не рассылаем»
        законны. Задень один флаг другим — и они стали бы недостижимы.
        """
        await dating_profile_action_auto(
            profile, DatingProfileAuto(auto_answer=False), request=None, user=None)
        await _set(profile, True)
        await dating_profile_action_enabled(
            profile, DatingProfileEnabled(enabled=False), user=None)

        row = await dating_profile_row(profile)

        assert bool(row['mailing_on']) is True, 'выключение анкеты сбросило рассылку'
        assert bool(row['auto_answer']) is False, 'рассылка вернула автоответ'
        assert bool(row['enabled']) is False

    async def test_mailing_reaches_the_card(self, rhapsody, profile):
        """Галочку рисует карточка — значит флаг должен в ней быть.

        ⚠ Без него после перезагрузки страницы галочка показывала бы снятую там,
        где стоит: человек поставил бы её второй раз и не понял, почему она не
        держится.
        """
        await _set(profile, True)
        card = (await dating_profile_view_card(profile, user=None))['result']

        assert card['mailing_on'] is True

    async def test_mailing_reaches_the_list(self, rhapsody, profile):
        """Список анкет отдаёт флаг — по нему идёт отбор вкладки «Письма рассылка».

        ⚠ Без него отбор делать нечем, и вкладка была бы пуста всегда — при любом
        числе анкет с включённой рассылкой. Зеркало `test_flag_reaches_the_list`
        из `test_profile_enabled.py`: тот же урок, тот же способ проверки.
        """
        await _set(profile, True)
        rows = (await dating_profile_list(site_id=SITE, user=None))['result']
        found = next(row for row in rows if int(row['id']) == profile)

        assert found['mailing_on'] is True

    async def test_mailing_reaches_the_list_over_http(self, rhapsody, profile, auth_client):
        """Тот же список, но по-настоящему по HTTP — где поле и терялось.

        Соседний тест зовёт `dating_profile_list` в процессе и сериализацию
        FastAPI обходит стороной: что вернула функция, то и получил тест. Здесь
        ответ идёт через реальный HTTP-стек панели, и `response_model`, не
        объявивший поле, отбрасывает его молча — без исключения, без падения,
        просто ключа не будет в JSON. Нужны оба теста: один ловит потерю в
        хранении, другой — потерю в сериализации.
        """
        await _set(profile, True)

        answer = await auth_client.get(f'/api/dating/profile?site_id={SITE}')
        assert answer.status_code == 200

        rows = answer.json()['result']
        found = next(row for row in rows if int(row['id']) == profile)

        assert found['mailing_on'] is True

    async def test_unknown_profile_is_404(self):
        """404, а не молчаливое ничего: правка несуществующей анкеты — ошибка
        вызывающего, и знать о ней он должен."""
        with pytest.raises(HTTPException) as refusal:
            await _set(99_999_999, True)

        assert refusal.value.status_code == 404

    async def test_closed_without_login(self, client):
        """То, что решается до входа в обработчик, — по HTTP, как и вся сюита."""
        answer = await client.put('/api/dating/profile/1/mailing',
                                  json={'mailing_on': True})

        assert answer.status_code == 401


class TestPageCarriesTheBoxes:
    """Страница отдаёт разметку галочки «Письма рассылка» и её привязку.

    Блок собирается скриптом уже в браузере, но сам скрипт лежит в шаблоне —
    значит идентификаторы видны прямо в ответе. Сторожим и разметку (`setRow`), и
    привязку (`setBind`): без привязки галочка останется нарисованной и мёртвой —
    щелчок ничего не шлёт, а на следующей перерисовке через 15 секунд состояние
    вернётся как было. Нарисованная мёртвая галочка хуже пропавшей: пропажу видно
    сразу, а мёртвую галочку — только щелчком.

    ⚠ **«Чат автоответ» отсюда ушёл.** Решение переехало на сайт
    (`dating_site.chat_answer_on`) и живёт на «Профилях», в полосе подвкладок
    «Чата» — рядом с диалогами, то есть там, где на чат и смотрят. Прежде, чтобы
    его поднять, человек уходил на «Анкеты», искал учётку и щёлкал, не видя ни
    одного разговора; а разложенное по сорока анкетам, оно требовало сорока
    включений, и забытая анкета молча не отвечала.
    """

    async def test_the_mailing_box_is_in_the_page(self, auth_client):
        page = (await auth_client.get('/admin/dating')).text

        # ⚠ Ищем **вызов**, а не голое имя: `id="mailingBox"` в ответе не
        # встретится вовсе (разметку собирает скрипт, в шаблоне лежит
        # `id="${id}"`), а голое имя совпало бы и внутри комментария — тест
        # пережил бы удаление самой строки.
        assert "setRow('mailingBox'" in page, 'галочка «Письма рассылка» пропала'
        assert "setBind('mailingBox'" in page, 'галочка «Письма рассылка» не привязана'

    async def test_the_chat_answer_box_moved_to_the_site(self, auth_client):
        """⚠ Сторож переезда: галочки на «Анкетах» нет, а выключатель сайта есть.

        Вернись она — решений стало бы два, и человек, поднявший одно, спрашивал
        бы, почему анкета молчит.
        """
        card = (await auth_client.get('/admin/dating')).text
        profiles = (await auth_client.get('/admin/dating/profile')).text

        assert "setRow('autoBox'" not in card, 'галочка «Чат автоответ» вернулась'
        assert "setBind('autoBox'" not in card, 'обработчик галочки вернулся'
        assert 'chat-answer-on' in profiles, \
            'выключателя автоответа нет на «Профилях» — решение потерялось'


async def _set(profile_id: int, mailing_on: bool) -> None:
    await dating_profile_action_mailing(
        profile_id, DatingProfileMailing(mailing_on=mailing_on), user=None)


async def _mailing(profile_id: int) -> bool:
    return bool((await dating_profile_row(profile_id))['mailing_on'])
