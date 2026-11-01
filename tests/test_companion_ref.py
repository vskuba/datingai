"""Агент знает, кому именно пишет: ключ собеседника и сторож чужого имени.

## ⚠⚠ Что было сломано

`/chat/ask` собеседника в запросе больше не приносит, и троицу читающих workflow
имён зовущий кладёт в `variables` сам (`companion_vars`). Прежде номер ехал полем
запроса и считался «типом собеседника»; у соседа по нему резолвилось **имя**, и
единица оказалась конкретной персоной, «Vadim».

**Имя в тексте.** Промпты зовут собеседника `%companion_title%`, и на проде агент
написал живому человеку по имени Gerard: «ok vadim, let's go».

**Факты, и это хуже.** Шаг разбора кладёт факты в Qdrant по ключу
`document_id: '%companion_name%'`, переписку — по `'%girl_id%_%companion_name%'`. С
одним именем на всех факты обо **всех** мужчинах сайта валились в один документ, а
разговоры девушки со всеми — в один. Ровно та беда, от которой у девушек стоит
`girl_id`.

## ⚠⚠ И что было сломано во второй раз: троица разошлась с девушкиной

Починка выше свалила **весь ключ** в `companion_name` (`dating_343_128282`), а
`companion_id` оставила приколоченным к единственной общей записи справочника
(`10`). Записи той больше нет — сосед перестал резолвить собеседника из запроса
(`remove_companion`, `agent_session_companion_drop`), — и десятка осталась мёртвой
константой, приставкой ко всякому ключу.

Склейка у соседа одна и та же на обе стороны:

    companion_unique_id = %companion_id%_%companion_name%
    girl_unique_id      = %girl_id%_%girl_name%

У девушки выходило `643694_diana`, у него — `10_dating_343_128282`: константа,
приставка, сайт, номер и **ни одного имени**. Читают эти ключи люди, разбирая, чьи
факты в документе.

⚠ **Наружу сюита не ходит** (`testing.md`): проверяется то, что мы передаём.
"""

import re
from pathlib import Path

import pytest

from src.dating.agent import agent_key
from src.dating.companion_ref import (companion_name, companion_ref_foreign,
                                      companion_title, companion_uid,
                                      companion_vars)

pytestmark = pytest.mark.chat


class TestTheNumber:
    """`companion_id` — его номер **на сайте**, как `girl_id` у девушки."""

    def test_it_is_the_uid(self):
        assert companion_uid('128282') == '128282'

    def test_it_is_not_the_dead_constant(self):
        """⚠⚠ Десятка была номером общей записи справочника — её больше нет.

        Пока она стояла здесь, ключ всякого собеседника начинался с `10_`, и
        номер человека ехал не в той половине склейки.
        """
        assert companion_uid('128282') != 10
        assert companion_uid('128282') != '10'

    def test_two_people_never_share_it(self):
        assert companion_uid('111') != companion_uid('222')

    def test_it_is_safe_for_a_document_id(self):
        """⚠ Половина ключа уезжает в `document_id` чужого хранилища."""
        assert companion_uid('a b/c#1') == 'abc1'

    def test_no_uid_no_number(self):
        """Нет номера — звать агента незачем: факты некуда положить."""
        assert companion_uid('') == ''
        assert companion_uid(None) == ''


class TestTheTechnicalName:
    """⚠⚠ `companion_name` собирается **той же** функцией, что `girl_name`."""

    def test_it_is_the_translit_of_the_name(self):
        assert companion_name('Gerard', '128282') == 'gerard'

    def test_it_is_the_very_same_rule_as_the_girls(self):
        """⚠⚠ Не вторая копия правила, а `agent_key` — один на оба конца.

        Разойдись они, ключ девушки и ключ мужчины собирались бы по разным
        правилам, и первая же правка транслита развела бы их молча.
        """
        for said in ('Gerard', 'Jan Kowalski', 'Соломія', 'VINCENT'):
            assert companion_name(said, '1') == agent_key(said)

    def test_spaces_become_underscores(self):
        """⚠ Ключ читают люди: «jan_kowalski» читается, «jankowalski» — нет."""
        assert companion_name('Jan Kowalski', '1') == 'jan_kowalski'

    def test_a_cyrillic_name_arrives_in_latin(self):
        assert companion_name('Євген', '1') == agent_key('Євген')

    def test_without_a_name_the_uid_stands_in(self):
        """Как `girl_name` скатывается на `girl_id`: ключ обязан быть."""
        assert companion_name('', '128282') == '128282'
        assert companion_name('許萊納', '128282') == '128282'

    def test_it_is_no_longer_the_whole_key(self):
        """⚠⚠ Ради этого правка: имя переменной больше не врёт о содержимом.

        В «имени собеседника» лежал ключ `dating_343_128282`, и всякий, кто правил
        промпт, видел не то, что там на самом деле.
        """
        assert 'dating' not in companion_name('Gerard', '128282')
        assert '343' not in companion_name('Gerard', '128282')


class TestTheTitle:
    """Имя, которым агент зовёт человека в тексте."""

    def test_the_name_is_used(self):
        assert companion_title('Gerard', '128282') == 'Gerard'

    def test_spaces_are_tidied(self):
        assert companion_title('  Jan   Kowalski ', '1') == 'Jan Kowalski'

    def test_without_a_name_the_uid_stands_in(self):
        """⚠ Не пустая строка: промпт построен на «=== %companion_title% пишет ===»,
        и пустая метка оставила бы агенту фразу без подлежащего."""
        assert companion_title('', '128282') == '128282'

    def test_without_anything_there_is_still_a_word(self):
        assert companion_title('', '') == 'собеседник'


class TestTheVariables:
    """⚠⚠ Три величины, и слепить их снова нельзя. Троица едет целиком: с
    `…remove_companion` сосед из запроса ничего не подставляет, и шаг «Переменные»
    склеивает `companion_unique_id` из `%companion_id%_%companion_name%`."""

    def test_all_three_go_out(self):
        got = companion_vars(343, '128282', 'Gerard')

        assert got == {'companion_id': '128282',
                       'companion_name': 'gerard',
                       'companion_title': 'Gerard'}

    def test_the_agent_never_greets_a_key(self):
        """Слей их в одно, и агент написал бы «привет, dating_343_128282»."""
        got = companion_vars(343, '128282', 'Gerard')

        assert got['companion_title'] != got['companion_name']

    def test_the_site_is_no_longer_asked_for(self):
        """⚠ Сайт из ключа ушёл — довод и цена в докстринге `companion_vars`.

        Довод здесь один: у девушки его тоже нет, а склейка одна на обе стороны.
        """
        assert companion_vars(343, '128282', 'Gerard') \
            == companion_vars(999, '128282', 'Gerard')


class TestTheKeyMatchesTheGirls:
    """⚠⚠ Ради этого вся правка: ключ собеседника читается так же, как ключ девушки.

    Склейку делает сосед (`%companion_id%_%companion_name%`), и проверить её здесь
    можно только собрав то же самое из того, что мы отдаём.
    """

    @staticmethod
    def _key(said: dict, who: str) -> str:
        return f'{said[f"{who}_id"]}_{said[f"{who}_name"]}'

    def test_the_companion_key_reads_like_a_person(self):
        assert self._key(companion_vars(343, '128282', 'Gerard'),
                         'companion') == '128282_gerard'

    def test_the_dead_constant_is_gone_from_the_key(self):
        """`10_dating_343_128282` — константа, приставка, сайт и номер без имени."""
        got = self._key(companion_vars(343, '128282', 'Gerard'), 'companion')

        assert not got.startswith('10_')
        assert 'dating' not in got

    def test_both_halves_are_shaped_like_the_girls(self):
        """⚠ Девушка даёт `643694_diana` — та же выкройка «номер + имя»."""
        from src.dating.agent import agent_vars

        hers = self._key(agent_vars({'profile_id': '643694'},
                                    {'name': 'Diana'}), 'girl')
        his = self._key(companion_vars(343, '128282', 'Gerard'), 'companion')

        assert hers == '643694_diana'
        # Одна выкройка: цифры, подчёркивание, латиница строчными.
        for said in (hers, his):
            assert re.fullmatch(r'[a-z0-9_]+', said), said


class TestTheForeignNameGuard:
    """⚠⚠ Текст с чужим именем не уходит вовсе."""

    def test_the_prod_case_is_caught(self):
        """Тот самый ответ, что ушёл Gerard'у."""
        got = companion_ref_foreign("ok vadim, let's go. what's on your mind today?",
                                    'Gerard')

        assert got == 'vadim'

    def test_case_does_not_matter(self):
        assert companion_ref_foreign('Hi Vadim!', 'Gerard') == 'vadim'

    def test_his_own_name_is_allowed(self):
        """⚠ Собеседника вправду может звать Вадим, и обращение к нему законно."""
        assert companion_ref_foreign('ok vadim, lets go', 'Vadim') == ''

    def test_a_clean_answer_passes(self):
        assert companion_ref_foreign('ok gerard, what about tonight?', 'Gerard') == ''

    def test_a_substring_is_not_a_name(self):
        """⚠ По границе слова: иначе сторож ловил бы невинные слова."""
        assert companion_ref_foreign('i love olenkaville pizza', 'Gerard') == ''

    def test_empty_text_is_not_a_problem(self):
        assert companion_ref_foreign('', 'Gerard') == ''


class TestBothDoorsUseIt:
    """Сборщик троицы один на оба входа к соседу."""

    def test_the_chat_uses_our_record(self):
        """Чат зовёт агента с номером из общего сборщика."""
        assert companion_vars(343, '128282', 'Gerard')['companion_id'] == '128282'

    def test_the_mail_agent_uses_it_too(self):
        """Почтовый вход берёт тот же сборщик — второго нет."""
        assert companion_vars(343, '1', 'X')['companion_id'] == '1'

    def test_the_answer_door_still_sends_the_trio(self):
        """⚠ Сторож против второй сборки: `answer.py` кладёт троицу `**`-раскрытием.

        Собери её кто-нибудь руками — и `companion_id` снова разошёлся бы с
        `companion_name`, как уже было с мёртвой десяткой.
        """
        code = Path('src/dating/answer.py').read_text(encoding='utf-8')

        assert '**await companion_vars_known(' in code
        # Мёртвой константы не осталось нигде: она была приставкой ко всякому ключу.
        assert 'COMPANION_REF_ID' not in code


class TestTheNameComesFromTheCard:
    """⚠⚠⚠ Третий виток той же беды: ключ вырождался в «номер_номер».

    Правая половина ключа — имя, и при пустом имени `companion_name` законно
    скатывается на номер: `207556_207556` вместо `207556_christopher_buchas`.
    Ключ при этом **выглядит ключом** — сосед пары по нему не находит и заводит
    вторую запись. Агент продолжает разговор, не видя половины сказанного; ни
    ошибки, ни пустой строки.

    Обе дороги чата брали имя из **реплики** (`message.sender_name`), а она
    почти всегда пуста: на проде 01.11.2026 — 42110 входящих из 42520, то есть
    99%. Расщеплялась пара ровно тогда, когда у человека приходили сообщения и с
    именем, и без: первое заводило именной ключ, второе — вырожденный. Так
    разошлись шесть пар, самая большая на 126 обменов.

    ⚠⚠ Имя живёт **в карточке**, и живёт надёжно: на том же проде она знает его
    у **всех** 2475 собеседников, ни одной пустой.
    """

    def _doors(self) -> str:
        return (Path('src/dating/chat.py').read_text(encoding='utf-8')
                + Path('src/dating/answer.py').read_text(encoding='utf-8'))

    def test_neither_chat_door_keys_on_the_message_name(self):
        """⚠⚠⚠ Главное утверждение: `sender_name` больше не идёт в ключ.

        Сторож смотрит **текст дорог**, а не поведение: беда молчаливая, и
        увидеть её можно лишь у соседа в списке разговоров — то есть случайно и
        через недели.
        """
        for door in ('src/dating/chat.py', 'src/dating/answer.py'):
            code = Path(door).read_text(encoding='utf-8')
            spot = code.find('companion_vars(')

            assert spot < 0 or 'companion_vars_known' in code[spot - 24:spot], (
                f'{door} снова собирает ключ чистой `companion_vars` — имя '
                'придёт из реплики, а она пуста в 99% случаев, и ключ '
                'выродится в «номер_номер»')

    def test_both_doors_ask_the_card(self):
        """Карточку спрашивают **обе** дороги: одна починенная оставила бы
        расщепление ровно наполовину — а расщеплённая пара хуже, чем явно
        сломанная, её не видно."""
        for door, who in (('src/dating/chat.py', 'приём реплики'),
                          ('src/dating/answer.py', 'ответ агента')):
            code = Path(door).read_text(encoding='utf-8')

            assert 'companion_vars_known' in code or 'companion_name_known' in code, (
                f'{who} ({door}) не спрашивает имя у карточки')

    def test_the_card_wins_over_what_came_with_the_message(self):
        """⚠ Порядок именно такой: карточка главная, присланное запасное.

        Наоборот — и пустая реплика снова перебивала бы известное имя.
        """
        import inspect

        from src.dating.companion_card import companion_name_known

        body = inspect.getsource(companion_name_known)

        assert 'known or said' in body, (
            'карточка перестала быть главной: присланное имя перебивает '
            'известное, и пустая реплика снова выродит ключ')
