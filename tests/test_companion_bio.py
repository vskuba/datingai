"""Анкета собеседника одним текстом: что уезжает агенту, когда фактов нет.

⚠ **Проверяем сборку, а не запрос.** Правило здесь одно — «что показываем и как
подписываем», — и проверяется оно на словаре карточки: живая строка ничего к
этому не добавит, а стенд и уборку потребует. Тот же приём, что у разбора анкеты
(`companion_about_text`).

⚠⚠ **Зачем это вообще есть.** Агент чата узнаёт о человеке из фактов, а факты
извлекаются из переписки. В начале разговора переписки нет — и агент писал, не
зная о собеседнике ничего, хотя его карточка у нас лежала. На проде из 736
карточек рассказ о себе снят у 537, имя и возраст — у всех.
"""

import pytest

from src.dating.companion_bio import (COMPANION_BIO_MAX, companion_bio_build)

pytestmark = pytest.mark.core

FULL = {
    'name': 'James coakley',
    'age': 67,
    'city': 'christchurch',
    'country': 'New Zealand',
    'about': 'О себе: single have 10 year old son\n'
             'Увлечения: cars trucks buses\n'
             'Занятие: engineer',
}


class TestWhatTheAgentSees:
    """Из чего складывается текст."""

    def test_a_full_card_gives_every_line(self):
        got = companion_bio_build(FULL)

        assert 'Имя: James coakley' in got
        assert 'Возраст: 67' in got
        assert 'Занятие: engineer' in got

    def test_city_and_country_are_one_line(self):
        """⚠ Порознь они читаются как два разных факта, а это один ответ на
        вопрос «откуда он»."""
        got = companion_bio_build(FULL)

        assert 'Живёт: christchurch, New Zealand' in got
        assert 'Город:' not in got and 'Страна:' not in got

    def test_the_about_text_keeps_its_own_labels(self):
        """⚠ Подписи внутри рассказа ставит `companion_about.py`, и свой их набор
        здесь разошёлся бы с тем при первой же правке."""
        got = companion_bio_build(FULL)

        assert 'Увлечения: cars trucks buses' in got


class TestWhatIsLeftOut:
    """⚠ Чего нет, о том не сказано."""

    def test_empty_fields_are_not_named(self):
        """«Город: —» модель читает как факт («город неизвестен») и может об этом
        заговорить. Молчание честнее."""
        got = companion_bio_build({'name': 'Luis', 'age': 46, 'city': '',
                                   'country': 'Mexico', 'about': ''})

        assert got == 'Имя: Luis\nВозраст: 46\nЖивёт: Mexico'

    def test_zero_age_means_unknown(self):
        """⚠ Столбец `NOT NULL DEFAULT 0`: ноль — это «не знаем», а не «ноль лет».
        Напиши мы «Возраст: 0», агент получил бы прямую неправду."""
        got = companion_bio_build({'name': 'Bjorn', 'age': 0})

        assert 'Возраст' not in got, got

    def test_an_empty_card_gives_nothing(self):
        """Пустой текст промпт не вставит вовсе — `%if companion_bio%` его гасит.

        ⚠ Это важнее, чем кажется: пустой заголовок «Анкета на сайте ===» без
        строк под ним модель читает как «анкета пуста», то есть как факт.
        """
        assert companion_bio_build({}) == ''
        assert companion_bio_build({'name': '', 'age': 0, 'about': ''}) == ''

    def test_service_fields_never_leak(self):
        """⚠ Почта, фото и внутренние номера агенту не нужны: в реплику им попасть
        нечем, а утечь — есть куда."""
        got = companion_bio_build({**FULL, 'email': 'him@example.com',
                                   'photo': 'https://site/1.jpg',
                                   'uid': '216905', 'id': 42})

        for leak in ('him@example.com', 'https://site/1.jpg', '216905'):
            assert leak not in got, f'в анкету просочилось {leak}'


class TestTheTextIsBounded:
    """⚠ Предел — про внимание модели, а не про экономию токенов.

    Полторы тысячи знаков это «простыня», в которой тонет сама реплика
    собеседника. Самое важное в рассказе стоит первым («О себе» идёт до религии),
    поэтому обрезаем хвост.
    """

    def test_a_long_card_is_cut(self):
        long = {'name': 'X', 'age': 30,
                'about': '\n'.join(f'Пункт {n}: ' + 'я' * 60 for n in range(60))}

        got = companion_bio_build(long)

        assert len(got) <= COMPANION_BIO_MAX

    def test_the_cut_falls_on_a_line_break(self):
        """⚠ Режем по границе строки, а не по знаку: оборванная на полуслове
        анкета выглядит как сбой и провоцирует модель её «досочинить»."""
        long = {'name': 'X', 'age': 30,
                'about': '\n'.join(f'Пункт {n}: ' + 'я' * 60 for n in range(60))}
        whole = companion_bio_build({'name': 'X', 'age': 30, 'about': ''})

        got = companion_bio_build(long)

        assert got.startswith(whole)
        for line in got.split('\n'):
            assert line.endswith('я') or line.startswith(('Имя', 'Возраст')), \
                f'строка оборвана посреди: {line[-30:]!r}'
