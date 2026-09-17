"""Выход анкеты наружу: свой IP на все её двери.

⚠ **Зачем это вообще.** Сайт знакомств считает учётки **по внешнему адресу**: с
одного IP пускают несколько девушек, дальше вход отвергается. Значит анкете нужен
свой выход — и один и тот же везде, где мы стучимся к сайту:

| Дверь | Через что ходит |
|-------|-----------------|
| вход сценарием | Chromium у соседа (`proxy_id` в сессии) |
| сценарии без своей вкладки | `proxy_id` в прогоне |
| `POST /auth/login` и всё API сайта | `site_api` |
| присутствие «в сети» | `heartbeat` |
| вебсокет чата | `chat`, `send` |
| кабинет и анкета страницей | `profile_cache` |

⚠⚠ **Разные адреса у одной учётки хуже одного общего.** Вход браузером с одного
IP и присутствие с другого выглядят для сайта как две попытки на одну учётку —
то есть как угон, а не как несколько девушек. Отсюда правило: **новая дверь
наружу берёт выход здесь**, а не ходит напрямую.

## Кто что решает

| Вопрос | Кто отвечает |
|--------|--------------|
| какие выходы есть и какие живые | реестр соседа (`/api/browser/proxy`) |
| **кому какой достанется** | здесь |
| **сколько анкет допустимо на один IP** | настройка **сайта** `proxy_seats_per_ip` |

⚠ **Раздача живёт здесь не по удобству, а по границе.** Сутки она была у соседа:
он хранил, кто на каком выходе сидит, и знал предел «в анкетах». Но про анкеты
там не знают, а предел выдумывает сайт знакомств — у Goldenbride он один, у
Ladadate другой, и в проекте, где нет ни анкет, ни сайтов знакомств, этому числу
взяться было неоткуда.

Теперь сосед отдаёт список с **увиденным адресом**, а считаем мы: своих анкет по
`profile_.proxy_ip`, предел — из настройки сайта.

## Считаем по увиденному IP, а не по номеру выхода

Два разных прокси нередко выходят одним адресом — так устроены пулы у
провайдеров. Считай мы по номеру выхода, предел сайта они делили бы молча, и
«одна анкета на прокси» означало бы двух на адрес.

## Кэш у анкеты

`profile_.proxy_id`, `proxy_url`, `proxy_ip`, `proxy_at`. Нужен по цене
вопроса: присутствие бьёт каждые полминуты по всем анкетам, круги идут
непрерывно — поход к соседу на каждый запрос означал бы, что его перезапуск
гасит работу всей установки.

Правка выхода у соседа доезжает по сроку `PROXY_FRESH_SEC`.

## Два выключателя, и первый главнее

⚠ **`proxy_enabled` — настройка сайта, галочка в шапке вкладки «Прокси».** Она
отвечает на вопрос «пользуется ли этот сайт выходами вообще», и снята она **по
умолчанию**. Пока снята, механизма для сайта нет: реестр не спрашивается, выходы
не занимаются, круг проверки к соседу не идёт, присутствие бьёт общим адресом, а
в журнал ничего про прокси не пишется. Ровно так проект работал до появления
прокси, и включение — сознательный шаг человека, у которого каналы уже куплены.

`proxy_required` — второй, и спрашивается он только у сайта, где первый включён.

## Выключатель `proxy_required`

⚠ **Он существует ради выкладки.** В момент, когда это приезжает на живую
установку, выхода нет ни у одной анкеты: реестр пуст. Включи мы требование сразу
— встала бы вся работа разом.

По умолчанию **выключено**: анкета без выхода ходит как раньше, общим адресом
сервера, а в журнале видно, что выхода у неё нет. Когда реестр заполнен и анкеты
разъехались, человек включает `proxy_required` на странице «Настройки →
Значения», и с этого момента анкета без выхода не работает вовсе.

⚠ **Пока выключатель снят, защиты нет.** Это не «мягкий режим», а «как было».
"""

from datetime_.datetime_ import datetime_utc_now
from logging_.logging_ import logger_info
from mysql_.mysql_ import mysql_get_db_async
from setting_.setting_ import setting_get

from src.dating.log import log_write
from src.rhapsody.rhapsody_api import RhapsodyError, rhapsody_api_proxy_list

# Сколько живёт кэш адреса. Полчаса: пароль канала меняют редко, а перечитывать
# его чаще значит ходить к соседу ради строки, которая не менялась неделями.
PROXY_FRESH_SEC = 30 * 60

# ⚠⚠ **Главный выключатель: пользуется ли сайт выходами вообще.** Настройка
# **сайта**, галочка в шапке вкладки «Прокси». Снята — ни одна дверь наружу за
# выходом не идёт: ни раздача, ни реестр соседа, ни круг проверки, ни удары
# присутствия. Не «идём общим адресом на всякий случай», а «этого механизма для
# сайта нет».
#
# ⚠ **По умолчанию выключено, и это правильное умолчание.** Прокси заводят не
# сразу: реестр пуст, каналы не куплены, страны не отмечены. Включённый механизм в
# этот момент означал бы поход к соседу на каждой двери и строку «своего выхода
# нет» в журнале каждой анкеты — шум там, где ещё нечего показывать.
PROXY_ENABLED_SETTING = 'proxy_enabled'
PROXY_ENABLED_DEFAULT = '0'

# Требовать ли выход. Настройка панели («Настройки → Значения»), не окружение:
# её включают один раз, когда реестр заполнен, и делает это человек, а не выкладка.
#
# ⚠ Она **вторая**, а не единственная: спрашивать её у сайта, который выходами не
# пользуется, незачем — там нет ни требования, ни послабления, там нет прокси.
PROXY_REQUIRED_SETTING = 'proxy_required'
PROXY_REQUIRED_DEFAULT = '0'

# Сколько анкет допустимо на один внешний адрес. Настройка **сайта**
# (`site_.settings`): предел выдумывает сайт знакомств, и у каждого он свой.
PROXY_SEATS_SETTING = 'proxy_seats_per_ip'
PROXY_SEATS_DEFAULT = 1

# Из каких стран берём выходы: коды ISO через запятую. ⚠ Пусто значит **все**,
# а не «ни одной»: обратное умолчание оставило бы новый сайт без выходов молча.
PROXY_COUNTRIES_SETTING = 'proxy_countries'

# Как часто круг просит реестр проверить выходы. Настройка сайта, но круг один на
# установку и берёт **наименьший** срок из настроенных (`proxy_round.py`).
PROXY_PING_SETTING = 'proxy_ping_every'

# Имя замка на время раздачи. ⚠ Раздача читает занятость всех адресов и пишет
# одну строку: два круга внахлёст увидели бы одно и то же свободное место и заняли
# бы его оба. Замок MySQL проще транзакции с `FOR UPDATE` по нескольким строкам, а
# раздача — случай редкий: она бывает раз в жизни анкеты.
PROXY_LOCK = 'proxy_take'
PROXY_LOCK_WAIT = 5


class DatingProxyError(RuntimeError):
    """Свободного выхода нет. Текст пригоден для журнала анкеты и для страницы."""


async def proxy_url(profile: dict, claim: bool = True) -> str:
    """Адрес выхода анкеты. Пусто — выхода нет и работать ей нечем.

    Args:
        profile: строка анкеты. Нужны `id`, `site_id` и поля `proxy_*`.
        claim: занимать ли выход, если он ещё не закреплён. `False` — только
            читать кэш; годится там, где занимать нельзя (показ страницы).

    Returns:
        str: `socks5://user:pass@host:1080` либо пустая строка.

    ⚠ **Ничего не бросает.** Выход спрашивают в круге присутствия, в отправке, в
    ответе агента — везде, где отказ соседа не должен ронять работу остальных
    анкет. Пустая строка означает «этой анкете сейчас ходить нечем», и решает,
    что с этим делать, вызывающий.

    ⚠⚠ **Выключатель сайта проверяется первым — до кэша.** Здесь единственное
    место, через которое двери берут выход, и снятая галочка обязана гасить
    механизм целиком, а не только новые захваты: иначе анкета с адресом в кэше
    ходила бы прокси ещё полчаса после того, как человек его выключил.
    """
    if not await proxy_enabled(await _site_of(profile)):
        return ''

    cached = str(profile.get('proxy_url') or '')

    if cached and not _stale(profile):
        return cached

    if not claim:
        return cached

    try:
        got = await proxy_take(profile)
    except RhapsodyError as e:
        # ⚠ **«Сосед молчит» и «свободных нет» — разные беды.**
        #
        # Молчит (обрыв связи) — списка мы не получили, но прежний выход за нами
        # никуда не делся: идём им. Не ходить вовсе было бы хуже — сайт видел нас
        # именно с него.
        if cached:
            logger_info(f'[proxy] анкета #{profile.get("id")}: сосед молчит ({e}), '
                        f'идём прежним выходом')

            return cached

        logger_info(f'[proxy] анкета #{profile.get("id")}: выхода нет — {e}')

        return ''
    except DatingProxyError as e:
        # ⚠ Список пришёл, но годного места в нём нет — либо наш прежний выход из
        # реестра исчез. Держаться за кэш здесь нельзя: он ведёт через прокси,
        # которого больше нет, запросы будут падать, а причина выглядеть как беда
        # сайта. Поймано живьём: выход удалили у соседа, а анкеты держали адрес.
        if cached:
            logger_info(f'[proxy] анкета #{profile.get("id")}: выход потерян — {e}')
            await proxy_forget(int(profile.get('id') or 0))
            profile['proxy_id'], profile['proxy_url'], profile['proxy_ip'] = 0, '', ''
        else:
            logger_info(f'[proxy] анкета #{profile.get("id")}: выхода нет — {e}')

        return ''

    return str(got.get('url') or '')


async def proxy_take(profile: dict) -> dict:
    """Закрепляет за анкетой выход и запоминает его.

    Returns:
        dict: строка выхода как её отдал реестр (`id`, `url`, `check_ip`, …).

    Raises:
        RhapsodyError: сосед недоступен — списка нет вовсе.
        DatingProxyError: список есть, но годного места в нём нет.

    ⚠ **Прежний выход подтверждаем, а не выдаём заново** — но только пока он
    влезает в предел. Анкете, которой выход уже достался, нужен тот же самый:
    сайт помнит, с какого адреса она входила, и переезд для него подозрительнее
    паузы. Исчез из реестра, умер или перестал помещаться в предел — ищем новый.

    ⚠ **«Перестал помещаться» бывает без нашего участия**: предел сайта снизили,
    или человек посадил руками вторую анкету на тот же адрес. Кого оставить,
    решаем по старшинству — по времени, когда выход был занят: первые остаются,
    лишние переезжают. Иначе две анкеты на одном адресе решили бы, что лишняя
    каждая, и менялись бы выходами по кругу.
    """
    profile_id = int(profile.get('id') or 0)
    site_id = await _site_of(profile)

    # ⚠ Второй раз после `proxy_url`, и это не лишнее: функция публичная,
    # и захват выхода в обход выключателя — ровно то, чего он не должен допускать.
    # Стоит копейку (одна строка `site`), а закрывает дверь целиком.
    if not await proxy_enabled(site_id):
        raise DatingProxyError(
            'выходы у сайта выключены: включите их галочкой на вкладке «Прокси» '
            'в настройках сайта')

    rows = await rhapsody_api_proxy_list()
    countries = await proxy_countries(site_id)
    if countries:
        # Сайт назвал страны — из других каналов он не ходит.
        rows = [row for row in rows if str(row.get('check_country') or '') in countries]

    if not rows:
        raise DatingProxyError(
            f'нет годных выходов из стран {", ".join(countries)}: заведите прокси '
            f'на странице «Браузер → Прокси» у соседа либо отметьте другую страну'
            if countries else
            'в реестре соседа нет ни одного годного выхода: заведите прокси на '
            'странице «Браузер → Прокси» и проверьте')

    seats = await proxy_seats(site_id)

    async with mysql_get_db_async() as db:
        await db.execute('SELECT GET_LOCK(%s, %s)', (PROXY_LOCK, PROXY_LOCK_WAIT))
        try:
            was = int(profile.get('proxy_id') or 0)
            mine = next((row for row in rows if int(row['id']) == was), None) if was else None

            if mine and not await _fits(db, mine, seats, profile_id):
                logger_info(f'[proxy] анкета #{profile_id}: прежний выход #{was} '
                            f'больше не влезает в предел — ищем новый')
                mine = None

            got = mine or await _pick(db, rows, seats, profile_id)
            await _remember(db, profile_id, got)
        finally:
            await db.execute('SELECT RELEASE_LOCK(%s)', (PROXY_LOCK,))

    profile['proxy_id'] = int(got.get('id') or 0)
    profile['proxy_url'] = str(got.get('url') or '')
    profile['proxy_ip'] = str(got.get('check_ip') or '')

    return got


async def proxy_seats(site_id: int) -> int:
    """Сколько анкет сайт терпит с одного внешнего адреса.

    Настройка сайта, а не панели: у Goldenbride предел один, у Ladadate другой.
    Не задана — единица: она всегда безопасна.
    """
    from src.uvicorn.routers.dating.site_ import site_setting

    try:
        value = await site_setting(int(site_id), PROXY_SEATS_SETTING)
    except Exception:
        return PROXY_SEATS_DEFAULT

    try:
        return max(1, int(str(value or '').strip() or PROXY_SEATS_DEFAULT))
    except ValueError:
        return PROXY_SEATS_DEFAULT


async def proxy_set(profile_id: int, got: dict) -> None:
    """Ставит анкете **этот** выход, минуя раздачу.

    Для выбора руками: человек знает то, чего не знает раздача, — что канал
    быстрее, что он из нужной страны, что анкета уже входила с этого адреса.

    ⚠ **Предел «анкет на IP» здесь не проверяется намеренно.** Раздача его
    соблюдает, а человек решает; спорить с ним настройкой, которую он же и
    ставил, — не дело кода. Ручка, которая эту функцию зовёт, показывает, сколько
    анкет уже сидит за адресом.
    """
    async with mysql_get_db_async() as db:
        await _remember(db, profile_id, got)


async def proxy_countries(site_id: int) -> list[str]:
    """Страны, из которых сайт берёт выходы. Пусто — из любых.

    ⚠ **Пустой список значит «все», и это важно не перепутать.** Настройку
    заводят не сразу, а сайт должен работать с первого дня: «ни одной» здесь
    означало бы анкеты, которые молча не входят.
    """
    from src.uvicorn.routers.dating.site_ import site_setting

    try:
        raw = await site_setting(int(site_id), PROXY_COUNTRIES_SETTING)
    except Exception:
        return []

    out = []
    for part in str(raw or '').replace(';', ',').split(','):
        part = part.strip().upper()[:2]
        if part and part not in out:
            out.append(part)

    return out


async def proxy_pool(site_id: int = 0) -> list[dict]:
    """Выходы реестра с нашей занятостью — для страницы настроек сайта.

    ⚠ **Порядок показа — по пингу**: живые сверху, среди них быстрые первыми,
    мёртвые вниз. Человек смотрит в пул, чтобы выбрать канал руками, и решает он
    по скорости: 200 мс против 15 секунд — это разница между «анкета вошла» и
    «сценарий отвалился по таймауту».

    ⚠ **Это не тот порядок, в котором выходы раздаются.** Раздача берёт сперва
    самый свободный адрес (`_pick`), потому что ей важна равномерность, а не
    скорость: падение одного канала не должно уносить всех. Порядки разные
    намеренно, и путать их не надо — колонка «Анкеты» показывает занятость, по
    ней видно, кого возьмут следующим.
    """
    from http_.http_proxy import http_proxy_direct_ip

    rows = await rhapsody_api_proxy_list(ready=False)
    seats = await proxy_seats(site_id) if site_id else 0
    countries = await proxy_countries(site_id) if site_id else []
    # Свой адрес — чтобы отметить выходы, которые ничего не скрывают.
    direct = (await http_proxy_direct_ip()).get('ip') or ''

    async with mysql_get_db_async() as db:
        await db.execute("SELECT proxy_ip, COUNT(*) AS n FROM profile "
                         " WHERE proxy_ip <> '' GROUP BY proxy_ip")
        busy = {str(row['proxy_ip']): int(row['n']) for row in await db.fetchall() or []}

    out = []
    for row in rows:
        ip = str(row.get('check_ip') or '')
        country = str(row.get('check_country') or '')
        # ⚠ Страна отсечена настройкой — канал живой, но **не наш**: в работу он
        # не пойдёт. Показываем его всё равно, бледным: спрятанный выход человек
        # искал бы в реестре, гадая, почему его нет в списке.
        allowed = not countries or country in countries
        live = (bool(row.get('check_ok')) and bool(row.get('is_enabled'))
                and bool(ip) and allowed)
        taken = busy.get(ip, 0)

        out.append({
            'id': int(row['id']), 'name': row['name'],
            'scheme': row.get('scheme') or '', 'host': row.get('host') or '',
            'port': int(row.get('port') or 0),
            'check_ip': ip,
            # Страна выхода (ISO-3166 alpha-2) — из реестра, тем же ответом.
            # ⚠ Не то же, что в имени канала: «UA 193.93.54.238» на деле выходил
            # через турецкий адрес. Сайт сверяет геолокацию входа с анкетой.
            'check_country': country,
            # Разводим два «нет»: канал мёртв — или жив, но страна не отмечена.
            # На странице это разные состояния и разные действия человека.
            'country_off': bool(countries) and country not in countries,
            'check_ok': bool(row.get('check_ok')),
            'check_ms': int(row.get('check_ms') or 0),
            'check_error': row.get('check_error') or '',
            'checked_at': row.get('checked_at'),
            'is_enabled': bool(row.get('is_enabled')),
            # ⚠ «Живой» — это годный **и** включённый **и** с увиденным адресом.
            # Без адреса выход не годится, даже если пинг прошёл: считать его не с
            # чем ни нам, ни сайту.
            'live': live,
            # ⚠ **Выход, выходящий нашим же адресом, не скрывает ничего**:
            # запрос идёт мимо него либо он прозрачный. Для сайта это худший
            # случай — все «разные» анкеты окажутся на одном IP, и узнаем мы об
            # этом из блокировок. Считаем здесь, а не на странице: правило то же,
            # по которому раздаются выходы.
            'is_direct': bool(ip) and ip == direct,
            'taken': taken,
            'full': bool(seats) and taken >= seats,
        })

    # Живые выше мёртвых, среди живых — по пингу. ⚠ Непроверенный пинг (`0`)
    # уходит в конец живых, а не в начало: ноль здесь значит «не мерили», и
    # ставить такую строку впереди самой быстрой было бы враньём.
    out.sort(key=lambda row: (not row['live'],
                              row['check_ms'] or 10 ** 9,
                              row['id']))

    return out


async def proxy_release(profile_id: int) -> None:
    """Отпускает выход анкеты.

    ⚠ **Зовётся при снятии с ведения, а не при каждой остановке.** Выход стоит
    держать за анкетой: сайт помнит, с какого адреса она входила, и смена IP у
    живой учётки заметнее, чем пауза в её работе.

    Соседу об этом не сообщаем — ему и не надо: реестр мест не ведёт, место
    освобождается тем, что мы стёрли его у себя.
    """
    await proxy_forget(profile_id)


async def proxy_forget(profile_id: int) -> None:
    """Забывает выход анкеты: чистит кэш."""
    async with mysql_get_db_async() as db:
        await db.execute("UPDATE profile SET proxy_id = 0, proxy_url = '', "
                         "       proxy_ip = '', proxy_at = NULL WHERE id = %s",
                         (int(profile_id),))
        await db.connection.commit()


async def proxy_enabled(site_id: int) -> bool:
    """Пользуется ли сайт выходами вообще. Снято — прокси для него не существует.

    ⚠⚠ **Это выключатель всего механизма, а не «мягкий режим».** Снятая галочка
    означает: не спрашивать реестр, не занимать выходов, не проверять их кругом,
    не бить присутствием через канал и не жаловаться в журнал на то, что выхода
    нет. Анкеты ходят общим адресом сервера — так же, как ходили до того, как
    прокси появились в проекте.

    Настройка **сайта**, а не панели: у одного сайта каналы куплены и анкеты
    разъехались, у другого их нет и не планируется, а работать должны оба.

    ⚠ **Умолчание — выключено.** Механизм включает человек, когда реестр заполнен;
    до этого включённым он давал бы поход к соседу на каждой двери и строку
    «своего выхода нет» в журнале каждой анкеты.

    ⚠ **Читается каждый раз, а не запоминается.** Галочку снимают на живой
    установке, и запомненное значение означало бы, что выключатель срабатывает
    только после перезапуска воркера — то есть тогда, когда о нём уже забыли.
    Цена — одна строка из `site`, таблицы в единицы записей.
    """
    from src.uvicorn.routers.dating.site_ import site_setting

    if not int(site_id or 0):
        # Сайт неизвестен — считаем выключенным. Здесь это верное «не знаю»:
        # включённый механизм ходит к соседу и занимает адреса, а выключенный
        # просто оставляет всё как было.
        return False

    try:
        value = await site_setting(int(site_id), PROXY_ENABLED_SETTING)
    except Exception as e:
        logger_info(f'[proxy] настройка выходов сайта #{site_id} не прочиталась: {e}')

        return False

    return str(value or '').strip() in ('1', 'true', 'on')


async def proxy_forget_site(site_id: int) -> int:
    """Снимает выходы со всех анкет сайта. Возвращает, скольких коснулось.

    ⚠ **Зовётся, когда человек снял галочку «использовать выходы».** Без этого
    кэш в `profile_.proxy_url` пережил бы выключение, а его читают не только
    через `proxy_url`: удары присутствия берут адрес прямо из колонки, а
    прогоны сценариев — номер выхода (`proxy_id`). Выключатель, оставляющий
    работать половину дверей, хуже отсутствующего: он обещает то, чего не делает.

    ⚠ **Живой анкете это меняет адрес наружу**, и сайт такое видит. Иначе нельзя:
    выключение прокси и есть переезд на общий адрес, и растянуть его во времени
    значило бы держать анкеты на двух адресах разом — то самое, что для сайта
    выглядит угоном.
    """
    async with mysql_get_db_async() as db:
        await db.execute("UPDATE profile SET proxy_id = 0, proxy_url = '', "
                         "       proxy_ip = '', proxy_at = NULL "
                         " WHERE site_id = %s AND (proxy_id <> 0 OR proxy_url <> '')",
                         (int(site_id),))
        touched = int(db.rowcount or 0)
        await db.connection.commit()

    if touched:
        logger_info(f'[proxy] выходы сайта #{site_id} выключены: снято с {touched} анкет')

    return touched


async def proxy_required() -> bool:
    """Обязателен ли свой выход. Выключено — работаем как раньше, общим адресом.

    ⚠ Читается **каждый раз**, а не запоминается: настройку включают на живой
    установке, и запомненное значение означало бы, что защита начинает
    действовать только после перезапуска — то есть тогда, когда о ней забыли.
    """
    return str(await setting_get(PROXY_REQUIRED_SETTING,
                                 PROXY_REQUIRED_DEFAULT)).strip() in ('1', 'true', 'on')


async def proxy_ready(profile: dict) -> tuple[bool, str]:
    """Можно ли анкете идти к сайту — с причиной словами, если нет.

    Для тех, кто решает «пускать ли в работу»: вход, круг присутствия, отправка.

    Выход есть — да. Выхода нет и требование включено — нет, с причиной в журнал
    анкеты. Выхода нет и требование снято — да, но со строкой в журнале: молчать
    об этом нельзя, иначе разбирать блокировку учётки будут, не зная, что она
    ходила общим адресом.

    ⚠ **Сайт выходами не пользуется — да, и молча.** Строка «своего выхода нет»
    предупреждает о расхождении с намерением; когда намерения не было, она не
    предупреждение, а шум в журнале каждой анкеты при каждом входе.
    """
    if not await proxy_enabled(await _site_of(profile)):
        return True, ''

    url = await proxy_url(profile)
    if url:
        return True, ''

    profile_id = int(profile.get('id') or 0)

    if not await proxy_required():
        await log_write(profile_id, 'warn',
                               'своего выхода нет — идём общим адресом сервера')

        return True, ''

    reason = ('нет свободного выхода: заведите прокси на странице '
              '«Браузер → Прокси» у соседа')
    await log_write(profile_id, 'fail', reason)

    return False, reason


# ── Приватное ────────────────────────────────────────────────────────────────

async def _site_of(profile: dict) -> int:
    """Номер сайта анкеты. Нет в строке — спрашиваем базу и запоминаем в ней же.

    ⚠ **Строки анкет приходят сюда из десятка мест**, и не все выбирают `site_id`:
    отправке, например, нужны логин да cookie. Раньше это сходило с рук — раздача
    подставляла умолчания, — но выключатель живёт у сайта, и «сайт неизвестен»
    означало бы молча выключенные выходы у сайта, где их включили.

    Результат кладём в ту же строку: она живёт в пределах одного вызова круга, и
    второй запрос за тем же числом не нужен.
    """
    site_id = int(profile.get('site_id') or 0)
    if site_id:
        return site_id

    profile_id = int(profile.get('id') or 0)
    if not profile_id:
        return 0

    try:
        async with mysql_get_db_async() as db:
            await db.execute('SELECT site_id FROM profile WHERE id = %s',
                             (profile_id,))
            row = await db.fetchone()
    except Exception as e:
        logger_info(f'[proxy] сайт анкеты #{profile_id} не спросился: {e}')

        return 0

    site_id = int((row or {}).get('site_id') or 0)
    if site_id:
        profile['site_id'] = site_id

    return site_id


async def _fits(db, row: dict, seats: int, profile_id: int) -> bool:
    """Влезает ли анкета в предел на своём прежнем адресе.

    ⚠ **Считаем тех, кто занял адрес раньше нас.** Простое «сколько всего» дало
    бы качели: при двух анкетах на адресе с пределом в одну обе увидели бы
    «занято 2 ≥ 1», обе пошли бы за новым выходом, и так по кругу. Старшинство по
    `proxy_at` разрывает симметрию: кто занял раньше — остаётся.

    Своё время читаем из базы, а не из переданной строки: в `profile` оно может
    быть прошлым снимком, а решение принимается сейчас.
    """
    ip = str(row.get('check_ip') or '')
    if not ip:
        return False

    await db.execute('SELECT proxy_at FROM profile WHERE id = %s', (int(profile_id),))
    mine = (await db.fetchone() or {}).get('proxy_at')

    if mine is None:
        # Времени нет — значит выход достался не через нас (правка руками в базе).
        # Считаем анкету младшей: это осторожная сторона, она переедет.
        await db.execute("SELECT COUNT(*) AS n FROM profile "
                         " WHERE proxy_ip = %s AND id <> %s", (ip, int(profile_id)))
    else:
        await db.execute("SELECT COUNT(*) AS n FROM profile "
                         " WHERE proxy_ip = %s AND id <> %s "
                         "   AND proxy_at IS NOT NULL AND proxy_at <= %s",
                         (ip, int(profile_id), mine))

    older = int(((await db.fetchone()) or {}).get('n') or 0)

    return older < max(1, int(seats))


async def _pick(db, rows: list, seats: int, profile_id: int) -> dict:
    """Выбирает выход под анкету: считает своих по адресу и берёт самый свободный.

    ⚠ **Считаем по `check_ip`, а не по номеру выхода.** Два прокси одного пула
    выходят одним адресом, и по номерам они выглядели бы как два разных места — а
    сайт видел бы один и тот же предел.

    Из годных берём тот, за чьим адресом сидит меньше всех: анкеты расходятся по
    адресам ровным слоем, а не набиваются в первый попавшийся до предела.
    """
    await db.execute('SELECT proxy_ip, COUNT(*) AS n FROM profile '
                     " WHERE proxy_ip <> '' AND id <> %s GROUP BY proxy_ip",
                     (int(profile_id),))
    busy = {str(row['proxy_ip']): int(row['n']) for row in await db.fetchall() or []}

    free = [(busy.get(str(row.get('check_ip') or ''), 0), row) for row in rows]
    free = [(taken, row) for taken, row in free if taken < seats]

    if not free:
        addresses = len({str(row.get('check_ip') or '') for row in rows})
        raise DatingProxyError(
            f'мест нет: годных выходов {len(rows)}, разных адресов {addresses}, '
            f'предел сайта {seats} на адрес. Заведите ещё прокси либо поднимите '
            f'«Анкет на один внешний IP» в настройках сайта')

    # ⚠ **Порядок — по пингу**, а место лишь отсекает переполненные. Так же
    # выходы показаны в пуле, и это не совпадение: человек, глядя на таблицу,
    # должен видеть тот же порядок, в каком их разбирает раздача.
    #
    # Прежде первичной была равномерность (сперва самый свободный), чтобы падение
    # канала не уносило всех. От неё отказались осознанно: канал, отвечающий 15
    # секунд, роняет работу вернее, чем его падение, — сценарий отваливается по
    # таймауту, а присутствие не успевает ударить.
    free.sort(key=lambda pair: (int(pair[1].get('check_ms') or 10 ** 9),
                                pair[0], int(pair[1]['id'])))

    return free[0][1]


def _stale(profile: dict) -> bool:
    """Пора ли перечитать адрес у соседа."""
    at = profile.get('proxy_at')
    if not at:
        return True

    try:
        age = (datetime_utc_now().replace(tzinfo=None) - at).total_seconds()
    except TypeError:
        return True

    return age > PROXY_FRESH_SEC


async def _remember(db, profile_id: int, got: dict) -> None:
    """Кладёт выход в кэш анкеты."""
    await db.execute('UPDATE profile SET proxy_id = %s, proxy_url = %s, '
                     '       proxy_ip = %s, proxy_at = UTC_TIMESTAMP() WHERE id = %s',
                     (int(got.get('id') or 0), str(got.get('url') or ''),
                      str(got.get('check_ip') or ''), int(profile_id)))
    await db.connection.commit()

    logger_info(f'[proxy] анкета #{profile_id} ходит через выход #{got.get("id")} '
                f'({got.get("check_ip")})')
