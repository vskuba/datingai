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
                                                       dating_profile_row)
from src.uvicorn.routers.dating.dating_profile_action import (
    DatingProfileAuto, DatingProfileEnabled, DatingProfileMailing,
    dating_profile_action_auto, dating_profile_action_enabled,
    dating_profile_action_mailing)
from src.uvicorn.routers.dating.dating_profile_view import dating_profile_view_card

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
    """Страница отдаёт разметку обеих новых галочек и их привязку к обработчику.

    Блок собирается скриптом уже в браузере, но сам скрипт лежит в шаблоне —
    значит идентификаторы видны прямо в ответе. Сторожим и разметку (`setRow`), и
    привязку (`setBind`): без привязки галочка останется нарисованной и мёртвой —
    щелчок ничего не шлёт, а на следующей перерисовке через 15 секунд состояние
    вернётся как было. Нарисованная мёртвая галочка хуже пропавшей: пропажу видно
    сразу, а мёртвую галочку — только щелчком.
    """

    async def test_both_boxes_are_in_the_page(self, auth_client):
        page = (await auth_client.get('/admin/dating')).text

        # ⚠ Ищем **вызов**, а не голое имя: `id="autoBox"` в ответе не встретится
        # вовсе (разметку собирает скрипт, в шаблоне лежит `id="${id}"`), а голое
        # `autoBox` совпало бы и внутри комментария — тест пережил бы удаление
        # самой строки.
        assert "setRow('autoBox'" in page, 'галочка «Чат автоответ» пропала со страницы'
        assert "setRow('mailingBox'" in page, 'галочка «Письма рассылка» пропала со страницы'
        assert "setBind('autoBox'" in page, 'галочка «Чат автоответ» ни к чему не привязана'
        assert "setBind('mailingBox'" in page, 'галочка «Письма рассылка» ни к чему не привязана'


async def _set(profile_id: int, mailing_on: bool) -> None:
    await dating_profile_action_mailing(
        profile_id, DatingProfileMailing(mailing_on=mailing_on), user=None)


async def _mailing(profile_id: int) -> bool:
    return bool((await dating_profile_row(profile_id))['mailing_on'])
