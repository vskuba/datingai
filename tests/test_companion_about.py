"""Анкета собеседника с сайта: разбор текста и правило «ходим один раз».

⚠ **Наружу сюита не ходит** (`testing.md`): сбор — это вход браузером под живой
анкетой и полсотни секунд. Поэтому проверяется то, что и ломается, — **разбор
снятого текста**: он чистая функция над строкой, и подписи полей меняет сайт, а не
мы.

Образец взят с живого прогона (мужчина 352443, LadaDate): так текст и приходит —
одной строкой, где значение поля это всё между его подписью и следующей.
"""

import pytest

from src.dating.companion_about import (companion_about_english,
                                               companion_about_head,
                                               companion_about_parse,
                                               companion_about_place,
                                               companion_about_text)

pytestmark = pytest.mark.mail

# Живой ответ сценария, слово в слово. ⚠ Не выдуманный: выдуманный проверял бы
# нашу же мысль о том, как выглядит страница, а не саму страницу.
LIVE = (
    "Id: 352443 Location London, United Kingdom Occupation Musician Marital Status "
    "Single Children None Height 140 cm Weight 53 kg Hair Color Light Brown "
    "Hair Style Eyes Color Blue Body Type Athletic Birthday (Pisces) 22 Feb 1968 "
    "Religion Not Religious Ethnicity Caucasian Education University Smoke "
    "On Occasions Drink On Occasions Level of English About Myself Hi, I'm a "
    "Composer Musician living in London . Worked hard in Schools and theatre. "
    "Looking for a girl to enjoy my nice life... More Hobby music, Composing , "
    "walking, Talking, Musing, partying, Coffee About Partner (age 18 - 35) "
    "Chilled but exciting."
)

# Анкета, которую человек не заполнял: подписи есть, значений нет.
EMPTY = (
    'Id: 275296 Location Stuttgart, Germany Occupation Marital Status Widowed '
    'Children None Height 175 cm Weight 85 kg Hair Color Dark Brown Hair Style '
    'Eyes Color Blue Body Type Average Birthday (Libra) 17 Oct 1961 Religion '
    'Not Religious Ethnicity White/European Education University Smoke No Drink '
    'On Occasions Level of English About Partner (age 18 - 40)'
)


class TestParse:
    """Разбор текста на поля."""

    def test_fields_are_read(self):
        got = companion_about_parse(LIVE)

        assert got['Id:'] == '352443'
        assert got['Occupation'] == 'Musician'
        assert got['Marital Status'] == 'Single'
        assert got['Education'] == 'University'

    def test_value_is_everything_up_to_the_next_label(self):
        """⚠ Значение — всё между своей подписью и следующей.

        Иначе пришлось бы знать, из скольких слов состоит каждое, а «Location» это
        и «London», и «Stuttgart, Germany», и «New York, United States».
        """
        got = companion_about_parse(LIVE)

        assert got['Location'] == 'London, United Kingdom'
        assert got['Hobby'].startswith('music, Composing')

    def test_empty_field_is_dropped(self):
        """У незаполненной анкеты между подписями пусто — такого поля быть не
        должно вовсе. ⚠ Иначе агент прочитал бы пустоту как факт."""
        got = companion_about_parse(EMPTY)

        assert 'Occupation' not in got, 'пустое занятие попало в поля'
        assert 'About Myself' not in got
        # А заполненные рядом — на месте: пустота одного не роняет соседей.
        assert got['Marital Status'] == 'Widowed'

    @pytest.mark.parametrize('text', ['', None, '   ', 'ничего похожего'])
    def test_junk_gives_nothing(self, text):
        assert companion_about_parse(text) == {}


class TestAbout:
    """Описание для карточки."""

    def test_only_what_makes_a_question(self):
        """⚠ Берём не всё: рост, вес и цвет глаз вопроса не рождают, а описание
        читает агент — лишнее в нём соревнуется за внимание с полезным."""
        about = companion_about_text(companion_about_parse(LIVE))

        assert 'Занятие: Musician' in about
        assert 'Увлечения:' in about
        assert 'Height' not in about and '140 cm' not in about
        assert 'Eyes' not in about

    def test_id_and_location_stay_out(self):
        """⚠ Номер у карточки свой, город и страна лежат отдельными колонками.
        Дублируй мы их в описании, правка одного места расходилась бы с другим."""
        about = companion_about_text(companion_about_parse(LIVE))

        assert '352443' not in about
        assert 'London, United Kingdom' not in about

    def test_more_tail_is_cut(self):
        """⚠ «... More» — это кнопка сайта, а не слова человека.

        В описании она попадает агенту как часть рассказа о себе: «Looking for a
        girl to enjoy my nice life... More».
        """
        about = companion_about_text(companion_about_parse(LIVE))

        assert 'More' not in about
        assert 'nice life…' in about, 'обрыв не отмечен многоточием'

    def test_unfilled_fields_leave_no_lines(self):
        """У анкеты 275296 пусты «Occupation» и «About Myself» — этих строк в
        описании быть не должно. А заполненное рядом остаётся: пустота одного поля
        не повод терять соседей."""
        about = companion_about_text(companion_about_parse(EMPTY))

        assert 'Занятие' not in about, 'пустое занятие попало в описание'
        assert 'О себе' not in about
        assert 'Семейное положение: Widowed' in about

    def test_nothing_at_all_gives_nothing(self):
        """Совсем пустой ответ — законный исход, а не сбой: страница могла не
        открыться, и описание останется пустым."""
        assert companion_about_text(companion_about_parse('')) == ''


class TestEnglishLabels:
    """⚠⚠ Описание хранится по-русски, а агенту уезжает по-английски.

    Поле «Описание» в карточке читает и правит человек, и панель у нас русская
    целиком; промпт же у соседа английский — узел Dating объявляет `en`, и рядом в
    том же вызове лежит английский `girl_bio`.

    ⚠ Перевод стоит **при сборке био**, а не при записи в базу. Иначе «починено»
    значило бы «починено у новых»: карточек на живом сайте 1636, а переснимаются
    они раз в две недели.
    """

    def test_known_labels_turn_into_the_site_ones(self):
        """⚠ Английская подпись не выдумана и не записана вторым списком — это
        левая колонка `COMPANION_ABOUT_FIELDS`, то есть подпись на самом сайте."""
        got = companion_about_english('О себе: likes sailing\n'
                                      'Увлечения: chess\n'
                                      'Семейное положение: Divorced')

        assert got == ('About Myself: likes sailing\n'
                       'Hobby: chess\n'
                       'Marital Status: Divorced')

    def test_the_value_is_never_touched(self):
        """⚠ Меняется только подпись: в хвосте слова человека, и нормализовать их
        по дороге здесь нечем и незачем."""
        got = companion_about_english('Занятие:   IT: поддержка серверов  ')

        assert got == 'Occupation:   IT: поддержка серверов  '

    def test_an_unknown_line_passes_as_it_is(self):
        """⚠ Описание правят свободным текстом, и выбросить непонятое значило бы
        потерять написанное человеком."""
        got = companion_about_english('Говорит, что был в Киеве\n'
                                      'Дети: None')

        assert got == 'Говорит, что был в Киеве\nChildren: None'

    def test_a_line_without_a_colon_is_left_alone(self):
        assert companion_about_english('просто строка') == 'просто строка'

    def test_empty_in_empty_out(self):
        assert companion_about_english('') == ''
        assert companion_about_english(None) == ''

    def test_every_label_the_writer_puts_has_a_translation(self):
        """⚠⚠ Сторож на расхождение: подписи ставит `companion_about_text`, а
        переводит их карта рядом. Появись поле в одной и не появись в другой —
        агент молча перестал бы понимать именно его.
        """
        about = companion_about_text(companion_about_parse(LIVE))
        assert about, 'образец перестал разбираться — проверять нечего'

        for line in companion_about_english(about).split('\n'):
            title = line.partition(':')[0]
            assert title.isascii(), f'подпись осталась без перевода: {title}'


class TestHead:
    """Имя и возраст из шапки анкеты.

    ⚠ **Снимаем со страницы, а не берём из ленты.** Анкету открывают и для тех,
    кого в лентах не было: адресата берут из пула, а пул живёт дольше снимка ленты.
    Без этого карточка выходила безымянной — и в списке её было не отличить от
    мусора, о чём человек и спросил.
    """

    @pytest.mark.parametrize('head, name, age', [
        ('Kevin , 58 SEND MESSAGE START LIVE CHAT ADD TO FAVORITES', 'Kevin', 58),
        ('Gerhard, 64 SEND MESSAGE', 'Gerhard', 64),
        # Двойное имя с дефисом и пробелами — режем по кнопке, не по пробелу.
        ('Jean-Paul Marie , 47 ADD TO FAVORITES', 'Jean-Paul Marie', 47),
        # Возраст сайт показывает не всегда.
        ('Chris Gunn START LIVE CHAT', 'Chris Gunn', 0),
    ])
    def test_name_and_age(self, head, name, age):
        assert companion_about_head(head) == (name, age)

    def test_header_without_a_name_gives_nothing(self):
        """⚠ Шапка без имени — не повод класть в карточку что попало.

        Резать нечего, и остаётся текст анкеты: «Id: 12345 Location Berlin». В
        списке собеседников это читается как поломка — лучше пусто, и страница
        покажет букву.
        """
        assert companion_about_head('Id: 12345 Location Berlin') == ('', 0)

    @pytest.mark.parametrize('head', ['', None, '   '])
    def test_nothing(self, head):
        assert companion_about_head(head) == ('', 0)


class TestJunk:
    """Мусор самого сайта: разметка внутри текста.

    ⚠ Поймано живым прогоном. Сайт отдаёт «о себе» с тегами — это следы его
    встроенного переводчика:

        <font style="vertical-align: inherit;">Mein Leben ist eine Achterbahn…

    В описание они уходили как есть, и агент читал их как слова.
    """

    def test_tags_are_stripped(self):
        got = companion_about_text({
            'About Myself': '<font style="vertical-align: inherit;">Mein Leben ist '
                            'eine Achterbahnfahrt.</font>'})

        assert '<' not in got and '>' not in got
        assert 'vertical-align' not in got
        assert 'Achterbahnfahrt' in got, 'вместе с тегами вырезан текст'

    def test_truncated_tag_is_stripped(self):
        """⚠ И **обрывок тега в конце**: сайт режет длинный текст по числу знаков,
        не разбирая разметку, и на срезе остаётся «…dem Tod zu stellen. </fo»."""
        got = companion_about_text({
            'About Myself': 'Mein Leben ist eine Achterbahnfahrt. </fo'})

        assert '<' not in got
        assert 'Achterbahnfahrt' in got

    def test_spaces_are_squeezed(self):
        """Пробелы схлопываем **после** тегов: на их месте остаются пустые места, и
        без этого в описании зияли бы дыры."""
        got = companion_about_text({'Hobby': 'Ich   habe  <b>viele</b>  Hobbys'})

        assert got == 'Увлечения: Ich habe viele Hobbys'


class TestPlace:
    """Город и страна — отдельными колонками карточки."""

    def test_city_and_country(self):
        got = companion_about_parse(LIVE)

        assert companion_about_place(got) == ('London', 'United Kingdom')

    def test_country_only(self):
        """Сайт пишет «Location Germany», когда города нет. ⚠ Одно слово — это
        страна, а не город: страна у мужчины есть всегда, город не всегда."""
        assert companion_about_place({'Location': 'Germany'}) == ('', 'Germany')

    def test_nothing(self):
        assert companion_about_place({}) == ('', '')


class TestOnceOnly:
    """⚠⚠ «Ходим один раз, дальше по сроку» — и решает это одно место.

    Прежде правило жило в наборе пула: `_card` смотрела `about_state == 'none'` и
    предел походов `_probe_left`. Оба ушли — вместе с браузером.

    Теперь поход стоит долей секунды (обычный GET), предел не нужен вовсе, а «пора
    ли» считает `companion_about_due`: нового снимаем всегда, снятого — когда
    выйдет срок сайта (`companion_about_every`, две недели по умолчанию).
    """

    def test_the_rule_lives_in_one_place(self):
        """⚠ Условие в наборе пула больше не повторяется: вторая копия разошлась бы
        с первой на первой же правке срока."""
        import inspect

        from src.dating import mailing

        code = inspect.getsource(mailing._card)

        assert '_probe_left' not in code, 'предел походов вернулся — он больше не нужен'
        assert "== 'none'" not in code, \
            'условие похода снова в наборе пула — оно живёт в companion_about_due'

    def test_the_maker_asks_the_rule(self):
        """А спрашивается оно там, где карточку заводят."""
        import inspect

        from src.dating import mailing

        assert 'companion_about_due' in inspect.getsource(mailing._about_fresh)

    async def test_date_is_stored(self, auth_client):
        """⚠ Дата съёма пишется вместе с состоянием: по ней видно, насколько
        свежо описание, и её же показывает карточка на «Анкетах»."""
        import uuid

        from src.uvicorn.routers.dating.companion import (
            companion_get, companion_touch)

        uid = f'tst-{uuid.uuid4().hex[:10]}'
        try:
            await companion_touch(999_777, uid, about='Играет на трубе',
                                         about_state='done')
            row = await companion_get(999_777, uid)

            assert row['about'] == 'Играет на трубе'
            assert row['about_state'] == 'done'
            assert row['about_at'], 'дата съёма не записана'
        finally:
            from mysql_.mysql_ import mysql_get_db_async

            async with mysql_get_db_async() as db:
                await db.execute('DELETE FROM companion WHERE uid = %s', (uid,))
                await db.connection.commit()

    async def test_touch_keeps_what_it_was_not_told(self, auth_client):
        """⚠ Правим только названное: имя и фото приходят из ленты, описание и
        город — со страницы анкеты. Пиши мы строку целиком, каждый источник затирал
        бы чужое своими пустотами."""
        import uuid

        from src.uvicorn.routers.dating.companion import (
            companion_get, companion_touch)

        uid = f'tst-{uuid.uuid4().hex[:10]}'
        try:
            await companion_touch(999_777, uid, name='Джон', photo='p.jpg')
            await companion_touch(999_777, uid, about='Ходит под парусом',
                                         about_state='done')
            row = await companion_get(999_777, uid)

            assert row['name'] == 'Джон', 'описание затёрло имя'
            assert row['about'] == 'Ходит под парусом'
        finally:
            from mysql_.mysql_ import mysql_get_db_async

            async with mysql_get_db_async() as db:
                await db.execute('DELETE FROM companion WHERE uid = %s', (uid,))
                await db.connection.commit()
