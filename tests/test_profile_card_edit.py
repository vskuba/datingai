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
from src.dating.profile_card import (PROFILE_CARD_FIELDS, profile_card_edited,
                                     profile_card_keep)

pytestmark = pytest.mark.profile


async def test_column_is_there_and_empty(girl):
    """⚠ Пусто — `NULL`, а не `'[]'`: «отметок нет» должно иметь одно написание."""
    async with mysql_get_db_async() as db:
        await db.execute('SELECT profile_edited FROM profile WHERE id = %s', (girl,))
        row = await db.fetchone() or {}

    assert 'profile_edited' in row, 'колонки profile_edited нет'
    assert row['profile_edited'] is None, 'у свежей анкеты отметок быть не должно'


class TestKeepRule:
    """Правило слияния: что сайт имеет право положить поверх.

    ⚠ Чистая функция и тесты без базы: правило читают **два** писателя карточки —
    импорт и чтение страницы кабинета, — и своя копия в каждом разошлась бы на
    первой правке. Сторож стоит на самом правиле, а не на его применении.
    """

    def test_untouched_card_passes_whole(self):
        fresh = {'id': '77', 'name': 'Olga', 'age': 31, 'city': 'Kyiv', 'about': 'текст'}

        assert profile_card_keep(fresh, None) == fresh

    def test_edited_field_is_dropped(self):
        """⚠ Выбрасываем из пришедшего, а не перекрываем прежним: класть поверх
        нечего — у вызывающего и так остаётся то, что лежит."""
        fresh = {'name': 'Olga', 'about': 'с сайта'}

        assert profile_card_keep(fresh, ['about']) == {'name': 'Olga'}

    def test_edited_takes_a_json_string_too(self):
        """Столбец приезжает то списком, то строкой — зависит от драйвера."""
        assert profile_card_keep({'about': 'с сайта'}, '["about"]') == {}

    def test_unknown_names_are_ignored(self):
        """⚠ Чужое имя в отметках ничего не защищает и ничего не ломает.

        Иначе строка `["password"]`, попавшая в колонку как угодно, отняла бы у
        сайта поле, которого в карточке нет вовсе.
        """
        assert profile_card_edited('["about", "password", "login"]') == ['about']

    def test_broken_json_is_no_marks(self):
        """Испорченное значение — «отметок нет», а не исключение: карточку пишет
        вход, и падать там из-за мусора в колонке нельзя."""
        assert profile_card_edited('{не json') == []

    def test_every_field_has_its_empty(self):
        """⚠ У каждого правимого поля объявлено, чем у него «пусто».

        Без этого «возраст 0 там, где ключа нет вовсе» считалось бы правкой, и
        открытое окно, сохранённое без изменений, пометило бы возраст правленым.
        """
        from src.dating.profile_card import PROFILE_CARD_EMPTY

        assert set(PROFILE_CARD_EMPTY) == set(PROFILE_CARD_FIELDS)
        assert PROFILE_CARD_EMPTY['age'] == 0
        assert PROFILE_CARD_EMPTY['about'] == ''
