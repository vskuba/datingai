"""Письмо избранному собирается шаблоном: один вызов LLM на весь заход.

Прежде письмо сочинял агент **на каждую пару**: пул в полсотни писем стоил
полусотни вызовов LLM. Теперь вызов один, а карточки собирает подстановка — тот
же размен, что у инвайтов (`test_invite.py`).

⚠ **Наружу сюита не ходит** (`testing.md`): агент подменяется заглушкой. Здесь
проверяется то, что решаем мы, — жребий, доли, подстановка и уникальность
адресата, — а не то, что ответит модель.

⚠⚠ **Главное, что здесь стережётся, — уникальность адресата.** Мужчина бывает
избранным у двух наших девушек; с общим шаблоном он получил бы два одинаковых
письма от двух разных женщин, то есть увидел бы агентство. Это не придирка к
стилю: ровно на этом ловили инвайты двух Диан, #849 и #850.
"""

import inspect
import uuid

import pytest

from mysql_.mysql_ import mysql_get_db_async

from src.dating import favorite_mail
from src.dating.favorite_mail import (FAVORITE_MAIL_ANGLES,
                                      FAVORITE_MAIL_LENGTHS,
                                      FAVORITE_MAIL_STORIES,
                                      _fill, _template)
from src.dating.percent import percent_share
from src.dating.mail_agent import INVITE_SLOT_MAN, _favorite

pytestmark = pytest.mark.mail

STAND_SITE = 999_343


class TestFill:
    """Подстановка имени в шаблон."""

    def test_the_first_word_only(self):
        """⚠ В карточке «Brian Ritter», а обращаются «Brian».

        Фамилия в письме звучит как обращение из документа. То же правило, что у
        инвайтов (`invite._fill`), и оно должно быть одним: человек получает
        письма из обоих пулов.
        """
        got = _fill(f'Hi [[{INVITE_SLOT_MAN}]], as promised.',
                    {'name': 'Brian Ritter'})

        assert got == 'Hi Brian, as promised.'

    def test_a_mark_without_a_name_is_cut_out(self):
        """⚠ Метка, для которой имени нет, уехала бы человеку прямо в скобках."""
        got = _fill(f'Hi [[{INVITE_SLOT_MAN}]], as promised.', {'name': ''})

        assert '[[' not in got and ']]' not in got, got

    def test_a_template_without_marks_passes_through(self):
        """В девяти письмах из десяти метки нет вовсе — это обычный случай."""
        assert _fill('Just thinking about you.', {'name': 'Brian'}) \
            == 'Just thinking about you.'


class TestParagraphsSurvive:
    """⚠⚠ Абзацы письма сохраняются — вот здесь и проходит разница с инвайтом."""

    def test_paragraphs_are_kept(self):
        """Инвайт сводится в строку, письмо — нет: его открывают в почте."""
        got = _favorite('VERDICT: yes\nWHY: \nTEXT: Первый абзац.\n\nВторой абзац.')

        assert got == 'Первый абзац.\n\nВторой абзац.'

    def test_ragged_blank_lines_are_evened_out(self):
        """Модель ставит то один перевод, то три; сайт показывает их как есть."""
        got = _favorite('VERDICT: yes\nWHY: \nTEXT: Раз.\n\n\n\n  Два.  ')

        assert got == 'Раз.\n\nДва.'

    def test_a_judge_refusal_kills_the_template(self):
        """⚠ Вердикт судьи сильнее наших проверок: он про смысл, они про форму.

        Забракованный шаблон ушёл бы **всему пулу**, а не одному человеку.
        """
        assert _favorite('VERDICT: no\nWHY: выдумано прошлое\nTEXT: Помнишь Прагу?') == ''

    def test_a_model_refusal_is_dropped(self):
        """Отказ выглядит текстом и уехал бы десяткам людей вместе с извинением."""
        assert _favorite("I can't help with that, but here's a neutral note: hi") == ''

    def test_an_invented_mark_drops_the_whole_template(self):
        """⚠ `[[his_city]]` подставить нечем — она уехала бы прямо в скобках."""
        assert _favorite('VERDICT: yes\nWHY: \nTEXT: Hi from [[his_city]]') == ''


@pytest.fixture
async def share():
    """Ставит долю у сайта стенда и убирает за собой.

    ⚠ Прямо в `site.settings`, а не ручкой: вопрос здесь про чтение доли, и
    поход через HTTP проверял бы заодно разбор формы — то, что уже проверено
    своим тестом.
    """
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


class TestPercent:
    """Доли: в настройке проценты, в коде — доля от единицы."""

    async def test_an_absent_setting_gives_the_default(self):
        assert await percent_share(STAND_SITE, f'net-{uuid.uuid4().hex[:6]}', 0.4) == 0.4

    async def test_zero_is_a_decision_not_an_absence(self, share):
        """⚠⚠ Ноль означает «никогда», а не «настройки нет».

        Отличать обязательно: проверь код истинность вместо пустоты — и ноль
        молча превратился бы в умолчание, то есть в «каждое четвёртое».
        """
        await share('favorite_mail_question_percent', 0)

        assert await percent_share(STAND_SITE, 'favorite_mail_question_percent', 0.25) == 0.0

    async def test_percents_become_a_share(self, share):
        await share('favorite_mail_story_percent', 40)

        assert await percent_share(STAND_SITE, 'favorite_mail_story_percent', 0.1) == 0.4

    async def test_nonsense_falls_back(self, share):
        """Не-число не роняет заход: пул важнее точности доли."""
        await share('favorite_mail_name_percent', 'пятнадцать')

        assert await percent_share(STAND_SITE, 'favorite_mail_name_percent', 0.1) == 0.1


class TestTheLotIsOurs:
    """⚠⚠ Жребий бросает панель, а не модель.

    «Вставляй имя изредка» модель понимает как «вставляй всегда», «будь разной» —
    как «пиши одинаково». Вероятность у неё не считается: она стилистическая.
    Проверяем, что решение приходит к агенту готовым.
    """

    async def test_the_agent_gets_a_decision_not_a_probability(self, monkeypatch):
        seen = {}

        async def _agent(site_id, language, **kwargs):
            seen.update(kwargs)

            return 'письмо'

        monkeypatch.setattr(favorite_mail, 'favorite_mail_template', _agent)
        monkeypatch.setattr(favorite_mail, '_avoid', _none([]))
        monkeypatch.setattr(favorite_mail, '_country', _none(''))

        await _template(STAND_SITE, 'en')

        assert isinstance(seen['man_slot'], bool)
        assert isinstance(seen['question'], bool)
        assert seen['angle'] in FAVORITE_MAIL_ANGLES
        assert seen['length'] in FAVORITE_MAIL_LENGTHS
        assert seen['story'] == '' or seen['story'] in FAVORITE_MAIL_STORIES

    async def test_time_travels_only_with_a_story(self, monkeypatch):
        """⚠ «Как прошёл день» без часа выходит про несуществующий день.

        А во всех остальных случаях время молчит: инвайты показали, что модель
        поминает его почти везде, стоит ей его дать — 94 письма из 202 за сутки.
        """
        seen = []

        async def _agent(site_id, language, **kwargs):
            seen.append((bool(kwargs['story']), kwargs['with_time']))

            return 'письмо'

        monkeypatch.setattr(favorite_mail, 'favorite_mail_template', _agent)
        monkeypatch.setattr(favorite_mail, '_avoid', _none([]))
        monkeypatch.setattr(favorite_mail, '_country', _none(''))

        for _ in range(40):
            await _template(STAND_SITE, 'en')

        assert all(story == with_time for story, with_time in seen), seen


class TestOneCallPerBatch:
    """⚠⚠ Вызов LLM один на заход, и спрашивается он лениво."""

    def test_the_agent_is_asked_once(self):
        """Шаблон запрашивается **вне** тела цикла по парам.

        Проверка по исходнику: поставить это заглушкой значило бы завести десяток
        пар на стенде и ждать их отбора, а вопрос здесь ровно один — не вернулся
        ли вызов агента внутрь цикла, откуда его и убирали.
        """
        code = inspect.getsource(favorite_mail.favorite_mail_make)

        assert code.count('await _template(') == 1, \
            'шаблон снова просят чаще одного раза за заход'
        assert 'if template is None:' in code, \
            'шаблон перестал спрашиваться лениво — заход без единой пары платит зря'

    def test_a_silent_agent_stops_the_batch(self):
        """Агент промолчал — дальше по списку будет тот же шаблон, то есть ничего."""
        code = inspect.getsource(favorite_mail.favorite_mail_make)

        assert 'FAVORITE_MAIL_SKIP_SILENT' in code


class TestOneLetterPerMan:
    """⚠⚠ Один человек — одно письмо за заход."""

    def test_the_addressee_is_remembered(self):
        """Мужчина бывает избранным у двух девушек, и отбор вернёт обе пары.

        С общим шаблоном он получил бы два одинаковых письма от двух разных
        женщин. Правило живёт набором `written` в самом заходе.
        """
        code = inspect.getsource(favorite_mail.favorite_mail_make)

        assert 'if uid in written:' in code, 'адресат перестал быть уникальным'
        assert 'written.add(uid)' in code, 'записанному адресату не ставится метка'


def _none(value):
    """Заглушка на чтение настройки: всегда одно и то же."""
    async def _got(*args, **kwargs):
        return value

    return _got
