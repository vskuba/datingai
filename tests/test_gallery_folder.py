"""Папка галереи у анкеты: карточка её отдаёт, общий `PUT` её не трогает.

## ⚠⚠ Что здесь на самом деле проверяется

Не «колонка есть» — это сказала бы и миграция. Проверяется **обещание об одной
двери**: папку пишет только ручка галереи, а общий `PUT /api/dating/profile`
её не принимает даже когда поле прислали.

Обещание структурное: `DatingProfileSave` поля не содержит, а лишние pydantic
отбрасывает. Но «структурно невозможно» — это свойство сегодняшнего кода, и
стоит кому-нибудь добавить поле в модель «чтобы форма сохраняла всё разом»,
появится второй способ задать папку. Два способа разойдутся: вкладка покажет
папку, которую человек уже сменил.

## ⚠ Пусто — обычный исход, а не пробел в данных

Папки на Диске названы логином анкеты, поэтому пустая колонка читается как
«как у всех», и логин подставляет сервер. Тест на это и смотрит: у свежей
анкеты поле есть и оно пустое — а не отсутствует.

## ⚠⚠ Карточка спрашивается **в процессе**, а не по HTTP

Это намеренное расхождение с общим правилом, и у него есть причина
(`testing.md` §1.2 — расхождение уже записано там). Поднятая локально панель
бежит ту ветку, из которой её запустили; спроси мы карточку по HTTP, тест
краснел бы не потому, что код плох, а потому, что рядом работает другая
версия. Запись в базу и запрет общего `PUT` проверяются по HTTP — там эта
разница ничего не решает.
"""

import uuid

import pytest

from mysql_.mysql_ import mysql_get_db_async

from src.uvicorn.routers.dating.profile_ import profile_delete
from src.uvicorn.routers.dating.profile_view import profile_view_card

pytestmark = pytest.mark.profile

STAND_SITE = 999_343

FOLDER = 'bogiladiana9021@bigmir.net'


async def test_card_gives_empty_folder(girl):
    """У свежей анкеты поле есть и оно пустое — «как у всех», а не «нет поля»."""
    card = (await profile_view_card(girl, user=None))['result']

    assert 'gallery_folder' in card, 'карточка обязана отдавать поле всегда'
    assert card['gallery_folder'] == ''


async def test_card_gives_chosen_folder(girl):
    """Выбранная папка доезжает до карточки."""
    async with mysql_get_db_async() as db:
        await db.execute('UPDATE profile SET gallery_folder = %s WHERE id = %s',
                         (FOLDER, girl))
        await db.connection.commit()

    card = (await profile_view_card(girl, user=None))['result']

    assert card['gallery_folder'] == FOLDER


async def test_common_put_does_not_touch_folder(auth_client, girl):
    """⚠ Главное: общий `PUT` папку не меняет, даже когда поле прислали.

    Одна дверь записи — ручка галереи. Появись вторая, вкладка показывала бы
    папку, которую человек уже сменил.
    """
    async with mysql_get_db_async() as db:
        await db.execute('UPDATE profile SET gallery_folder = %s WHERE id = %s',
                         (FOLDER, girl))
        await db.connection.commit()

    answer = await auth_client.put(f'/api/dating/profile/{girl}', json={
        'site_id': STAND_SITE,
        'login': f'gallery-{uuid.uuid4().hex[:8]}@example.com',
        'password': '',
        'gallery_folder': 'подсунутая-мимо-своей-двери'})
    assert answer.status_code == 200, answer.text

    async with mysql_get_db_async() as db:
        await db.execute('SELECT gallery_folder FROM profile WHERE id = %s', (girl,))
        got = ((await db.fetchone()) or {}).get('gallery_folder')

    assert got == FOLDER, 'общий PUT не должен знать про папку галереи'
