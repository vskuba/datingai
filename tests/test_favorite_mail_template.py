"""Письмо избранному пишет агент чата — тому, кого знает.

## ⚠⚠ Что здесь изменилось и почему файл переписан

Прежде письмо сочинял свой workflow (`dating_favorite_mail_write`) **одним
шаблоном на весь заход**: панель раскладывала его по парам, подставляя имя. Этот
файл проверял ту механику — подстановку, жребий на пять осей, «один вызов на
пул».

Механики больше нет, и вот почему. Избранный — человек, **с которым уже
говорили**: звёздочку ставят тому, кто отвечал. А шаблон не знал о нём ничего;
промпт того занятия прямо запрещал ссылаться на прошлое («переписки перед тобой
нет, и ты не знаешь о ней НИЧЕГО»). Живому человеку письмо читалось ровно так,
как есть: женщина, с которой он переписывался, пишет ему как незнакомому.

Теперь письмо пишет `answer_mail_write` через **workflow чата** (`dating_talk`):
он видит переписку пары, ищет факты о ней, о нём и о договорённостях.

## ⚠ Цена размена, и она названа прямо

| | было | стало |
|---|---|---|
| вызовов на заход в 10 писем | 1 | 10, и каждый дороже |

Считано по живому проду перед правкой: писем уходит 1–23 в сутки, то есть
десятки вызовов в день, а не тысячи. Персональное письмо нельзя написать, не
прочитав переписку.

⚠ Проверки доли (`percent_share`) остались: они про общий механизм процентов и
живут своей жизнью. Остальное здесь — про новый способ.
"""

import inspect

import pytest

from src.dating import answer, favorite_mail
from src.dating.percent import percent_share

pytestmark = pytest.mark.mail

STAND_SITE = 999_343


@pytest.fixture
async def share():
    """Кладёт долю в настройки стенда и убирает её за собой."""
    from mysql_.mysql_ import mysql_get_db_async

    keys = []

    async def _share(key: str, value) -> None:
        keys.append(key)
        async with mysql_get_db_async() as db:
            await db.execute(
                'INSERT INTO site (site_id, settings) VALUES (%s, JSON_OBJECT(%s, %s))'
                ' ON DUPLICATE KEY UPDATE'
                ' settings = JSON_SET(COALESCE(settings, JSON_OBJECT()),'
                "                     CONCAT('$.', %s), %s)",
                (STAND_SITE, key, str(value), key, str(value)))
            await db.connection.commit()

    yield _share

    async with mysql_get_db_async() as db:
        for key in keys:
            await db.execute(
                "UPDATE site SET settings = JSON_REMOVE(settings, CONCAT('$.', %s))"
                ' WHERE site_id = %s', (key, STAND_SITE))
        await db.connection.commit()


class TestTheLetterKnowsTheMan:
    """⚠⚠ Главное: письмо пишется паре, а не всему заходу."""

    def test_the_pool_asks_the_agent_per_pair(self):
        """Вызов стоит **внутри** цикла по парам, а не до него.

        Вынеси его наружу — вернулся бы шаблон: одно письмо на всех, только
        дорогой дорогой.
        """
        code = inspect.getsource(favorite_mail.favorite_mail_make)

        assert 'answer_mail_write' in code, \
            'набор пула перестал звать агента письма'
        assert 'for pair in pairs' in code, \
            'цикл по парам пропал — письмо снова одно на заход'

        loop = code.split('for pair in pairs', 1)[1]
        assert 'answer_mail_write' in loop, \
            'агент зовётся до цикла — это снова шаблон на весь заход'

    def test_the_letter_gets_the_conversation(self):
        """⚠ Переписка пары уезжает агенту: ради неё всё и затевалось."""
        code = inspect.getsource(answer.answer_mail_write)

        assert 'talk_history_text' in code, \
            'письмо пишется без переписки — агент снова не знает собеседника'

    def test_the_letter_is_not_an_initiative_reply(self):
        """⚠⚠ Флаг `mail_mode`, а не `make_initiative`.

        У второго свой смысл: «собеседник молчит, пишем первыми **репликой в
        чат**». Попади письмо в ту ветку промпта, оно получило бы «до 25 слов,
        как в мессенджере» — то есть перестало бы быть письмом.
        """
        import ast
        import textwrap

        code = inspect.getsource(answer.answer_mail_write)

        assert 'mail=True' in code, 'письмо не помечено как письмо'

        # ⚠ Разбираем **вызов**, а не ищем подстроку: слово `initiative` стоит
        # рядом в оговорке, объясняющей, почему флаг другой, — и поиск текстом
        # ловил его. Первая версия теста упала ровно на этом.
        tree = ast.parse(textwrap.dedent(code))
        named = {kw.arg for node in ast.walk(tree)
                 if isinstance(node, ast.Call)
                 for kw in node.keywords if kw.arg}

        assert 'initiative' not in named, \
            'письмо уходит веткой инициативы — получится реплика в чат'

    def test_silence_is_not_allowed_for_a_letter(self):
        """⚠ Молчать нельзя: заход набора стоил отбора и вызова модели.

        В чате молчание — законный исход (реальная девушка не отвечает на каждое
        сообщение). Здесь письмо мы решили написать сами.
        """
        code = inspect.getsource(answer.answer_mail_write)

        assert 'anyway=True' in code, \
            'агенту письма разрешено промолчать — заход потратится впустую'


class TestTheGirlProfileTravels:
    """⚠ Агенту нужна анкета девушки: её имя, ключ фактов, биография.

    Прежде письмо писал шаблон, не знавший ни её, ни его, — и полей этих не
    требовалось вовсе. Теперь без них агент назовётся умолчанием, а факты всех
    девушек сложатся в один документ.
    """

    def test_the_query_takes_the_profile(self):
        code = inspect.getsource(favorite_mail._quiet)

        for field in ('girl_id', 'girl_site', 'girl_login', 'girl_card'):
            assert field in code, f'в выборке пар нет поля {field}'

    def test_the_row_becomes_a_profile(self):
        """⚠ Анкета собирается в том же виде, что ждёт вызов агента."""
        row = {'profile_id': 7, 'uid': 'u1', 'name': 'Gerard',
               'girl_id': 7, 'girl_site': 343, 'girl_login': 'v@e.com',
               'girl_card': '{"name": "Vera"}'}

        pair = favorite_mail._pair_row(row)

        assert pair['profile']['id'] == 7
        assert pair['profile']['site_id'] == 343
        assert pair['profile']['login'] == 'v@e.com'
        assert pair['profile']['profile'] == '{"name": "Vera"}'

    def test_the_service_fields_do_not_leak(self):
        """⚠ Поля `girl_*` — способ довезти анкету, а не часть пары.

        Останься они в строке, их увидел бы и показ пула, и запись в базу.
        """
        pair = favorite_mail._pair_row(
            {'profile_id': 7, 'uid': 'u1', 'girl_id': 7, 'girl_site': 343,
             'girl_login': 'x', 'girl_card': None})

        for field in ('girl_id', 'girl_site', 'girl_login', 'girl_card'):
            assert field not in pair, f'служебное поле {field} осталось в паре'


class TestFailuresAreToldApart:
    """⚠⚠ Отказ агента и его молчание — разные исходы, и это новое.

    У шаблона молчание значило «дальше будет то же самое», и заход обрывался. У
    письма на пару молчание — про **эту** пару: у следующей своя переписка и свой
    промпт.
    """

    def test_a_refusal_stops_the_batch(self):
        code = inspect.getsource(favorite_mail.favorite_mail_make)

        assert 'stopped = error' in code, \
            'отказ агента больше не обрывает заход — следующие пары упрутся в то же'

    def test_silence_skips_one_pair(self):
        code = inspect.getsource(favorite_mail.favorite_mail_make)
        loop = code.split('for pair in pairs', 1)[1]

        assert 'if not body:' in loop and 'continue' in loop, \
            'молчание агента обрывает заход, хотя оно про одну пару'


class TestTheAgentIsItsOwn:
    """⚠ Агент писем свой, хотя workflow общий с чатом.

    Различает их системный промпт: тот про реплики в мессенджере, этот про
    письма. Возьми мы агента чата — письмо вышло бы в две строки, и отдельный
    агент стал бы незачем.
    """

    def test_the_agent_is_the_mail_one(self):
        code = inspect.getsource(favorite_mail._agent)

        assert 'FAVORITE_MAIL_NAME' in code, \
            'письма пишет не свой агент'

    def test_it_is_asked_once_per_batch(self):
        """⚠ Номер агента — один на сайт, и спрашивается он лениво.

        Это поход к соседу; заход, где не прошла ни одна пара, не должен идти
        туда вовсе.
        """
        code = inspect.getsource(favorite_mail.favorite_mail_make)

        assert 'if not agent_id:' in code, \
            'агент спрашивается на каждое письмо — лишние походы к соседу'


class TestPercent:
    """Доли: в настройке проценты, в коде — доля от единицы.

    ⚠ Переехало сюда из проверок шаблона и остаётся в силе: механизм процентов
    общий на инвайты, рассылку и письма.
    """

    async def test_an_absent_setting_gives_the_default(self):
        import uuid

        assert await percent_share(
            STAND_SITE, f'net-{uuid.uuid4().hex[:6]}', 0.4) == 0.4

    async def test_zero_is_a_decision_not_an_absence(self, share):
        """⚠⚠ Ноль означает «никогда», а не «настройки нет».

        Отличать обязательно: проверь код истинность вместо пустоты — и ноль
        молча превратился бы в умолчание, то есть в «каждое четвёртое».
        """
        await share('favorite_mail_question_percent', 0)

        assert await percent_share(
            STAND_SITE, 'favorite_mail_question_percent', 0.25) == 0.0

    async def test_percents_become_a_share(self, share):
        await share('favorite_mail_story_percent', 40)

        assert await percent_share(
            STAND_SITE, 'favorite_mail_story_percent', 0.1) == 0.4

    async def test_nonsense_falls_back(self, share):
        """Не-число не роняет заход: пул важнее точности доли."""
        await share('favorite_mail_name_percent', 'пятнадцать')

        assert await percent_share(
            STAND_SITE, 'favorite_mail_name_percent', 0.1) == 0.1
