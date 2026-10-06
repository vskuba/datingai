"""⚠ Отправка фотографий в письмах: разрешение хранится, исполнителя пока нет.

`profile_.mail_photo_on` — четвёртый выключатель анкеты, и заводится он раньше
своей механики: вложений в письмах проект не шлёт вовсе. Всё, что зовётся `photo`
в почтовом коде, — аватарка собеседника из списка сайта, а не снимок в письме.

⚠ **Поэтому здесь проверяется хранение, а не поведение.** Поведения у флага
сейчас нет, и сторожить его нечем. Тот же порядок и те же проверки, что у
`mailing_on` в `test_profile_mailing.py`: когда отправку напишут, она придёт к
живым данным и к галочке, которую человек уже поставил.

⚠ **Галочка без привязки опаснее пропавшей**, и сторож на это есть ниже: пропажу
видно сразу, а нарисованная мёртвая галочка возвращается в прежний вид только на
следующей перерисовке — через пятнадцать секунд, когда человек уже ушёл со
страницы, уверенный, что разрешил.

База настоящая, сосед поддельный. Ручки зовутся в процессе, а не по HTTP:
заведение анкеты сверяет сайт у соседа, и по HTTP это дало бы два запроса к
RhapsodyAi на каждую тестовую учётку — то есть сюиту, падающую от его перезапуска.
То же правило, что в `test_profile_mailing.py` и `test_profile_enabled.py`.
"""

import uuid

import pytest
from fastapi import HTTPException

from src.uvicorn.routers.dating.profile_ import (DatingProfileSave,
                                                       profile_create,
                                                       profile_delete,
                                                       profile_row)
from src.uvicorn.routers.dating.profile_action import (
    DatingProfileEnabled, DatingProfileMailPhoto, profile_action_enabled,
    profile_action_mail_photo)
from src.uvicorn.routers.dating.profile_view import profile_view_card

pytestmark = pytest.mark.mail
SITE = 343


@pytest.fixture
async def profile(rhapsody):
    """Одноразовая анкета в живой базе.

    ⚠ Чужую трогать нельзя: щёлкая её флагами, сюита меняла бы работу настоящей
    учётки — и человек искал бы поломку там, где её нет.
    """
    answer = await profile_create(
        DatingProfileSave(site_id=SITE, login=f'photo-{uuid.uuid4().hex[:10]}',
                          password='secret', note='тест отправки фотографий'),
        user=None)
    profile_id = int(answer['result'])

    yield profile_id

    try:
        await profile_delete(profile_id, user=None)
    except HTTPException:
        pass


class TestMailPhotoEndpoint:
    """Ручка разрешения: хранение и независимость от соседних флагов."""

    async def test_new_profile_has_it_off(self, profile):
        """⚠ Умолчание — «нельзя», в отличие от `enabled` («ведём»).

        Включённая галочка у функции, которой нет, — это ложь: человек решит, что
        фотографии уходят, и пойдёт искать их у получателя. И даже когда отправку
        напишут, разрешение слать снимки обязано остаться выбором человека по
        каждой анкете, а не достаться сорока учёткам молча.
        """
        assert await _flag(profile) is False

    async def test_toggle_is_stored(self, profile):
        await _set(profile, True)

        assert await _flag(profile) is True

        await _set(profile, False)

        assert await _flag(profile) is False

    async def test_it_is_independent(self, profile):
        """⚠ Флаги про разное, и ни один не задевает соседей.

        «Анкету ведём руками, но фотографии ей разрешены» — законное состояние:
        разрешение переживает выключение анкеты, иначе человек, вернув её в
        работу, молча терял бы прежнее решение.
        """
        await _set(profile, True)
        await profile_action_enabled(
            profile, DatingProfileEnabled(enabled=False), user=None)

        row = await profile_row(profile)

        assert bool(row['mail_photo_on']) is True, \
            'выключение анкеты сбросило разрешение на фотографии'
        assert bool(row['enabled']) is False
        assert bool(row['reply_on']) is False, 'разрешение подняло генерацию ответов'
        assert bool(row['mailing_on']) is False, 'разрешение подняло рассылку'

    async def test_it_reaches_the_card(self, rhapsody, profile):
        """Галочку рисует карточка — значит флаг должен в ней быть.

        ⚠ Без него после перезагрузки страницы галочка показывала бы снятую там,
        где стоит: человек поставил бы её второй раз и не понял, почему она не
        держится.
        """
        await _set(profile, True)
        card = (await profile_view_card(profile, user=None))['result']

        assert card['mail_photo_on'] is True

    async def test_unknown_profile_is_404(self):
        """404, а не молчаливое ничего: правка несуществующей анкеты — ошибка
        вызывающего, и знать о ней он должен."""
        with pytest.raises(HTTPException) as refusal:
            await _set(99_999_999, True)

        assert refusal.value.status_code == 404

    async def test_closed_without_login(self, client):
        """То, что решается до входа в обработчик, — по HTTP, как и вся сюита."""
        answer = await client.put('/api/dating/profile/1/mail-photo',
                                  json={'mail_photo_on': True})

        assert answer.status_code == 401


class TestPageCarriesTheBox:
    """Страница отдаёт и разметку галочки, и её привязку.

    Блок собирается скриптом уже в браузере, но сам скрипт лежит в шаблоне —
    значит идентификаторы видны прямо в ответе.

    ⚠ Сторожим **обе** половины. Без привязки галочка останется нарисованной и
    мёртвой: щелчок ничего не шлёт, а на следующей перерисовке состояние вернётся
    как было. Это хуже пропажи — пропажу видно сразу.

    ⚠ Ищем **вызов**, а не голое имя: разметку собирает скрипт, в шаблоне лежит
    `id="${id}"`, а голое имя совпало бы и внутри пояснения.
    """

    async def test_the_box_is_drawn(self, auth_client):
        page = (await auth_client.get('/admin/dating')).text

        assert "setRow('mailPhotoBox'" in page, 'галочки «Отправка фотографий» нет'

    async def test_the_box_is_bound(self, auth_client):
        page = (await auth_client.get('/admin/dating')).text

        assert "setBind('mailPhotoBox'" in page, 'галочка нарисована, но мертва'


async def _set(profile_id: int, mail_photo_on: bool) -> None:
    await profile_action_mail_photo(
        profile_id, DatingProfileMailPhoto(mail_photo_on=mail_photo_on), user=None)


async def _flag(profile_id: int) -> bool:
    return bool((await profile_row(profile_id))['mail_photo_on'])
