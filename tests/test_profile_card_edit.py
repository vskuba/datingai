"""Правка персональных данных анкеты: шесть полей, три места, одна дверь.

Спека — `docs/superpowers/specs/2026-10-08-profile-card-edit-design.md`.

⚠⚠ Главное, что здесь сторожится, — **не «поле сохранилось»**, а две вещи,
которые ломаются молча:

1. Номер анкеты на сайте записан дважды — колонкой `profile.profile_id` и ключом
   `id` в карточке, — и читают их разные места: ключ фактов агента берёт колонку,
   сведение переписки из панели и ссылка на сайт берут карточку. Разойдись они, и
   половина кода увидит номер, а половина пустоту; ни одна проверка не упадёт.
2. Правленое руками обязано переживать импорт и чтение анкеты со страницы. Иначе
   человек увидит затёртую правку и перестанет править вовсе.

База настоящая, сосед поддельный. Ручки зовутся в процессе, а не по HTTP: тот же
довод, что в `test_profile_mail_photo.py`.
"""

import pytest

from mysql_.mysql_ import mysql_get_db_async

pytestmark = pytest.mark.profile


async def test_column_is_there_and_empty(girl):
    """⚠ Пусто — `NULL`, а не `'[]'`: «отметок нет» должно иметь одно написание."""
    async with mysql_get_db_async() as db:
        await db.execute('SELECT profile_edited FROM profile WHERE id = %s', (girl,))
        row = await db.fetchone() or {}

    assert 'profile_edited' in row, 'колонки profile_edited нет'
    assert row['profile_edited'] is None, 'у свежей анкеты отметок быть не должно'
