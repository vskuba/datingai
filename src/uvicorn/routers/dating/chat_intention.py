"""Намерения разговора: ручки вкладки «Намерения и действия».

Правила самих намерений — в `src/dating/chat_intention.py`, исполнение — в
`src/dating/intention_do.py`; здесь только дверь.

⚠ **Своя дверь, а не хвост чата.** Вкладка живёт в полосе «Чата», но намерения
приезжают и из писем — их пишет одна точка `_agent_ask` на все четыре канала.
Повесь мы ручку на `/api/dating/chat`, адрес обещал бы только чат.

⚠ **Права — как у пулов, всем вошедшим.** Это работа оператора: он смотрит, что
агент наобещал и что из этого сделано. Админского здесь нет ничего — ни адресов,
ни паролей.
"""

from fastapi import APIRouter, Depends, HTTPException, status

from logging_.logging_ import logger_info
from mysql_.mysql_ import mysql_get_db_async

from src.auth.auth import AuthUser, auth_required
from src.dating.chat_intention import (chat_intention_drop,
                                       chat_intention_pairs,
                                       chat_intention_row,
                                       chat_intention_rows)
from src.dating.companion_gallery import companion_gallery_pick
from src.dating.intention_do import (DatingIntentionError, intention_mail_make,
                                     intention_mail_ready)
from src.dating.mail_send import (DatingMailSendError, mail_send_fail,
                                  mail_send_one)
from src.dating.site_setting import site_setting
from src.uvicorn.routers.dating.pool_handle import (pool_handle_clear,
                                                    pool_handle_drop,
                                                    pool_handle_sender)

router = APIRouter(prefix='/api/dating/chat-intention', tags=['dating'])

# Пул писем-исполнений. ⚠ Имя таблицы уезжает в SQL текстом, поэтому живёт
# константой, а не собирается из довода запроса.
INTENTION_MAIL_TABLE = 'intention_mail'


@router.get('/pairs', include_in_schema=False)
async def chat_intention_pairs_list(site_id: int = 0, profile_id: int = 0,
                                    page: int = 1, per: int = 0,
                                    user: AuthUser = Depends(auth_required)):
    """Пары с намерениями — левая колонка вкладки.

    Args:
        site_id: сайт-закладка. Ноль — пустой список, а не пары всех сайтов.
        profile_id: сузить до одной анкеты. Ноль — все анкеты сайта.
        page: страница, с единицы. За концом — поправится сама.
        per: строк на странице. Ноль — предел по умолчанию.

    Returns:
        dict: `result` — пары страницы, рядом `page`, `pages`, `total`.
    """
    got = await chat_intention_pairs(int(site_id or 0), int(profile_id or 0),
                                     int(page or 1), int(per or 0))

    return {'result': got['rows'], 'page': got['page'], 'pages': got['pages'],
            'total': got['total']}


@router.get('', include_in_schema=False)
async def chat_intention_list(site_id: int = 0, profile_id: int = 0,
                              uid: str = '', status: str = '',
                              page: int = 1, per: int = 0,
                              user: AuthUser = Depends(auth_required)):
    """Намерения страницей — средняя и правая колонки вкладки.

    Args:
        site_id: сайт-закладка. Ноль — пустой список.
        profile_id: сузить до одной анкеты. Ноль — все анкеты сайта.
        uid: сузить до собеседника. Пусто — все.
        status: `done` — средняя колонка (что сделано), `open` — правая (пул).
            Пусто — и те, и другие.
        page: страница, с единицы.
        per: строк на странице. Ноль — предел по умолчанию.

    Returns:
        dict: `result` — записи страницы, рядом `page`, `pages`, `total`.

    ⚠ **Одна ручка на две колонки.** Они спрашивают одно и то же разными
    доводами; вторая ручка держала бы две копии отбора и разошлась бы с первой.
    """
    got = await chat_intention_rows(int(site_id or 0), int(profile_id or 0),
                                    str(uid or ''), str(status or ''),
                                    int(page or 1), int(per or 0))

    return {'result': got['rows'], 'page': got['page'], 'pages': got['pages'],
            'total': got['total']}


@router.get('/{intention_id}/photo', include_in_schema=False)
async def chat_intention_photo(intention_id: int,
                               user: AuthUser = Depends(auth_required)):
    """Снимок под приметы намерения — для превью на карточке.

    Args:
        intention_id: строка намерения.

    Returns:
        dict: `result` — `{file, score, fresh, why}`; `file` пуст, когда снимок
        не выбрался, и тогда `why` говорит почему словами.

    ## ⚠⚠ Своя ручка, а список снимка не несёт

    Отбор ходит в галерею **на каждую строку**, и вшей он в список — страница
    пула на полсотни строк стоила бы полусотни лишних запросов на каждое
    обновление, а обновляется вкладка сама. Тот же приём, что у полей карточки
    собеседника (`/{id}/params`): тяжёлое по строке — отдельной дверью, и
    спрашивают её только те карточки, которым есть что показать.

    ⚠ Выбор **не запоминается**: он считается заново при каждом вопросе, и
    между двумя вопросами может смениться — снимок ушёл этому человеку другим
    путём. Запомнить его значило бы обещать карточкой то, чего отправка уже не
    сделает; отметку ставит сама отправка, когда появится.

    ⚠ Намерения без примет в контексте сюда ходить незачем, но запрет не нужен:
    ответ придёт с причиной «в контексте нет примет», и это честный ответ.
    """
    row = await chat_intention_row(int(intention_id or 0))

    if not row:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                            detail='намерения нет')

    got = await companion_gallery_pick(int(row['profile_id']),
                                       int(row['site_id']), str(row['uid']),
                                       (row.get('context') or {}).get('tags'))

    return {'result': got}


@router.get('/mail', include_in_schema=False)
async def intention_mail_list(site_id: int = 0, status_: str = 'draft',
                              user: AuthUser = Depends(auth_required)):
    """Пул писем-исполнений сайта — свежие сверху.

    Args:
        site_id: сайт. Ноль — пустой список.
        status_: `draft` (ждут отправки) или `delivery` (что ушло и что не
            ушло). ⚠ Имя с подчёркиванием: `status` занято модулем FastAPI,
            импортированным здесь же.

    Returns:
        dict: `result` — строки пула с номером намерения и выбранным снимком.

    ⚠ **Путь `/mail` объявлен до `/{intention_id}/…`**, иначе слово «mail»
    однажды прочиталось бы как номер намерения.
    """
    if not int(site_id or 0):
        return {'result': []}

    # ⚠ Два отбора, а не поле-фильтр у страницы: «доставка» это две строки
    # состояния разом (`sent` и `failed`), и собрать их может только сервер.
    where = ("status = 'draft'" if str(status_) != 'delivery'
             else "status IN ('sent', 'failed')")

    async with mysql_get_db_async() as db:
        await db.execute(
            'SELECT m.*, g.path AS gallery_path, g.file_id AS gallery_file'
            '  FROM intention_mail m'
            '  LEFT JOIN gallery_file g ON g.id = m.gallery_file_id'
            f' WHERE m.site_id = %s AND m.{where}'
            ' ORDER BY m.id DESC LIMIT 200', (int(site_id),))

        return {'result': [dict(row) for row in (await db.fetchall() or [])]}


@router.post('/mail/make', include_in_schema=False)
async def intention_mail_collect(site_id: int = 0, profile_id: int = 0,
                                 user: AuthUser = Depends(auth_required)):
    """Собрать черновики по открытым намерениям — кнопкой, не дожидаясь круга.

    Returns:
        dict: `result` — `{made, skipped, why}`; `why` перечисляет, кому не
        собралось и почему.

    ⚠ **Ничего не отправляет.** Кнопка делает ровно то же, что круг: кладёт
    письма в пул. Уносит их отправка — своим сроком либо второй кнопкой.
    """
    if not int(site_id or 0):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                            detail='не выбран сайт')

    return {'result': await intention_mail_make(int(site_id),
                                                profile_id=int(profile_id or 0))}


@router.post('/mail/{mail_id}/send', include_in_schema=False)
async def intention_mail_send(mail_id: int,
                              user: AuthUser = Depends(auth_required)) -> dict:
    """Отправить одно письмо со снимком — то, на кнопку которого нажали.

    Raises:
        HTTPException: 404 — письма нет; 409 — не ушло, с причиной словами.

    ⚠ **Кнопка рядом с кругом — не противоречие.** Круг шлёт сам, своим сроком
    и с паузой; кнопка нужна тому, кто прочитал письмо и хочет отправить его
    сейчас. Так же устроены рассылка и письма избранным.

    ⚠⚠ **Отметки ставит не эта ручка.** Память о снимке и закрытие обещания
    живут в `mail_send_mark`, через которую проходят обе дороги. Поставь мы их
    здесь, круг забывал бы то, что помнит кнопка, — и один снимок ушёл бы
    дважды по следующему обещанию.

    ⚠ Снимок готовится **до** отправки и одной дверью на обе дороги
    (`intention_mail_ready`): между `check-send` и `send` нельзя ходить в сеть.
    """
    letter = await _mail_row(mail_id)

    if letter['status'] != 'draft':
        raise HTTPException(status_code=status.HTTP_409_CONFLICT,
                            detail='письмо уже ушло — второй раз не отправляем')

    profile = await pool_handle_sender(int(letter['profile_id']),
                                       'обещанных фото')
    site_id = int(letter['site_id'])

    base = str(await site_setting(site_id, 'api_base')).strip()
    password = str(await site_setting(site_id, 'api_password')).strip()

    try:
        letter = await intention_mail_ready(letter)
    except DatingIntentionError as e:
        # ⚠ Это не отказ сайта — наружу мы ещё не ходили. Но причину храним так
        # же: человек закроет вкладку, а строка останется с объяснением.
        await mail_send_fail(letter, DatingMailSendError(str(e)),
                             table=INTENTION_MAIL_TABLE)
        logger_info(f'[dating] письмо-исполнение #{mail_id} без снимка: {e}')

        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e))

    try:
        await mail_send_one(letter, profile, base, password,
                            INTENTION_MAIL_TABLE)
    except DatingMailSendError as e:
        # ⚠ Причину храним **до** ответа: страница покажет её на карточке, а в
        # ответе HTTP к завтрашнему дню её не будет.
        await mail_send_fail(letter, e, table=INTENTION_MAIL_TABLE)
        logger_info(f'[dating] письмо-исполнение #{mail_id} не ушло: {e}')

        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e))

    return {'result': await _mail_row(mail_id)}


@router.delete('/mail/{mail_id}', include_in_schema=False)
async def intention_mail_drop(mail_id: int,
                              user: AuthUser = Depends(auth_required)):
    """Убрать черновик из пула. Ушедшее не трогается.

    ⚠ Обещание при этом **остаётся открытым**: человек убрал письмо, а не
    отменил обещание, и ближайший заход круга соберёт черновик заново — может
    быть, уже с другим снимком. Отменить обещание можно, только исполнив его.
    """
    return await pool_handle_drop(INTENTION_MAIL_TABLE, mail_id,
                                  missing='письма уже нет',
                                  sent='письмо уже ушло')


@router.delete('/mail', include_in_schema=False)
async def intention_mail_clear(site_id: int = 0,
                               user: AuthUser = Depends(auth_required)):
    """Очистить пул сайта — разом всё, что ещё не ушло.

    ⚠ **Путь без номера объявлен после `/{mail_id}`** — у FastAPI порядок
    решает, и пустой хвост иначе прочитался бы номером.
    """
    return await pool_handle_clear(INTENTION_MAIL_TABLE, site_id,
                                   what='обещанных фото')


@router.delete('/{intention_id}', include_in_schema=False)
async def chat_intention_forget(intention_id: int,
                                user: AuthUser = Depends(auth_required)):
    """Убрать карточку намерения из пула — вместе с её черновиком письма.

    Returns:
        dict: `{'dropped': 1, 'mail': сколько черновиков ушло следом}`.

    Raises:
        HTTPException: 404 — карточки уже нет.

    ⚠⚠ **Путь объявлен после всех `/mail…`**: у FastAPI порядок решает, и
    слово «mail» иначе прочиталось бы номером намерения. Та же грабля уже
    названа у `/mail` без номера.

    ⚠ Отличие от «исполнено»: исполнение говорит «обещание сдержано», а
    удаление — «этой работы не будет». Поэтому строка уходит совсем, а не
    метится: иначе пул «Сделано» наполнялся бы тем, чего не делали.
    """
    got = await chat_intention_drop(intention_id)

    if not got['dropped']:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                            detail='карточки уже нет')

    return {'result': got}


async def _mail_row(mail_id: int) -> dict:
    """Строка пула целиком. Нет — `404`."""
    async with mysql_get_db_async() as db:
        await db.execute('SELECT * FROM intention_mail WHERE id = %s',
                         (int(mail_id or 0),))
        row = await db.fetchone()

    if not row:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                            detail='письма нет')

    return dict(row)
