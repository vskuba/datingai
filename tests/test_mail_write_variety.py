"""Шаблон рассылки: разнообразие задаёт панель, а не просьба к модели.

Третий раз один и тот же вывод, и здесь цена ошибки выше всего: шаблон рассылки
живёт **сутками** и уходит сотням людей, а круг пишет по три штуки в день. Три
похожих текста в сутки и есть «рассылка робота», которой мы стараемся не быть.

⚠ **Наружу сюита не ходит** (`testing.md`): агент подменяется заглушкой.
Проверяется то, что решаем мы, — жребий, доли и то, какие переменные уезжают, — а
не то, что ответит модель.
"""

import inspect

import pytest

from mysql_.mysql_ import mysql_get_db_async

from src.dating import mail_agent
from src.dating.mail import (MAILING_ANGLES, MAILING_LENGTHS, MAILING_STORIES)
from src.dating.mail_agent import _percent, mail_write

pytestmark = pytest.mark.mail

STAND_SITE = 999_343

# Реестр переменных, как его отдаёт `mail_var_rows`: живая — вопрос, остальные
# подставляются из карточек.
VARS = [
    {'name': 'companion_name', 'source': 'companion', 'title': 'имя', 'uses': {}},
    {'name': 'girl_name', 'source': 'profile', 'title': 'её имя', 'uses': {}},
    {'name': 'question_to_companion', 'source': 'live', 'title': 'вопрос', 'uses': {}},
]


@pytest.fixture
async def share():
    """Ставит долю у сайта стенда и убирает за собой."""
    keys = []

    async def _set(key, value):
        keys.append(key)
        async with mysql_get_db_async() as db:
            await db.execute(
                'UPDATE site SET settings = JSON_SET(COALESCE(settings, JSON_OBJECT()),'
                ' %s, %s) WHERE site_id = %s', (f'$.{key}', str(value), STAND_SITE))
            await db.connection.commit()

    yield _set

    async with mysql_get_db_async() as db:
        for key in keys:
            await db.execute('UPDATE site SET settings = JSON_REMOVE(settings, %s)'
                             ' WHERE site_id = %s', (f'$.{key}', STAND_SITE))
        await db.connection.commit()


@pytest.fixture
def agent(monkeypatch):
    """Подменяет агента и отдаёт то, что ему уехало."""
    seen = {}

    async def _ask(site_id, **kwargs):
        seen.update(kwargs.get('variables') or {})

        return '{"letter": "текст", "letter_title": "Short and direct"}'

    monkeypatch.setattr(mail_agent, '_chat_ask', _ask)
    monkeypatch.setattr(mail_agent, '_agent_id', _one(1))
    monkeypatch.setattr(mail_agent, 'mail_agent_language', _one('en'))
    monkeypatch.setattr(mail_agent, '_mail_avoid', _one([]))

    return seen


class TestTheLotIsOurs:
    """⚠⚠ Приём и длина приходят готовыми, а не просьбой «будь разной»."""

    async def test_the_angle_and_the_length_travel(self, agent, share):
        await mail_write(STAND_SITE, VARS)

        assert agent['mail_angle'] in MAILING_ANGLES
        assert agent['mail_length'] in MAILING_LENGTHS

    async def test_the_story_is_a_topic_not_a_plea(self, agent, share):
        """Выпал рассказ — приходит **тема**, а не «расскажи что-нибудь о себе».

        Дай модели список и попроси выбрать — она возьмёт первый пункт, ровно как
        брала «you're trouble» из всех приёмов флирта.
        """
        await share('mailing_story_percent', 100)
        await mail_write(STAND_SITE, VARS)

        assert agent['mail_story'] in MAILING_STORIES

    async def test_no_story_means_an_empty_section(self, agent, share):
        """⚠ Пустая строка гасит секцию у соседа (`%if mail_story%`).

        «Рассказа не нужно» промптом не говорят — его просто не просят.
        """
        await share('mailing_story_percent', 0)
        await mail_write(STAND_SITE, VARS)

        assert agent['mail_story'] == ''


class TestTheQuestionIsALot:
    """⚠⚠ Вопрос выключается и данными, и прямым запретом — нужно и то и другое.

    Первая версия обходилась данными: живые переменные уходили из списка, и
    только. Живой прогон показал, что этого мало — модель дописала вопрос своими
    словами. Теперь уезжает ещё и флаг, по которому промпт ветвится.
    """

    async def test_without_the_lot_live_variables_are_gone(self, agent, share):
        """Выпало «без вопроса» — живой переменной в списке нет вовсе.

        ⚠ Не «можно не ставить»: разрешение модель читает как приглашение. Нет в
        списке — нечего ставить. Это половина дела; вторая — прямой запрет,
        см. `test_the_flag_travels_too`.

        ⚠ Заодно это дешевле: `question_to_companion` считается у соседа
        **отдельным вызовом LLM на каждого адресата**, а не один раз на шаблон.
        """
        await share('mailing_question_percent', 0)
        await mail_write(STAND_SITE, VARS)

        assert 'question_to_companion' not in agent['mail_var_names']
        assert 'question_to_companion' not in agent['mail_var_must']
        assert agent['mail_var_live'] == ''
        assert 'companion_name' in agent['mail_var_must'], \
            'вместе с живой пропали и обычные переменные'

    async def test_with_the_lot_the_live_variable_is_required(self, agent, share):
        await share('mailing_question_percent', 100)
        await mail_write(STAND_SITE, VARS)

        assert 'question_to_companion' in agent['mail_var_must']
        assert agent['mail_var_live'] == 'question_to_companion'

    async def test_the_flag_travels_too(self, agent, share):
        """⚠⚠ Молчания данными мало — нужен и прямой запрет.

        Первая версия выключала вопрос **молча**: живые переменные уходили из
        списка, и только. Расчёт был — нет в списке, нечего ставить. Сработало
        наполовину: переменной в письме нет, дорогой вызов на адресата не идёт, но
        вопрос модель дописала **сама, обычным текстом**, в трёх шаблонах из пяти.
        Поймано на живом прогоне прода.

        Теперь промпт ветвится по флагу и запрещает вопрос прямо.
        """
        await share('mailing_question_percent', 0)
        await mail_write(STAND_SITE, VARS)

        assert agent['mail_question'] == '', 'флаг не уехал — запрет не включится'

        await share('mailing_question_percent', 100)
        await mail_write(STAND_SITE, VARS)

        assert agent['mail_question'] == '1'


class TestPercent:
    """Доли: в настройке проценты, в коде — доля от единицы."""

    async def test_zero_is_a_decision_not_an_absence(self, share):
        """⚠⚠ Ноль означает «никогда», а не «настройки нет»."""
        await share('mailing_story_percent', 0)

        assert await _percent(STAND_SITE, 'mailing_story_percent', 0.4) == 0.0

    async def test_an_absent_setting_gives_the_default(self, share):
        assert await _percent(STAND_SITE, 'mailing_question_percent', 0.25) == 0.25


class TestTheNameStaysMandatory:
    """⚠⚠ Имя получателя жребием **не** делали, и это решение, а не пропуск.

    У инвайтов и у писем избранным имя — доля: там шаблон несёт готовый текст, и
    обращение по имени в каждом выдаёт рамку. Здесь иначе: обращение приходит
    переменной реестра, и «шаблон обязан нести все переменные» — договор, на
    котором стоит вся рассылка (`mail_var_must`). Сделай имя жребием — половина
    писем перестала бы обращаться к человеку вовсе.
    """

    async def test_companion_name_is_always_required(self, agent, share):
        for percent in (0, 100):
            await share('mailing_question_percent', percent)
            await mail_write(STAND_SITE, VARS)

            assert 'companion_name' in agent['mail_var_must'], percent


class TestTheCeilingBecameARange:
    """⚠ «До четырёх абзацев» — это потолок, а не вилка: модель писала четыре."""

    def test_the_lengths_are_a_range(self):
        assert len(MAILING_LENGTHS) >= 3, 'вилка длины схлопнулась в одно значение'

    def test_the_stories_fit_any_girl(self):
        """⚠⚠ Рассказ обязан подходить **любой** анкете — их сорок.

        «После репетиции» годится одной, «варила кофе» — каждой. Проверка грубая,
        по словам-приметам, и это нарочно: тонкой проверки тут не бывает, а забыть
        правило, дописывая восьмую тему, — дело одной минуты.
        """
        concrete = ('хореограф', 'репетици', 'Киев', 'лаборатор', 'офис')

        for story in MAILING_STORIES:
            assert not any(mark.lower() in story.lower() for mark in concrete), story


class TestAvoid:
    """«Так уже было» — десяток прошлых шаблонов сайта."""

    def test_the_avoid_list_is_asked_for(self):
        code = inspect.getsource(mail_write)

        assert '_mail_avoid(' in code, 'прошлые шаблоны перестали показывать модели'

    def test_it_groups_by_body(self):
        """⚠ `GROUP BY body`, а не строки как есть: одинаковые тексты схлопываются.

        Иначе список оказался бы десятком копий одного шаблона, и модель увидела бы
        один пример вместо десяти.
        """
        code = inspect.getsource(mail_agent._mail_avoid)

        assert 'GROUP BY body' in code


def _one(value):
    """Заглушка, всегда отдающая одно и то же."""
    async def _got(*args, **kwargs):
        return value

    return _got
