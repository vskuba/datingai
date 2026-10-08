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

import uuid

import pytest

from mysql_.mysql_ import mysql_get_db_async
from src.dating.profile_card import (PROFILE_CARD_FIELDS, profile_card_edited,
                                     profile_card_forget, profile_card_keep,
                                     profile_card_save, profile_card_why)

pytestmark = pytest.mark.profile

STAND_SITE = 999_343


def _login() -> str:
    """Логин тестовой анкеты. ⚠ Домен `@example.com` — метка сюиты, по ней
    уборка находит мусор, если тест оборвётся на полуслове."""
    return f'card-{uuid.uuid4().hex[:10]}@example.com'


async def _row(profile_id: int) -> dict:
    """Строка анкеты как она лежит: нужны обе записи номера и отметки."""
    async with mysql_get_db_async() as db:
        await db.execute('SELECT login, profile_id, profile, profile_edited '
                         '  FROM profile WHERE id = %s', (profile_id,))

        return dict(await db.fetchone() or {})


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


class TestSave:
    """Запись правки: шесть полей, две колонки, одна карточка."""

    async def test_all_six_fields_are_stored(self, girl):
        login = _login()
        got = await profile_card_save(girl, {
            'login': login, 'id': '643694', 'name': 'Diana', 'age': 29,
            'city': 'Kyiv', 'about': 'люблю шахматы'})

        assert got['login'] == login
        assert got['card']['name'] == 'Diana'
        assert got['card']['age'] == 29
        assert got['card']['city'] == 'Kyiv'
        assert got['card']['about'] == 'люблю шахматы'

    async def test_uid_reaches_both_writings(self, girl):
        """⚠⚠ Главный сторож задачи: номер уезжает **и в колонку, и в ключ**.

        Читают их разные места — ключ фактов агента берёт колонку
        (`agent_site_uid`), сведение переписки из панели и ссылка на сайт берут
        ключ карточки. Разойдись они, половина кода увидит номер, а половина
        пустоту, и ни одна проверка не упадёт.
        """
        from src.dating.json_column import json_column_load

        await profile_card_save(girl, {'login': _login(), 'id': '643694'})
        row = await _row(girl)

        assert row['profile_id'] == '643694', 'номер не лёг в колонку'
        assert json_column_load(row['profile']).get('id') == '643694', \
            'номер не лёг в ключ карточки'

    async def test_empty_uid_is_null_not_empty_string(self, girl):
        """⚠ Уникальный ключ `(site_id, profile_id)` считает пустые строки
        одинаковыми, а `NULL`-ы — разными: с `''` вторая анкета без номера на том
        же сайте не сохранилась бы вовсе."""
        await profile_card_save(girl, {'login': _login(), 'id': ''})

        assert (await _row(girl))['profile_id'] is None

    async def test_two_profiles_may_both_have_no_uid(self, girl_make):
        """Та же мысль, но проверенная тем, ради чего правило и стоит."""
        one, two = await girl_make(), await girl_make()

        await profile_card_save(one, {'login': _login(), 'id': ''})
        await profile_card_save(two, {'login': _login(), 'id': ''})

        assert (await _row(one))['profile_id'] is None
        assert (await _row(two))['profile_id'] is None

    async def test_changed_fields_are_marked(self, girl):
        await profile_card_save(girl, {'login': _login(), 'name': 'Diana',
                                       'about': 'текст'})

        # ⚠ Колонка приезжает из базы строкой JSON, а не списком (так её отдаёт
        # драйвер — см. `profile_card_edited`), поэтому сравниваем через него.
        assert set(profile_card_edited((await _row(girl))['profile_edited'])) == \
            {'name', 'about'}

    async def test_unchanged_save_marks_nothing(self, girl):
        """⚠⚠ Метится изменённое, а не присланное.

        Иначе человек, открывший окно поглядеть и нажавший «Сохранить», заморозил
        бы все пять полей разом — и сайт больше не обновил бы ни одного, без
        единого признака того, что произошло.
        """
        await profile_card_save(girl, {'login': _login(), 'name': 'Diana'})
        before = await _row(girl)

        # Второй раз — ровно то же, что лежит: так выглядит «открыл и сохранил».
        await profile_card_save(girl, {'login': before['login'], 'name': 'Diana',
                                       'age': 0, 'city': '', 'about': '', 'id': ''})

        assert profile_card_edited((await _row(girl))['profile_edited']) == ['name'], \
            'сохранение без изменений пометило лишние поля'

    async def test_clearing_is_a_change(self, girl):
        """⚠ Стирание — тоже правка: вписанное пустым значит «неправда, не знаем».

        Без отметки импорт вернул бы то, что человек только что убрал.
        """
        login = _login()
        await profile_card_save(girl, {'login': login, 'about': 'текст'})
        await profile_card_save(girl, {'login': login, 'about': ''})

        row = await _row(girl)
        assert 'about' in profile_card_edited(row['profile_edited'])

    async def test_duplicate_login_says_why(self, girl_make):
        one, two = await girl_make(), await girl_make()
        taken = _login()
        await profile_card_save(one, {'login': taken})

        with pytest.raises(ValueError) as refusal:
            await profile_card_save(two, {'login': taken})

        assert 'уже заведена' in str(refusal.value), str(refusal.value)

    async def test_duplicate_uid_says_which(self, girl_make):
        """⚠ Два уникальных ключа, и путать их нельзя: «почта занята» вместо
        «номер занят» отправляет человека править не то поле."""
        one, two = await girl_make(), await girl_make()
        await profile_card_save(one, {'login': _login(), 'id': '643694'})

        with pytest.raises(ValueError) as refusal:
            await profile_card_save(two, {'login': _login(), 'id': '643694'})

        assert '643694' in str(refusal.value)
        assert 'другой анкеты' in str(refusal.value), str(refusal.value)

    async def test_unknown_profile_is_lookup_error(self):
        with pytest.raises(LookupError):
            await profile_card_save(99_999_999, {'login': _login()})


class TestForget:
    """«Вернуть сайту»: отметка снимается, значение остаётся."""

    async def test_mark_goes_value_stays(self, girl):
        """⚠ Стереть значение значило бы выбросить то, что человек видел в поле.
        Обновит его следующий импорт, когда сайт отдаст своё."""
        from src.dating.json_column import json_column_load

        await profile_card_save(girl, {'login': _login(), 'about': 'мой текст'})

        left = await profile_card_forget(girl, 'about')

        row = await _row(girl)
        assert left == []
        assert row['profile_edited'] is None, 'пустые отметки обязаны лечь NULL'
        assert json_column_load(row['profile']).get('about') == 'мой текст'

    async def test_other_marks_survive(self, girl):
        await profile_card_save(girl, {'login': _login(), 'about': 'текст',
                                       'name': 'Diana'})

        assert await profile_card_forget(girl, 'about') == ['name']

    async def test_unknown_field_is_refused(self, girl):
        with pytest.raises(ValueError):
            await profile_card_forget(girl, 'password')


class TestWhy:
    """Причина словами, а не текстом драйвера."""

    def test_uid_key_is_recognised_before_the_generic_1062(self):
        """⚠ Порядок проверок важен: общий признак дубля (`1062`) стоит последним,
        иначе занятый номер доложился бы как занятая почта."""
        error = Exception("1062 (23000): Duplicate entry '343-643694' for key "
                          "'uk_dating_profile_site_uid'")

        assert 'другой анкеты' in profile_card_why(error, 'a@example.com', '643694')

    def test_login_key_is_recognised(self):
        error = Exception("1062 (23000): Duplicate entry '343-a@example.com' for key "
                          "'uk_dating_profile_site_login'")

        assert 'уже заведена' in profile_card_why(error, 'a@example.com', '')

    def test_anything_else_keeps_the_text(self):
        """Незнакомую беду не прячем: иначе причина пропадёт и в журнале."""
        assert 'что-то своё' in profile_card_why(Exception('что-то своё'), 'a', '')


class TestRace:
    """⚠⚠ Чужая одновременная правка не исчезает.

    Беда, от которой стоит этот класс, тиха и полна: прежде запись кладлась
    безусловным `UPDATE` поверх снимка, снятого отдельным обращением к базе, и
    уносила **всю** карточку вместе со списком отметок. Два человека, правящие
    разные поля одной анкеты в одну секунду, теряли одну правку целиком.
    """

    async def test_open_and_save_unchanged_is_not_a_conflict(self, girl):
        """⚠⚠ Самое обычное действие человека — открыл окно и сохранил, не правя.

        Сторож именно на него: `rowcount = 0` приходит и когда строку увели, и
        когда менять было нечего. Прими мы ноль за проигранную гонку — и это
        действие отвечало бы отказом «вас опередили».
        """
        login = _login()
        await profile_card_save(girl, {'login': login, 'name': 'Diana',
                                       'city': 'Kyiv'})

        # Второй раз ровно то же: писать нечего, но и отказывать не за что.
        got = await profile_card_save(girl, {'login': login, 'name': 'Diana',
                                             'city': 'Kyiv'})

        assert got['card']['name'] == 'Diana'
        assert got['card']['city'] == 'Kyiv'
        assert set(got['edited']) == {'name', 'city'}, \
            'повторное сохранение потеряло отметки'

    async def test_a_rival_write_is_not_clobbered(self, girl, monkeypatch):
        """⚠⚠ Главный сторож задачи: чужая правка переживает нашу.

        «Кто-то ещё» правит описание ровно в ту щель, когда наш снимок уже на
        руках, а запись ещё не ушла. Наш город обязан лечь, его описание —
        остаться.
        """
        from src.dating import profile_card as mod
        from src.dating.json_column import json_column_load

        login = _login()
        await profile_card_save(girl, {'login': login, 'city': 'Kyiv'})

        real_read = mod._card_read
        been = []

        async def read_then_rival(profile_id):
            row = await real_read(profile_id)
            # Один раз: пока снимок на руках, строку правит кто-то ещё.
            if not been:
                been.append(1)
                async with mysql_get_db_async() as db:
                    await db.execute(
                        "UPDATE profile"
                        " SET profile = JSON_SET(profile, '$.about', %s)"
                        " WHERE id = %s", ('чужая правка', int(profile_id)))
                    await db.connection.commit()

            return row

        monkeypatch.setattr(mod, '_card_read', read_then_rival)

        await profile_card_save(girl, {'login': login, 'city': 'Lviv'})

        card = json_column_load((await _row(girl))['profile'])
        assert card['city'] == 'Lviv', 'наша правка не легла'
        assert card['about'] == 'чужая правка', \
            'чужая правка затёрта — гонка не закрыта'

    async def test_losing_every_try_refuses_with_words(self, girl, monkeypatch):
        """⚠ Цикл обязан кончаться, и кончаться словами для человека.

        Если строку правят в каждую щель, попытки исчерпываются — и отказ должен
        быть предложением, которое человек поймёт, а не молчаливым проигрышем и
        не вечным кругом.
        """
        from src.dating import profile_card as mod

        login = _login()
        # ⚠⚠ Карточку заводим **до** гонки, и это не украшение теста. У свежей
        # анкеты столбец `profile` пуст, а `JSON_SET(NULL, …)` отдаёт `NULL` —
        # соперничающая правка оказалась бы пустышкой, запись прошла бы с первой
        # попытки, и тест сторожил бы несуществующее.
        await profile_card_save(girl, {'login': login, 'city': 'Kyiv'})

        real_read = mod._card_read
        been = []

        async def read_then_rival(profile_id):
            row = await real_read(profile_id)
            been.append(1)
            async with mysql_get_db_async() as db:
                await db.execute(
                    "UPDATE profile"
                    " SET profile = JSON_SET(profile, '$.about', %s)"
                    " WHERE id = %s", (f'чужая правка {len(been)}', int(profile_id)))
                await db.connection.commit()

            return row

        monkeypatch.setattr(mod, '_card_read', read_then_rival)

        with pytest.raises(ValueError) as refusal:
            await profile_card_save(girl, {'login': login, 'city': 'Lviv'})

        assert 'правил' in str(refusal.value), str(refusal.value)
        assert len(been) == mod.PROFILE_CARD_TRIES, \
            'число попыток разошлось с PROFILE_CARD_TRIES'
