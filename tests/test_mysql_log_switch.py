"""Печать запросов в журнал выключается, а беды печатаются всегда.

## ⚠⚠ Зачем

Замерено на живом сервере: воркер писал **6832 строки в минуту**, из них **6809** —
`[MySQL SQL]` и `[MySQL POOL]`. Полтора гигабайта логов в сутки с одного
контейнера при двадцати трёх полезных строках в минуту.

Это трассировка отладки, а не журнал. На проде она гасится, при разборе — зажигается.

## ⚠ Умолчание не менялось

Выключатели живут в **общей библиотеке** (`py_modules`), и молчаливая смена
поведения застала бы врасплох соседние проекты: человек ищет запрос в журнале, а
его там больше нет, и причина — в чужом коммите.
"""

import inspect

import pytest

from mysql_.mysql_ import (LOG_POOL, LOG_QUERIES, LoggingCursor,
                           MySQLConnectionManager)

pytestmark = pytest.mark.core


class TestTheDefaultIsUnchanged:
    """⚠ Без переменных всё печатается, как печаталось годами."""

    def test_queries_are_printed_by_default(self):
        assert LOG_QUERIES is True

    def test_pool_is_printed_by_default(self):
        assert LOG_POOL is True


class TestTheSwitchIsRead:
    """Значение берётся из окружения тем же `config_get`, что адрес базы."""

    def test_zero_turns_it_off(self, monkeypatch):
        """⚠ Проверяем **разбор строки**, а не импорт: модуль уже загружен, и
        перечитать его в том же процессе нельзя."""
        import mysql_.mysql_ as mod

        source = inspect.getsource(mod)

        assert "config_get('MYSQL_LOG_QUERIES', '1')" in source
        assert "config_get('MYSQL_LOG_POOL', '1')" in source
        # Ложным считается не только «0»: человек пишет и «false», и «off».
        assert "('0', 'false', 'no', 'off')" in source

    def test_both_switches_are_separate(self):
        """⚠ Порознь, потому что отлаживают ими разное: `QUERIES` — что спросили
        у базы, `POOL` — куда делись соединения."""
        import mysql_.mysql_ as mod

        source = inspect.getsource(mod)

        assert source.count('MYSQL_LOG_QUERIES') == 1
        assert source.count('MYSQL_LOG_POOL') == 1


class TestTheFlowIsGuarded:
    """Поток запросов под выключателем — весь."""

    def test_execute_checks_the_switch(self):
        code = inspect.getsource(MySQLConnectionManager.execute)

        assert 'if LOG_QUERIES:' in code

    def test_executemany_checks_it_too(self):
        """⚠ Иначе выключатель врал бы вполовину: пакетная вставка — тот же поток."""
        import mysql_.mysql_ as mod

        source = inspect.getsource(mod)
        место = source.index('[MySQL SQL Many]')
        кусок = source[место - 200:место]

        assert 'if LOG_QUERIES:' in кусок


def _parent(source: str, needle: str) -> str:
    """Строка, в чей блок вложена строка с `needle`.

    ⚠ Ищем **по отступам**, а не подстрокой в окне: в том же методе печать запроса
    уже стоит под `if LOG_QUERIES:`, и поиск по окну нашёл бы именно её — сказав,
    что ошибка замолчала, хотя она в соседнем блоке.
    """
    lines = source.split('\n')
    at = next(n for n, line in enumerate(lines) if needle in line)
    depth = len(lines[at]) - len(lines[at].lstrip())

    for line in reversed(lines[:at]):
        if not line.strip():
            continue

        if len(line) - len(line.lstrip()) < depth:
            return line.strip()

    return ''


class TestTroublesAreAlwaysPrinted:
    """⚠⚠ Беда печатается при любом значении: это не поток, а происшествие."""

    def test_query_errors_are_not_silenced(self):
        code = inspect.getsource(MySQLConnectionManager.execute)

        assert _parent(code, '[MySQL ERROR]').startswith('except'), \
            'ошибка запроса замолчала вместе с потоком — беду станет не видно'

    def test_a_failed_checkout_is_not_silenced(self):
        """Сорвавшийся чекаут — та беда, из-за которой пул течёт и запросы виснут
        на `acquire` навсегда."""
        import mysql_.mysql_ as mod

        assert _parent(inspect.getsource(mod),
                       'failed before enter').startswith('except')

    def test_the_flow_really_is_guarded(self):
        """Обратная сторона: поток **под** выключателем, иначе проверка выше
        проходила бы и на коде, где ничего не гасится."""
        code = inspect.getsource(MySQLConnectionManager.execute)

        assert _parent(code, 'mysql_log_line').startswith('if LOG_QUERIES')
