"""Универсальный агент девушки: один на все анкеты, имя приходит переменной.

⚠ **Смена подхода у соседа.** Прежде у каждой анкеты был свой агент
(`natalia_1430119`) со своей копией того же промпта: тринадцать анкет —
тринадцать записей, и правка поведения требовала обойти их все. Теперь отвечает
`dating_girl_universal` («Девушка»), а кто именно говорит — задаётся переменными
вызова.

⚠ **Две переменные обязательны, и вторая опаснее первой.** `girl_name`
подставляется в промпт; без неё все девушки назовутся значением по умолчанию у
агента. `girl_id` — ключ фактов (`document_id` в Qdrant); без него факты всех
анкет лягут в один документ, и Diana начнёт отвечать историями Natalia.

Сосед здесь поддельный: сюита не ходит в чужой проект. Живьём проверено на
Ladadate — один агент, два вызова: «I'm diana» и «i'm ekaterina».
"""

import pytest

from src.dating.agent import (AGENT_SETTING, AGENT_UNIVERSAL,
                                     agent_id, agent_key,
                                     agent_site_uid, agent_vars)

pytestmark = pytest.mark.profile

SITE = 999_784


class TestVars:
    """Чем агент узнаёт, кто он сейчас: номер, имя для текста, имя для ключа.

    ## ⚠⚠ Три величины, и каждая отвечает на свой вопрос

        girl_id     599939   номер анкеты **на сайте** — ключ фактов
        girl_title  Elena    как её видит собеседник — уходит в промпт
        girl_name   elena    то же имя техническим видом — вторая часть ключа

    Слепи любые две, и получишь одну из двух бед: агент обращается номером либо
    ключует факты именем, которое на сайте носят десятки людей.
    """

    def test_the_three_travel_apart(self):
        got = agent_vars({'login': 'zliznukolenavlad89@gmail.com', 'id': 43},
                                {'name': 'Elena', 'id': '599939'})

        assert got == {'girl_id': '599939',
                       'girl_title': 'Elena',
                       'girl_name': 'elena'}

    def test_the_key_is_the_site_number(self):
        """⚠⚠⚠ Номер сайта, а не цифры логина.

        Прежде ключ собирался как `''.join(ch for ch in login if ch.isdigit())`, и
        это было случайное число: у `elena2795kirichenko01@bigmir.net` выходило
        `279501` — склейка двух кусков.

        ⚠ Самый большой документ фактов на проде (`643694_diana`, 23 факта) собран
        из номера сайта — то есть это **восстановление исходного замысла**, а не
        новая выдумка.
        """
        got = agent_vars({'login': 'elena2795kirichenko01@bigmir.net', 'id': 44},
                                {'name': 'Elena', 'id': '486221'})

        assert got['girl_id'] == '486221'

    def test_a_login_without_digits_no_longer_breaks_the_key(self):
        """⚠⚠ Живой случай: у анкеты Solomia в логине цифр нет вовсе.

        Прежний код отдавал в этом случае **весь логин**, то есть ключом фактов
        становился адрес почты. Теперь номер есть всегда.
        """
        got = agent_vars({'login': 'solomia@bigmir.net', 'id': 50},
                                {'name': 'Solomia', 'id': '924237'})

        assert got['girl_id'] == '924237'

    def test_two_namesakes_keep_their_facts_apart(self):
        """⚠ Имена повторяются: на проде две Diana, две Elena, две Natalia."""
        first = agent_vars({'login': 'a@mail.test', 'id': 46},
                                  {'name': 'Natalia', 'id': '363331'})
        second = agent_vars({'login': 'b@mail.test', 'id': 47},
                                   {'name': 'Natalia', 'id': '201992'})

        assert first['girl_id'] != second['girl_id']
        assert first['girl_name'] == second['girl_name'] == 'natalia'

    def test_no_site_number_falls_back_to_ours(self):
        """⚠ Карточка ещё не снята — берём свой номер анкеты.

        Пустой `girl_id` слил бы факты всех девушек в один документ, и это хуже
        любого запасного варианта: наш номер тоже уникален и не меняется.
        """
        got = agent_vars({'login': 'diana9021@mail.test', 'id': 40},
                                {'name': 'Diana'})

        assert got['girl_id'] == '40'

    def test_no_name_leaves_the_number(self):
        """⚠ Пустое имя не значит пустую подстановку.

        Уйди в промпт пустота — агент назвался бы никак, а собеседник получил бы
        «You are a girl .». Номер хуже читается, но он есть всегда.
        """
        got = agent_vars({'login': 'diana9021@mail.test', 'id': 40},
                                {'id': '643694'})

        assert got['girl_title'] == got['girl_name'] == '643694'


class TestTheTechnicalName:
    """⚠⚠ `girl_name` — устойчивый идентификатор, а не текст для человека."""

    @pytest.mark.parametrize('name, want', [
        ('Elena', 'elena'),
        ('Diana', 'diana'),
        # ⚠ Кириллица — `i18n_translit_cyr`, а не КМУ: та дала бы `Evhenyia`
        # (украинское «г» → `h`) и русских букв не покрывает вовсе.
        ('Евгения', 'evgeniya'),
        ('Юлия', 'yuliya'),
        ('Соломія', 'solomiya'),
        # ⚠ Пробел становится подчёркиванием, а не исчезает: ключ читают глазами.
        ('Anna Maria', 'anna_maria'),
        # ⚠⚠ Знаки препинания выбрасываются: ключ уезжает в `document_id` Qdrant
        # и в фильтры поиска.
        ("O'Hara", 'ohara'),
        ('  Elena  ', 'elena'),
        ('', ''),
    ])
    def test_it_becomes_a_key(self, name, want):
        assert agent_key(name) == want

    def test_it_never_leaves_anything_but_a_key(self):
        """⚠ Сторож на всё разом: что бы ни пришло, на выходе только латиница,
        цифры и подчёркивание."""
        import re

        for one in ('Ёлка', 'Ann@ Mari#a', '寿司', 'Zoë', '!!!', '123'):
            got = agent_key(one)

            assert re.fullmatch(r'[a-z0-9_]*', got), f'{one!r} → {got!r}'


class TestTheNumberComesFromTheColumn:
    """⚠⚠ Номер сайта берётся колонкой `profile.profile_id`, а не из JSON.

    Колонку завела миграция `20260927130000`; JSON остаётся запасной ступенью, и
    убирать его рано — словарь анкеты собирают шесть разных запросов.
    """

    def test_the_column_wins(self):
        got = agent_site_uid({'id': 43, 'profile_id': '599939',
                                     'profile': '{"id": "000"}'})

        assert got == '599939', 'колонка проиграла JSON-у'

    def test_the_json_is_the_fallback(self):
        """⚠ Запасная ступень: запрос, забывший колонку, не должен обнулять номер."""
        got = agent_site_uid({'id': 43, 'profile': '{"id": "599939"}'})

        assert got == '599939'

    def test_the_card_counts_too(self):
        """⚠ Карточку читаем и разобранной: половина зовущих парсит JSON заранее."""
        got = agent_site_uid({'id': 43}, {'id': '599939'})

        assert got == '599939'

    def test_our_own_number_is_the_last_resort(self):
        """⚠⚠⚠ Пусто не возвращаем никогда: пустой `girl_id` сливает факты всех
        девушек в один документ, и беда тихая — ответы осмысленны, просто чужие."""
        assert agent_site_uid({'id': 43}) == '43'
        assert agent_site_uid({}) == ''  # анкеты нет вовсе — сказать нечего


class TestEveryLoaderTakesTheColumn:
    """⚠⚠⚠ Сторож на **все** запросы, чьи строки доезжают до `agent_vars`.

    Словарь анкеты собирают шесть разных мест, и половина перечисляет колонки
    поимённо. Забудь одно — `girl_id` скатится на запасную ступень, и заметить это
    можно будет только по перемешавшимся фактам.

    ⚠ Проверяется исходник, а не поведение: чтобы поймать это прогоном, нужен живой
    сокет, живой круг писем и живая инициатива разом.
    """

    @pytest.mark.parametrize('where, what', [
        # Держатель сокетов: главный путь автоответа.
        ('src/dating/chat_hold.py', 'p.profile_id'),
        ('src/dating/chat_hold.py', "'profile_id': row['profile_id']"),
        # Ответы на входящие письма.
        ('src/dating/mail_reply.py', 'SELECT id, site_id, login, profile_id'),
        # Инициатива: заговорить первой.
        ('src/dating/initiative.py', 'SELECT id, site_id, login, profile_id'),
        # Письма избранным: строка пула собирается своим именем колонки.
        ('src/dating/favorite_mail.py', 'p.profile_id AS girl_uid'),
        ('src/dating/favorite_mail.py', "'profile_id': pair.pop('girl_uid'"),
    ])
    def test_the_loader_selects_it(self, where, what):
        import pathlib

        text = (pathlib.Path(__file__).resolve().parents[1] / where
                ).read_text(encoding='utf-8')

        assert what in text, f'{where}: нет «{what}» — номер поедет запасной ступенью'


class TestWhichAgentAnswers:
    """Кого зовём отвечать."""

    async def test_universal_agent_is_taken_first(self, rhapsody):
        """Все анкеты ходят к одному агенту — ради этого всё и затевалось."""
        rhapsody.agents = [{'id': 1663, 'name': AGENT_UNIVERSAL}]

        got = await agent_id({'id': 1, 'site_id': SITE, 'login': 'a@b.test'},
                                    {'name': 'Diana'})

        assert got == 1663

    async def test_personal_agent_is_the_fallback(self, rhapsody):
        """⚠ Прежний агент анкеты остаётся запасным путём.

        Агенты, заведённые до перехода, живы и со своими фактами. Молча перестать
        их звать значило бы потерять историю без единого следа — а универсального
        у соседа может не оказаться вовсе (новая установка, откат).
        """
        rhapsody.agents = [{'id': 726, 'name': 'diana_9021'}]

        got = await agent_id(
            {'id': 1, 'site_id': SITE, 'login': 'bogiladiana9021@bigmir.net'},
            {'name': 'Diana'})

        assert got == 726

    async def test_nobody_found_is_zero_not_a_crash(self, rhapsody):
        """Ни того ни другого — ноль. Круг автоответа пропустит анкету и скажет
        об этом в журнал; падать здесь нельзя, обход идёт по всем анкетам."""
        rhapsody.agents = []

        got = await agent_id({'id': 1, 'site_id': SITE, 'login': 'a@b.test'},
                                    {'name': 'Diana'})

        assert got == 0


class TestSiteMayChooseAnother:
    """Настройка сайта сильнее умолчания."""

    async def test_default_is_the_universal_one(self, rhapsody):
        from src.dating.agent import agent_universal_name

        assert await agent_universal_name(SITE) == AGENT_UNIVERSAL

    async def test_site_setting_wins(self, auth_client, rhapsody):
        """⚠ Настройка, а не константа: подход новый, и вернуться к прежним
        агентам должно быть можно правкой строки, а не выкладкой."""
        from src.dating.agent import agent_universal_name

        before = ((await auth_client.get(f'/api/dating/site/{SITE}')).json()
                  ['result']['settings'])
        try:
            await auth_client.put(f'/api/dating/site/{SITE}',
                                  json={'settings': {AGENT_SETTING: 'свой_агент'}})

            assert await agent_universal_name(SITE) == 'свой_агент'
        finally:
            await auth_client.put(f'/api/dating/site/{SITE}',
                                  json={'settings': before})
