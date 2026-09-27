"""Агент знает, кому именно пишет: ключ собеседника и сторож чужого имени.

## ⚠⚠ Что было сломано

`/chat/ask` требует `companion_id` и берёт по нему **имя** из своего справочника.
Мы слали туда единицу, считая поле «типом собеседника» — так и было написано в
комментарии. Единица оказалась конкретной персоной, «Vadim».

**Имя в тексте.** Промпты зовут собеседника `%companion_title%`, и на проде агент
написал живому человеку по имени Gerard: «ok vadim, let's go».

**Факты, и это хуже.** Шаг разбора кладёт факты в Qdrant по ключу
`document_id: '%companion_name%'`, переписку — по `'%girl_id%_%companion_name%'`. С
одним именем на всех факты обо **всех** мужчинах сайта валились в один документ, а
разговоры девушки со всеми — в один. Ровно та беда, от которой у девушек стоит
`girl_id`.

⚠ **Наружу сюита не ходит** (`testing.md`): проверяется то, что мы передаём.
"""

import pytest

from src.dating.answer import ANSWER_COMPANION_ID
from src.dating.companion_ref import (COMPANION_REF_ID, companion_ref,
                                      companion_ref_foreign, companion_title,
                                      companion_vars)
from src.dating.mail_agent import MAIL_AGENT_COMPANION_ID

pytestmark = pytest.mark.chat


class TestTheKey:
    """⚠ Ключ — пара «сайт + номер», а не имя."""

    def test_it_joins_site_and_uid(self):
        assert companion_ref(343, '128282') == 'dating_343_128282'

    def test_two_people_never_share_it(self):
        """⚠⚠ Ради этого всё: имена на сайте повторяются десятками.

        Привяжи мы факты к имени, двое разных мужчин делили бы один документ, и
        агент рассказывал бы одному биографию другого.
        """
        first = companion_ref(343, '111')
        second = companion_ref(343, '222')

        assert first != second

    def test_sites_do_not_collide(self):
        """Номера у сайтов свои, и один и тот же может встретиться на обоих."""
        assert companion_ref(343, '111') != companion_ref(999, '111')

    def test_no_uid_no_key(self):
        """Нет номера — звать агента незачем: факты некуда положить."""
        assert companion_ref(343, '') == ''
        assert companion_ref(343, None) == ''

    def test_the_key_is_safe_for_a_document_id(self):
        """⚠ Ключ уезжает в `document_id` чужого хранилища — мусор в нём вычищаем."""
        assert companion_ref(343, 'a b/c#1') == 'dating_343_abc1'


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
    """⚠⚠ Ключ и имя — **разные** поля. Троица едет целиком: с `…remove_companion`
    сосед из запроса ничего не подставляет, и шаг «Переменные» склеивает
    `companion_unique_id` из `%companion_id%_%companion_name%` — без номера
    ключ съедает приставку."""

    def test_both_go_out(self):
        got = companion_vars(343, '128282', 'Gerard')

        assert got == {'companion_id': COMPANION_REF_ID,
                       'companion_name': 'dating_343_128282',
                       'companion_title': 'Gerard'}

    def test_the_agent_never_greets_a_key(self):
        """Слей их в одно, и агент написал бы «привет, dating_343_128282»."""
        got = companion_vars(343, '128282', 'Gerard')

        assert got['companion_title'] != got['companion_name']


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
    """⚠ Константа одна на оба входа к соседу, и это больше не единица."""

    def test_the_chat_uses_our_record(self):
        assert ANSWER_COMPANION_ID == COMPANION_REF_ID

    def test_the_mail_agent_uses_it_too(self):
        assert MAIL_AGENT_COMPANION_ID == COMPANION_REF_ID

    def test_it_is_not_the_stranger(self):
        """Единица в справочнике соседа — «Vadim», живая персона."""
        assert COMPANION_REF_ID != 1
