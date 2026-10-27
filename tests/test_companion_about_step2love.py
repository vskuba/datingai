"""Step2Love: та же страница, другие подписи — разбор по карте драйвера.

⚠ Образец — живой дамп страницы мужчины 1182655 (Step2Love, прогон сценария
«анкета мужчины: карточка»), сплющенный тем же правилом, что и разбор. Он
проверяет ровно то, что не видно на Ladadate: подписи с двоеточием, одну
подпись («Looking for:»), стоящую на странице дважды, пустое поле
(«Graduated from:»), за которым идёт служебный заголовок, и блок вопросника с
длинными ответами.

Наружу сюита не ходит (`testing.md`): здесь чистые функции над снятым текстом.
"""

import pytest

from src.dating.companion_about import (DatingCompanionAboutError,
                                        _from_blocks, companion_about_parse)
from src.dating.companion_param import (COMPANION_PARAM_COLUMNS,
                                        COMPANION_PARAM_TITLES,
                                        companion_param_shown,
                                        companion_param_values)
from src.dating.site.site_about_step2love import COMPANION_FIELDS_STEP2LOVE
from src.dating.site.site_registry import site_registry_about

pytestmark = pytest.mark.mail

# Живой ответ сценария, сплющенный пробелами. ⚠ Не выдуманный: выдуманный
# проверял бы нашу мысль о странице, а не саму страницу.
PAGE = (
    'Оповещения разрешены! (Тест) ![IOS app] IOS App ![Android app] Android App - Главная - М'
    'ужчины - Поиск - Pickup lines - Реферальная программа - Моя анкета - Новости - Помощь Пр'
    'ивет, Oksana Статус: изменить статус Мои сообщения Входящие 0 - Фавориты 0 - Непрочитанн'
    'ые 0 - Диалоги 0 Исходящие Черновики Рассылка (бета!) Отклоненные Блок рассылки Черный с'
    'писок Activity Мои контакты 0 (0 Online) Мои Фавориты 0 (0 Online) Чей я фаворит 9 (1 On'
    'line) Сторис Мои приложения Мои галереи Пропущенные чаты (0) Статистика Hello video My g'
    'uests ![Graeme] ![Lance] ![daniele] View all guests Мои сервисы - Видео чат - Виртуальны'
    'е подарки - Реальные подарки - My Wish List - Именинники! - Гороскоп - Гороскоп совмести'
    'мости - Комплименты от мужчин New! - Уведомления о событиях New! 58 лет Switzerland ![da'
    'niele] Switzerland Age: 58 years Star sign: Sagittarius Photo Album: 0 photo ID: #118265'
    '5 Send message Virtual gifts Video Message Добавить в ЧС Статус: le donne ucraine sono l'
    'e più belle del mondo !!!!!! Personal Information Name: daniele Date of birth: 16 Decemb'
    'er 1967 Sex: Male Looking for: Female (30-45 years) For: Friendship, Marriage, Romance, '
    'Business, Correspondence, Love relations Country: Switzerland Graduated from: Photo Albu'
    'm My details Weight: 195 lbs. (88kg.) Height: 6\'3" (1.90m) Languages: English, French, G'
    'erman, Italian, Spanish Body type: Athletic Hair length: Short Eye color: Green Ethnicit'
    'y: European Marital status: Divorced Life priorities: Family, long term relations, Maxim'
    'um of sex Smoker: No Drinker: No Financial position: Stable average income Place of resi'
    'dence: Separate house Children: 1 child Questionnaire Looking for: principesse ucraine e'
    ' non russe per costruire relazione seria About me: sono un uomo molto serio e determinat'
    'o Qualities that I appreciate in people the most: bellezza, dolcezza, sincerità e seriet'
    'à My goals for the future: avere un partner eccellente in tutto e le migliori del mondo '
    'sono le ragazze ucraine My travel destinations (past or future): svizzera, italia, germa'
    'nia, francia, austria, paesi bassi, danimarca, belgio, ucraina, brasile e colombia I spe'
    'nd a lot of time thinking about (or my dream is): viaggiando con la mia dolcissima princ'
    'ipessa ucraina My favorite movies, books, music, shows: commedia italiana e non mi piace'
    ' la musica da discoteca My favorite food: italiano, tedesco, francese e giorgiano. Non m'
    'i piace il cibo americano, asiatico ed africano Pets in my life: no My hobbies and what '
    "I'm good at: viaggiare, camminare, gite romantiche al mare, in montagna o al lago Talkin"
    "g about sports: calcio tifoso del milan The five things I can't live without: cene roman"
    'tiche al lume di candela, gite in europa in luoghi romantici, viaggiare in auto in tutta'
    " l'europa, You should message me if: per relazione seria e se sei una principessa ucrain"
    'a da favola bellissima dentro e fuori a casa mia si spalancano le porte del paradiso Job'
    ': pre pensione, esperto dei trasporti su strada in europa ed ho il certificato per aprir'
    "e o dirigere un'azienda di trasporto merci su strada in tutta l'europa, ottimo contabile"
    ' e parlo bene 5 lingue Start Chat Now!'
)


LABELS = COMPANION_FIELDS_STEP2LOVE


class TestParse:
    """Разбор по карте подписей Step2Love."""

    def test_identity(self):
        got = companion_about_parse(PAGE, LABELS)

        assert got['Name'] == 'daniele'
        assert got['Age'] == '58 years'
        assert got['Birthday'] == '16 December 1967'
        assert got['Sex'] == 'Male'
        assert got['Star sign'] == 'Sagittarius'

    def test_id_with_hash_and_junk(self):
        """Номер в шапке — «#1182655», и до поля он собран мусором кнопок.

        Цифры при этом остаются цифрами: на них и смотрит сторож чужой
        страницы (`_from_blocks`), а не на значение целиком.
        """
        got = companion_about_parse(PAGE, LABELS)

        assert str(got['Id:']).replace('#', '').startswith('1182655')

    def test_duplicate_label_splits_by_occurrence(self):
        """⚠ «Looking for:» стоит дважды, и это **два разных поля**.

        Первое — анкетный выбор («Female (30-45 years)»), второе — рассказ о
        партнёре своими словами. Порядок entries в карте решает, что чем стало.
        """
        got = companion_about_parse(PAGE, LABELS)

        assert got['Looking for'] == 'Female (30-45 years)'
        assert got['About Partner'].startswith('principesse')

    def test_questionnaire_fields(self):
        """Ответы вопросника — на языке человека (здесь по-итальянски)."""
        got = companion_about_parse(PAGE, LABELS)

        assert got['About Myself'] == 'sono un uomo molto serio e determinato'
        assert got['Qualities'] == 'bellezza, dolcezza, sincerità e serietà'
        assert got['Pets'] == 'no'
        assert 'viaggiando' in got['Dream']
        assert 'viaggiare' in got['Hobby']
        assert 'cene romantiche' in got['Five things']
        assert 'trasporti' in got['Occupation']

    def test_headless_fields(self):
        """Внешность подписана не канонически — карта переводит к канону."""
        got = companion_about_parse(PAGE, LABELS)

        assert got['Hair Color'] == 'Short'      # на странице «Hair length»
        assert got['Eyes Color'] == 'Green'       # на странице «Eye color»
        assert got['Body Type'] == 'Athletic'     # на странице «Body type»
        assert got['Smoke'] == 'No'               # на странице «Smoker»
        assert got['Marital Status'] == 'Divorced'
        assert got['Location'] == 'Switzerland'   # на странице «Country»

    def test_empty_field_does_not_swallow_the_section(self):
        """⚠ Пустое «Graduated from:» не утащило заголовки раздела.

        Без границы-«Photo Album» значением образования стал бы текст
        «Photo Album My details». Граница с `None` ставит ей отсечку.
        """
        got = companion_about_parse(PAGE, LABELS)

        assert 'Education' not in got
        assert got['Children'] == '1 child'
        assert 'My details' not in str(got.values())
        assert 'Questionnaire' not in str(got.values())
        assert 'Start Chat Now' not in str(got.values())


class TestCard:
    """Сборка карточки из одного куска текста (сценарий шапку не снимает)."""

    def test_name_age_fall_back_to_fields(self):
        got = _from_blocks({'profile': PAGE}, '1182655', LABELS)

        assert got['name'] == 'daniele'
        assert got['age'] == 58
        assert got['country'] == 'Switzerland'

    def test_uid_guard_reads_digits(self):
        with pytest.raises(DatingCompanionAboutError):
            _from_blocks({'profile': PAGE}, '999999', LABELS)

    def test_params_hold_everything_else(self):
        """Колонки карточки в `params` не дублируются, остальное — целиком."""
        fields = companion_about_parse(PAGE, LABELS)
        params = companion_param_values(fields)

        for label in COMPANION_PARAM_COLUMNS:
            assert label not in params
        assert params['Marital Status'] == 'Divorced'
        assert params['About Myself'] == 'sono un uomo molto serio e determinato'

        lines = companion_param_shown(params)
        assert lines[0]['title'] == 'Дата рождения'          # порядок — по реестру
        assert 'Семейное положение' in [one['title'] for one in lines]
        assert any(one['wide'] for one in lines)


class TestTheyAgree:
    """Канон карты Step2Love живёт в реестре подписей, а не мимо него.

    ⚠ Подпись, которой нет в `COMPANION_PARAM_TITLES` и которая не колонка
    карточки, уедет на вкладку английской как есть — не потеряется, но человек
    её не прочитает. Тот же сторож, что у параметров девушки
    (`profile_param.py`).
    """

    def test_map_labels_are_known(self):
        known = {one for one, _ru in COMPANION_PARAM_TITLES}
        known |= set(COMPANION_PARAM_COLUMNS)

        for _site, canonical in COMPANION_FIELDS_STEP2LOVE:
            if canonical is not None:
                assert canonical in known, canonical

    def test_registry_knows_the_driver(self):
        assert site_registry_about({'driver': 'step2love'}).about_fields \
            is COMPANION_FIELDS_STEP2LOVE
