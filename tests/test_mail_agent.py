"""Агент, заполняющий переменные писем: сборка промпта и разбор ответа.

⚠ **К соседу отсюда не ходим.** Сюита наружу не ходит вовсе (`testing.md`), а
живой вызов LLM стоил бы секунд и денег на каждом прогоне. Проверяется то, что и
ломается: как собирается промпт и как разбирается ответ — обе стороны чистые
функции над строками.

Живьём агент проверен вручную: на анкете Diana он вернул `girl_name = Diana`,
`girl_city = Kyiv`, `girl_job = choreographer` за 3.7 секунды.
"""

import uuid

import pytest

from src.dating.mail_agent import (MAIL_AGENT_VALUE_MAX,
                                          MAIL_COMPANION_BIO_MAX,
                                          MAIL_QUESTION_MAX, _companion_card,
                                          _girl_bio, _json_chunk, _letter,
                                          _question, _values, _wanted_text)

pytestmark = pytest.mark.mail

# Что просим у агента — как это приходит из реестра сайта.
WANTED = {'girl_name', 'girl_city'}


class TestGirlBio:
    """Сведения об анкете — то, из чего агент берёт ответы.

    ⚠⚠ С 21.10.2026 собирает их **общий с чатом** `agent_bio`, а не свой `_card`.
    Отсюда и английская проза от первого лица вместо строк «Имя: …»: прежняя
    форма была второй, и она отстала — параметры анкеты до писем не доезжали.
    """

    def test_it_is_prose_in_english(self):
        """⚠ Проза, а не перечень: в таком виде биографию ждёт workflow соседа,
        и на этом же языке ведётся переписка."""
        got = _girl_bio({'login': 'lena@mail.ru',
                         'profile': {'name': 'Diana', 'age': 36, 'city': 'Kyiv'}})

        assert 'My name is Diana.' in got, got
        assert 'I am 36 years old.' in got, got
        assert 'I live in Kyiv.' in got, got

    def test_missing_fields_are_skipped(self):
        """⚠ Пустое поле не упоминается вовсе: строка «Город: » учит модель, что
        пустое значение допустимо, и она начинает возвращать такие же."""
        got = _girl_bio({'login': 'x@mail.ru',
                         'profile': {'name': 'Diana', 'city': 'Kyiv'}})

        assert 'years old' not in got, got

    def test_zero_age_is_not_a_fact(self):
        """Ноль в возрасте значит «не знаем» — так его кладёт разбор карточки."""
        got = _girl_bio({'login': 'x', 'profile': {'age': 0, 'city': 'Kyiv',
                                                   'about': 'x'}})

        assert 'years old' not in got, got

    def test_long_about_is_cut(self):
        """Биография бывает на две страницы, а раздувает она промпт больше, чем
        добавляет ответов. ⚠ Предел у писем **свой**, ниже чатовского."""
        got = _girl_bio({'login': 'x', 'profile': {'about': 'я' * 5000}})

        assert len(got) < 2000, len(got)

    def test_empty_profile_is_empty(self):
        """⚠⚠ Пустая строка, а не фраза «ничего не известно».

        Прежний сборщик возвращал такую фразу, и она уезжала агенту как данные.
        У соседа секция `girl_bio` гасится **по пустоте**: пустая строка молчит,
        а фраза занимала бы место в контексте, ничего не сообщая.
        """
        assert _girl_bio({'login': '', 'profile': None}) == ''


class TestWanted:
    """Список полей — задание для агента."""

    def test_title_becomes_the_task(self):
        """«girl_name — имя девушки» говорит модели, что искать, куда точнее
        голого имени."""
        text = _wanted_text([{'name': 'girl_name', 'title': 'Имя девушки'}])

        assert text == 'girl_name — Имя девушки'

    def test_name_alone_is_enough(self):
        assert _wanted_text([{'name': 'girl_city', 'title': ''}]) == 'girl_city'


class TestJsonChunk:
    """Вырезание JSON из ответа LLM."""

    def test_plain_json(self):
        assert _json_chunk('{"a": 1}') == '{"a": 1}'

    def test_markdown_fence_is_stripped(self):
        """⚠ Просить «только JSON» надо, а полагаться на это нельзя: LLM любит
        обрамить ответ ```json."""
        assert _json_chunk('```json\n{"a": 1}\n```') == '{"a": 1}'

    def test_words_around_are_cut(self):
        assert _json_chunk('Вот ответ: {"a": 1}. Готово.') == '{"a": 1}'

    def test_nested_object_survives(self):
        """⚠ Ищем по первой `{` и последней `}`: ленивая регулярка обрезала бы
        вложенный объект по первой закрывающей скобке."""
        assert _json_chunk('{"a": {"b": 1}}') == '{"a": {"b": 1}}'

    @pytest.mark.parametrize('answer', ['', None, 'просто текст', '}{'])
    def test_nothing_to_cut(self, answer):
        assert _json_chunk(answer) == ''


class TestValues:
    """Разбор ответа: берём только ожидаемое."""

    def test_expected_names_pass(self):
        values = _values('{"girl_name": "Diana", "girl_city": "Kyiv"}', WANTED)

        assert values == {'girl_name': 'Diana', 'girl_city': 'Kyiv'}

    def test_unknown_name_is_dropped(self):
        """⚠ Принять его значило бы завести переменную, которой нет ни в одном
        реестре: письмо на неё сослаться не может, а у анкеты она висела бы
        непонятной строкой."""
        values = _values('{"girl_name": "Diana", "выдумка": "мусор"}', WANTED)

        assert values == {'girl_name': 'Diana'}

    @pytest.mark.parametrize('raw', ['""', '"   "', 'null'])
    def test_empty_value_is_dropped(self, raw):
        """⚠ Пустое значение лучше выдуманного, но хранить его незачем: у анкеты
        оно означало бы «заполнено», а письмо всё равно не уйдёт."""
        assert _values('{"girl_name": %s}' % raw, WANTED) == {}

    def test_number_becomes_a_string(self):
        """Возраст приходит числом законно — отбрасывать его было бы потерей."""
        assert _values('{"girl_city": 42}', WANTED) == {'girl_city': '42'}

    def test_long_value_is_cut(self):
        """Агент иногда отвечает предложением там, где просили слово. Режем, а не
        отбрасываем: обрезанный город полезнее пустого."""
        values = _values('{"girl_city": "%s"}' % ('я' * 900), WANTED)

        assert len(values['girl_city']) == MAIL_AGENT_VALUE_MAX

    @pytest.mark.parametrize('answer', ['не знаю', '', '[1, 2]', '{сломано'])
    def test_bad_answer_gives_nothing(self, answer):
        """⚠ Пустой словарь, а не исключение: агент мог не найти ничего из
        просимого, и это законный исход, а не сбой."""
        assert _values(answer, WANTED) == {}


class TestCompanionCard:
    """Сведения об адресате — то, ради чего вкладка «Собеседники» и заведена."""

    def test_card_is_text_with_labels(self):
        card = _companion_card({'name': 'John', 'age': 52, 'country': 'Denmark',
                                'about': 'О себе: Ходит под парусом'})

        assert 'Name: John' in card
        assert 'Lives in: Denmark' in card
        assert 'About Myself: Ходит под парусом' in card

    def test_it_is_the_same_builder_the_chat_uses(self):
        """⚠⚠ Переменная зовётся `companion_bio` и в почте, и в чате.

        Пока текст собирали два места, содержимое у них было разное: свой набор
        подписей, свой предел и двойная подпись у описания («О нём: О себе: …» —
        `about` приходит уже подписанным). Расхождение видно только в журнале
        прогонов у соседа, то есть не видно.
        """
        from src.dating.companion_bio import companion_bio_build

        companion = {'name': 'John', 'age': 52, 'country': 'Denmark',
                     'about': 'Увлечения: chess'}

        assert _companion_card(companion) == companion_bio_build(companion, 500)

    def test_the_budget_stays_tighter_than_the_chat_one(self):
        """⚠⚠ Сборщик общий, бюджет — нет. Письмо везёт **две** биографии, и у
        ноды Dating маленькая модель: живой прогон упирался в «request (3940
        tokens) exceeds the available context size»."""
        long = {'name': 'John',
                'about': '\n'.join(f'Hobby {n}: ' + 'a' * 60 for n in range(60))}

        assert len(_companion_card(long)) <= MAIL_COMPANION_BIO_MAX

    @pytest.mark.parametrize('companion', [None, {}, {'name': '', 'age': 0}])
    def test_no_card_says_so(self, companion):
        """⚠ Пустой блок промпта хуже честного «ничего не известно»: во втором
        случае агент напишет общее письмо, в первом — начнёт выдумывать
        несуществующие увлечения.

        ⚠ Фраза по-английски, как и само био: один русский абзац внутри
        английского промпта модель читает как чужой кусок.
        """
        assert _companion_card(companion) == 'Nothing is known about this person.'


class TestLetter:
    """Разбор ответа агента письма."""

    def test_plain_letter_passes(self):
        assert _letter('Привет!\n\nКак дела?') == 'Привет!\n\nКак дела?'

    def test_thinking_tail_is_cut(self):
        """⚠ Поймано на первом же живом прогоне: хвост размышлений модели
        («…let\'s stop and write the final answer now.</think>») уехал бы живому
        человеку в начале письма."""
        text = _letter("exceeded, let's stop and write the final answer now."
                       '</think>\n\nПривет, John!')

        assert text == 'Привет, John!'

    def test_paired_think_is_cut(self):
        assert _letter('<think>рассуждаю</think>Письмо') == 'Письмо'

    def test_markdown_fence_is_stripped(self):
        assert _letter('```\nПисьмо\n```') == 'Письмо'

    def test_long_letter_is_cut(self):
        """Предел тот же, что у шаблона: письмо длиннее не сохранится, и обрезать
        его лучше здесь, чем отказывать человеку после ожидания."""
        assert len(_letter('я' * 9000)) == 4000

    @pytest.mark.parametrize('answer', ['', None, '   '])
    def test_empty_answer(self, answer):
        assert _letter(answer) == ''


class TestQuestion:
    """Разбор ответа агента вопроса.

    ⚠ Вопрос встаёт **внутрь абзаца** готового письма, и всё лишнее уедет туда
    вместе с ним — в отличие от письма, которое человек перечитывает.
    """

    def test_plain_question_passes(self):
        assert _question('Давно ли вы этим занимаетесь?') == 'Давно ли вы этим занимаетесь?'

    @pytest.mark.parametrize('raw', ['«Что вас вдохновляет?»', '"Что вас вдохновляет?"',
                                     '“Что вас вдохновляет?”'])
    def test_quotes_are_stripped(self, raw):
        """⚠ Просили «только вопрос», а модель подаёт его репликой. Такие кавычки
        в письме выглядят цитатой неизвестно кого."""
        assert _question(raw) == 'Что вас вдохновляет?'

    def test_explanation_after_is_cut(self):
        """⚠ Берём первый абзац: модель охотно поясняет свой выбор («Вот вопрос: …
        Он опирается на его увлечения»), и пояснение уехало бы в письмо."""
        text = _question('Давно ли вы реставрируете мотоциклы?\n\n'
                         'Я выбрал этот вопрос, потому что в профиле упомянуты байки.')

        assert text == 'Давно ли вы реставрируете мотоциклы?'

    def test_thinking_is_cut(self):
        """Тот же хвост размышлений, что у письма: разбор общий (`_letter`)."""
        assert _question('рассуждаю</think>\n\nЧем увлекаетесь?') == 'Чем увлекаетесь?'

    def test_long_question_is_cut_at_the_mark(self):
        """Длинный вопрос режется **по знаку вопроса**, а не по счёту знаков.

        ⚠ Сначала было по счёту — и обрезка съедала «?», после чего проверка
        «это вообще вопрос?» отбрасывала текст целиком. То есть правило, которое
        защищает от рассуждений, убивало длинные вопросы. Поймано этим же тестом.
        """
        text = _question('я' * 200 + '? ' + 'б' * 300 + '?')

        assert text == 'я' * 200 + '?'
        assert len(text) <= MAIL_QUESTION_MAX

    def test_question_without_a_mark_in_range_is_dropped(self):
        """Знак есть, но за пределом: обрубок без него в письме читается как
        оборванная мысль, и лучше не отправить, чем отправить такое."""
        assert _question('к' * 500 + '?') == ''

    def test_reasoning_instead_of_a_question_is_dropped(self):
        """⚠ Поймано живым кругом: из ста писем одно ушло бы с рассуждением
        модели вместо вопроса.

            «Given the description of the letter, I need to ask Matthias a
            question about his interests. Since we don't know much about his
            profile…»

        Первым абзацем оно и было, кавычек не имело, длину прошло. Отличает его
        от вопроса ровно одно, и признак этот формальный: вопрос кончается знаком
        вопроса.
        """
        assert _question('Given the description of the letter, I need to ask '
                         'Matthias a question about his interests.') == ''

    def test_mark_in_the_middle_is_not_enough(self):
        """Рассуждение о вопросе тоже содержит «?» — в середине. Вопросом оно от
        этого не становится, потому знак и ищется в конце."""
        assert _question('Should I ask about hobbies? Let me write one.') == ''

    @pytest.mark.parametrize('answer', ['', None, '   '])
    def test_empty_answer(self, answer):
        assert _question(answer) == ''


class TestAgentEndpoint:
    """Ручка «заполнить агентом» — то, что решается без похода к соседу."""

    async def test_missing_profile_is_404(self, auth_client):
        answer = await auth_client.post('/api/dating/profile/999999/var/agent',
                                        json={})

        assert answer.status_code == 404

    async def test_nothing_to_fill_asks_no_agent(self, auth_client, rhapsody):
        """⚠ Заполнено всё — к агенту не идём: это вызов LLM ради пустого ответа.

        Заполняем руками, а не ищем сайт без переменных: реестр живого сайта не
        пуст (`girl_name` заведена миграцией), и завтра переменных станет больше.
        Тест, опирающийся на пустой реестр, ломался бы от каждой новой — так и
        вышло, когда базовые две завели.

        Фикстура `rhapsody` тут затем, чтобы проверка была честной: походи ручка к
        соседу, тест это заметил бы (сюита наружу не ходит).
        """
        # ⚠ Стенд, а не живой сайт: анкета сюиты не должна попадать в круги
        # воркера (`test_mail_in.py`, `STAND_SITE`).
        created = await auth_client.post('/api/dating/profile', json={
            'site_id': 999_343,
            'login': f'agent-none-{uuid.uuid4().hex[:8]}@example.com',
            'password': 'x'})
        profile_id = created.json()['result']

        try:
            rows = (await auth_client.get(
                f'/api/dating/profile/{profile_id}/var')).json()['result']
            if rows:
                await auth_client.put(
                    f'/api/dating/profile/{profile_id}/var',
                    json={'values': {row['name']: 'занято' for row in rows}})

            answer = await auth_client.post(
                f'/api/dating/profile/{profile_id}/var/agent', json={})

            assert answer.status_code == 200
            assert answer.json()['filled'] == 0
        finally:
            await auth_client.delete(f'/api/dating/profile/{profile_id}')

    async def test_button_is_on_the_page(self, auth_client):
        """Кнопка живёт на вкладке «Переменные» правой колонки «Анкет»."""
        page = (await auth_client.get('/admin/dating')).text

        assert 'varAgentBtn' in page
        assert '/var/agent' in page


class TestDraftEndpoint:
    """Ручка «AI сгенерировать» — то, что решается без похода к соседу."""

    async def test_site_without_profiles_is_allowed(self, auth_client):
        """⚠ Сайт без анкет — не повод отказывать, хотя когда-то был.

        Прежде ручка требовала анкету: письмо, дескать, пишется от её лица, и без
        неё агент не знает ни имени, ни города. Требование оказалось ложным ровно
        наоборот — знай агент имя с городом, он вписал бы их словами вместо
        переменных, что и происходило («Diana here! I'm a choreographer living in
        Kyiv»).

        Шаблон не пишется от лица анкеты. Он пишется **для всех**, и заготовку
        можно готовить на пустом сайте — анкеты подставятся при рассылке.
        """
        answer = await auth_client.post('/api/dating/mail/draft',
                                        json={'site_id': 999_777})

        # 502 — до соседа дошло и он не ответил (сюита наружу не ходит). Важно,
        # что отказа «нет анкет» больше нет.
        assert answer.status_code != 422, 'ручка снова требует анкету'

    async def test_operator_may_draft(self, dating_client):
        """⚠ Черновик шаблона заводит и оператор — вместе с самими шаблонами.

        Прежде это было админским правом, и довод звучал так: черновик — вызов LLM,
        а он стоит денег. Но текст письма правят **первым**, когда рассылка идёт
        плохо, и запирать его значило звать админа на каждую переформулировку.
        Расход при этом ограничен не правами, а потолком `template_per_day`.
        """
        answer = await dating_client.post('/api/dating/mail/draft',
                                          json={'site_id': 999_777})

        # 502 — до соседа дошло и он не ответил (сюита наружу не ходит). Важно,
        # что отказа по правам больше нет.
        assert answer.status_code != 403, 'черновик шаблона снова заперт от оператора'

    async def test_button_is_on_the_templates_tab(self, auth_client):
        """⚠ Кнопка переехала вместе с шаблонами: страницы «Письма» больше нет,
        они живут подвкладкой в «Профили» → «Письма»."""
        page = (await auth_client.get('/admin/dating/profile')).text

        assert 'aiBtn' in page
        assert '/api/dating/mail/draft' in page

    async def test_nobody_is_chosen_on_the_page(self, auth_client):
        """⚠ Собеседника при заведении шаблона **не выбирают**, и это главное.

        Селект здесь был — и оказался вреден. Получив карточку человека, агент
        писал письмо ему: «Diana here! I'm a choreographer living in Kyiv» вместо
        [[girl_name]] и городa переменной. Шаблон рассылается от всех анкет всем
        адресатам, и вписанное имя делает его годным одному из ста.

        Кому и от кого — выясняется при генерации пула, там переменные и
        подставляются.
        """
        page = (await auth_client.get('/admin/dating/mail')).text

        assert 'id="mailWho"' not in page, 'выбор собеседника вернулся'
        assert 'companion_uid' not in page, 'адресат снова уходит в ручку'



class TestOneVariableInsteadOfFour:
    """⚠⚠⚠ Писателю едет **одна** переменная вместо четырёх.

    Прежде: `mail_vars` (список с подписями — его читает модель) и три голых
    списка имён под инструменты соседа — `mail_var_names` (разрешённые),
    `mail_var_must` (обязательные), `mail_var_live` (только отдельным абзацем).
    Два из них собирались **побайтово одинаковым** выражением из одного списка.

    Теперь всё в одной: объект «имя → подпись» плюс ключ `@live` со списком
    живых. Workflow выводит из неё все три списка циклом по ключам, без единого
    инструмента — проверено прогоном `ai_workflow_text_render` на коде соседа.
    """

    def test_only_mail_vars_goes_to_the_writer(self):
        """Трёх голых списков в вызове писателя больше нет."""
        import inspect

        from src.dating import mail_agent

        code = inspect.getsource(mail_agent.mail_write)

        for gone in ('mail_var_names', 'mail_var_must', 'mail_var_live'):
            assert f"'{gone}':" not in code, f'{gone} снова уезжает писателю'

        assert "'mail_vars': _wanted_map(allowed)" in code, 'список уехал не объектом'

        # ⚠⚠ Имя списка — `allowed`, а не `must`: обязательных меток у шаблона
        # **нет** (правило снято 21.10.2026). Вернись имя — вернулось бы и
        # требование полноты, стоившее четырёх кругов переписывания на текст.
        assert 'must = ' not in code, \
            'вернулся список обязательных: полнота снята, см. mail_agent'

    def test_the_payload_is_a_flat_map(self):
        """⚠⚠⚠ Плоский объект: по массиву объектов цикл печатает `repr` словаря —
        доступа к полям у него нет."""
        import json

        from src.dating.mail_agent import _wanted_map

        got = json.loads(_wanted_map([
            {'name': 'girl_name', 'title': 'The name', 'source': 'profile'},
            {'name': 'question_to_companion', 'title': 'A question', 'source': 'live'},
        ]))

        assert got == {'girl_name': 'The name',
                       'question_to_companion': 'A question'}, got
        assert all(isinstance(one, str) for one in got.values()), \
            'значение не строка — цикл промпта напечатает repr'

    def test_the_order_follows_the_registry(self):
        """⚠⚠ Порядок ключей — порядок реестра: он же задаёт порядок строк в
        промпте. `@live` последним: он не переменная."""
        import json

        from src.dating.mail_agent import _wanted_map

        rows = [{'name': 'zebra', 'title': 'z'}, {'name': 'alpha', 'title': 'a'}]
        keys = list(json.loads(_wanted_map(rows)))

        assert keys == ['zebra', 'alpha'], keys

    def test_names_stay_bare(self):
        """⚠ Скобки дописывает промпт (`[[%k%]]`): инструменты ждут голые имена."""
        import json

        from src.dating.mail_agent import _wanted_map

        got = json.loads(_wanted_map([{'name': 'girl_name', 'title': 'x'}]))

        assert 'girl_name' in got, 'имя уехало со скобками — инструмент не узнает'

    def test_nothing_but_variables_travels(self):
        """⚠⚠ В списке только настоящие переменные — служебного ключа нет.

        Был `@live` со списком тех, чьё значение — целое предложение. Снят
        20.10.2026: признак переехал в **шаг workflow** константой, а проверку
        делает универсальный предикат `text_paragraph_has`. Знание «эта метка —
        целая фраза» принадлежит автору промпта и видно в графе, а не едет
        служебным ключом внутри списка, который читает модель.
        """
        import json

        from src.dating.mail_agent import _wanted_map

        got = json.loads(_wanted_map([
            {'name': 'girl_name', 'title': 'x', 'source': 'profile'},
            {'name': 'question_to_companion', 'title': 'y', 'source': 'live'},
        ]))

        assert all(not key.startswith('@') for key in got), \
            f'в список переменных вернулся служебный ключ: {sorted(got)}'
        assert set(got) == {'girl_name', 'question_to_companion'}, got

    def test_the_values_agent_is_untouched(self):
        """⚠ У агента значений своя нужда: он отвечает JSON, имя у него ключ, и
        список ему по-прежнему уходит строками с голыми именами."""
        import inspect

        from src.dating import mail_agent

        code = inspect.getsource(mail_agent.mail_agent_fill)

        assert "'mail_vars': _wanted_text(wanted)" in code, \
            'агенту значений поехал объект — его промпт ждёт строки'


class TestNothingIsMandatory:
    """⚠⚠⚠ Обязательных меток у шаблона **нет**, и это решение.

    Правило «все переменные реестра обязательны» пришло коммитом `2cc22b4` с
    доводом: агент писал имя словами («Hi Mark»), и три прежние проверки это
    пропускали. Снято 21.10.2026, потому что довод не подтвердился на данных:
    ни одного имени словами — ни в 38 свежих шаблонах, ни в 57 живых на проде,
    где метка имени требовалась лишь в четверти случаев. Выдумать имя модели
    неоткуда: о получателе ей не говорят ничего.

    ⚠⚠ Цена была настоящей: шаг недостачи срабатывал **147 раз на 38 шаблонов**,
    около четырёх кругов переписывания на текст, каждый круг — вызов модели.
    После снятия — 8 шагов на шаблон вместо 36.

    ⚠⚠⚠ И требования этого **нигде не было в интерфейсе**: у переменной одна
    галочка, «участвует», и читается она как «можно использовать».

    ⚠ Проверка **разрешённого** осталась и нужна: выдуманная метка
    (`[[recipient_name]]`) ломает отправку по-настоящему — значения на неё нет,
    и письмо не уходит никогда. Отсутствующая так не вредит.
    """

    def test_the_rule_is_gone_from_the_code(self):
        import inspect

        from src.dating import mail_agent

        code = inspect.getsource(mail_agent.mail_write)

        assert 'весь реестр как обязательный' not in code, 'правило вернулось'
        assert "_wanted_map(allowed)" in code, 'список снова зовётся обязательным'

    def test_the_dice_still_shapes_what_is_offered(self):
        """⚠ Жребий имени остался и работает **умолчанием, а не запретом**: не
        выпало — метку не предлагаем, и модель о ней не знает.

        На этом держится доля `mailing_name_percent`.
        """
        import inspect

        from src.dating import mail_agent

        code = inspect.getsource(mail_agent.mail_write)

        assert "allowed = ([row for row in known if row['name'] != INVITE_SLOT_MAN]" in code, \
            'жребий имени перестал править списком разрешённых'


class TestTheBioIsBuiltOnce:
    """⚠⚠⚠ Сведения об анкете собирает **один** сборщик — общий с чатом.

    Здесь стоял свой `_card`: четыре строки с русскими подписями. Параллельно
    `agent_bio` нарастили параметрами анкеты (21 ключ реестра), и письма об этом
    не узнали — их сборщик остался прежним с сентября.

    Цена была двойная. **Писатель не видел того, ради чего `girl_bio`
    заводили**: у анкеты в параметрах хореография и джиу-джитсу, а в письмо шла
    одна проза «о себе» — выходило «я люблю своё дело» там, где есть о чём
    сказать. И **страница врала**: вкладка «Информация» показывает `agent_bio`
    под подписью «то, что уезжает агенту», а уезжало другое.

    ⚠ Расхождение было не решением, а расползанием: одну функцию улучшили,
    вторую забыли.
    """

    def test_the_bio_comes_from_the_shared_builder(self):
        """⚠ Своего сборщика у писем нет: `_girl_bio` — тонкая дверь к общему."""
        import inspect

        from src.dating import mail_agent

        code = inspect.getsource(mail_agent._girl_bio)

        assert 'agent_bio(' in code, 'письма снова собирают био сами'

    def test_every_letter_agent_uses_it(self):
        """Все три занятия почты зовут одну дверь, а не каждое своё."""
        import inspect
        import re

        from src.dating import mail_agent

        code = re.sub(r'#.*', '', inspect.getsource(mail_agent))
        sends = re.findall(r"'girl_bio':\s*([a-zA-Z_]+)\(", code)

        assert sends, 'girl_bio больше никуда не уезжает — проверка устарела'
        assert set(sends) == {'_girl_bio'}, f'био собирают разные функции: {set(sends)}'

    def test_the_params_are_there(self):
        """⚠ Ради этого всё и сводилось: писатель видит параметры анкеты."""
        from src.dating.mail_agent import _girl_bio

        got = _girl_bio({'profile': {
            'name': 'Diana', 'age': 36, 'city': 'Kyiv', 'about': 'choreographer.',
            'parameters': {'height': '172 cm', 'hobby': 'Jiu Jitsu'}}})

        assert 'Height: 172 cm' in got, got
        assert 'Hobby: Jiu Jitsu' in got, got
        assert 'My name is Diana' in got, got


class TestTheLetterBudgetIsItsOwn:
    """⚠⚠ Сборщик общий, а бюджет **свой** — тот же размен, что у `companion_bio`.

    В чате контекст держит одну биографию, у вопроса получателю — две, и за ними
    ещё правила письма. У ноды Dating стоит `qwen-small`, и живой прогон уже
    упирался: «request (3940 tokens) exceeds the available context size».

    ⚠⚠⚠ Режется **по частям**: проза собрана в одну строку, и `text_trim_lines`
    по общему тексту унёс бы её целиком — анкета осталась бы без имени.
    """

    def test_the_prose_survives_a_long_about(self):
        """⚠⚠ Главная проверка: длинное «о себе» не уносит имя."""
        from src.dating.mail_agent import _girl_bio

        got = _girl_bio({'profile': {
            'name': 'Diana', 'age': 36, 'city': 'Kyiv',
            'about': 'очень длинная проза. ' * 200,
            'parameters': {'height': '172 cm'}}})

        assert got.startswith('My name is Diana.'), got[:80]
        assert 'I live in Kyiv.' in got, 'город потерялся при обрезке'

    def test_the_params_block_is_capped(self):
        """Перечень режется своим пределом, строками целиком."""
        from src.dating.mail_agent import MAIL_AGENT_PARAM_MAX, _girl_bio
        from src.dating.profile_param import PROFILE_PARAM_KEYS

        got = _girl_bio({'profile': {
            'name': 'Diana', 'age': 36, 'city': 'Kyiv', 'about': 'x',
            'parameters': {key: f'значение {key} подлиннее для предела'
                           for key in PROFILE_PARAM_KEYS}}})
        _, _, block = got.partition('\n\n')

        assert block, 'перечень пропал вовсе'
        assert len(block) <= MAIL_AGENT_PARAM_MAX, len(block)
        assert not block.endswith('…'), 'строка обрезана посередине'

    def test_the_budget_is_smaller_than_the_chat_one(self):
        """⚠ Предел письма обязан быть **ниже** чатовского: иначе вернётся тот
        самый отказ по контексту."""
        from src.dating.mail_agent import MAIL_AGENT_PARAM_MAX
        from src.dating.profile_param import PROFILE_PARAM_BIO_MAX

        assert MAIL_AGENT_PARAM_MAX < PROFILE_PARAM_BIO_MAX, \
            'почта получила чатовский бюджет — контекст qwen-small его не держит'

    def test_an_empty_card_is_empty(self):
        """⚠ Пустая строка законна: у соседа секция `girl_bio` тогда гасится."""
        from src.dating.mail_agent import _girl_bio

        assert _girl_bio({'profile': {}}) == ''
        assert _girl_bio({}) == ''
