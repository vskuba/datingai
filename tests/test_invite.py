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

from src.dating.dating_invite import _cards, _fill
from src.dating.dating_mail_agent import (DATING_INVITE_LANGUAGE,
                                          DATING_INVITE_MAX,
                                          DATING_INVITE_SLOT_GIRL,
                                          DATING_INVITE_SLOT_MAN, _invite)

pytestmark = pytest.mark.chat

# Стенд у соседа: круги воркера про него не знают — его нет в `dating_site`.
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

MAN_SLOT = f'[[{DATING_INVITE_SLOT_MAN}]]'
GIRL_SLOT = f'[[{DATING_INVITE_SLOT_GIRL}]]'


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
        got = _invite('А' * (DATING_INVITE_MAX + 100))

        assert len(got) == DATING_INVITE_MAX

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
    `dating_answer.DATING_ANSWER_LANGUAGE`), то есть пул был бы пулом
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
               == DATING_INVITE_LANGUAGE

    async def test_slots_are_asked_by_us(self, monkeypatch):
        """⚠ Какие метки ставить — говорим мы, а не решает модель.

        «Вставляй имя изредка» она понимает как «вставляй всегда»: вероятность у
        неё стилистическая. Жребий (5% и 30%) бросает панель, а сюда едет прямое
        указание.
        """
        got = await _asked_with(language='en', man_slot=True)

        assert DATING_INVITE_SLOT_MAN in got.get('invite_slots', '')

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
    from src.dating import dating_mail_agent as agent

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
        await agent.dating_invite_template(343, language, girl_slot, man_slot)
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
        from src.dating.dating_people import dating_people_name_parts

        assert dating_people_name_parts('Brian Ritter') == {
            'firstname': 'Brian', 'lastname': 'Ritter'}

    def test_first_word_wins_not_the_last(self):
        """⚠ Делим по **первому** пробелу: «JULIO RODRIGO ZAPATA OLIVA» — это
        Хулио и четыре фамилии, а не наоборот. Где кончается имя, мы не знаем, а
        первое слово угадывается всегда."""
        from src.dating.dating_people import dating_people_name_parts

        assert dating_people_name_parts('JULIO RODRIGO ZAPATA OLIVA') == {
            'firstname': 'JULIO', 'lastname': 'RODRIGO ZAPATA OLIVA'}

    def test_single_word_gives_nothing(self):
        from src.dating.dating_people import dating_people_name_parts

        assert dating_people_name_parts('Kevin') == {'firstname': '', 'lastname': ''}

    def test_underscores_are_not_spaces(self):
        """⚠ `Tall_Good_Man` — одно слово, хотя выглядит как три. Разбери мы его
        по подчёркиванию, человека звали бы «Tall»."""
        from src.dating.dating_people import dating_people_name_parts

        assert dating_people_name_parts('Tall_Good_Man')['firstname'] == ''

    def test_extra_spaces_do_not_make_parts(self):
        from src.dating.dating_people import dating_people_name_parts

        assert dating_people_name_parts('  Ales   kumer ') == {
            'firstname': 'Ales', 'lastname': 'kumer'}

    def test_empty_name_is_not_a_crash(self):
        from src.dating.dating_people import dating_people_name_parts

        assert dating_people_name_parts(None) == {'firstname': '', 'lastname': ''}


class TestCompanionVars:
    """Чем подставятся `companion`-переменные — вместе с выводимыми частями имени."""

    def test_parts_are_derived_not_stored(self):
        """⚠ Части имени выводятся из `name`, а не лежат в карточке: заведи мы под
        них колонки, они разошлись бы с именем в первую же правку."""
        from src.dating.dating_mailing import dating_mailing_person_fields

        got = dating_mailing_person_fields({'name': 'Brian Ritter', 'age': 51})

        assert got['firstname'] == 'Brian' and got['lastname'] == 'Ritter'
        assert got['name'] == 'Brian Ritter', 'полное имя должно остаться'

    def test_real_field_beats_the_derived_one(self):
        """Появись у сайта настоящее `firstname` — в дело идёт оно: разбор по
        пробелу это догадка, а присланное сайтом — факт."""
        from src.dating.dating_mailing import dating_mailing_person_fields

        got = dating_mailing_person_fields({'name': 'Kevin', 'firstname': 'Kev'})

        assert got['firstname'] == 'Kev'

    def test_variable_resolves_to_the_first_name(self):
        """Переменная в общем виде — `companion_first_name` — берёт поле `firstname`."""
        from src.dating.dating_mailing import dating_mailing_companion_vars

        known = [{'name': 'companion_first_name', 'source': 'companion',
                  'title': '', 'default_value': ''},
                 {'name': 'companion_last_name', 'source': 'companion',
                  'title': '', 'default_value': ''}]
        rows = {r['name']: r for r in
                dating_mailing_companion_vars({'name': 'Brian Ritter'}, known)}

        assert rows['companion_first_name']['value'] == 'Brian'
        assert rows['companion_last_name']['value'] == 'Ritter'

    def test_long_tail_wins_over_the_short_one(self):
        """⚠ Хвосты пробуются от длинного к короткому, и порядок здесь — всё.

        `companion_first_name` обязан взять `firstname`, а не `name`: возьми он
        короткий хвост, в обращение молча уехало бы **полное имя**. Ради этого
        ограничения переменные и звались `companion_firstName` — camelCase посреди
        snake_case; разбор научился длинным хвостам, и помесь убрана.
        """
        from src.dating.dating_mailing import dating_mailing_person_key

        fields = {'name': 'Brian Ritter', 'firstname': 'Brian', 'lastname': 'Ritter'}

        assert dating_mailing_person_key('companion_first_name', fields) == 'firstname'
        assert dating_mailing_person_key('companion_name', fields) == 'name'

    def test_camel_case_still_works(self):
        """⚠ Старая форма понимается по-прежнему, и это не совместимость ради неё.

        Имена переменных пишет человек — руками в реестре и в текстах шаблонов, — а
        сайт в другой инсталляции может держать camelCase до сих пор. Отказать ему
        значило бы остановить его рассылку накатом.
        """
        from src.dating.dating_mailing import dating_mailing_person_key

        fields = {'name': 'Brian Ritter', 'firstname': 'Brian'}

        assert dating_mailing_person_key('companion_firstName', fields) == 'firstname'

    def test_single_word_leaves_the_variable_unfilled(self):
        """Пусто — законный исход: письмо не уйдёт, пока человек не решит, чем
        заменить. Это лучше, чем уйти с фамилией вместо имени."""
        from src.dating.dating_mailing import dating_mailing_companion_vars

        known = [{'name': 'companion_first_name', 'source': 'companion',
                  'title': '', 'default_value': ''}]
        row = dating_mailing_companion_vars({'name': 'Kevin'}, known)[0]

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

        from src.uvicorn.routers.dating.dating_invite import dating_invite_list

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
                        'INSERT INTO dating_invite'
                        ' (site_id, profile_id, uid, name, body, status, error)'
                        ' VALUES (%s, %s, %s, %s, %s, %s, %s)',
                        (STAND_SITE, made, uid, 'X', 'hi', state, error))
                await db.connection.commit()

            got = await dating_invite_list(site_id=STAND_SITE, status_='delivery',
                                           user=None)
            uids = {str(row['uid']) for row in got['result']}

            assert 'u-sent' in uids, 'ушедшего нет в журнале'
            assert 'u-stuck' in uids, 'неудавшаяся отправка потерялась'
            assert 'u-fresh' not in uids, 'нетронутый черновик попал в журнал'
        finally:
            await auth_client.delete(f'/api/dating/profile/{made}')


class TestRoundReadsTheClock:
    """⚠ Круг сверяет смены с **минутами суток**, а не со сдвигом пояса.

    Живая поломка, и тихая: в `dating_schedule_now` уезжал `offset` (то есть
    `3`), и проверка сравнивала расписание с 00:03 — в смену `07:45–22:15` это не
    попадает никогда. Круг исправно находил сайт, молча его пропускал и возвращал
    `sites: 0`. Включённая на странице генерация не давала ни одной реплики, а
    понять это по логу было нечем: он пишет только про сделанное.

    Отсюда и тест: не «работает ли круг», а **что именно он передаёт** в проверку
    смен. Два числа отличаются на три порядка, и ошибка в них не видна глазом.
    """

    def test_minutes_of_the_local_day(self):
        import time

        from src.dating.dating_invite_round import _minutes_now

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
        `dating_schedule_now(shifts, offset)` и `(shifts, minutes)` ровно в одном
        слове.
        """
        import inspect

        from src.dating import dating_invite_round

        code = inspect.getsource(dating_invite_round.dating_invite_round_run)

        assert 'dating_schedule_now(shifts, minutes)' in code, \
            'в проверку смен снова уходит не то число'
        assert 'dating_schedule_now(shifts, offset)' not in code, \
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

        async def _template(site_id, language, girl_slot=False, man_slot=False):
            calls.append(site_id)

            return 'Спорим, ты сейчас улыбнулся?'

        monkeypatch.setattr('src.dating.dating_invite.dating_invite_template', _template)
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
    from src.dating import dating_invite

    if template is not None:
        async def _template(site_id, language, girl_slot=False, man_slot=False):
            return template

        monkeypatch.setattr(dating_invite, 'dating_invite_template', _template)

    monkeypatch.setattr(dating_invite, '_girls', _answer([{'id': 1, 'profile': '{}'}]))
    monkeypatch.setattr(dating_invite, '_people', _answer(people or [
        {'uid': 'a1', 'name': '許萊納'}, {'uid': 'a2', 'name': 'Brian'}]))
    monkeypatch.setattr(dating_invite, '_invited', _answer(set()))
    monkeypatch.setattr(dating_invite, '_talked', _answer(set()))
    monkeypatch.setattr(dating_invite, '_pool_uids', _answer(set()))
    monkeypatch.setattr(dating_invite, 'dating_block_pairs', _answer(set()))
    monkeypatch.setattr(dating_invite, '_language', _answer('en'))
    monkeypatch.setattr(dating_invite, '_put', _nothing())

    return await dating_invite.dating_invite_make(STAND_SITE, 50)


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
