"""Журнал запросов не печатает секретов.

⚠⚠ **Проверяется общий слой (`py_modules/mysql_/mysql_log.py`), и это осознанно.**
Своей сюиты у него нет — код там проверяют проекты-потребители, — а цена дефекта
здесь та же, что у утечки пароля: журнал читают в консоли, копируют в переписку,
отдают в поддержку и хранят месяцами. Секрет, попавший туда, считается раскрытым.

Поймано живьём в этом проекте: сохранение настроек сайта печатало весь JSON
целиком, вместе с `api_password` и `companion_password`; заведение анкеты —
пароль от чужого сайта обычным аргументом `INSERT`; вход — весь набор cookie.

Здесь стережётся, что каждое из этих мест закрыто, и **что закрыто не всё
подряд**: журнал без значений не отвечает ни на один вопрос отладки.
"""

import pytest

from mysql_.mysql_log import (MYSQL_LOG_MARK, mysql_log_args, mysql_log_line,
                              mysql_log_secret)

pytestmark = pytest.mark.core


class TestByColumn:
    """Секрет узнаётся по столбцу, в который едет значение."""

    def test_profile_password_on_insert(self):
        """Пароль анкеты от чужого сайта — по списку столбцов `INSERT`.

        Живой случай: строка из журнала выглядела так, что пароль читался целиком
        рядом с логином. Ни имени, ни ключа рядом нет — узнать секрет можно
        только по столбцу.
        """
        line = mysql_log_line(
            'INSERT INTO profile` (`site_id`, `login`, `password`, `note`) '
            'VALUES (%s, %s, %s, %s)',
            (19, 'girl@example.invalid', 'SekretnyParol123', 'пометка'))

        assert 'SekretnyParol123' not in line
        assert MYSQL_LOG_MARK in line
        # ⚠ Соседние значения остались: журнал без них бесполезен.
        assert 'girl@example.invalid' in line and 'пометка' in line

    def test_storage_state_on_update(self):
        """Набор cookie после входа — это доступ к учётке, и он не печатается."""
        line = mysql_log_line(
            'UPDATE profile SET storage_state = %s, status = %s WHERE id = %s',
            ('{"cookies":[{"name":"PHPSESSID","value":"abc123deadbeef"}]}', 'online', 40))

        assert 'abc123deadbeef' not in line
        assert "'online'" in line and '40' in line

    def test_proxy_url_with_channel_password(self):
        """Строка подключения к прокси несёт пароль канала."""
        line = mysql_log_line(
            'UPDATE profile SET proxy_url = %s WHERE id = %s',
            ('socks5://user:kanalny@10.0.0.1:1080', 7))

        assert 'kanalny' not in line

    def test_hash_is_a_secret_too(self):
        """Хеш пароля — тоже секрет: по нему подбирают не спрашивая нас."""
        line = mysql_log_line('SELECT id FROM user WHERE login = %s AND password_hash = %s',
                              ('admin', 'e3b0c44298fc1c14'))

        assert 'e3b0c44298fc1c14' not in line

    def test_empty_stays_empty(self):
        """⚠ Пустое значение остаётся пустым, а не превращается в метку.

        «Пароль не доехал» — самая частая беда в этих местах, и отличать её надо
        с одного взгляда. Метка вместо пустоты стёрла бы эту разницу.
        """
        line = mysql_log_line("SELECT id FROM profile WHERE password <> %s", ('',))

        assert "''" in line and MYSQL_LOG_MARK not in line

    def test_column_name_survives_prefix_and_quotes(self):
        """`g.password` и `` `password` `` — тот же столбец, что и `password`."""
        assert mysql_log_secret('g.password') is True
        assert mysql_log_secret('`api_password`') is True
        assert mysql_log_secret('note') is False


class TestByContent:
    """Секрет узнаётся по содержимому — там, где столбец о нём не говорит."""

    def test_settings_json(self):
        """Живой случай: `dating_site.settings` печатался целиком.

        Столбец `settings` секретным не назовёшь — в нём адреса, сроки, флаги, —
        а пароли внутри есть. Поэтому вторая проверка, по ключам JSON.
        """
        line = mysql_log_line(
            'INSERT INTO site (site_id, settings) VALUES (%s, %s) '
            'ON DUPLICATE KEY UPDATE settings = VALUES(settings)',
            (343, '{"api_base": "https://site/api", "api_password": "vasya1212", '
                  '"companion_password": "14Sem14en_", "send_every": "60"}'))

        assert 'vasya1212' not in line and '14Sem14en_' not in line
        # Всё несекретное на месте: по этой строке и отлаживают настройки.
        assert 'https://site/api' in line and '"send_every": "60"' in line

    def test_escaped_json_inside_json(self):
        """JSON, лежащий строкой внутри JSON: кавычки экранированы, ключ тот же."""
        line = mysql_log_line(
            'UPDATE site SET settings = %s WHERE site_id = %s',
            ('{"heartbeat": "{\\"token\\": \\"deadbeef\\", \\"base\\": \\"https://x\\"}"}', 343))

        assert 'deadbeef' not in line
        assert 'https://x' in line

    def test_url_password_anywhere(self):
        """Пароль в `scheme://user:pass@host` — где бы значение ни ехало."""
        line = mysql_log_line('INSERT INTO log (message) VALUES (%s)',
                              ('не дозвонились: socks5://user:tajna@10.0.0.1:1080',))

        assert 'tajna' not in line
        assert '10.0.0.1:1080' in line, 'адрес нужен для отладки и секретом не является'


class TestKeepsWorking:
    """Журнал остаётся журналом: значения на месте, форма прежняя."""

    def test_ordinary_query_is_untouched(self):
        args = ('обычная пометка', 5)
        line = mysql_log_line('UPDATE profile SET note = %s WHERE id = %s', args)

        assert line.endswith(f'| Args: {args}')

    def test_no_args_no_tail(self):
        assert mysql_log_line('SELECT 1') == '[MySQL SQL]: SELECT 1'

    def test_on_duplicate_tail_is_read_by_text(self):
        """⚠ `%s` после `VALUES` раскладывать по списку столбцов нельзя.

        В хвосте `ON DUPLICATE KEY UPDATE` свои столбцы, и круг по списку назвал
        бы секретным что попало — или, наоборот, пропустил бы настоящий секрет.
        """
        got = mysql_log_args(
            'INSERT INTO t (id, note) VALUES (%s, %s) '
            'ON DUPLICATE KEY UPDATE password = %s, note = %s',
            (1, 'заметка', 'tajniy', 'вторая'))

        assert got == (1, 'заметка', MYSQL_LOG_MARK, 'вторая')

    def test_multi_row_insert_repeats_columns(self):
        """Многострочная вставка: столбцы те же, просто перечислены пачками."""
        got = mysql_log_args(
            'INSERT INTO t (login, password) VALUES (%s, %s), (%s, %s)',
            ('a@x', 'one', 'b@x', 'two'))

        assert got == ('a@x', MYSQL_LOG_MARK, 'b@x', MYSQL_LOG_MARK)

    def test_unparsed_query_does_not_break(self):
        """Незнакомая форма запроса — печатаем как есть, а не молчим.

        Журнал обязан напечатать строку при любом входе. Разбор не полный
        намеренно; защиту это не ослабляет — секреты по содержимому ловятся всё
        равно.
        """
        line = mysql_log_line('CALL some_procedure(%s)', ('значение',))

        assert 'значение' in line

    def test_number_and_none_survive(self):
        assert mysql_log_args('UPDATE t SET a = %s, b = %s WHERE id = %s',
                              (None, 12, 5)) == (None, 12, 5)
