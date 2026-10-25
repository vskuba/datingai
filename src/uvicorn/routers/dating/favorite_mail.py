"""Переписка с избранными: ручки пула на вкладке «Фавориты».

Повторяют пул рассылки (`mailing.py`) — чтение, правка, удаление, повод
набрать, отправка одной карточки, — и это намеренно: занятие устроено так же, а
второй набор соглашений на ту же работу человеку пришлось бы разгадывать.

Расходятся они в двух местах, и оба про природу занятия:

⚠ **Шаблона нет.** Текст пишет агент под конкретный разговор, глядя на его
историю, и `generate` не принимает списка шаблонов — принимать было бы нечего.

⚠ **Пара «анкета — адресат» не одноразовая.** Рассылка пишет человеку один раз
каждым шаблоном; здесь ему пишут снова и снова — в том и смысл возобновления.
Частоту держат два предела на человека (`favorite_mail_per_day`,
`favorite_mail_gap`), а не уникальный ключ в базе.
"""

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from logging_.logging_ import logger_info
from mysql_.mysql_ import mysql_get_db_async

from src.auth.auth import AuthUser, auth_required
from src.dating.send_round import send_why
from src.dating.favorite import (FAVORITE_STATES,
                                        FAVORITE_STATES_SQL)
from src.dating.favorite_mail import (FAVORITE_MAIL_BATCH_DEFAULT,
                                             FAVORITE_MAIL_BATCH_MAX,
                                             favorite_mail_make,
                                             favorite_mail_pool_left,
                                             favorite_mail_queue,
                                             favorite_mail_why)
from src.dating.favorite_mail_round import favorite_mail_round_next
from src.dating.json_column import json_column_load
from src.dating.mail_explain import mail_explain
from src.dating.mail_reply import (mail_reply_delivery,
                                          mail_reply_delivery_scope,
                                          mail_reply_thread)
from src.dating.mail_send import (DatingMailSendError, mail_send_fail,
                                         mail_send_one)
from src.dating.online import online_show
from src.dating.pool_pair import (pool_delivery_where, pool_pair_girl,
                                         pool_pair_joins, pool_pair_select,
                                         pool_pick_where)
from src.dating.site_setting import site_setting
from src.uvicorn.routers.dating.chat import chat_dialogs_size
from src.uvicorn.routers.dating.pool_handle import (pool_handle_clear,
                                                    pool_handle_drop,
                                                    pool_handle_sender)
from src.uvicorn.routers.setting import setting_timezone_hours

router = APIRouter(prefix='/api/dating/favorite-mail', tags=['dating'])

# Куда пишется исход отправки. ⚠ Имя таблицы уезжает в `mail_send_one`, а
# там оно сверяется с белым списком: в SQL оно не параметризуется.
FAVORITE_MAIL_TABLE = 'favorite_mail'

# Сколько карточек отдаём странице. Тот же предел, что у пула писем.
FAVORITE_MAIL_LIMIT = 300

# Сколько строк на странице журнала «Доставка», когда страница не просит своё.
#
# ⚠ Число то же, что у журнала писем (`MAILING_DELIVERY_PER`): журналы стоят на
# соседних вкладках, выглядят одной выкройкой и листаются одним пейджером. Разные
# размеры страницы читались бы как разница по существу, которой нет.
FAVORITE_MAIL_DELIVERY_PER = 25

# Сколько переписок на страницу списка «Диалоги», когда страница не просит своё.
#
# ⚠⚠ **Список стал длиннее предела, и это видно.** Прежде он отдавался целиком,
# одним `LIMIT 300`: пока переписок было три десятка, разница между «все» и
# «первые триста» не проявлялась, а на живом сайте список уехал за край экрана —
# прокруткой его листали, а всё, что дальше трёхсот, не показывалось **вовсе и
# молча**.
#
# ⚠ Число то же, что у диалогов чата (`chat.CHAT_DIALOGS_PAGE`): списки стоят на
# соседних вкладках, выглядят одинаково и листаются одним и тем же пейджером.
# Разные размеры страницы читались бы как разница по существу, которой нет.
FAVORITE_MAIL_THREADS_PAGE = 50


class DatingFavoriteMailGenerate(BaseModel):
    """Повод набрать пул. ⚠ Шаблонов здесь нет — их у этого занятия не бывает."""

    site_id: int = Field(..., gt=0, description='Сайт-закладка')
    limit: int = Field(FAVORITE_MAIL_BATCH_DEFAULT, ge=1,
                       le=FAVORITE_MAIL_BATCH_MAX,
                       description='Сколько пар пробуем за это нажатие')
    profile_id: int = Field(0, ge=0, description='Писать только от этой анкеты. '
                                                 'Ноль — от всех включённых')


@router.get('', include_in_schema=False)
async def favorite_mail_list(site_id: int = 0, status_: str = 'draft',
                                    profile_id: int = 0,
                                    page: int = 1, per: int = 0,
                                    user: AuthUser = Depends(auth_required)):
    """Пул писем избранным — свежие сверху.

    Args:
        site_id: сайт-закладка. Ноль — пустой список, а не письма всех сайтов.
        status_: `draft` (пул), `sent`, `failed`, `delivery` или `all`; можно
            несколько через запятую. `delivery` — «что происходило с отправкой»:
            статусы плюс черновики с причиной отказа, потому что неудача
            **оставляет письмо в пуле** (`mail_send.py`).
        profile_id: сузить до одной анкеты. Ноль — все анкеты сайта.

            ⚠ **Это фильтр, а не условие показа.** Список отвечает на вопрос «что
            происходит на сайте», и без выбранной анкеты он полон; выбор анкеты
            меняет **сколько** строк в нём, а не показывать ли его вообще. Тот же
            порядок, что у диалогов (§3.8) и у пула рассылки.
        page: страница, с единицы. `per` — сколько строк на ней; ноль означает
            «все», и это **умолчание**: пул читают целиком, страницами ходит
            только журнал доставки. Ровно как у инвайтов (`invite.py`).

            ⚠⚠ **Перелистывание на сервере, а не в браузере.** Прежде здесь стоял
            один `LIMIT 300` без счёта: пока журнал был короче, разница между
            «все» и «первые триста» не проявлялась, а на живом сайте всё, что за
            тремястами, не показывалось **вовсе и молча** — ни пейджера, ни
            намёка, что дальше что-то есть. Ту же беду уже проходили соседние
            «Диалоги» в этом же файле.

            ⚠ Страницу возвращаем в ответе (`page`): список мог укоротиться, пока
            человек листал, и сервер поправит номер, уехавший за конец.

    В ответе, помимо полей строки, — имя анкеты и признак «в сети»: карточка
    показывает «от кого кому», и без них страница делала бы запрос на письмо.
    Рядом `total`, `page`, `pages` — ими живёт пейджер «Доставки».
    """
    if not int(site_id or 0):
        # ⚠ Пустой ответ несёт те же поля пейджера, что и полный: страница читает
        # их без проверок, и `pages: undefined` нарисовало бы пейджер из одной
        # мёртвой страницы вместо пустого подвала.
        return {'result': [], 'pool': 0, 'send_on': False, 'send_why': '',
                'page': 1, 'pages': 1, 'total': 0}

    # ⚠ **Имя и лицо собеседника подмешиваем из карточек**, а не берём только из
    # строки пула. В строку они попали при наборе, а у пары, приехавшей со списка
    # избранных сайта, их не было вовсе: в «Доставке» человек оставался голым
    # номером. Порядок источников тот же, что у диалогов: контакт анкеты (свежее)
    # → карточка сайта → то, что записали при наборе.
    sql = ('SELECT m.id, m.site_id, m.profile_id, m.uid,'
           + pool_pair_select() +
           ' m.body, m.status, m.error, m.answer, m.sent_at, m.created_at,'
           # ⚠ Время исхода: у ушедшего письма это `sent_at`, у неудачного
           # его нет вовсе — отметку снимает сама неудача, — и остаётся
           # `updated_at`. Без него красные строки журнала стояли без даты.
           ' m.updated_at,'
           ' p.login AS profile_login, p.profile AS profile_card'
           ' FROM favorite_mail m'
           ' LEFT JOIN profile p ON p.id = m.profile_id'
           + pool_pair_joins() +
           ' WHERE m.site_id = %s')
    args = [int(site_id)]

    asked = [one.strip() for one in str(status_ or '').split(',')]
    wanted = [one for one in asked if one in ('draft', 'sent', 'failed')]

    if 'delivery' in asked:
        sql += (" AND (m.status IN ('sent', 'failed')"
                "      OR (m.status = 'draft' AND m.error <> ''))")
    elif wanted:
        sql += ' AND m.status IN (%s)' % ', '.join(['%s'] * len(wanted))
        args.extend(wanted)

    if int(profile_id or 0):
        sql += ' AND m.profile_id = %s'
        args.append(int(profile_id))

    # ⚠ Сколько всего — **отдельным счётом по тем же условиям**, а не длиной
    # выборки: страница показывает 25 строк, а «страниц 7» считается по всем.
    #
    # ⚠⚠ Считаем **обёрнутый запрос целиком**, вместе с его `LEFT JOIN`-ами, а не
    # лёгкий `COUNT(*) FROM favorite_mail`. Условия сейчас смотрят только на `m.*`,
    # то есть лёгкий счёт дал бы то же число — но `LIMIT` ниже применяется к
    # **склеенному** набору, и появись однажды связь не один-к-одному, лёгкий счёт
    # молча разошёлся бы с числом строк на странице. Тот же приём у инвайтов.
    async with mysql_get_db_async() as db:
        await db.execute('SELECT COUNT(*) AS n FROM (' + sql + ') AS all_rows',
                         tuple(args))
        total = int(((await db.fetchone()) or {}).get('n') or 0)

    per = min(int(per or 0) or FAVORITE_MAIL_LIMIT, FAVORITE_MAIL_LIMIT)
    pages = max(1, -(-total // per))
    # ⚠ Страницу поправляем, а не отвергаем: журнал мог укоротиться, пока человек
    # листал, и отказ вместо последней страницы читался бы как поломка.
    page = min(max(int(page or 1), 1), pages)

    # ⚠ По времени исхода, а не по номеру: журнал читают как ленту
    # «что происходило», и неудача, случившаяся позже удачи, обязана
    # стоять выше. Тот же порядок, что у журнала инвайтов.
    sql += (' ORDER BY COALESCE(m.sent_at, m.updated_at, m.created_at) DESC,'
            ' m.id DESC LIMIT %s OFFSET %s')
    args.extend([per, (page - 1) * per])

    async with mysql_get_db_async() as db:
        await db.execute(sql, tuple(args))
        rows = await db.fetchall()

    # ⚠ Флаг отправки и счёт пула едут **вместе со списком**, как у инвайтов: по
    # первому страница решает, живая ли кнопка на карточке, по второму — показывать
    # ли «пул полон». Спрашивай она их порознь, полоса подвкладок успевала бы
    # показать кнопку живой там, где отправка снята.
    # ⚠ **Срок круга и повод пустоты — разные ответы, и нужны оба.** Срок говорит,
    # когда круг зайдёт; повод — почему он уйдёт ни с чем. Наблюдалось живьём:
    # анкета в сети, её избранный в сети, генерация включена, пул пуст — а причина
    # («писали 21 минуту назад, пауза 4 часа») была видна только в журнале воркера.
    # Один срок без повода читается как обещание набора к этому сроку.
    return {'result': [_answer(row) for row in rows or []],
            'page': page, 'pages': pages, 'total': total,
            'pool': await favorite_mail_pool_left(int(site_id)),
            'next_sec': favorite_mail_round_next(int(site_id)),
            # ⚠ Сдвиг нужен и здесь: первый вопрос повода — «идёт ли смена», а
            # смены заданы местным временем панели, не UTC.
            'why': await favorite_mail_why(int(site_id),
                                           await setting_timezone_hours()),
            # ⚠ `why` — про **набор** («почему пул пуст»), `send_why` — про
            # **отправку** («почему полный пул не уходит»). Вопроса два, и ответ на
            # один ничего не говорит о другом: пул бывает пуст при живой отправке и
            # полон при выбранной норме.
            'send_why': await send_why(
                int(site_id), FAVORITE_MAIL_TABLE,
                await setting_timezone_hours()),
            'send_on': await _send_on(int(site_id))}


@router.get('/delivery', include_in_schema=False)
async def favorite_mail_delivery(site_id: int = 0, profile_id: int = 0,
                                        uid: str = '',
                                        page: int = 1, per: int = 0,
                                        user: AuthUser = Depends(auth_required)):
    """Журнал доставки избранным: **приставание и ответы одним списком**, страницей.

    Args:
        site_id: сайт-закладка. Ноль — пустой список.
        profile_id: сузить до одной анкеты. Ноль — все анкеты сайта.

            ⚠ **Фильтр, а не условие показа**, как у списка разговоров на той же
            вкладке: без выбора журнал полон и отвечает на «что вообще уходило
            избранным этого сайта».
        uid: только письма этому собеседнику. Пусто — всем.

            ⚠ Отбор именами — нажатием по имени **в строке журнала**, как у инвайтов
            (§3.9). Обе стороны порознь и обе разом: «от Дианы и ему» — законный
            вопрос. Считается он **на сервере**: отбери мы среди присланного, «все
            письма Семёну» означало бы «те, что попали на открытую страницу».
        page: страница, с единицы. Больше последней — отдаём последнюю.
        per: сколько строк на ней. Ноль — умолчание `FAVORITE_MAIL_DELIVERY_PER`.

    Returns:
        dict: строки обоих родов, свежие сверху, у каждой `kind` (`favorite` — письмо
        замолчавшему, `reply` — ответ на его письмо). Рядом `total`, `page`, `pages` —
        ими страница рисует пейджер.

    ## ⚠⚠ Зачем здесь ответы

    Вкладка «Фавориты» ведёт одно занятие целиком: кому пишем (лента и пул), о чём
    говорим («Диалоги»), что ушло («Доставка»). Ответы стоят в том же ряду — третьей
    колонкой «Диалогов», — а их исход виден был только на «Письмах → Доставка», то
    есть работа делалась на одной вкладке, а её итог показывался на другой.

    Мера сходимости, снятая с прода: **из четырёх ушедших ответов все четыре** —
    паре, которая есть в избранных. Это не совпадение, а устройство: звёздочку жмёт
    круг избранных по входящему письму (`favorite_round`), ответ пишет круг ответов по
    тому же письму. Один повод — два занятия, и получатель у них один.

    ⚠⚠ **Журналы делят ответы, а не показывают каждый своё.** Пары из звёздочек — здесь,
    все прочие — в «Письмах» (§3.7.5); вместе это все ответы ровно по одному разу.

    Сперва было иначе: строка честно попадала в оба журнала («журнал, а не пул»), — и
    человек это отменил. Довод простой: ответ избранному ищут на «Фаворитах», а второй
    список того же самого лишь заставляет сверять их между собой.

    ⚠ Ответ незнакомому сюда не попадает — на этой вкладке его не с чем связать: ни
    разговора в списке, ни звёздочки.

    ## Как берётся страница

    Тем же приёмом, что у журнала писем (`mailing_delivery`), и по той же причине:
    «вторая страница приставания» плюс «вторая страница ответов» — не вторая страница
    журнала. Берём по `page * per` свежих из каждой таблицы, сливаем, отрезаем нужный
    кусок; счёт — сумма двух `COUNT` по тем же условиям.

    ⚠ Цена приёма растёт с номером страницы: сороковая по 25 строк читает по тысяче
    строк из каждой таблицы. Журнал читают с первых страниц — терпимо; предел назван,
    чтобы следующий читатель не счёл это недосмотром.

    ⚠⚠ У ответа **нет** `updated_at`, и у неудачного его дата — время сочинения
    (`created_at`), а не отказа. У писем избранным столбец есть, и там показывается
    минута неудачи. Разница видна в журнале и лечится миграцией `mail_reply`, а не
    показом: подставлять «сейчас» там, где времени нет, — это соврать свежей датой.
    """
    if not int(site_id or 0):
        # ⚠ Поля пейджера есть и в пустом ответе: страница читает их без проверок, и
        # `pages: undefined` нарисовало бы пейджер из одной мёртвой страницы.
        return {'result': [], 'page': 1, 'pages': 1, 'total': 0}

    per = min(int(per or 0) or FAVORITE_MAIL_DELIVERY_PER, FAVORITE_MAIL_LIMIT)
    page = max(1, int(page or 1))
    # Сколько свежих строк хватит взять из каждой таблицы, чтобы отрезать нужную
    # страницу слияния.
    take = page * per

    # ⚠ Отбор ответов — **общими кусками** (`mail_reply_delivery_scope`): те же, что
    # уйдут в чтение строк ниже. Своё условие здесь разошлось бы со чтением, и число
    # страниц перестало бы отвечать содержимому.
    join, where, args = mail_reply_delivery_scope(favorites=True,
                                                 profile_id=int(profile_id),
                                                 uid=str(uid or ''))
    # ⚠ Отбор по своей таблице — той же `pool_pick_where`, что и у ответов выше:
    # условие одно, и два его написания разошлись бы на первой правке.
    own, own_args = pool_pick_where('m', int(profile_id), str(uid or ''))

    async with mysql_get_db_async() as db:
        await db.execute(
            'SELECT (SELECT COUNT(*) FROM favorite_mail m WHERE m.site_id = %s'
            + pool_delivery_where('m') + own +
            ') AS letters,'
            ' (SELECT COUNT(*) FROM mail_reply r' + join +
            '   WHERE r.site_id = %s' + where +
            ') AS replies',
            tuple([int(site_id)] + own_args + [int(site_id)] + args))
        counts = (await db.fetchone()) or {}
        total = int(counts.get('letters') or 0) + int(counts.get('replies') or 0)

        pages = max(1, -(-total // per))
        # ⚠ Страницу поправляем, а не отвергаем: журнал мог укоротиться, пока человек
        # листал, и отказ вместо последней страницы читался бы как поломка.
        page = min(page, pages)
        take = page * per

        # ⚠ Письма избранным — **тем же куском полей, что пул** (`pool_pair_*`): обе
        # стороны рисуются общим видом, и второй набор полей разошёлся бы с первым.
        await db.execute(
            'SELECT m.id, m.site_id, m.profile_id, m.uid,'
            + pool_pair_select() +
            ' m.body, m.status, m.error, m.answer, m.sent_at, m.created_at,'
            ' m.updated_at,'
            ' p.login AS profile_login, p.profile AS profile_card'
            ' FROM favorite_mail m'
            ' LEFT JOIN profile p ON p.id = m.profile_id'
            + pool_pair_joins() +
            ' WHERE m.site_id = %s'
            + pool_delivery_where('m') + own
            # ⚠ По времени события, а не по номеру: журнал читают как ленту «что
            # происходило», и неудача, случившаяся позже удачи, обязана стоять выше.
            + ' ORDER BY COALESCE(m.sent_at, m.updated_at, m.created_at) DESC,'
            ' m.id DESC LIMIT %s',
            tuple([int(site_id)] + own_args + [take]))
        letters = [{**_answer(row), 'kind': 'favorite'}
                   for row in (await db.fetchall()) or []]

        # ⚠ Ответы — той же общей функцией, что у журнала писем, с одним отличием:
        # `favorites=True`. Отличие и есть весь смысл этой вкладки.
        replies = await mail_reply_delivery(db, int(site_id), take, favorites=True,
                                           profile_id=int(profile_id),
                                           uid=str(uid or ''))

    # ⚠ Ключ сортировки — **время события**, и он же стоит в обоих запросах.
    # Разойдись они — срез брал бы строки не в том порядке, в котором их считали.
    rows = sorted(letters + replies,
                  key=lambda one: (str(one.get('sent_at')
                                       or one.get('updated_at')
                                       or one.get('created_at') or ''),
                                   int(one.get('id') or 0)),
                  reverse=True)

    return {'result': rows[(page - 1) * per:page * per],
            'page': page, 'pages': pages, 'total': total}


@router.get('/threads', include_in_schema=False)
async def favorite_mail_threads(site_id: int = 0, profile_id: int = 0,
                                       page: int = 1, size: int = 0,
                                       answered: int = 1,
                                       user: AuthUser = Depends(auth_required)):
    """Переписки с избранными: пары «анкета — человек», свежие сверху, страницей.

    Args:
        site_id: сайт-закладка. Ноль — пустой список, а не переписки всех сайтов.
        profile_id: сузить до одной анкеты. Ноль — все анкеты сайта.

            ⚠ **Фильтр, а не условие показа**, как у диалогов чата (§3.8) и у
            журнала доставки: без выбора список полон и отвечает на «что вообще
            происходит на сайте».
        page: страница, с единицы. Больше последней — отдаём последнюю.
        size: сколько строк на странице. Ноль — умолчание
            (`FAVORITE_MAIL_THREADS_PAGE`). Считает страница, по высоте видимой
            области; потолок — на сервере.
        answered: показывать только переписки, где собеседник **ответил хотя бы
            раз**. Единица — да, и это **умолчание**; ноль — показывать все.

            ⚠⚠ **То же правило, что у диалогов чата**, где оно зовётся «диалог
            начинается с ответа» (`chat_dialogs`) и включено всегда. Здесь у него
            есть выключатель, и вот почему: у чата ушедшие инвайты видны на своей
            подвкладке «Доставка», а тут вопрос «кому мы писали, а он молчит»
            задают и самому списку — но задают редко, поэтому умолчание отсекает.

            ⚠ Проверяется по **входящим письмам** (`side = 'him'`), а не по длине
            переписки: десять наших писем без единого ответа — это не разговор, и
            `letters` у такой пары как раз десять.

    Returns:
        dict: `result` — по записи на пару: `profile_id`, `profile_name`, `uid`,
        `name`, `photo`, `letters` (сколько писем в переписке), `last_at` (когда
        последнее), `last_side` (`him` — ждёт нашего ответа, `her` — ждём его),
        `in_pool` (лежит ли ему черновик), `is_online` (запасной признак «в
        сети», см. ниже). Рядом `total`, `page`, `pages` — ими страница рисует
        пейджер.

    ⚠ **Переписка — это письма, а не звёздочка.** В списке только те избранные, с
    кем письма были; добавленный в избранные и молчащий сюда не попадает —
    показывать пустой разговор незачем.

    ⚠⚠ **`is_online` здесь не ответ, а слагаемое.** «В сети ли он» решает лента
    онлайна, и считает это **одно правило страницы** на оба списка разговоров
    (`talkOnline`); поле из `contact` — запасное, для сайтов без ленты. Отдаём мы
    его затем, что правило одно: приезжай слагаемые в две ручки по-разному, один
    и тот же человек вышел бы зелёным в «Чате» и серым здесь.

    ⚠ **Считаем по всем трём источникам**: входящие, наши ответы и оба пула
    ушедшего (рассылка и возобновление). Смотри мы только входящие, переписка, где
    последними писали мы, выглядела бы короче, чем есть, — а именно она здесь
    главная: это и есть затихший разговор.

    ⚠⚠ **Страницами, а не «первыми тремястами».** Прежде список отдавался одним
    `LIMIT 300` без счёта, и на живом сайте всё, что дальше, не показывалось
    **молча**: прокрутка доезжала до конца, и конец выглядел концом списка.
    Пейджеру нужен ещё и счёт — иначе он не знает, сколько страниц, а человек не
    знает, сколько всего переписок.
    """
    if not int(site_id or 0):
        return {'result': [], 'total': 0, 'page': 1, 'pages': 1}

    # ⚠⚠ **Отбор пар — один кусок SQL на счёт и на список.**
    #
    # Пейджер рисуется по счёту, а листается список: разойдись условия — число
    # страниц перестало бы отвечать содержимому, и последняя выходила бы пустой.
    # Тот же приём и по той же причине стоит у диалогов чата (`chat.py`), где
    # правило «диалог начинается с ответа» повторено в обоих запросах.
    #
    # ⚠ `contact` и `companion` сюда не входят: они дают **имя и лицо**, а не
    # участвуют в отборе. Ключи у обоих уникальны (`uk_dating_contact`,
    # `uq_dating_companion`), строк не размножают — значит счёту не нужны, а
    # лишний `JOIN` в нём стоил бы двух проходов по чужим таблицам.
    #
    # ⚠⚠ Кусок разрезан **на соединение и условие**, чтобы список вставил свои
    # `LEFT JOIN` между ними. Резать готовую строку `replace`-ом нельзя: правка
    # пробела в одном месте молча оставила бы список без имён.
    pairs_join = (
        '  FROM favorite f'
        '  JOIN profile p ON p.id = f.profile_id'
        '  JOIN ('
        '     SELECT profile_id, uid, COUNT(*) AS letters, MAX(at) AS last_at,'
        # ⚠ Сколько написал **он**: по этому числу отсекаются переписки, где
        # говорили только мы. Считается здесь же, в той же группировке, а не
        # отдельным `EXISTS`: строки уже собраны и размечены сторонами, и второй
        # проход по четырём таблицам ради того же ответа был бы платой ни за что.
        "            SUM(side = 'him') AS his_letters,"
        '            SUBSTRING_INDEX(MAX(CONCAT(at, %s, side)), %s, -1) AS last_side'
        '       FROM ('
        "          SELECT profile_id, uid, date_post AS at, 'him' AS side"
        '            FROM mail_in'
        '          UNION ALL'
        "          SELECT profile_id, uid, sent_at AS at, 'her' AS side"
        "            FROM mail_reply WHERE status = 'sent'"
        '          UNION ALL'
        "          SELECT profile_id, uid, sent_at AS at, 'her' AS side"
        "            FROM mailing WHERE status = 'sent'"
        '          UNION ALL'
        "          SELECT profile_id, uid, sent_at AS at, 'her' AS side"
        "            FROM favorite_mail WHERE status = 'sent'"
        '       ) AS all_mail'
        '      WHERE at IS NOT NULL'
        '      GROUP BY profile_id, uid) AS talk'
        '    ON talk.profile_id = f.profile_id AND talk.uid = f.uid')

    pairs_where = (
        ' WHERE f.site_id = %s AND (%s = 0 OR f.profile_id = %s)'
        # ⚠⚠ «Он ответил хотя бы раз» — **здесь, в общем куске**, а не в запросе
        # списка. Кусок один на счёт и на список: поставь условие только списку —
        # пейджер считал бы страницы по всем парам, и последние выходили бы
        # пустыми. Та же причина, по которой здесь же стоит отбор по анкете.
        '   AND (%s = 0 OR talk.his_letters > 0)'
        # ⚠ Тем же набором состояний, что отсекает их у «Входящих»: разойдись
        # перечисления — пара либо задвоилась бы в двух списках, либо исчезла из
        # обоих. `fail` не считается избранным: звёздочку нажать не вышло.
        f'   AND f.state IN ({FAVORITE_STATES_SQL})')

    # Имя и лицо собеседника. ⚠ Только в списке: счёту они не нужны.
    pairs_card = (
        '  LEFT JOIN contact c'
        '    ON c.profile_id = f.profile_id AND c.uid = f.uid'
        '  LEFT JOIN companion comp'
        '    ON comp.site_id = f.site_id AND comp.uid = f.uid')

    # Доводы отбора: две метки разделителя `last_side`, сайт, анкета дважды,
    # «только отвеченные» и состояния избранного. Нужны обоим запросам, и в том же
    # порядке — список вставляет свои соединения между частями куска, но доводы
    # идут одним списком, и порядок в нём задаёт сам SQL.
    pairs_args = [' ', ' ', int(site_id), int(profile_id), int(profile_id),
                  1 if int(answered or 0) else 0, *FAVORITE_STATES]

    # ⚠ Предел и его потолок — общие с диалогами чата (`chat_dialogs_size`):
    # списки соседние, листаются одним пейджером, и два правила разошлись бы.
    size = chat_dialogs_size(size, FAVORITE_MAIL_THREADS_PAGE)

    async with mysql_get_db_async() as db:
        await db.execute(f'SELECT COUNT(*) AS n {pairs_join}{pairs_where}',
                         tuple(pairs_args))
        total = int(((await db.fetchone()) or {}).get('n') or 0)

        pages = max(1, (total + size - 1) // size)
        page = min(max(int(page or 1), 1), pages)

        # Свежие сверху: список читают, чтобы найти работу, а работа — в том, что
        # шевелилось последним.
        await db.execute(
            'SELECT f.profile_id, f.uid,'
            # ⚠ Тот же кусок, что у пулов писем: вопрос «как зовут и как
            # выглядит собеседник» один на весь раздел.
            + pool_pair_select('f', own_name=False, own_photo=False) +
            '       p.login AS profile_login, p.profile AS profile_card,'
            # ⚠ «В сети» — тем же полем, что у диалогов чата (`chat_dialogs`).
            # Решает его не оно, а лента онлайна: страница считает признак одним
            # правилом на оба списка (`talkOnline`), и поле там запасное — для
            # сайтов, у которых ленты нет. Но слагаемые правила обязаны приезжать
            # в обе ручки, иначе один и тот же человек выйдет зелёным в «Чате» и
            # серым в «Фаворитах», а объяснить разницу будет нечем.
            #
            # ⚠ Лишнего соединения не стоит: `contact c` уже присоединён ради
            # имени и лица (`pairs_card`).
            '       c.is_online,'
            '       talk.letters, talk.last_at, talk.last_side,'
            '       EXISTS (SELECT 1 FROM favorite_mail d'
            '                WHERE d.profile_id = f.profile_id AND d.uid = f.uid'
            "                  AND d.status = 'draft') AS in_pool"
            + pairs_join + pairs_card + pairs_where
            + ' ORDER BY talk.last_at DESC LIMIT %s OFFSET %s',
            # ⚠ Шести пустых строк здесь больше нет: общий кусок
            # (`pool_pair_select`) ставит их литералами, а не заполнителями.
            tuple([*pairs_args, size, (page - 1) * size]))
        rows = await db.fetchall()

    threads = []
    for row in rows or []:
        got = dict(row)
        card = json_column_load(got.pop('profile_card', None)) or {}

        got['profile_name'] = str(card.get('name') or got.pop('profile_login', '') or '')
        got.pop('profile_login', None)
        # ⚠ И лицо анкеты: карточка разговора показывает **обе** стороны, как в
        # диалогах чата. Берём из той же анкеты с сайта (`profile_.profile`),
        # что и список слева, — иначе одна и та же девушка выглядела бы в двух
        # местах по-разному.
        got['profile_photo'] = str(card.get('photo') or '')
        got['letters'] = int(got.get('letters') or 0)
        got['last_at'] = str(got['last_at']) if got.get('last_at') else ''
        got['in_pool'] = bool(got.get('in_pool'))
        # ⚠ К `bool` здесь же: у пары без карточки в `contact` поле приезжает
        # `None`, и страница получила бы третье состояние там, где их два.
        got['is_online'] = bool(got.get('is_online'))
        threads.append(got)

    return {'result': threads, 'total': total, 'page': page, 'pages': pages}


@router.get('/threads/{profile_id}/{uid}', include_in_schema=False)
async def favorite_mail_thread(profile_id: int, uid: str,
                                      user: AuthUser = Depends(auth_required)):
    """Сама переписка с человеком: письма обеих сторон, старые сверху.

    ⚠ **Собирает её `mail_reply_thread`** — тот же код, что показывает
    разговор на «Входящих». Разговор один, и второй способ его собрать разошёлся
    бы с первым: там письмо рассылки уже входит в нить, а неотправленный черновик
    стоит последним, а не по времени сочинения.

    ⚠⚠ **`with_favorite` — здесь обязательно, и вот почему.** Список диалогов
    считает пары по четырём источникам, включая ушедшие приставания, а нить по
    умолчанию собирается тремя: на «Входящих» приставаниям не место. Пара, у
    которой ушло только приставание, попадала в список — и открывалась пустой.

    Поймано на проде: у Дианы с Семёном лежали входящие письма, и разговор
    показывался; у Екатерины ушли одни приставания, и справа было пусто.
    """
    return {'result': await mail_reply_thread(int(profile_id), str(uid),
                                              with_favorite=True)}


@router.get('/queue', include_in_schema=False)
async def favorite_mail_queue_get(site_id: int = 0,
                                  user: AuthUser = Depends(auth_required)):
    """Очередь приставания по анкетам сайта — под значок в списке слева.

    Returns:
        dict: `{номер анкеты: {wait_sec, gap_sec, ready, next_name, next_uid}}`.
        Анкеты, которой писать некому, в ответе нет вовсе — значка у неё тоже
        не будет.

    ⚠ **Считает домен** (`favorite_mail_queue`) теми же выражениями, что и круг:
    паузой и отбором пар. Своя копия счёта в ручке разошлась бы с ним на первой
    правке — та же грабля, что у `explain` строкой ниже.

    ⚠ **Пул общий на сайт**, и ответ тоже: значок стоит у каждой строки списка,
    и запрос на анкету означал бы сорок запросов на отрисовку одной страницы.

    ⚠ Стоит **выше** путей с номером — тот же довод, что у `explain`.
    """
    if not int(site_id or 0):
        return {'result': {}}

    return {'result': {str(key): value for key, value
                       in (await favorite_mail_queue(int(site_id))).items()}}


@router.get('/explain', include_in_schema=False)
async def favorite_mail_explain(profile_id: int = 0, uid: str = '',
                                user: AuthUser = Depends(auth_required)):
    """Что с почтой у этой пары: что уйдёт следующим и что мешает сейчас.

    ⚠ **Считает домен** (`mail_explain.mail_explain`) теми же выражениями, что и
    круги. Своя копия счёта в ручке разошлась бы с ними на первой правке — ровно
    та грабля, что уже была с `initiative_why` и со значком потолка.

    ⚠ Стоит **выше** путей с номером (`/{mail_id}/send`, `/{mail_id}`): соседний
    роутер уже ловил на этом — путь-слово, объявленный ниже пути-числа,
    достаётся преобразователю номера, и `explain` отвечал бы 422. Методы у них
    сейчас разные, но порядок надёжнее совпадения методов.

    ⚠ Незнакомая пара — не ошибка, а обычный исход: человек открывает окно у
    разговора, которого в наших таблицах ещё нет. Отвечаем `known: false` со
    словами, а не 404: 404 страница нарисовала бы красным.

    ⚠ Сдвиг нужен: первый вопрос обоих занятий — «идёт ли смена», а смены
    заданы местным временем панели, не UTC.
    """
    if not int(profile_id or 0) or not str(uid or ''):
        raise HTTPException(status_code=422, detail='нужны profile_id и uid')

    got = await mail_explain(int(profile_id), str(uid),
                             await setting_timezone_hours())

    return {'result': got or {'known': False}}


@router.post('/generate', include_in_schema=False)
async def favorite_mail_generate(payload: DatingFavoriteMailGenerate,
                                        user: AuthUser = Depends(auth_required)):
    """Набирает пул: письма тем избранным, чей разговор затих.

    Returns:
        dict: `made`, `skipped`, `reasons`, `left`, `stopped` и `pool` — сколько
        в пуле стало.

    ⚠ **Заход ничего не отправляет.** Он готовит письма; уносит их круг отправки,
    и только при поднятом `favorite_mail_send_on`.

    ⚠ **Повторное нажатие продолжает с того же места**: пары, которым письмо уже
    лежит в пуле или ушло недавно, отсеиваются самим отбором. Поэтому кнопку можно
    жать, пока пул не наберётся, и не бояться дублей.
    """
    site_id = int(payload.site_id)
    got = await favorite_mail_make(site_id, int(payload.limit),
                                          int(payload.profile_id))

    return {'result': {**got, 'pool': await favorite_mail_pool_left(site_id)}}


# ⚠⚠ **Правки текста письма здесь нет, и это решение.** Письмо сочиняет агент
# `dating_talk`, и сосед записывает сказанное в свой `companion_session` **внутри
# прогона** — то есть до того, как круг отправки унесёт письмо человеку. Исправь
# текст здесь, и уйдёт одно, а в памяти агента останется другое: в следующем
# разговоре он продолжит от слов, которых собеседник не читал.
#
# Расхождение **молчаливое**: ни в журнале, ни на странице его не видно, а
# всплывает оно странным ответом агента через неделю.
#
# ⚠ Не нравится черновик — его **удаляют** (`DELETE` ниже, поштучно и пулом), а
# не переписывают. Круг сочинит новое.


@router.post('/{mail_id}/send', include_in_schema=False)
async def favorite_mail_send(mail_id: int,
                                    user: AuthUser = Depends(auth_required)) -> dict:
    """Отправляет одно письмо из пула — то, на кнопку которого нажали.

    Raises:
        HTTPException: 404 — письма нет; 409 — не ушло, с причиной словами.

    ⚠ **Кнопка живёт рядом с кругом отправки, и это не противоречие.** Круг шлёт
    сам, по часам сайта и с паузой; кнопка нужна тому, кто прочитал письмо и хочет
    отправить его сейчас, не дожидаясь очереди. Так же устроена «Рассылка».

    ⚠ **Не ушло — 409, а не 500.** Отказ сайта это не поломка панели: она
    отработала верно и принесла причину. Письмо при этом **остаётся в пуле** —
    почему именно так, сказано в `mail_send.py`.
    """
    letter = await _row(mail_id)

    if letter['status'] != 'draft':
        raise HTTPException(status_code=status.HTTP_409_CONFLICT,
                            detail='письмо уже ушло — второй раз не отправляем')

    profile = await pool_handle_sender(int(letter['profile_id']), 'письма')
    site_id = int(letter['site_id'])

    base = str(await site_setting(site_id, 'api_base')).strip()
    password = str(await site_setting(site_id, 'api_password')).strip()

    try:
        await mail_send_one(letter, profile, base, password,
                                   FAVORITE_MAIL_TABLE)
    except DatingMailSendError as e:
        # ⚠ Причину храним **до** ответа: страница покажет её на карточке, а человек
        # закроет вкладку и вернётся завтра — в ответе HTTP её к тому времени нет.
        # ⚠ Но **не всякую**: временная причина письмо не портит (`mail_send_fail`).
        await mail_send_fail(letter, e, table=FAVORITE_MAIL_TABLE)
        logger_info(f'[dating] письмо избранному #{mail_id} не ушло: {e}')

        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e))

    return {'result': await _row(mail_id)}


@router.delete('', include_in_schema=False)
async def favorite_mail_clear(site_id: int = 0,
                                     user: AuthUser = Depends(auth_required)):
    """Очищает пул сайта — разом всё, что ещё не ушло.

    ⚠ **Ушедшее остаётся.** Оно уже случилось, и стереть запись значило бы соврать
    журналу — а по этим же строкам считается, сколько человеку уже написали.

    ⚠ **Путь без номера объявлен до `/{mail_id}`**: иначе пустой хвост однажды
    прочитался бы как номер письма.
    """
    return await pool_handle_clear('favorite_mail', site_id,
                                   what='писем избранным')


@router.delete('/{mail_id}', include_in_schema=False)
async def favorite_mail_drop(mail_id: int,
                                    user: AuthUser = Depends(auth_required)):
    """Убирает письмо из пула.

    Raises:
        HTTPException: 404 — письма нет; 409 — оно уже ушло.
    """
    return await pool_handle_drop(
        'favorite_mail', mail_id,
        missing=f'письма #{mail_id} нет',
        sent='письмо уже ушло — из журнала оно не убирается')


# ── Приватное ────────────────────────────────────────────────────────────────

async def _send_on(site_id: int) -> bool:
    """Разрешена ли отправка писем избранным.

    ⚠ Не прочиталось — считаем снятым. Верная сторона ошибки: молчание дешевле
    письма, ушедшего живому человеку вопреки решению.
    """
    try:
        async with mysql_get_db_async() as db:
            await db.execute('SELECT favorite_mail_send_on FROM site'
                             ' WHERE site_id = %s', (int(site_id),))
            row = await db.fetchone()
    except Exception as e:
        logger_info(f'[dating] выключатель писем избранным сайта #{site_id} '
                    f'не прочитался: {e}')

        return False

    return bool((row or {}).get('favorite_mail_send_on'))


async def _row(mail_id: int) -> dict:
    """Письмо по номеру или 404."""
    async with mysql_get_db_async() as db:
        await db.execute(
            'SELECT m.id, m.site_id, m.profile_id, m.uid, m.name, m.photo,'
            ' m.body, m.status, m.error, m.sent_at, m.created_at, m.message_id,'
            ' p.login AS profile_login, p.profile AS profile_card'
            ' FROM favorite_mail m'
            ' LEFT JOIN profile p ON p.id = m.profile_id'
            ' WHERE m.id = %s', (int(mail_id),))
        row = await db.fetchone()

    if not row:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                            detail=f'письма #{mail_id} нет')

    return _answer(row)


def _answer(row) -> dict:
    """Строка пула в вид для страницы."""
    got = dict(row)
    card = json_column_load(got.pop('profile_card', None)) or {}

    # В сети ли адресат — из снимка ленты, тем же `online_show`, что у
    # карточек рассылки: правило одно, и вторая копия разошлась бы.
    online = online_show(int(got['site_id']), str(got['uid']))

    got['online'] = bool(online.get('online'))
    got['online_known'] = bool(online.get('known'))
    # ⚠ Имя, лицо и номер анкеты — общим куском (`pool_pair.py`): «Доставка» и
    # окно письма рисуют обе стороны тем же видом, что «Диалоги» (`talkSide`), и
    # так же их рисует рассылка.
    pool_pair_girl(got, card)
    got['sent_at'] = str(got['sent_at']) if got.get('sent_at') else ''
    got['created_at'] = str(got.get('created_at') or '')
    got['updated_at'] = str(got['updated_at']) if got.get('updated_at') \
        else ''

    return got
