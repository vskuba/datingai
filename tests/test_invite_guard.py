"""Предохранитель на ответ агента инвайтов: столько ли текстов и все ли разные.

## ⚠⚠⚠ Что чинится: один шаблон ушёл всем двенадцати анкетам

Раздача текстов девушкам шла по кругу — `templates[at % len(templates)]`. При
нехватке текстов это читалось как «лучше, чем ничего», но на **одном** тексте
круг замыкался на нём же: все анкеты сайта получали один и тот же шаблон и
писали его разным людям в одну минуту.

Ни ошибки, ни отказа при этом не было: пул набирался, инвайты уходили, панель
докладывала «готово 93». Нашлось по журналу глазами, через час работы.

Сломалось не у нас: у соседа в шаге цикла `repeat.count` потерял метку
переменной и стал единицей — цикл давал один текст вместо одиннадцати. Но мы
приняли это как законный ответ, **потому что не проверяли ответ вовсе**.

## Что проверяется здесь

**Сам предохранитель** (`invite_templates_check`): считает тексты, сводит
одинаковые, называет претензию словами.

**Что нехватка отнимает девушек, а не разнообразие.** Главный тест файла:
один текст на дюжину анкет — и в пул уходит **одна** анкета.

⚠ Наружу сюита не ходит: агент подменён заглушкой (`testing.md`, §1.2).
"""

import pytest

from src.dating import invite
from src.dating.invite import invite_templates_check

pytestmark = pytest.mark.chat

STAND_SITE = 999_343


class TestTheCheckCountsTexts:
    """Претензия по числу: столько ли текстов, сколько просили."""

    def test_exactly_as_asked_is_silent(self):
        texts, why = invite_templates_check(['раз', 'два', 'три'], 3)

        assert texts == ['раз', 'два', 'три']
        assert why == '', 'претензия на здоровом ответе'

    def test_fewer_is_told(self):
        texts, why = invite_templates_check(['раз'], 11)

        assert texts == ['раз']
        assert '1 из 11' in why, why

    def test_more_is_told_too(self):
        """⚠ Лишние не вредят — их просто не раздадут. Но сказать стоит: это
        признак, что промпт соседа понял число иначе."""
        _, why = invite_templates_check(['раз', 'два', 'три'], 2)

        assert 'больше' in why, why

    def test_empty_answer_is_empty(self):
        texts, why = invite_templates_check([], 5)

        assert texts == []
        assert '0 из 5' in why, why


class TestTheCheckFindsTwins:
    """⚠⚠ Одинаковые тексты: сосед обещал сверить их судьёй — значит не сверил."""

    def test_twins_collapse(self):
        texts, why = invite_templates_check(['раз', 'раз', 'два'], 3)

        assert texts == ['раз', 'два'], 'близнец остался в раздаче'
        assert 'одинаковых' in why, why

    def test_case_and_spaces_do_not_make_them_different(self):
        """⚠ Сравниваем нормализованно: регистр и двойные пробелы человек не
        различит, а сайт отправит обе реплики."""
        texts, _ = invite_templates_check(['Stop  being cute', 'stop being cute'], 2)

        assert len(texts) == 1, 'регистр и пробелы сочтены разными текстами'

    def test_order_is_kept(self):
        """Порядок прихода сохраняем: по нему тексты раздаются девушкам."""
        texts, _ = invite_templates_check(['первый', 'второй', 'первый'], 3)

        assert texts == ['первый', 'второй']

    def test_blank_texts_are_dropped(self):
        texts, _ = invite_templates_check(['раз', '', '   ', None], 1)

        assert texts == ['раз']


class TestOneTextDoesNotGoToEveryone:
    """⚠⚠⚠ То самое, что ушло в прод: один текст на всех анкет сайта."""

    async def test_one_text_takes_one_girl(self, monkeypatch):
        async def _agent(site_id, language, **kwargs):
            return ['Stop being cute']

        put = _quiet(monkeypatch, _agent)
        monkeypatch.setattr(invite, '_girls', _answer(
            [{'id': one, 'profile': '{}'} for one in range(1, 13)]))
        monkeypatch.setattr(invite, '_people', _answer(
            [{'uid': f'u{one}', 'name': f'M{one}'} for one in range(1, 25)]))

        await invite.invite_make(STAND_SITE, 12 * 24)

        assert put, 'инвайты не набрались вовсе — предохранитель съел заход'
        assert len({girl for girl, _ in put}) == 1, \
            'один текст снова раздан нескольким анкетам'

    async def test_the_reason_says_why_the_pool_is_small(self, monkeypatch):
        """⚠ Пул от одной анкеты вместо двенадцати выглядит поломкой набора —
        значит причина обязана быть словами, а не только в журнале."""
        async def _agent(site_id, language, **kwargs):
            return ['Stop being cute']

        _quiet(monkeypatch, _agent)
        monkeypatch.setattr(invite, '_girls', _answer(
            [{'id': one, 'profile': '{}'} for one in range(1, 13)]))

        got = await invite.invite_make(STAND_SITE, 50)

        assert any('меньше, чем анкет' in one for one in got['reasons']), \
            got['reasons']

    async def test_the_complaint_reaches_the_errors_page(self, monkeypatch):
        """⚠ И на «Ошибки» — отдельной строкой: претензия про соседа, а не про
        анкету, и в журнале анкеты её искать никто не станет."""
        said = []

        async def _agent(site_id, language, **kwargs):
            return ['один']

        async def _write(source, message, site_id=0):
            said.append((source, message))

        _quiet(monkeypatch, _agent)
        monkeypatch.setattr(invite, 'error_write', _write)
        monkeypatch.setattr(invite, '_girls', _answer(
            [{'id': one, 'profile': '{}'} for one in (1, 2, 3)]))

        await invite.invite_make(STAND_SITE, 10)

        assert said, 'претензия не дошла до страницы «Ошибки»'
        assert said[0][0] == 'invite_templates'
        assert 'из 3' in said[0][1], said[0][1]

    async def test_a_healthy_answer_clears_the_complaint(self, monkeypatch):
        """⚠ Удачный заход стирает строку: иначе она висела бы вечно, и человек
        читал бы её как «прямо сейчас сломано»."""
        cleared = []

        async def _agent(site_id, language, **kwargs):
            return ['раз', 'два', 'три']

        async def _clear(source, site_id=0):
            cleared.append(source)

        _quiet(monkeypatch, _agent)
        monkeypatch.setattr(invite, 'error_clear', _clear)
        monkeypatch.setattr(invite, '_girls', _answer(
            [{'id': one, 'profile': '{}'} for one in (1, 2, 3)]))

        await invite.invite_make(STAND_SITE, 10)

        assert cleared == ['invite_templates'], cleared


class TestTheGuardStandsInTheRealPath:
    """⚠ Проверка стоит **в дороге набора**, а не рядом с ней."""

    def test_the_maker_does_not_cycle_templates(self):
        """⚠ Смотрим **код, а не комментарии**: про прежний круг там написано
        прямым текстом — ради того и написано, — и поиск по всему исходнику
        краснел бы на объяснении, которое и должно остаться."""
        import inspect

        code = '\n'.join(line for line in
                         inspect.getsource(invite.invite_make).splitlines()
                         if not line.lstrip().startswith('#'))

        assert 'templates[at]' in code, 'раздача снова не по одному на анкету'
        assert 'at % len(templates)' not in code, \
            'круг раздачи вернулся — один текст опять уйдёт всем'

    def test_the_asker_validates(self):
        import inspect

        code = inspect.getsource(invite._templates)

        assert 'invite_templates_check' in code, \
            'ответ соседа снова принимается на слово'


# ── Помощники ────────────────────────────────────────────────────────────────

def _answer(value):
    async def _got(*args, **kwargs):
        return value

    return _got


def _quiet(monkeypatch, agent) -> list:
    """Заглушает всё, что ходит наружу, и копит уложенное в пул.

    ⚠ Вместе с `error_write`/`error_clear`: предохранитель о претензии
    докладывает, а сюите незачем писать настоящие строки отказов в базу.
    """
    put = []

    async def _put(site_id, girl, person, text):
        put.append((int(girl['id']), text))

        return True

    monkeypatch.setattr(invite, 'invite_template', agent)
    monkeypatch.setattr(invite, '_put', _put)
    monkeypatch.setattr(invite, '_girls', _answer([{'id': 1, 'profile': '{}'}]))
    monkeypatch.setattr(invite, '_people', _answer(
        [{'uid': 'u1', 'name': 'Brian'}, {'uid': 'u2', 'name': 'Don'}]))
    monkeypatch.setattr(invite, '_invited', _answer(set()))
    monkeypatch.setattr(invite, '_talked', _answer(set()))
    monkeypatch.setattr(invite, '_pool_uids', _answer(set()))
    monkeypatch.setattr(invite, 'block_pairs', _answer(set()))
    monkeypatch.setattr(invite, '_language', _answer('en'))
    monkeypatch.setattr(invite, '_avoid', _answer([]))
    monkeypatch.setattr(invite, 'error_write', _nothing())
    monkeypatch.setattr(invite, 'error_clear', _nothing())

    return put


def _nothing():
    async def _got(*args, **kwargs):
        return None

    return _got
