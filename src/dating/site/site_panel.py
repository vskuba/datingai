"""Разбор страницы анкеты в админ-панели агентства: имена полей и нормализация.

Четвёртая половина драйвера сайта. Три прежних отвечают на свои вопросы: чат — про
кадры сокета, анкета девушки — про имена полей JSON, разбор страницы собеседника —
про классы в вёрстке. Эта — про **форму админ-панели**: как зовутся её поля и в
каком виде она пишет значения.

## ⚠ Почему по именам полей, а не по подписям

У собеседника-мужчины (`companion_about.py`) выбора не было: сценарий снимал весь
текст блока одной строкой, и подписи («Occupation», «Marital Status») были
единственными ориентирами. Здесь ориентир лучше: `name="HeightId"` — машинное имя,
оно переживёт перевёрстку, а подпись «HEIGHT» нарисована рядом и станет другой при
первом же переводе интерфейса.

## ⚠ Что делает нормализация и почему она здесь

Панель Ladadate пишет рост `5'8" - 172 cm`, вес `112 lb - 51 kg`, а дату рождения
кладёт в **три** поля. В био нужны `172 cm`, `51 kg` и `1990-01-21`. Это знание о
том, как пишет конкретная панель: в общем коде оно стало бы миной для второго
сайта, чья панель напишет иначе.

## ⚠⚠ `known` — не удобство, а предохранитель

Смена разметки у сайта означала бы, что форма разобралась, а знакомых имён в ней
нет. Без счётчика узнанных имён читатель параметров записал бы пустоту **всем
анкетам сразу**, по одному нажатию кнопки. Поэтому `known` считает, сколько наших
имён в форме нашлось, а решение «отказ» принимает `profile_panel.py`.
"""

from src.dating.profile_param import profile_param_frame

# Месяцы, как их пишет панель в `BirthdayMonth`. ⚠ Трёхбуквенные и по-английски —
# так на странице; русские названия сюда не попадут никогда.
SITE_PANEL_MONTHS = {'jan': 1, 'feb': 2, 'mar': 3, 'apr': 4, 'may': 5, 'jun': 6,
                     'jul': 7, 'aug': 8, 'sep': 9, 'oct': 10, 'nov': 11, 'dec': 12}

# Чем панель разделяет две системы мер в росте и весе: `5'8" - 172 cm`.
SITE_PANEL_UNIT_SPLIT = ' - '

# Раньше этого года рождения не бывает. ⚠ Та же граница, что у импорта анкет
# (`IMPORT_YEAR_MIN`): мусор вроде «0» превратился бы в дату двухтысячелетней
# давности, и она уехала бы агенту настоящей.
SITE_PANEL_YEAR_MIN = 1900


class DatingSitePanel:
    """Панель, которой у сайта нет: путь пуст, параметров не бывает."""

    name = 'base'

    # Путь страницы анкеты от корня панели; `{uid}` — номер анкеты на сайте.
    # ⚠ Пусто значит «читать нечем», и это единственный выключатель занятия.
    path = ''

    # Имя поля формы → наш ключ, один к одному.
    FIELDS: dict = {}

    # Ключи, которые драйвер собирает сам из нескольких полей формы.
    BUILT: tuple = ()

    # Поля формы, из которых собрано `BUILT`. ⚠ Нужны `known`: без них страница с
    # одной только датой рождения считалась бы незнакомой.
    SOURCES: tuple = ()

    def __init__(self, presence: dict | None = None):
        self.presence = presence or {}

    def params(self, form: dict) -> dict:
        """Параметры анкеты из значений формы — полным каркасом.

        Args:
            form: то, что вернул `panel_fetch_form`.

        Returns:
            dict: ровно ключи реестра; неизвестное — пустой строкой.
        """
        got = form or {}
        values = {our: got.get(theirs, '') for theirs, our in self.FIELDS.items()}

        return profile_param_frame(values)

    def known(self, form: dict) -> int:
        """Сколько наших имён нашлось в форме. Ноль — разметка сменилась."""
        got = form or {}
        names = tuple(self.FIELDS) + tuple(self.SOURCES)

        return sum(1 for name in names if name in got)


class DatingSitePanelLadadate(DatingSitePanel):
    """Панель Ladadate: `/partner/agency/userprofile/edit/{ID}`."""

    name = 'ladadate'

    path = 'userprofile/edit/{uid}'

    FIELDS = {
        'CountryId': 'country', 'Occupation': 'occupation',
        'EducationId': 'education', 'EnglishId': 'english',
        'MaritalStateId': 'marital_state', 'ChildrenId': 'children',
        'ReligionId': 'religion', 'EthnicityId': 'ethnicity',
        'BodyTypeId': 'body', 'HeightId': 'height', 'WeightId': 'weight',
        'HairColorId': 'hair', 'HairStyleId': 'hair_style',
        'EyesColorId': 'eyes', 'SmokeId': 'smoke', 'DrinkId': 'drink',
        'AgeFrom': 'age_from', 'AgeTo': 'age_to',
        'Interest': 'hobby', 'AboutPartner': 'about_partner',
    }

    BUILT = ('birthday',)

    SOURCES = ('BirthdayDay', 'BirthdayMonth', 'BirthdayYear')

    def params(self, form: dict) -> dict:
        """То же, что у базового, плюс нормализация мер и сборка даты."""
        got = form or {}
        values = {our: got.get(theirs, '') for theirs, our in self.FIELDS.items()}

        values['height'] = _metric(values.get('height'))
        values['weight'] = _metric(values.get('weight'))
        values['birthday'] = _birthday(got.get('BirthdayDay'),
                                       got.get('BirthdayMonth'),
                                       got.get('BirthdayYear'))

        return profile_param_frame(values)


class DatingSitePanelLuxee(DatingSitePanel):
    """Панель Luxee: `luxee.io/profile/update/{ID}/`, поля `FakeProfileModel[...]`.

    ⚠ Дата рождения приезжает **одним полем** и сразу ISO (`1971-06-08`): тройки
    дней-месяцев-года Ladadate здесь нет, сборка и не нужна. Рост и вес панель
    пишет обеими системами сразу, но не через черточку, а в скобках:
    `5'7.4&quot; (175 cm)`.
    """

    name = 'luxee'

    path = 'profile/update/{uid}/'

    FIELDS = {
        'FakeProfileModel[birthday]': 'birthday',
        'FakeProfileModel[country_id]': 'country',
        'FakeProfileModel[occupation]': 'occupation',
        'FakeProfileModel[education_level]': 'education',
        'FakeProfileModel[religion]': 'religion',
        'FakeProfileModel[marital_status]': 'marital_state',
        'FakeProfileModel[children]': 'children',
        'FakeProfileModel[body_type]': 'body',
        'FakeProfileModel[height]': 'height',
        'FakeProfileModel[weight]': 'weight',
        'FakeProfileModel[smoke]': 'smoke',
        'FakeProfileModel[drink]': 'drink',
        'FakeProfileModel[interests][]': 'hobby',
    }

    def params(self, form: dict) -> dict:
        """То же, что у базового, плюс рост и вес: метрическое — в скобках."""
        got = form or {}
        values = {our: got.get(theirs, '') for theirs, our in self.FIELDS.items()}

        values['height'] = _bracket_metric(values.get('height'))
        values['weight'] = _bracket_metric(values.get('weight'))

        return profile_param_frame(values)


def _bracket_metric(value) -> str:
    """Метрическая половина меры Luxee: `5'7.4&quot; (175 cm)` → `175 cm`.

    ⚠ Имперская половина **до** скобок, метрическая — **в** них. Скобок нет —
    возвращаем как есть, по той же причине, что у `_metric`.
    """
    said = str(value or '').strip()
    if not said.endswith(')') or '(' not in said:
        return said

    return said[said.rindex('(') + 1:-1].strip()


def _metric(value) -> str:
    """Метрическая половина меры: `5'8" - 172 cm` → `172 cm`.

    ⚠ Разделителя нет — возвращаем как есть, а не пустоту: сайт вправе писать
    иначе, и терять значение из-за этого незачем.
    """
    said = str(value or '').strip()
    if SITE_PANEL_UNIT_SPLIT not in said:
        return said

    return said.split(SITE_PANEL_UNIT_SPLIT)[-1].strip()


def _birthday(day, month, year) -> str:
    """Дата рождения из трёх полей панели: `21`, `Jan`, `1990` → `1990-01-21`.

    ⚠ **Неполная тройка и незнакомый месяц дают пустоту.** Половина даты хуже её
    отсутствия: `1990-00-21` уехало бы в био настоящей датой.
    """
    at = SITE_PANEL_MONTHS.get(str(month or '').strip().lower()[:3], 0)

    try:
        one, four = int(str(day or '').strip()), int(str(year or '').strip())
    except (TypeError, ValueError):
        return ''

    if not at or not one or four < SITE_PANEL_YEAR_MIN:
        return ''

    return f'{four:04d}-{at:02d}-{one:02d}'
