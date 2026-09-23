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


class TestTheGirlSendsHerOwnBio:
    """⚠⚠ Её анкета уходит агенту тем же кругом, что и анкета собеседника.

    Факты о девушке придумывает подпрогон `girl_fact_invent` и копит от разговора
    к разговору. У новой анкеты их нет вовсе — и она отвечала бы, не зная о себе
    ничего, хотя карточка с сайта у нас лежит.

    Промпт у соседа подставляет анкету, **только если** не нашлось ни одним из
    двух поисков (`wf_dating_talk_girl_bio`). Здесь проверяется наша половина:
    что она вообще уходит и собирается тем же кодом, что и при заведении агента.
    """

    def test_the_call_sends_the_girl_bio(self):
        """⚠ Сторож передачи: без неё ветка у соседа мертва, а маршрут зелёный."""
        import inspect

        from src.dating import answer

        code = inspect.getsource(answer._agent_ask)

        assert "'girl_bio'" in code, 'анкета девушки перестала уходить агенту'

    def test_it_is_built_by_the_same_maker(self):
        """⚠⚠ Тем же `agent_bio`, что кладёт биографию при заведении агента.

        Два сборщика значили бы, что агент **знает о себе одно**, а отвечает по
        другому: заводили его по одной биографии, а в разговор уходит вторая.
        Разойтись им достаточно одной правки.
        """
        import inspect

        from src.dating import answer

        code = inspect.getsource(answer._agent_ask)

        assert 'agent_bio(' in code, \
            'анкета девушки собирается не общим сборщиком'

    def test_an_empty_card_sends_nothing(self):
        """⚠ Карточки нет — уходит пустая строка, и ветка у соседа не сработает.

        Это правильный исход: `%if girl_bio%` пустую считает ложью и блок прячет.
        Заголовок «Твоя анкета ===» без строк под ним модель прочла бы как факт
        «анкета пуста».
        """
        from src.dating.agent import agent_bio

        assert agent_bio({}) == ''
        assert agent_bio({'name': 'Vera'}) == '', \
            'одно имя — ещё не анкета, фактов из него не добыть'
