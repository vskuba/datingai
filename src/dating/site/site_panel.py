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

import html
import json
import re

from datetime_.datetime_ import datetime_utc_now

from src.dating.profile_param import profile_param_frame

# Месяцы, как их пишет панель в `BirthdayMonth`. ⚠ Трёхбуквенные и по-английски —
# так на странице; русские названия сюда не попадут никогда.
SITE_PANEL_MONTHS = {'jan': 1, 'feb': 2, 'mar': 3, 'apr': 4, 'may': 5, 'jun': 6,
                     'jul': 7, 'aug': 8, 'sep': 9, 'oct': 10, 'nov': 11, 'dec': 12}

# Чем панель разделяет две системы мер в росте и весе: `5'8" - 172 cm`.
SITE_PANEL_UNIT_SPLIT = ' - '

# Английский в списке языков: по-английски и по-русски. ⚠ Интерфейс панели
# переводной, и галка может приехать любой из двух подписей.
SITE_PANEL_ENGLISH = ('english', 'английский')

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

    # Хвосты к пути страницы анкеты: у сайтов, разложивших анкету по вкладкам,
    # одной страницы не хватает. Читатель берёт каждую и сливает в одну форму.
    PAGES: tuple = ()

    # Имя поля формы → ключ карточки, один к одному. Параметры живут своим
    # каркасом, здесь — то, что лежит у карточки самой: город, описание.
    CARD: dict = {}

    # Страница панели со списком мужчин в сети — лента «Все онлайн» без
    # сценария (`panel_online.py`). ⚠ Пусто значит «у панели такой страницы
    # нет», и лента идёт прежней дорогой: API сайта или сценарий у соседа.
    # Ведущая черта — путь от корня **сайта**, а не панели (`panel_fetch`).
    ONLINE_PATH = ''

    # Ручки панели, которыми агентство само включает и гасит девушку «в сети»
    # (`panel_presence.py`). ⚠ Пусто — у панели такой кнопки нет, и анкета
    # входит прежней дорогой: сценарием или обменом токена.
    PRESENCE_SET_PATH = ''
    PRESENCE_READ_PATH = ''

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
        names = tuple(self.FIELDS) + tuple(self.SOURCES) + tuple(self.CARD)

        return sum(1 for name in names if name in got)

    def online_params(self, token: str) -> dict:
        """Строка запроса страницы ленты. У базовой панели ленты нет."""
        return {}

    def online_page(self, raw: str) -> tuple[list[dict], str]:
        """Одна страница ленты: карточки и метка следующей. У базовой — пусто."""
        return [], ''

    def presence_form(self, uids: list[str], online: bool | None = None) -> dict:
        """Поля запроса присутствия: чтение (`online=None`) или переключение."""
        return {}

    def presence_answer(self, raw: str) -> dict[str, bool]:
        """Ответ панели — «номер → в сети». У базовой панели присутствия нет."""
        return {}

    def card(self, form: dict) -> dict:
        """Поля карточки помимо параметров: пустой набор у базовой панели."""

        return {key: str((form or {}).get(field) or '').strip()
                for field, key in self.CARD.items()}


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

    # ⚠ От корня сайта: корень панели Luxee — `…/profile` (`admin_url`
    # обязан кончаться чертой), и относительный путь ушёл бы этажом ниже.
    path = '/profile/update/{uid}/'

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

    # ── Лента «Все онлайн»: раздел Clients ──────────────────────────────────
    #
    # ⚠⚠ У Luxee кабинета под девушкой нет — одна панель агентства на всех, и
    # мужчины, которые сейчас на сайте, видны в ней плитками `/clients/list/`.
    # Сценарий здесь не нужен: страница server-rendered, подкачка «Load more» —
    # тот же адрес с меткой `token`, ответ JSON `{success, html, token}`.
    #
    # ⚠⚠ Берём **только платящих** (`Payed`): бесплатный ответить не может, и
    # реплика ему потрачена впустую. Отбор двойной — фильтром сервера
    # (`purchased=2`, `is_online=10`: замер 16.11.2026 — 103 плитки за шесть
    # GET вместо листания всех) и проверкой ярлыков плитки: проглоти сайт
    # однажды фильтр молча, лента наполнилась бы бесплатными без единой ошибки.

    ONLINE_PATH = '/clients/list/'

    ONLINE_FILTER = {'ClientsFilterForm[is_online]': '10',
                     'ClientsFilterForm[purchased]': '2'}

    def online_params(self, token: str) -> dict:
        """Фильтр формы плюс метка страницы: `0` — первая."""
        return {'token': str(token or '0'), **self.ONLINE_FILTER}

    def online_page(self, raw: str) -> tuple[list[dict], str]:
        """Ответ подкачки — карточками нашего вида и меткой следующей страницы.

        Raises:
            ValueError: ответ не JSON подкачки или `success` ложен. ⚠ Отказ, а
                не пустой список: пустота погасила бы точки «в сети» у всех и
                читалась бы как «никого нет», хотя сломался разбор.
        """
        try:
            page = json.loads(raw)
        except (TypeError, ValueError):
            raise ValueError('панель ответила не JSON подкачки — '
                             'разметка раздела Clients сменилась?')

        if not isinstance(page, dict) or not page.get('success'):
            raise ValueError('панель не отдала страницу клиентов (success ложен)')

        rows = [card for card in map(_luxee_tile, _LUXEE_TILE_RE.split(
            str(page.get('html') or ''))[1:]) if card]

        # ⚠ Метка конца — пустая строка (иногда ноль): так сама страница
        # прячет кнопку «Load more».
        token = str(page.get('token') or '').strip()

        return rows, '' if token in ('', '0') else token

    # ── Присутствие девушки: «Set online» / «Set offline» ──────────────────
    #
    # ⚠⚠ У Luxee «в сети» девушку держит **сама панель**: кнопкой в меню плитки
    # анкеты, а не входом под ней — входить и некуда, кабинета нет. Кнопка —
    # обычный POST формой `profiles[]` + `online` (10 — в сети, 1 — нет), а
    # страница узнаёт статусы POST-ом `online-status` с теми же `profiles[]`.
    # CSRF ручки не спрашивают (замер 16.11.2026).

    PRESENCE_SET_PATH = '/profile/toggle-online/'
    PRESENCE_READ_PATH = '/profile/online-status/'

    PRESENCE_ON, PRESENCE_OFF = '10', '1'

    def presence_form(self, uids: list[str], online: bool | None = None) -> dict:
        """`profiles[]` всегда; `online` — только у переключения."""
        form = {'profiles[]': [str(uid) for uid in uids]}
        if online is not None:
            form['online'] = self.PRESENCE_ON if online else self.PRESENCE_OFF

        return form

    def presence_answer(self, raw: str) -> dict[str, bool]:
        """Оба ответа к одному виду «номер → в сети».

        Чтение отвечает `{"622348": true}`, переключение — тем же словарём
        внутри `data` и списком `errors` рядом.

        Raises:
            ValueError: не JSON, ошибки панели или пустой ответ. ⚠ Пустота —
                отказ: «никого не переключили» не должно читаться удачей.
        """
        try:
            page = json.loads(raw)
        except (TypeError, ValueError):
            raise ValueError('панель ответила не JSON — сессия или разметка?')

        if not isinstance(page, dict):
            raise ValueError('панель ответила не словарём статусов')

        errors = page.get('errors') or []
        if errors:
            raise ValueError('панель отказала: ' + '; '.join(
                str(one.get('message') if isinstance(one, dict) else one)
                for one in errors))

        got = page['data'] if isinstance(page.get('data'), dict) else page
        statuses = {str(uid): bool(state) for uid, state in got.items()
                    if str(uid).isdigit()}
        if not statuses:
            raise ValueError('панель не вернула ни одного статуса')

        return statuses


class DatingSitePanelStep2Love(DatingSitePanel):
    """Панель Step2Love: `my_profiles.php?com=edit_user&cmd=…&id_user={ID}`.

    ⚠ **Страница анкеты здесь разложена по вкладкам**: форма заведения (cmd=1)
    знает только имя, город, страну и дату рождения, параметры живут на вкладке
    `cmd=3`, все тексты — на `cmd=5`. Читатель берёт три страницы и сливает в
    одну форму (`PAGES`); по одной странице судить о сайте было бы «не знает»
    там, где он знает всё.

    ⚠ Параметры здесь — группы `radio` `info[N][]` с **числовыми** `value`:
    текст отмеченного варианта стоит сразу за тегом, и общий разбор формы
    (`panel_fetch.py`) берёт его значением — тем же движком, что разворачивает
    название города после скрытого `city`. Числа `info[7]` (дети у партнёра),
    `info[8]`–`info[10]` (доход, жильё, жизненные приоритеты) на форме есть,
    но ключей под них в реестре нет — в карточку им не попасть.

    ⚠ Религии у панели нет вовсе: ключ `religion` законно пуст.

    ⚠ Месяц панель пишет **цифрой** (`02`), а не `Feb`, как Ladadate; `_birthday`
    понимает обе записи с этих пор.
    """

    name = 'step2love'

    # Страница панели зовётся запросом, а не путём — черта здесь часть адреса.
    path = 'my_profiles.php?com=edit_user&id_user={uid}'

    # Вкладка параметров и вкладка текстов. Прочие (документы, контакты,
    # фотографии) ничего реестрового не содержат.
    PAGES = ('&cmd=3', '&cmd=5')

    FIELDS = {
        # Группы радио вкладки параметров; подписи — как на публичной анкете.
        'info[0][]': 'body',             # Body type
        'info[1][]': 'hair',             # Hair color
        'info[2][]': 'hair_style',       # Hair length
        'info[3][]': 'eyes',             # Eye color
        'info[4][]': 'ethnicity',        # Ethnicity
        'info[5][]': 'marital_state',    # Marital status
        'info[6][]': 'children',         # Children
        'info[11][]': 'smoke',           # Smoking
        'info[12][]': 'drink',           # Drinking
        'id_height': 'height',
        'id_weight': 'weight',
        'id_education_list': 'education',
        'country': 'country',            # скрытое поле, название — текстом за тегом
        # Вкладка «кого ищет»: окно возраста и блок «О себе» из её текстов.
        'age_min': 'age_from',
        'age_max': 'age_to',
        'info[400]': 'occupation',
        'info[192]': 'about_partner',    # LOOKING FOR
        'info[205]': 'hobby',            # hobbies
    }

    CARD = {'city': 'city', 'info[193]': 'about'}   # ABOUT ME

    BUILT = ('birthday', 'english')

    SOURCES = ('b_day', 'b_month', 'b_year', 'lang_sel[5]')

    def params(self, form: dict) -> dict:
        """То же, что у базового, плюс меры, дата и английский: мера у этого
        сайта в метрах, дата в трёх полях, языки — галками."""
        got = form or {}
        values = {our: got.get(theirs, '') for theirs, our in self.FIELDS.items()}

        values['height'] = _meters_cm(got.get('id_height'))
        values['weight'] = _kilos(got.get('id_weight'))
        values['education'] = (values.get('education')
                               or str(got.get('id_education_type') or '').strip())
        values['birthday'] = _birthday(got.get('b_day'),
                                       got.get('b_month'),
                                       got.get('b_year'))
        values['english'] = 'Yes' if _speaks_english(got) else ''

        return profile_param_frame(values)

    def card(self, form: dict) -> dict:
        """Город и описание — плюс возраст, собранный из даты рождения."""
        card = super().card(form)
        born = _birthday((form or {}).get('b_day'), (form or {}).get('b_month'),
                         (form or {}).get('b_year'))
        if born:
            card['age'] = _years(born)

        return card


# Граница плитки клиента: номер человека — в `data-key`.
_LUXEE_TILE_RE = re.compile(r'<div class="profile-tile-wrap-outside" data-key="')

# Заглушка вместо фотографии: силуэт, общий для всех без снимка.
_LUXEE_BLANK_PHOTO = '/blank/'


def _luxee_tile(chunk: str) -> dict | None:
    """Плитка клиента — карточкой ленты. Не платящий или не в сети — `None`.

    ⚠ Вид карточки тот же, что у API сайта и у сценария (`people_api.py`,
    `people_row_card`): лента одна, источники разные, и разойдись поля —
    страница рисовала бы плитки по-разному в зависимости от дороги.
    """
    from src.dating.people import (PEOPLE_NAME_MAX, PEOPLE_UID_MAX,
                                   people_name_case, people_photo_uid)

    uid = chunk.split('"', 1)[0].strip()
    if not uid.isdigit():
        return None

    status = _luxee_text(chunk, r'class="profile-status[^"]*">([^<]*)<')
    paid = _luxee_text(chunk, r'class="profile-sync[^"]*">([^<]*)<')
    if status.lower() != 'online' or paid.lower() != 'payed':
        return None

    photo = _luxee_text(chunk, r'background-image:\s*url\(&quot;([^&"]*)')
    if _LUXEE_BLANK_PHOTO in photo:
        photo = ''

    age = _luxee_text(chunk, r'<div class="age">\s*(\d+)')

    return {'uid': uid[:PEOPLE_UID_MAX],
            'name': people_name_case(
                _luxee_text(chunk, r'<div class="username">([^<]*)<'))[:PEOPLE_NAME_MAX],
            'age': int(age) if age else 0,
            'photo': photo,
            'photo_uid': people_photo_uid(photo),
            'online': True,
            'is_new': False,
            # Анкеты клиента по адресу у панели нет — только окно по номеру.
            'link': '',
            'city': _luxee_text(chunk, r'<div class="location">([^<]*)<')}


def _luxee_text(chunk: str, pattern: str) -> str:
    """Первая группа совпадения, без сущностей и лишних пробелов."""
    found = re.search(pattern, chunk)

    return ' '.join(html.unescape(found.group(1)).split()) if found else ''


def _bracket_metric(value) -> str:
    """Метрическая половина меры Luxee: `5'7.4&quot; (175 cm)` → `175 cm`.

    ⚠ Имперская половина **до** скобок, метрическая — **в** них. Скобок нет —
    возвращаем как есть, по той же причине, что у `_metric`.
    """
    said = str(value or '').strip()
    if not said.endswith(')') or '(' not in said:
        return said

    return said[said.rindex('(') + 1:-1].strip()


def _meters_cm(value) -> str:
    """Метры из скобок — в сантиметры: `5'5&quot; (1.65m)` → `165 cm`.

    ⚠ Та же скобка, что у Luxee, но внутри метры, а не сантиметры; био и
    агенту нужен единый `165 cm`. Метров нет — возвращаем как есть, по той же
    причине, что у `_metric`.
    """
    said = _bracket_metric(value)
    if not said.lower().endswith('m'):
        return said

    try:
        hundred = round(float(said[:-1].strip().replace(',', '.')) * 100)
    except ValueError:
        return said

    return f'{hundred} cm'


def _kilos(value) -> str:
    """Килограммы из скобок: `120 lbs. (54kg.)` → `54 kg`.

    ⚠ Точка после числа — часть записи сайта (`54kg.`), к весу она отношения не
    имеет. Килограммов нет — возвращаем как есть.
    """
    said = _bracket_metric(value)
    if 'kg' not in said.lower():
        return said

    number = said.lower().partition('kg')[0].strip().replace(',', '.').rstrip('.')

    return f'{number} kg' if number else said


def _speaks_english(form: dict) -> bool:
    """Отмечена ли галка английского в списке языков `lang_sel[NN]`."""

    return any(str(value).strip().lower() in SITE_PANEL_ENGLISH
               for key, value in (form or {}).items()
               if str(key).startswith('lang_sel['))


def _years(birthday: str) -> int:
    """Полных лет от даты рождения: `1990-05-04` → `36`.

    ⚠ По календарю, а не разницей суток: не наступивший день рождения в возраст
    не входит — так же считает сайт.
    """
    try:
        four, at, one = (int(part) for part in str(birthday).split('-'))
    except ValueError:
        return 0

    today = datetime_utc_now()
    years = today.year - four - ((today.month, today.day) < (at, one))

    return years if years > 0 else 0


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

    Месяц — английской тройкой (`Jan`) **или цифрой** (`02`, так пишет Step2Love).

    ⚠ **Неполная тройка и незнакомый месяц дают пустоту.** Половина даты хуже её
    отсутствия: `1990-00-21` уехало бы в био настоящей датой.
    """
    said = str(month or '').strip()
    at = SITE_PANEL_MONTHS.get(said.lower()[:3], 0)
    if not at and said.isdigit() and 1 <= int(said) <= 12:
        at = int(said)

    try:
        one, four = int(str(day or '').strip()), int(str(year or '').strip())
    except (TypeError, ValueError):
        return ''

    if not at or not one or four < SITE_PANEL_YEAR_MIN:
        return ''

    return f'{four:04d}-{at:02d}-{one:02d}'
