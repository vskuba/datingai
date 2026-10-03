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
from src.dating.mail import (MAILING_GREETINGS, MAILING_GREETINGS_NAME,
                             MAILING_STORIES)
from src.dating.mail_agent import mail_write
from src.dating.percent import percent_share

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
    """⚠⚠ Рассказ о себе приходит готовым, а не просьбой «будь разной».

    ⚠ Приёма и длины здесь больше нет: обе оси бросает шаг «Жребий» самого
    `dating_mail_write` (`dice_mail_angle`, `dice_mail_length`), объявления
    входящими сняты — присылка любой роняет весь вызов `400`-м.
    """

    async def test_neither_angle_nor_length_is_sent(self, agent, share):
        """⚠⚠ Имён нет в запросе вовсе — сосед отвергнет весь вызов.

        Сторож считает **имена** присланного, а не значения, и отвергает запрос
        целиком: одна забытая переменная останавливает занятие, а не портит его.
        """
        await mail_write(STAND_SITE, VARS)

        assert 'mail_angle' not in agent, \
            'приём уезжает соседу — он ответит 400 и шаблон не сочинится'
        assert 'mail_length' not in agent, \
            'длина уезжает соседу — он ответит 400 и шаблон не сочинится'

    async def test_the_story_is_a_topic_not_a_plea(self, agent, share):
        """Выпал рассказ — приходит **тема**, а не «расскажи что-нибудь о себе».

        Дай модели список и попроси выбрать — она возьмёт первый пункт, ровно как
        брала «you're trouble» из всех приёмов флирта.
        """
        await share('mailing_story_percent', 100)
        await mail_write(STAND_SITE, VARS)

        assert agent['mail_story'] in MAILING_STORIES

    async def test_no_story_means_an_empty_section(self, agent, share):
        """⚠ Пустая строка гасит секцию у соседа (`[<if mail_story>]`).

        «Рассказа не нужно» промптом не говорят — его просто не просят.
        """
        await share('mailing_story_percent', 0)
        await mail_write(STAND_SITE, VARS)

        assert agent['mail_story'] == ''


class TestTheQuestionLivesInTheRegistry:
    """⚠⚠ Живой вопрос шаблон несёт **всегда** — жребий стоит не здесь.

    Сперва бросали его тут: выпало — шаблон получал метку, и дальше каждое письмо
    по нему платило вызовом LLM на адресата; не выпало — не платило ни одно. Доля
    означала «в скольких шаблонах», а человек читал её как «в скольких письмах» —
    и читал правильно: настройка про деньги, а деньги тратятся на письмо.

    Решение переехало в сборку письма (`test_mailing_question_chance.py`), а здесь
    реестр снова решает всё сам.
    """

    async def test_the_live_variable_is_always_asked_for(self, agent, share):
        for percent in (0, 100):
            await share('mailing_question_percent', percent)
            await mail_write(STAND_SITE, VARS)

            assert 'question_to_companion' in agent['mail_var_must'], percent
            assert agent['mail_var_live'] == 'question_to_companion', percent

    async def test_no_flag_travels(self, agent, share):
        """⚠ Флага у соседа больше нет: промпт не ветвится, правило прямое."""
        await mail_write(STAND_SITE, VARS)

        assert 'mail_question' not in agent, \
            'флаг снова уезжает — значит жребий вернулся в шаблон'


class TestTheGreetingIsALotToo:
    """⚠⚠ Зачин — третья ось, и заведена по живому пулу.

    Восемь шаблонов подряд начинались словом «Hi» с новой строки: приём разный,
    длина разная, рассказ разный — а первое, что видит человек, одно и то же во
    всех письмах сайта. Модель зачин не выбирает: берёт первый попавшийся и
    повторяет, пока её не попросят иначе.
    """

    async def test_the_opening_is_drawn(self, agent, share):
        """⚠ Списков зачинов **два**, и выбирает между ними жребий имени.

        Приёмы с именем («приветствие с именем», «имя в первой фразе») годятся
        только когда метка в шаблоне есть. Достанься такой приём безымянному
        шаблону — модель выбрала бы между просьбой панели и отсутствием метки, а
        она, как показала живая работа, выбирает просьбу и пишет имя словами.
        """
        await share('mailing_no_greeting_percent', 0)
        await mail_write(STAND_SITE, VARS)

        assert agent['mail_greeting'] in MAILING_GREETINGS + MAILING_GREETINGS_NAME

    async def test_the_opening_matches_the_name_dice(self, agent, share):
        """⚠⚠ Приём обязан соответствовать жребию, а не просто быть из списка."""
        await share('mailing_no_greeting_percent', 0)

        await share('mailing_name_percent', 100)
        await mail_write(STAND_SITE, VARS)
        assert agent['mail_greeting'] in MAILING_GREETINGS_NAME, \
            'имя в шаблоне есть, а зачин его не зовёт'

        await share('mailing_name_percent', 0)
        await mail_write(STAND_SITE, VARS)
        assert agent['mail_greeting'] in MAILING_GREETINGS, \
            'зачин просит имя, которого в шаблоне нет'

    async def test_no_greeting_is_an_empty_section(self, agent, share):
        """Пусто — промпт гасит секцию и запрещает приветствие прямо.

        ⚠ Запрет формулируется **прямо**, а не молчанием: на вопросе мы уже
        обожглись — секция исчезла, а модель дописала вопрос сама.
        """
        await share('mailing_no_greeting_percent', 100)
        await mail_write(STAND_SITE, VARS)

        assert agent['mail_greeting'] == ''

    def test_the_openings_describe_a_way_not_a_word(self):
        """⚠ Описываем **приём**, а не текст.

        Дай образец («начни с Hey») — получишь «Hey» во всех письмах, где выпал
        этот пункт, то есть ту же беду в профиль.
        """
        for one in MAILING_GREETINGS:
            assert 'Hi' not in one and 'Hey' not in one and 'Hello' not in one, one


class TestPercent:
    """Доли: в настройке проценты, в коде — доля от единицы."""

    async def test_zero_is_a_decision_not_an_absence(self, share):
        """⚠⚠ Ноль означает «никогда», а не «настройки нет»."""
        await share('mailing_story_percent', 0)

        assert await percent_share(STAND_SITE, 'mailing_story_percent', 0.4) == 0.0

    async def test_an_absent_setting_gives_the_default(self, share):
        assert await percent_share(STAND_SITE, 'mailing_question_percent', 0.25) == 0.25


class TestTheNameIsMandatoryUntilAskedOtherwise:
    """⚠⚠ Имя получателя требуется от шаблона — пока долю не опустили.

    Здесь стояло «имя жребием не делали, и это решение»: обращение приходит
    переменной реестра, а «шаблон обязан нести все переменные» — договор, на
    котором стоит вся рассылка (`mail_var_must`). Довод верен и никуда не делся:
    сделай имя жребием без спроса — половина писем перестала бы обращаться к
    человеку вовсе.

    Теперь долей **можно** управлять (`mailing_name_percent`), но умолчание —
    сто процентов, то есть ровно прежнее поведение. Опускает её человек, а не мы
    за него; прежний довод и есть причина, по которой умолчание такое.

    ⚠ Проверка ниже не изменилась: при умолчании имя обязано быть обязательным.
    Стань она когда-нибудь красной без правки настройки — значит доля поехала
    сама, и это беда, а не новая возможность.
    """

    async def test_companion_name_is_required_by_default(self, agent, share):
        await mail_write(STAND_SITE, VARS)

        assert 'companion_name' in agent['mail_var_must']


class TestTheStoriesStayOurs:
    """⚠ Рассказ о себе — последняя ось, которую выбираем мы.

    Приём и длина уехали в шаг «Жребий» самого `dating_mail_write`, и число их
    граней стережёт теперь сюита соседа: оно видно только там, где брошен жребий.
    """

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
