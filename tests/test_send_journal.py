"""Каждая отправка реплики попадает в журнал запросов.

## ⚠⚠ Что было сломано

Во вкладке «Запросы» не было ни одной строки про чат. Журнал писала только
отправка сокетом (`_note_socket`); отправка ручкой сайта и сценарием не писала
ничего. На живом сайте с отправкой по HTTP это значило, что вкладка показывала
присутствие, письма и списки — и **ни одной реплики**, хотя именно они и есть
работа панели.

Нашлось при разборе дублей в переписке: чтобы понять, ушло сообщение один раз или
два, пришлось смотреть в журнал — а его там не оказалось.

## ⚠ Запись стоит в точке развилки, а не внутри путей

Внутри каждого пути её пришлось бы дописывать трижды, и четвёртый путь снова
остался бы немым. В развилке она одна на всех.
"""

import inspect

import pytest

import src.dating.send as send_mod

pytestmark = pytest.mark.chat


class TestEverySendIsLogged:
    """⚠⚠ Одна строка на одну отправку — каким бы путём она ни шла."""

    def test_the_journal_is_written_at_the_fork(self):
        code = inspect.getsource(send_mod.send_message)

        assert '_note_send' in code, 'отправка снова не пишется в журнал'

    def test_it_covers_all_three_transports(self):
        """⚠ Запись стоит **после** развилки: значит её видят все пути сразу."""
        code = inspect.getsource(send_mod.send_message)

        at_note = code.index('_note_send(')
        for path in ('_send_scenario(', '_send_ws(', '_send_http('):
            assert code.index(path) < at_note, \
                f'{path} идёт после записи — его отправка в журнал не попадёт'

    def test_the_door_is_the_send_one(self):
        code = inspect.getsource(send_mod._note_send)

        assert 'REQUEST_SEND' in code, 'отправка пишется не в свою дверь'

    def test_the_old_socket_only_note_is_gone(self):
        """⚠ Прежняя запись убрана, иначе сокет писал бы **две** строки на одну
        отправку: свою и общую."""
        assert not hasattr(send_mod, '_note_socket'), \
            'осталась прежняя запись — сокет двоит строки журнала'


class TestWhatTheLineSays:
    """⚠ Строка должна отвечать на вопросы, ради которых в журнал и смотрят."""

    def test_it_records_failures_too(self):
        """⚠⚠ Отказ — такой же стук в сайт, как удача, и без него след
        неотправленного сообщения пропадает совсем: в переписку оно не ложится."""
        code = inspect.getsource(send_mod.send_message)

        at_note = code.index('_note_send(')
        at_ok = code.index("if result['ok']:")

        assert at_note < at_ok, 'журнал пишется только при удаче'

    def test_it_does_not_store_the_text(self):
        """⚠ Текста реплики в журнале нет, только длина: разговор живёт в
        `message`, а здесь 52 тысячи строк в сутки."""
        code = inspect.getsource(send_mod._note_send)

        assert 'len(str(text' in code, 'длина реплики не пишется'
        assert 'path=f\'{where} → {to_uid}' in code

    def test_it_takes_ok_from_the_answer_not_the_code(self):
        """⚠ У LadaDate признак удачи — поле `IsSuccess` в теле: `200` с отказом
        внутри обычное дело, и по коду его не отличить."""
        code = inspect.getsource(send_mod._note_send)

        assert "ok=bool(got.get('ok'))" in code

    def test_the_http_path_returns_the_code(self):
        """Иначе во вкладке стоял бы ноль, и «сайт лёг» не отличалось бы от
        «сайт ответил отказом»."""
        code = inspect.getsource(send_mod._send_http)

        assert "'status': response.status_code" in code

    def test_the_socket_url_is_cut_before_the_query(self):
        """⚠ В строке сокета у некоторых сайтов едет токен сессии, а журнал
        читают глазами и копируют в переписку."""
        code = inspect.getsource(send_mod._note_send)

        assert "split('?')[0]" in code, 'в журнал уедет токен сессии'
