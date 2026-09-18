"""Инвайты: разбор шаблона от агента и раздача его по парам.

⚠ **Наружу сюита не ходит** (`testing.md`): инвайт — это вызов LLM у соседа, а
отправка ещё и реплика живому человеку в чат. Проверяется то, что и ломается, —
две чистые функции:

* `_invite` — что взять из ответа агента. Текст теперь **шаблон**: один абзац,
  свои метки на месте имён и ни одной чужой;
* `_cards` — кому и от кого. Здесь всё решается у нас, и цена ошибки высока в обе
  стороны: пропустим — человек не получит приглашения, возьмём лишнего — двое
  получат **одинаковую** реплику от разных наших девушек.

⚠⚠ **Что изменилось и почему тесты переписаны.** Прежде агента звали на каждую
пару, а требованием к тексту было имя собеседника внутри реплики. Теперь вызов
один на весь пул, текст общий, а имя — необязательная метка. Отсюда и новое
главное правило: **собеседники в пуле уникальны** (см. `TestCards`).
"""

import pytest

import uuid

from src.dating.invite import _cards, _fill
from src.dating.mail_agent import (INVITE_LANGUAGE,
                                          INVITE_MAX,
                                          INVITE_SLOT_GIRL,
                                          INVITE_SLOT_MAN, _invite)

pytestmark = pytest.mark.chat

# Стенд у соседа: круги воркера про него не знают — его нет в `site_`.
# Тот же, что у диалогов (`test_chat_dialogs.py`), и по той же причине.
STAND_SITE = 999_343

# Анкеты агентства. ⚠ Двух Диан не выдумали: на живом сайте их и есть две (#849 и
# #850), и первый же прогон выдал обеим одного и того же мужчину.
GIRLS = [{'id': 849, 'login': 'diana@example.com'},
         {'id': 850, 'login': 'diana2@example.com'},
         {'id': 861, 'login': 'yulia@example.com'}]

PEOPLE = [{'uid': '111', 'name': 'Brian'},
          {'uid': '222', 'name': 'Frank'},
          {'uid': '333', 'name': 'Ahmet'}]

MAN_SLOT = f'[[{INVITE_SLOT_MAN}]]'
GIRL_SLOT = f'[[{INVITE_SLOT_GIRL}]]'


class TestInviteText:
    """Что берём из ответа агента: шаблон, а не готовую реплику."""

    def test_plain_template_is_taken(self):
        assert _invite('Спорим, ты сейчас улыбнулся?') == 'Спорим, ты сейчас улыбнулся?'

    def test_slot_survives(self):
        """Метка — то, ради чего шаблон и просят: её заменит подстановка."""
        assert _invite(f'{MAN_SLOT}, ты всегда так смотришь?', man_slot=True) \
               == f'{MAN_SLOT}, ты всегда так смотришь?'

    def test_curly_and_percent_forms_are_normalised(self):
        """⚠ Модель пишет метку как привыкла — приводим к своему виду.

        `{companion_name}`, `%companion_name%` и `[[ companion_name ]]` означают
        одно; спорить об этом в промпте дороже, чем нормализовать здесь. Не
        приведи мы их — метка уехала бы в чат живому человеку прямо в скобках.
        """
        assert _invite('{companion_name}, привет', man_slot=True) == f'{MAN_SLOT}, привет'
        assert _invite('%companion_name%, привет', man_slot=True) == f'{MAN_SLOT}, привет'
        assert _invite('[[ companion_name ]], привет', man_slot=True) \
               == f'{MAN_SLOT}, привет'

    def test_unknown_slot_drops_the_template(self):
        """⚠⚠ Выдуманную метку подставить нечем — она уйдёт в чат в скобках.

        Отбрасываем весь шаблон: следующий заход попросит новый, и это дешевле
        полусотни испорченных реплик — текст-то один на весь пул.
        """
        assert _invite('Привет, [[his_city]] — красивый город.') == ''

    def test_paragraphs_become_one(self):
        """⚠ Один абзац — требование: в окне чата текст в три абзаца читается
        как вставленный из документа.

        Переносы **сводим в пробел**, а не режем по первому абзацу: модель охотно
        разбивает две фразы на две строки, и вторую терять незачем.
        """
        assert _invite('Скучно.\n\nРазвлечёшь?') == 'Скучно. Развлечёшь?'

    def test_quotes_are_stripped(self):
        """Просили «только текст», а модель подаёт его репликой в кавычках — в
        чате они выглядят цитатой неизвестно кого."""
        assert _invite('«Спорим, ты сейчас улыбнулся?»') == 'Спорим, ты сейчас улыбнулся?'

    def test_model_reasoning_is_dropped(self):
        """Рассуждающие модели обрамляют размышления `<think>…</think>`."""
        assert _invite('<think>подумаю…</think>Ты опасен, я вижу.') \
               == 'Ты опасен, я вижу.'

    def test_missing_slot_is_not_a_refusal(self):
        """⚠ Просили метку, а её нет — текст всё равно годен.

        Реплика без имени остаётся репликой, а отказ стоил бы всего пула: заход
        один, шаблон один.
        """
        assert _invite('Ты всегда так молчалив?', man_slot=True) \
               == 'Ты всегда так молчалив?'

    def test_long_template_is_cut_not_dropped(self):
        """Длинный текст режем: пустой пул хуже длинной реплики."""
        got = _invite('А' * (INVITE_MAX + 100))

        assert len(got) == INVITE_MAX

    def test_silence_is_empty(self):
        assert _invite('') == ''


class TestJudge:
    """⚠⚠ Судья у соседа: панель обязана видеть его вердикт, а не только текст.

    Судья заведён потому, что шаблон уходит полусотне живых людей без единого
    человеческого взгляда между «модель написала» и «сайт отправил». Прежде
    негодная реплика портила одну карточку из двадцати; теперь — весь пул.

    Цепочка у соседа: черновик → судья → правка по замечанию → ответ конвертом
    (`VERDICT` / `WHY` / `TEXT`). Здесь проверяется наша сторона конверта.
    """

    def test_verdict_yes_passes(self):
        assert _invite('VERDICT: yes\nWHY: \nTEXT: Спорим, ты сейчас улыбнулся?') \
               == 'Спорим, ты сейчас улыбнулся?'

    def test_verdict_no_drops_the_text(self):
        """⚠ Судья забраковал — текста нет, каким бы правдоподобным он ни выглядел.

        Это главное, ради чего конверт и заведён: без вердикта панель видела бы
        одну строку и доверяла бы ей одинаково в обоих случаях.
        """
        assert _invite('VERDICT: no\nWHY: это отказ модели\n'
                       'TEXT: I cannot help with that request') == ''

    def test_verdict_fixed_passes(self):
        """`fixed` — «забраковал, но правка сняла замечание»: законный исход."""
        assert _invite('VERDICT: fixed\nWHY: было третье лицо\n'
                       'TEXT: Спорим, ты улыбнулся?') == 'Спорим, ты улыбнулся?'

    def test_text_may_carry_newlines(self):
        """⚠ `TEXT:` стоит последним намеренно: реплику берём до конца ответа.

        Обрежь мы её по строке — ушла бы половина текста, а с ним и смысл.
        """
        assert _invite('VERDICT: yes\nWHY: \nTEXT: Скучно.\nРазвлечёшь?') \
               == 'Скучно. Развлечёшь?'

    def test_answer_without_the_envelope_still_works(self):
        """⚠ Конверта может не быть — так отвечает workflow без судьи.

        Стенд, чужая инсталляция, старая версия у соседа: текст берём как есть, а
        проверки формы работают всё равно.
        """
        assert _invite('Спорим, ты сейчас улыбнулся?') == 'Спорим, ты сейчас улыбнулся?'


class TestSafetyNet:
    """Наши проверки поверх судьи: они про форму, он — про смысл.

    ⚠ Обе нужны. Судья пропустил выдуманную метку на живой проверке — форму ловим
    сами; отказ модели формально безупречен — тут решает судья.
    """

    def test_model_refusal_is_dropped(self):
        """⚠⚠ Поймано на живом прогоне первого же дня.

        «I can't help with crafting flirt or pickup messages, but here's a neutral
        opener: …» — отказ выглядит текстом и уехал бы в чат **всему пулу разом**,
        вместе с извинением. Прежней версии это почти не грозило: отказ не
        содержал имени собеседника и отсеивался проверкой имени.
        """
        assert _invite("I can't help with crafting flirt messages, but here's a "
                       'neutral opener: Hi there!') == ''

    def test_apology_is_dropped(self):
        assert _invite("I'm sorry, but I won't write that.") == ''

    def test_cyrillic_is_dropped_when_latin_is_asked(self):
        """⚠⚠ Кириллица там, где ждут латиницу, — это **недоставка**.

        Фильтр сайта отклоняет такое молча для нас («Message not delivered.
        Unacceptable words: …»), и однажды это уже стоило целого пула. Поймано
        снова живьём: шаг правки читает замечание судьи — оно по-русски, для
        человека, — и уходит в его язык.
        """
        assert _invite('VERDICT: fixed\nWHY: метка\nTEXT: Угадаешь, о чём я подумала?',
                       language='en') == ''

    def test_cyrillic_is_fine_for_a_cyrillic_site(self):
        """Язык не латинский — кириллица и есть ожидаемое."""
        assert _invite('VERDICT: yes\nWHY: \nTEXT: Угадаешь, о чём я подумала?',
                       language='ru') == 'Угадаешь, о чём я подумала?'


class TestFill:
    """Подстановка имён в шаблон."""

    def test_first_names_only(self):
        """⚠ В чате обращаются «Brian», а не «Brian Ritter»: фамилия звучит как
        обращение из документа. То же правило у имени девушки."""
        got = _fill(f'{GIRL_SLOT} тут, {MAN_SLOT}?',
                    {'id': 849, 'profile': '{"name": "Diana K."}'},
                    {'uid': '111', 'name': 'Brian Ritter'})

        assert got == 'Diana тут, Brian?'

    def test_template_without_slots_is_untouched(self):
        got = _fill('Спорим, ты сейчас улыбнулся?',
                    {'id': 849, 'profile': '{"name": "Diana"}'},
                    {'uid': '111', 'name': 'Brian'})

        assert got == 'Спорим, ты сейчас улыбнулся?'


class TestCards:
    """Кому и от кого. Главное правило теперь — уникальность собеседников."""

    def test_one_card_per_person(self):
        """⚠⚠ **Один человек — одна карточка на весь пул.**

        Текст в пуле общий: отправь его две наши девушки одному мужчине — он
        получит два одинаковых сообщения от разных незнакомок, то есть увидит
        агентство. Прежнее правило («один инвайт за заход») ловило ту же беду на
        двух Дианах, #849 и #850, написавших одному человеку в первом же прогоне.
        """
        got = list(_cards(GIRLS, PEOPLE, set(), set()))

        assert len(got) == len(PEOPLE), 'человек получил больше одной карточки'
        assert sorted(person['uid'] for _, person in got) == ['111', '222', '333']

    def test_girls_take_turns(self):
        """⚠ Очередь по девушкам делит пул поровну.

        Бери мы каждый раз первую свободную — весь пул уходил бы от анкеты с
        меньшим `id`: она выигрывает всегда.
        """
        people = [{'uid': str(one), 'name': f'M{one}'} for one in range(1, 10)]
        got = list(_cards(GIRLS, people, set(), set()))

        # Девять человек на три анкеты — по три карточки каждой.
        counts = {}
        for girl, _ in got:
            counts[girl['id']] = counts.get(girl['id'], 0) + 1

        assert counts == {849: 3, 850: 3, 861: 3}, counts

    def test_first_round_gives_each_girl_one(self):
        """Порядок именно круговой: сперва всем по одной, потом по второй."""
        people = [{'uid': str(one), 'name': f'M{one}'} for one in range(1, 7)]
        got = [girl['id'] for girl, _ in _cards(GIRLS, people, set(), set())]

        assert sorted(got[:3]) == [849, 850, 861]
        assert sorted(got[3:]) == [849, 850, 861]

    def test_taken_person_is_not_given_twice(self):
        """⚠ Занятость считается **по всему пулу**, а не по заходу.

        Кнопку «Генерировать» жмут поверх непустого пула, и без этого набора
        второй заход выдал бы тем же людям вторую карточку — с тем же текстом, но
        от другой девушки.
        """
        got = list(_cards(GIRLS, PEOPLE, set(), set(), taken={'222'}))

        assert sorted(person['uid'] for _, person in got) == ['111', '333']

    def test_taken_is_filled_as_we_go(self):
        """Набор занятых пополняется на ходу — им же вызывающий и пользуется."""
        taken = set()
        list(_cards(GIRLS, PEOPLE, set(), set(), taken=taken))

        assert taken == {'111', '222', '333'}

    def test_priority_goes_first_but_once(self):
        """⚠ **Приоритет — теперь порядок, а не число реплик.**

        Прежде отмеченному писали все свободные девушки; с общим шаблоном это
        пять одинаковых сообщений подряд одному человеку. Он уходит первым, но
        карточку получает одну.
        """
        people = [{'uid': '111', 'name': 'Brian'},
                  {'uid': '999', 'name': 'Mark', 'priority': True}]
        got = list(_cards(GIRLS, people, set(), set()))

        assert got[0][1]['uid'] == '999', 'приоритетный ушёл не первым'
        assert [person['uid'] for _, person in got].count('999') == 1

    def test_a_busy_pair_is_skipped(self):
        """Этой девушке звать его рано — черновик в пуле либо не вышла пауза."""
        busy = {(girl['id'], '222') for girl in GIRLS}
        got = list(_cards(GIRLS, PEOPLE, busy, set()))

        assert sorted(person['uid'] for _, person in got) == ['111', '333']

    def test_a_busy_pair_does_not_take_him_from_the_others(self):
        """⚠⚠ **Предел у каждой девушки свой.**

        Прежде «позвали» считалось по человеку: занятость одной анкеты закрывала
        его для всех. Пять свободных девушек упирались в счёт, набранный тремя
        другими, — поймано на приоритетном собеседнике.
        """
        got = {person['uid']: girl['id'] for girl, person in
               _cards(GIRLS, PEOPLE, {(849, '222')}, set())}

        assert sorted(got) == ['111', '222', '333']
        assert got['222'] in (850, 861), got

    def test_girls_who_talked_are_not_chosen(self):
        """⚠ Свободные — те, кто с ним не говорил.

        Иначе случай раз за разом выпадал бы на анкету, у которой с ним уже
        переписка, и человек оставался без инвайта, хотя другая была свободна.
        """
        talked = {(849, '111'), (850, '111')}
        got = {person['uid']: girl['id'] for girl, person in
               _cards(GIRLS, PEOPLE, set(), talked)}

        assert got['111'] == 861, 'выбрана анкета, у которой с ним переписка'

    def test_person_nobody_is_free_for_is_skipped(self):
        """Со всеми нашими он уже говорит — звать некому, и это не ошибка."""
        talked = {(girl['id'], '111') for girl in GIRLS}
        got = list(_cards(GIRLS, PEOPLE, set(), talked))

        assert '111' not in [person['uid'] for _, person in got]

    def test_blocked_pair_is_skipped(self):
        """Закрылся от анкеты — пишет другая: запрет отнимает пару, не человека."""
        got = {person['uid']: girl['id'] for girl, person in
               _cards(GIRLS, PEOPLE, set(), set(), blocked={(849, '111'), (850, '111')})}

        assert got['111'] == 861

    def test_empty_uid_is_not_a_person(self):
        """Пустой номер из ленты — мусор, а не собеседник."""
        got = list(_cards(GIRLS, [{'uid': '', 'name': 'X'}, PEOPLE[0]], set(), set()))

        assert [person['uid'] for _, person in got] == ['111']

    def test_it_ends_when_nobody_is_left(self):
        """⚠ Обход конечен: круг без единой выдачи заканчивает набор.

        Генератор крутит девушек по кругу — без этого условия он крутился бы
        вечно, а вызывающий ждал бы, пока наберётся пул, которому неоткуда взяться.
        """
        got = list(_cards(GIRLS, PEOPLE, set(), set()))

        assert len(got) == 3


class TestInviteLanguage:
    """⚠ Настоящая ошибка: реплики вышли по-русски мужчинам на англоязычном сайте.

    Первый прогон дал все четыре инвайта на русском — «Frank, а чем вы любите
    заниматься в свободное время?». Причина была в промпте: правило «пиши на языке
    его профиля», профиля у этих людей нет, и модель ушла в язык самого промпта.

    ⚠ **Это про доставку, а не про вкус.** Фильтр сайта отклоняет кириллицу
    («Message not delivered. Unacceptable words: …», см.
    `answer.ANSWER_LANGUAGE`), то есть пул был бы пулом
    недоставляемых сообщений.

    Проверяем то, что и ломалось: язык **уезжает агенту переменной**. Самого
    ответа модели тест не касается — за него отвечает промпт у соседа.
    """

    async def test_language_goes_to_the_agent(self, monkeypatch):
        assert (await _asked_with(language='en')).get('user_language') == 'en'

    async def test_site_language_wins_over_the_default(self, monkeypatch):
        """Язык — свойство сайта (`presence.answer_language`), и передаётся как есть."""
        assert (await _asked_with(language='de')).get('user_language') == 'de'

    async def test_empty_language_falls_back_to_english(self, monkeypatch):
        """⚠ Пустое не пропускаем: без языка модель снова уйдёт в язык промпта.

        Английский — то же умолчание и по той же причине, что у автоответа.
        """
        assert (await _asked_with(language='')).get('user_language') \
               == INVITE_LANGUAGE

    async def test_slots_are_asked_by_us(self, monkeypatch):
        """⚠ Какие метки ставить — говорим мы, а не решает модель.

        «Вставляй имя изредка» она понимает как «вставляй всегда»: вероятность у
        неё стилистическая. Жребий (5% и 30%) бросает панель, а сюда едет прямое
        указание.
        """
        got = await _asked_with(language='en', man_slot=True)

        assert INVITE_SLOT_MAN in got.get('invite_slots', '')

    async def test_no_cards_go_to_the_agent(self, monkeypatch):
        """⚠⚠ Ни карточки девушки, ни карточки мужчины: шаблон общий.

        Просить персонализацию, а потом раздать её пятидесяти разным людям —
        обман самих себя, и стоит он вызова LLM на каждого.
        """
        got = await _asked_with(language='en')

        assert 'girl_card' not in got and 'companion_card' not in got


async def _asked_with(language: str, girl_slot: bool = False,
                      man_slot: bool = False) -> dict:
    """Какие переменные ушли бы агенту. Транспорт подменён: проверяется вызов."""
    from src.dating import mail_agent as agent

    sent = {}

    async def _agent_id(*args, **kwargs):
        return 7

    async def _ask(agent_id, question, companion_id=1, variables=None):
        sent.update(variables or {})

        return 'Спорим, ты сейчас улыбнулся?'

    real_id, real_ask = agent._agent_id, agent.rhapsody_api_chat_ask
    agent._agent_id = _agent_id
    agent.rhapsody_api_chat_ask = _ask
    try:
        # ⚠ Именованно, а не по порядку: новый аргумент в середине сигнатуры
        # сдвинул бы позиционные молча — так и вышло с `with_time`.
        await agent.invite_template(343, language, girl_slot=girl_slot,
                                    man_slot=man_slot)
    finally:
        agent._agent_id = real_id
        agent.rhapsody_api_chat_ask = real_ask

    return sent


class TestCompanionNameParts:
    """⚠ Имя собеседника на части: первое слово — имя, остальное — фамилия.

    Сайт отдаёт имя одной строкой, и обращение «Здравствуйте, Brian Ritter»
    читается как заполненная форма: по имени и фамилии разом не обращаются нигде.

    ⚠ **Нет пробела — обе части пусты**, а не «имя целиком». Соблазн отдать
    «Kevin» первым именем велик, но тогда пропадает разница между «имя известно
    отдельно» и «у нас просто строка», а решать, чем звать, должен тот, кто пишет
    текст: промпт через `%if% … %else%`, шаблон через `default_value`.
    """

    def test_two_words_split(self):
        from src.dating.people import people_name_parts

        assert people_name_parts('Brian Ritter') == {
            'firstname': 'Brian', 'lastname': 'Ritter'}

    def test_first_word_wins_not_the_last(self):
        """⚠ Делим по **первому** пробелу: «JULIO RODRIGO ZAPATA OLIVA» — это
        Хулио и четыре фамилии, а не наоборот. Где кончается имя, мы не знаем, а
        первое слово угадывается всегда."""
        from src.dating.people import people_name_parts

        assert people_name_parts('JULIO RODRIGO ZAPATA OLIVA') == {
            'firstname': 'JULIO', 'lastname': 'RODRIGO ZAPATA OLIVA'}

    def test_single_word_gives_nothing(self):
        from src.dating.people import people_name_parts

        assert people_name_parts('Kevin') == {'firstname': '', 'lastname': ''}

    def test_underscores_are_not_spaces(self):
        """⚠ `Tall_Good_Man` — одно слово, хотя выглядит как три. Разбери мы его
        по подчёркиванию, человека звали бы «Tall»."""
        from src.dating.people import people_name_parts

        assert people_name_parts('Tall_Good_Man')['firstname'] == ''

    def test_extra_spaces_do_not_make_parts(self):
        from src.dating.people import people_name_parts

        assert people_name_parts('  Ales   kumer ') == {
            'firstname': 'Ales', 'lastname': 'kumer'}

    def test_empty_name_is_not_a_crash(self):
        from src.dating.people import people_name_parts

        assert people_name_parts(None) == {'firstname': '', 'lastname': ''}


class TestCompanionVars:
    """Чем подставятся `companion`-переменные — вместе с выводимыми частями имени."""

    def test_parts_are_derived_not_stored(self):
        """⚠ Части имени выводятся из `name`, а не лежат в карточке: заведи мы под
        них колонки, они разошлись бы с именем в первую же правку."""
        from src.dating.mailing import mailing_person_fields

        got = mailing_person_fields({'name': 'Brian Ritter', 'age': 51})

        assert got['firstname'] == 'Brian' and got['lastname'] == 'Ritter'
        assert got['name'] == 'Brian Ritter', 'полное имя должно остаться'

    def test_real_field_beats_the_derived_one(self):
        """Появись у сайта настоящее `firstname` — в дело идёт оно: разбор по
        пробелу это догадка, а присланное сайтом — факт."""
        from src.dating.mailing import mailing_person_fields

        got = mailing_person_fields({'name': 'Kevin', 'firstname': 'Kev'})

        assert got['firstname'] == 'Kev'

    def test_variable_resolves_to_the_first_name(self):
        """Переменная в общем виде — `companion_first_name` — берёт поле `firstname`."""
        from src.dating.mailing import mailing_companion_vars

        known = [{'name': 'companion_first_name', 'source': 'companion',
                  'title': '', 'default_value': ''},
                 {'name': 'companion_last_name', 'source': 'companion',
                  'title': '', 'default_value': ''}]
        rows = {r['name']: r for r in
                mailing_companion_vars({'name': 'Brian Ritter'}, known)}

        assert rows['companion_first_name']['value'] == 'Brian'
        assert rows['companion_last_name']['value'] == 'Ritter'

    def test_long_tail_wins_over_the_short_one(self):
        """⚠ Хвосты пробуются от длинного к короткому, и порядок здесь — всё.

        `companion_first_name` обязан взять `firstname`, а не `name`: возьми он
        короткий хвост, в обращение молча уехало бы **полное имя**. Ради этого
        ограничения переменные и звались `companion_firstName` — camelCase посреди
        snake_case; разбор научился длинным хвостам, и помесь убрана.
        """
        from src.dating.mailing import mailing_person_key

        fields = {'name': 'Brian Ritter', 'firstname': 'Brian', 'lastname': 'Ritter'}

        assert mailing_person_key('companion_first_name', fields) == 'firstname'
        assert mailing_person_key('companion_name', fields) == 'name'

    def test_camel_case_still_works(self):
        """⚠ Старая форма понимается по-прежнему, и это не совместимость ради неё.

        Имена переменных пишет человек — руками в реестре и в текстах шаблонов, — а
        сайт в другой инсталляции может держать camelCase до сих пор. Отказать ему
        значило бы остановить его рассылку накатом.
        """
        from src.dating.mailing import mailing_person_key

        fields = {'name': 'Brian Ritter', 'firstname': 'Brian'}

        assert mailing_person_key('companion_firstName', fields) == 'firstname'

    def test_single_word_leaves_the_variable_unfilled(self):
        """Пусто — законный исход: письмо не уйдёт, пока человек не решит, чем
        заменить. Это лучше, чем уйти с фамилией вместо имени."""
        from src.dating.mailing import mailing_companion_vars

        known = [{'name': 'companion_first_name', 'source': 'companion',
                  'title': '', 'default_value': ''}]
        row = mailing_companion_vars({'name': 'Kevin'}, known)[0]

        assert row['value'] == '' and row['filled'] is False


class TestDelivery:
    """⚠ Подвкладка «Доставка»: что ушло и чем кончилось.

    Заведена потому, что ушедшие инвайты попадали в «Диалоги» и составляли там
    большую часть списка: реплика без ответа выглядела разговором из одного
    сообщения. Разговор начинается с ответа, а ушедшее без ответа — это вопрос
    доставки, и у него теперь своё место.

    ⚠ **Спрашивается `delivery`, а не `sent,failed`**, и здесь это проверяется
    прямо. Неудачная отправка **оставляет реплику в пуле** (`draft` с причиной),
    чтобы её можно было повторить; спроси страница по статусам — журнал молчал бы
    ровно о тех инвайтах, ради которых на него и заходят.
    """

    async def test_delivery_shows_sent_and_failed_attempts(self, auth_client):
        from mysql_.mysql_ import mysql_get_db_async

        from src.uvicorn.routers.dating.invite import invite_list

        made = (await auth_client.post('/api/dating/profile', json={
            'site_id': STAND_SITE,
            'login': f'inv-{uuid.uuid4().hex[:8]}@example.com',
            'password': 'x'})).json()['result']
        try:
            async with mysql_get_db_async() as db:
                for uid, state, error in (('u-sent', 'sent', ''),
                                          ('u-stuck', 'draft', 'собеседника нет в чате'),
                                          ('u-fresh', 'draft', '')):
                    await db.execute(
                        'INSERT INTO invite'
                        ' (site_id, profile_id, uid, name, body, status, error)'
                        ' VALUES (%s, %s, %s, %s, %s, %s, %s)',
                        (STAND_SITE, made, uid, 'X', 'hi', state, error))
                await db.connection.commit()

            got = await invite_list(site_id=STAND_SITE, status_='delivery',
                                           user=None)
            uids = {str(row['uid']) for row in got['result']}

            assert 'u-sent' in uids, 'ушедшего нет в журнале'
            assert 'u-stuck' in uids, 'неудавшаяся отправка потерялась'
            assert 'u-fresh' not in uids, 'нетронутый черновик попал в журнал'
        finally:
            await auth_client.delete(f'/api/dating/profile/{made}')


class TestRoundReadsTheClock:
    """⚠ Круг сверяет смены с **минутами суток**, а не со сдвигом пояса.

    Живая поломка, и тихая: в `schedule_now` уезжал `offset` (то есть
    `3`), и проверка сравнивала расписание с 00:03 — в смену `07:45–22:15` это не
    попадает никогда. Круг исправно находил сайт, молча его пропускал и возвращал
    `sites: 0`. Включённая на странице генерация не давала ни одной реплики, а
    понять это по логу было нечем: он пишет только про сделанное.

    Отсюда и тест: не «работает ли круг», а **что именно он передаёт** в проверку
    смен. Два числа отличаются на три порядка, и ошибка в них не видна глазом.
    """

    def test_minutes_of_the_local_day(self):
        import time

        from src.dating.invite_round import _minutes_now

        # Полдень по Гринвичу: при сдвиге +3 местное время — 15:00.
        noon = 12 * 3600
        before = time.time
        try:
            time.time = lambda: noon

            assert _minutes_now(0) == 12 * 60
            assert _minutes_now(3) == 15 * 60
            # ⚠ Сутки замыкаются: 23:00 плюс три часа — это 02:00 следующих.
            time.time = lambda: 23 * 3600

            assert _minutes_now(3) == 2 * 60
        finally:
            time.time = before

    def test_the_round_passes_minutes_not_the_offset(self):
        """⚠ Сторож самой подстановки: в проверку смен должно уходить число из
        `_minutes_now`, а не сам сдвиг.

        Проверяем по исходнику — позвать круг без базы нельзя, а разница между
        `schedule_now(shifts, offset)` и `(shifts, minutes)` ровно в одном
        слове.
        """
        import inspect

        from src.dating import invite_round

        code = inspect.getsource(invite_round.invite_round_run)

        assert 'schedule_now(shifts, minutes)' in code, \
            'в проверку смен снова уходит не то число'
        assert 'schedule_now(shifts, offset)' not in code, \
            'сдвиг часового пояса снова передан как минуты суток'


class TestAlienName:
    """Нелатинское имя мешает, **только когда шаблон его просит**.

    ⚠⚠ **Это изменение к лучшему, и оно неожиданное.** Прежде такая пара
    отбрасывалась всегда: агент писал по-английски и `許萊納` в реплику не
    вставлял — проверено дважды на живой ленте Ladadate, исход один. Теперь имя
    нужно лишь трети шаблонов, и в остальных случаях человек остаётся в работе.
    """

    async def test_alien_name_is_skipped_when_the_slot_is_there(self, monkeypatch):
        got = await _made_with(monkeypatch, f'{MAN_SLOT}, привет')

        assert got['made'] == 1 and got['skipped'] == 1
        assert any('латиниц' in one for one in got['reasons']), got['reasons']

    async def test_alien_name_is_fine_without_the_slot(self, monkeypatch):
        """Без метки имя не нужно вовсе — карточка делается обоим."""
        got = await _made_with(monkeypatch, 'Спорим, ты сейчас улыбнулся?')

        assert got['made'] == 2 and got['skipped'] == 0


class TestOneCallPerPool:
    """⚠⚠ Вызов агента — **один на заход**, сколько бы карточек ни вышло.

    Ради этого всё и делалось: пул в полсотни карточек стоил полусотни вызовов
    LLM и минут ожидания у нажатой кнопки.
    """

    async def test_agent_is_asked_once(self, monkeypatch):
        calls = []

        # ⚠ Подпись повторяет живую целиком, включая жребий на приём и длину:
        # заглушка, не принявшая их, ловила бы `TypeError` вместо проверяемого
        # правила.
        async def _template(site_id, language, with_time=True,
                            girl_slot=False, man_slot=False,
                            angle='', length='', avoid=None, country='',
                            how_many=1):
            calls.append(site_id)

            # ⚠ Список, а не строка: агент теперь присылает столько текстов,
            # сколько девушек в сети (`invite._templates`).
            return ['Спорим, ты сейчас улыбнулся?'] * max(1, int(how_many))

        monkeypatch.setattr('src.dating.invite.invite_template', _template)
        got = await _made_with(monkeypatch, None, people=[
            {'uid': str(one), 'name': f'M{one}'} for one in range(1, 8)])

        assert got['made'] == 7, got
        assert len(calls) == 1, f'агента позвали {len(calls)} раз вместо одного'

    async def test_silent_agent_makes_nothing(self, monkeypatch):
        """Шаблона нет — карточек нет, и это не отказ: следующий заход попросит
        новый."""
        got = await _made_with(monkeypatch, '')

        assert got['made'] == 0
        assert got['reasons'], 'причина простоя не названа'


async def _made_with(monkeypatch, template, people=None) -> dict:
    """Заход набора с подменённым агентом и базой. Возвращает его итог."""
    from src.dating import invite

    if template is not None:
        async def _template(site_id, language, with_time=True,
                            girl_slot=False, man_slot=False,
                            angle='', length='', avoid=None, country='',
                            how_many=1):
            # Заглушке дают строку — превращаем в список, как отвечает живой
            # агент. ⚠ Пустая строка значит «агент промолчал»: у живого это
            # пустой список, а не список с пустым текстом.
            if not isinstance(template, str):
                return template

            return [template] * max(1, int(how_many)) if template else []

        monkeypatch.setattr(invite, 'invite_template', _template)

    monkeypatch.setattr(invite, '_girls', _answer([{'id': 1, 'profile': '{}'}]))
    monkeypatch.setattr(invite, '_people', _answer(people or [
        {'uid': 'a1', 'name': '許萊納'}, {'uid': 'a2', 'name': 'Brian'}]))
    monkeypatch.setattr(invite, '_invited', _answer(set()))
    monkeypatch.setattr(invite, '_talked', _answer(set()))
    monkeypatch.setattr(invite, '_pool_uids', _answer(set()))
    monkeypatch.setattr(invite, '_avoid', _answer([]))
    monkeypatch.setattr(invite, 'block_pairs', _answer(set()))
    monkeypatch.setattr(invite, '_language', _answer('en'))
    monkeypatch.setattr(invite, '_put', _nothing())

    return await invite.invite_make(STAND_SITE, 50)


def _answer(value):
    async def _got(*args, **kwargs):
        return value

    return _got


def _nothing():
    """Подмена `_put`: в базу ничего не пишет, но отвечает как удача.

    ⚠ `True`, а не `None`: `_put` возвращает, легла ли строка, и круг считает
    сделанной только легшую. Верни заглушка `None` — набор считал бы, что ключ не
    пустил, и `made` был бы нулём при живом агенте.
    """
    async def _got(*args, **kwargs):
        return True

    return _got


class TestVariety:
    """⚠⚠ Разнообразие задаётся снаружи, а не просьбой к модели.

    **Поймано живым прогоном.** Промпт просил «игриво и с провокацией», и модель
    слушалась — но одинаково: три шаблона из трёх начинались с одного оборота,
    «I bet you're trouble», «You look like trouble», «I have a feeling you're
    trouble». Просьба «будь разной» этого не лечит: у модели сильные притяжения,
    и температура их не ломает.

    Лечится тем же приёмом, что и метки имён: жребий бросаем мы. Три оси — приём,
    длина и список «так уже было», — и модель получает не пожелание, а указание.
    """

    async def test_the_angle_and_length_reach_the_agent(self, monkeypatch):
        """Приём и длина уезжают агенту переменными, а не остаются у нас."""
        from src.dating import invite

        sent = {}

        async def _template(site_id, language, with_time=True,
                            girl_slot=False, man_slot=False,
                            angle='', length='', avoid=None, country='', how_many=1):
            sent.update({'angle': angle, 'length': length, 'avoid': avoid})

            return 'текст'

        async def _avoid(site_id):
            return []

        monkeypatch.setattr(invite, 'invite_template', _template)
        monkeypatch.setattr(invite, '_avoid', _avoid)

        await invite._templates(STAND_SITE, 'en', 1)

        assert sent['angle'] in invite.INVITE_ANGLES
        assert sent['length'] in invite.INVITE_LENGTHS

    async def test_the_dice_actually_roll(self, monkeypatch):
        """⚠ Жребий **на каждый заход**, а не один раз при запуске.

        Выбери мы приём однажды — пул выходил бы разнообразным между сайтами и
        одинаковым внутри дня, то есть ровно там, где это видно человеку.
        """
        from src.dating import invite

        seen = set()

        async def _template(site_id, language, with_time=True,
                            girl_slot=False, man_slot=False,
                            angle='', length='', avoid=None, country='', how_many=1):
            seen.add((angle, length))

            return 'текст'

        async def _avoid(site_id):
            return []

        monkeypatch.setattr(invite, 'invite_template', _template)
        monkeypatch.setattr(invite, '_avoid', _avoid)

        for _ in range(30):
            await invite._templates(STAND_SITE, 'en', 1)

        # Восемь приёмов на четыре длины: за тридцать бросков сочетаний обязано
        # набраться заметно больше одного. Точного числа не требуем — это жребий.
        assert len(seen) > 5, f'жребий не бросается: сочетаний всего {len(seen)}'

    async def test_the_angles_differ_by_substance(self):
        """⚠ Приёмы отличаются **по существу**, а не по словам.

        «Дерзкий вопрос» и «игривый вопрос» дали бы одно и то же: вопрос. Список
        разводит сами заходы — вопрос, вызов, признание, игра, — и только поэтому
        работает.
        """
        from src.dating.invite import INVITE_ANGLES

        assert len(INVITE_ANGLES) >= 6, 'приёмов мало — пул будет однообразным'
        assert len(set(INVITE_ANGLES)) == len(INVITE_ANGLES)

    async def test_the_lengths_cover_both_ends(self):
        """От трёх слов до абзаца: однообразие длины видно так же, как слов."""
        from src.dating.invite import INVITE_LENGTHS

        together = ' '.join(INVITE_LENGTHS)
        assert 'коротко' in together and 'абзац' in together

    def test_the_avoid_list_is_numbered(self):
        """Список прошлых шаблонов идёт перечнем, а не одним текстом с переносами."""
        from src.dating.mail_agent import _avoid_text

        assert _avoid_text(['раз', 'два']) == '1. раз\n2. два'

    def test_an_empty_avoid_list_is_empty_text(self):
        """⚠ Пусто — пустая строка: заголовок «так уже было» без единой строки под
        ним модель читает как противоречие и начинает выдумывать, чего избегать.

        Прячет его условный блок в промпте у соседа (`%if invite_avoid%`), и
        пустая строка — то, на что он смотрит.
        """
        from src.dating.mail_agent import _avoid_text

        assert _avoid_text([]) == '' and _avoid_text(None) == ''
        assert _avoid_text(['', '   ']) == '', 'пробелы просочились в список'


class TestNoRawMarks:
    """⚠⚠ Сырой метки в готовом инвайте не бывает. Ни при каких условиях.

    **Поймано в живом пуле прода: 23 карточки из 36** с текстом «I'm curious,
    [[companion_name]], what's on your mind?» — и это при том, что имя у человека
    есть: Paulo, CLAY, Chris.

    Причин было две, и обе здесь закрыты.

    **Корень.** `people_name_parts` отвечает пустотой на имя без пробела —
    так задумано **для писем**, где промпт сам решает через `%if%`, чем звать.
    Инвайт брал ответ буквально, а `mail_render` на пустое значение
    оставляет метку в тексте.

    **Страховка.** Даже с верным именем метка может дожить до отправки: человек
    правит текст на странице руками, а пул мог быть набран прежней версией. Письмо
    в таком случае прочтут и поправят, инвайт уносит круг — и `[[…]]` уедет в чат
    живому человеку прямо в скобках.
    """

    def test_a_one_word_name_is_a_name(self):
        """⚠ «Paulo» — это имя, а не «имени нет»."""
        got = _fill("I'm curious, [[companion_name]], what's on your mind?",
                    {'profile': '{}'}, {'name': 'Paulo'})

        assert got == "I'm curious, Paulo, what's on your mind?"

    def test_a_full_name_still_gives_the_first_word(self):
        """А двусловное по-прежнему режется: в чате зовут «Brian», не «Brian Ritter»."""
        got = _fill('Hey [[companion_name]]!', {'profile': '{}'}, {'name': 'Brian Ritter'})

        assert got == 'Hey Brian!'

    def test_a_missing_name_leaves_no_mark(self):
        """Имени нет вовсе — метки в тексте всё равно не остаётся."""
        got = _fill('Hey [[companion_name]], guess what?', {'profile': '{}'}, {'name': ''})

        assert '[[' not in got and ']]' not in got

    def test_stripping_does_not_leave_double_punctuation(self):
        """⚠ Вырезаем **вместе с лишним знаком**: «curious, , what» читается как
        опечатка, то есть беда остаётся, просто выглядит иначе."""
        from src.dating.invite import invite_marks_strip

        got = invite_marks_strip("I'm curious, [[companion_name]], what's up?")

        assert got == "I'm curious, what's up?"
        assert ', ,' not in got

    def test_a_leading_mark_keeps_the_capital(self):
        """«[[girl_name]] здесь» без этого стало бы «здесь» со строчной."""
        from src.dating.invite import invite_marks_strip

        assert invite_marks_strip('[[girl_name]] здесь, и мне скучно') \
               == 'Здесь, и мне скучно'

    def test_a_trailing_mark_leaves_no_dangling_comma(self):
        from src.dating.invite import invite_marks_strip

        assert invite_marks_strip('скажи мне, [[companion_name]]') == 'Скажи мне'

    def test_clean_text_is_untouched(self):
        """⚠ Текст без меток обязан пройти **байт в байт**: страховка не вправе
        переписывать то, что и так верно."""
        from src.dating.invite import invite_marks_strip

        was = 'Спорим, ты сейчас улыбнулся?'
        assert invite_marks_strip(was) == was

    def test_sending_strips_marks_too(self):
        """⚠ Последний рубеж — сама отправка: между набором и ею стоит человек.

        Проверяем по исходнику: позвать отправку без базы и живого сайта дороже,
        чем она стоит, а строка эта — единственное, что держит правило.
        """
        import inspect

        from src.dating import invite_send

        code = inspect.getsource(invite_send)

        assert 'invite_marks_strip(str(invite[' in code, \
            'отправка снова берёт текст как есть — метка уедет в чат'


class TestPoolRefills:
    """⚠⚠ Пул добирается порциями, а не ждёт, пока опустеет.

    **Поймано на живом проде: пул застыл навсегда.** Здесь стояло «набираем только
    пустой пул» — довод был, что шаблон один на заход и доложенные карточки лягут
    вторым текстом вперемешку с первым. Эстетически верно, а на деле:

        в пуле 21 черновик, отправка инвайтов выключена (её включают отдельным
        выключателем) → пул не расходуется → не опустеет → круг не наберёт
        никогда.

    Со стороны это выглядело как «работает, но пул не растёт»: круг исправно чистил
    ушедших собеседников и не добавлял ничего.

    ⚠ **Разные тексты в пуле оказались пользой.** Пул, набранный с нуля, выходит
    одним текстом на всех; дальше он расходуется отправкой, и каждый следующий
    заход добирает израсходованное **своим** текстом.
    """

    def test_the_round_refills_a_partial_pool(self):
        """Условие набора — «меньше потолка», а не «пусто».

        Проверяем по исходнику: позвать круг без базы и соседа дороже, чем он
        стоит, а строка эта — единственное, что держит правило.
        """
        import inspect

        from src.dating import invite_round

        code = inspect.getsource(invite_round.invite_round_run)

        assert '_pool_max(site_id) - await invite_pool_left' in code, \
            'круг снова ждёт пустого пула — при выключенной отправке он застынет'

    async def test_the_batch_is_not_a_setting_of_its_own(self, auth_client):
        """⚠⚠ Сколько карточек за заход — вопрос уже отвеченный, размером пула.

        Здесь недолго стояла отдельная настройка «сколько человек получат один
        текст», и завёл её я, а не задача: круг брал порцию в десять, число это
        ничем не выводилось, и настройка была попыткой оправдать его задним
        числом. Заказчик отверг — верно: два места, где заводят одно и то же
        число, расходятся на первой же правке, и человек не знает, какое из них
        главнее.

        Круг набирает всё, чего не хватает до `invite_pool_max`.
        """
        got = (await auth_client.get('/api/dating/site')).json()['result']
        keys = {row['key'] for row in got['known']}

        assert 'invite_pool_max' in keys
        assert 'invite_batch' not in keys, \
            'вернулась вторая настройка о том же числе'

    def test_the_round_takes_everything_missing(self):
        """Круг просит ровно недостающее, без своей мерки поверх."""
        import inspect

        code = inspect.getsource(
            __import__('src.dating.invite_round', fromlist=['x']
                       ).invite_round_run)

        assert 'invite_make(site_id, left)' in code, \
            'круг снова режет добор своей меркой'

    def test_failed_drafts_do_not_count_as_full(self):
        """⚠ Застрявшие с ошибкой в счёт не идут.

        Отправитель их не берёт — их дошлёт человек, — и одна такая строка иначе
        считалась бы за живую: пул выглядел бы полным, не будучи им.
        """
        import inspect

        from src.dating import invite_round

        code = inspect.getsource(invite_round.invite_round_run)

        assert 'ready_only=True' in code, \
            'застрявшие карточки снова считаются годными — добор встанет'


class TestNowAndPlace:
    """⚠⚠ Агент знает, который час и откуда девушка.

    **Поймано в живом пуле прода:**

        «My thoughts have turned sideways **tonight**, spinning loops of quiet…»

    Модель писала про вечер, не зная, вечер сейчас или утро. Получатель читает
    реплику в своё время, и перепутанное время суток выдаёт робота вернее любой
    другой приметы: живой человек про это не ошибается.

    ⚠ **Приём взят у чата, а не выдуман заново.** `dating_talk` давно держит шаг
    `datetime_current_get` и кладёт его ответ в промпт секцией «Текущее время».
    Инструмент отдаёт готовую фразу — день недели, дату, время и словесное время
    суток. Вторая реализация времени разошлась бы с первой на первой же правке.

    ⚠ **Страна — настройка сайта, а не анкеты.** Шаблон один на всех девушек
    сайта, и страна конкретной анкеты была бы враньём для остальных сорока
    девяти.
    """

    async def test_the_timezone_goes_to_the_agent(self, monkeypatch):
        """Часовой пояс уезжает той же переменной, что у чата."""
        from src.dating import mail_agent

        said = {}

        async def _ask(**kwargs):
            said.update(kwargs.get('variables') or {})

            return 'Привет!'

        monkeypatch.setattr(mail_agent, 'rhapsody_api_chat_ask', _ask)
        monkeypatch.setattr(mail_agent, '_agent_id',
                            lambda *a, **k: _async(7))

        await mail_agent.invite_template(343, 'en', country='Украина')

        assert 'datetime_current_get_zone' in said, \
            'агент снова не знает времени — вернутся реплики про вечер в полдень'
        assert said['invite_country'] == 'Украина'

    async def test_the_timezone_is_the_panel_one(self, monkeypatch):
        """⚠ Пояс берётся из настройки панели, а не пишется числом в коде.

        По той же настройке сдвигаются смены расписания и метки времени на
        страницах. Заведи мы второй источник — агент писал бы «доброе утро»
        ровно тогда, когда у панели смена ещё ночная.
        """
        import inspect

        from src.dating import mail_agent

        code = inspect.getsource(mail_agent._timezone)

        assert "setting_get('timezone'" in code

    async def test_an_empty_country_is_allowed(self, monkeypatch):
        """Пустая страна — законный случай: агент про место молчит.

        Лучше молчание, чем догадка: приписать девушке чужую страну хуже, чем
        не упоминать её вовсе.
        """
        from src.dating import mail_agent

        said = {}

        async def _ask(**kwargs):
            said.update(kwargs.get('variables') or {})

            return 'Привет!'

        monkeypatch.setattr(mail_agent, 'rhapsody_api_chat_ask', _ask)
        monkeypatch.setattr(mail_agent, '_agent_id', lambda *a, **k: _async(7))

        await mail_agent.invite_template(343, 'en')

        assert said['invite_country'] == ''

    async def test_the_country_is_a_site_setting(self, auth_client):
        """Ключ в реестре — иначе страну негде задать."""
        got = (await auth_client.get('/api/dating/site')).json()['result']
        known = {row['key']: row for row in got['known']}

        assert 'girl_country' in known
        assert known['girl_country']['default'] == '', \
            'у страны появилось умолчание — сайт без настройки соврёт о месте'

    def test_the_maker_asks_the_setting(self):
        """Набор пула читает настройку, а не константу."""
        import inspect

        from src.dating import invite

        assert 'country=await _country(site_id)' in inspect.getsource(
            invite._templates)


def _async(value):
    """Готовая корутина с этим значением — для подмены `_agent_id`."""
    async def _got():
        return value

    return _got()
