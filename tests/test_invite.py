"""Инвайты: разбор реплики агента и отбор «кого зовём».

⚠ **Наружу сюита не ходит** (`testing.md`): инвайт — это вызов LLM у соседа, а
отправка ещё и реплика живому человеку в чат. Проверяется то, что и ломается, —
две чистые функции:

* `_invite` — что взять из ответа агента. Требование к тексту одно и жёсткое:
  **имя собеседника внутри реплики**. Оно отличает написанное человеку от
  рассылки, и первая реплика — то место, где это решает, ответят или нет.
* `_pairs` — кого зовём и от кого. Здесь всё решается у нас, и цена ошибки высока
  в обе стороны: пропустим — человек не получит приглашения, возьмём лишнего —
  два «здравствуйте» от двух наших девушек одному мужчине.
"""

import pytest

import uuid

from src.dating.dating_invite import _pairs
from src.dating.dating_mail_agent import (DATING_INVITE_LANGUAGE,
                                          DATING_INVITE_MAX, _invite)

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


class TestInviteText:
    """Что берём из ответа агента."""

    def test_reply_with_the_name_is_taken(self):
        assert _invite('Brian, давно вы реставрируете мотоциклы?', 'Brian Ritter') \
               == 'Brian, давно вы реставрируете мотоциклы?'

    def test_name_may_stand_anywhere(self):
        """⚠ Место обращения выбирает модель, а не мы.

        Шаблон с зашитым местом («%s, а вы…») дал бы одну рамку на весь пул, и
        двадцать реплик подряд читались бы как двадцать копий.
        """
        assert _invite('Давно катаетесь, Brian?', 'Brian Ritter') \
               == 'Давно катаетесь, Brian?'

    def test_quotes_are_stripped(self):
        """Просили «только текст», а модель подаёт его репликой в кавычках — в
        чате они выглядят цитатой неизвестно кого."""
        assert _invite('«Давно катаетесь, Brian?»', 'Brian') \
               == 'Давно катаетесь, Brian?'

    def test_reply_without_the_name_is_dropped(self):
        """⚠ Имени нет — реплики нет.

        Отбрасываем, а не приписываем имя сами: приписанное встало бы в начало у
        всех инвайтов подряд, то есть вернуло бы ровно ту рамку, от которой уходим.
        Человек при этом не теряется: в пул он не лёг, и следующий заход круга
        возьмёт его заново.
        """
        assert _invite('Привет! Как дела?', 'Brian') == ''

    def test_model_reasoning_is_dropped(self):
        """Рассуждение модели про человека имени в себе не несёт — на этом и
        отсекается. ⚠ У вопроса в письме защита другая (знак вопроса на конце),
        и здесь она не годится: инвайт бывает и предложением."""
        assert _invite('Given the profile, I should ask Brian... no wait.', 'Frank') == ''

    def test_offer_without_a_question_mark_is_kept(self):
        """⚠ Знака вопроса **не требуем** — в отличие от вопроса в письме.

        Инвайт бывает предложением («расскажите, если захочется»), и правило
        «кончается на ?» отбрасывало бы годные реплики.
        """
        assert _invite('Frank, расскажите про свою мастерскую, если захочется.',
                       'Frank') \
               == 'Frank, расскажите про свою мастерскую, если захочется.'

    def test_unknown_companion_needs_no_name(self):
        """Имени в базе нет — проверять нечего, и общая реплика законна: сайт не
        всегда отдаёт имя, а без инвайта человек остался бы вовсе."""
        assert _invite('Расскажите про свои увлечения?', '') \
               == 'Расскажите про свои увлечения?'

    def test_short_name_is_not_checked(self):
        """⚠ Слова короче трёх букв не проверяем: они нашлись бы в любом тексте по
        совпадению, и проверка пропускала бы что угодно, изображая работу."""
        assert _invite('Расскажите про свои увлечения?', 'Li') \
               == 'Расскажите про свои увлечения?'

    def test_only_the_first_paragraph_is_taken(self):
        """Модель охотно поясняет свой выбор, и пояснение уехало бы в чат следом
        за репликой."""
        assert _invite('Brian, давно катаетесь?\n\nЯ спросил про мотоциклы, потому '
                       'что они у него в профиле.', 'Brian') \
               == 'Brian, давно катаетесь?'

    def test_long_reply_is_cut_not_dropped(self):
        """Предел держит правило «одна-две фразы» и тогда, когда модель просьбу
        пропустила. Режем, а не отбрасываем: обрезанная реплика полезнее
        человека, оставшегося без приглашения."""
        got = _invite('Brian, ' + 'очень длинная фраза. ' * 100, 'Brian')

        assert got and len(got) == DATING_INVITE_MAX


class TestPairs:
    """Кого зовём и от кого."""

    def test_one_invite_per_person(self):
        """⚠ Настоящая ошибка, поймана живым прогоном.

        У агентства две Дианы (#849 и #850), и первый заход выдал **два
        приветствия одному и тому же мужчине** — от обеих. Для него это не два
        знакомства, а агентство, которое выдало себя первой же репликой.

        Правило строже, чем ключ в базе (`(profile_id, uid)`): тот про то, что
        допустимо в истории, а это — про то, как набирается пул.
        """
        got = list(_pairs(GIRLS, PEOPLE, set(), set()))

        assert len(got) == len(PEOPLE), 'человек получил больше одного инвайта'
        assert sorted(person['uid'] for _, person in got) == ['111', '222', '333']

    def test_already_invited_is_skipped(self):
        """Позвали — больше не зовём, **какой бы анкетой это ни было**."""
        got = list(_pairs(GIRLS, PEOPLE, {'222'}, set()))

        assert [person['uid'] for _, person in got] == ['111', '333']

    def test_girls_who_talked_are_not_chosen(self):
        """⚠ Свободные — те, кто с ним не говорил.

        Выбирай мы наугад из всех, случай раз за разом выпадал бы на анкету, у
        которой с ним уже переписка, и человек оставался без инвайта, хотя другая
        девушка была свободна.
        """
        # С «111» говорили все, кроме #861.
        talked = {(849, '111'), (850, '111')}
        got = {person['uid']: girl['id'] for girl, person in
               _pairs(GIRLS, PEOPLE, set(), talked)}

        assert got['111'] == 861, 'выбрана анкета, у которой с ним переписка'

    def test_person_nobody_is_free_for_is_skipped(self):
        """Со всеми нашими он уже говорит — звать некому, и это не ошибка."""
        talked = {(girl['id'], '111') for girl in GIRLS}
        got = list(_pairs(GIRLS, PEOPLE, set(), talked))

        assert '111' not in [person['uid'] for _, person in got]

    def test_empty_uid_is_not_a_person(self):
        """Пустой номер из ленты — мусор, а не собеседник."""
        got = list(_pairs(GIRLS, [{'uid': '', 'name': 'X'}, PEOPLE[0]], set(), set()))

        assert [person['uid'] for _, person in got] == ['111']

    def test_order_follows_the_feed(self):
        """⚠ Порядок — тот, в каком пришла лента (приоритетные первыми).

        Потолок захода отрезает хвост, и отрезать он должен **тех, до кого не
        дошли**, а не тех, кого лента поставила вперёд.
        """
        got = [person['uid'] for _, person in _pairs(GIRLS, PEOPLE, set(), set())]

        assert got == ['111', '222', '333']


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

    async def test_companion_name_goes_separately(self, monkeypatch):
        """⚠ Имя — своей переменной, а не только внутри карточки: карточку модель
        читает целиком и обращение из неё берёт не всегда."""
        got = await _asked_with(language='en')

        assert got.get('invite_companion_name') == 'Brian Ritter'


async def _asked_with(language: str) -> dict:
    """Какие переменные ушли бы агенту. Транспорт подменён: проверяется вызов."""
    from src.dating import dating_mail_agent as agent

    sent = {}

    async def _agent_id(*args, **kwargs):
        return 7

    async def _ask(agent_id, question, companion_id=1, variables=None):
        sent.update(variables or {})

        return 'Brian, давно катаетесь?'

    real_id, real_ask = agent._agent_id, agent.rhapsody_api_chat_ask
    agent._agent_id = _agent_id
    agent.rhapsody_api_chat_ask = _ask
    try:
        await agent.dating_invite_write(
            {'id': 849, 'site_id': 343, 'login': 'd@e.com'}, 343,
            {'uid': '111', 'name': 'Brian Ritter'}, language)
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


class TestAlienNameIsCheap:
    """Имя чужой письменности отсекается **до** агента, а не после.

    ⚠ Реплика без обращения всё равно будет отброшена, но узнать об этом ценой
    вызова LLM — плата ни за что, и платится она каждый заход: человек в сети,
    значит попадает в ленту снова.
    """

    async def test_alien_name_never_reaches_the_agent(self, monkeypatch):
        from src.dating import dating_invite

        asked = []

        async def _body(girl, person, site_id, language=''):
            asked.append(str(person.get('name')))

            return 'Hi!'

        monkeypatch.setattr(dating_invite, '_body', _body)
        monkeypatch.setattr(dating_invite, '_girls', _answer([{'id': 1}]))
        monkeypatch.setattr(dating_invite, '_people', _answer(
            [{'uid': 'a1', 'name': '許萊納'}, {'uid': 'a2', 'name': 'Brian'}]))
        monkeypatch.setattr(dating_invite, '_invited', _answer(set()))
        monkeypatch.setattr(dating_invite, '_talked', _answer(set()))
        monkeypatch.setattr(dating_invite, '_language', _answer('en'))
        monkeypatch.setattr(dating_invite, '_put', _nothing())

        got = await dating_invite.dating_invite_make(STAND_SITE, 5)

        assert asked == ['Brian'], f'агента позвали зря: {asked}'
        assert got['made'] == 1 and got['skipped'] == 1
        assert any('латиниц' in one for one in got['reasons']), got['reasons']


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
