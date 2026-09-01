"""Настройки панели: ключ → значение, правятся из браузера.

Второй способ настроить панель, помимо окружения. Разделение простое: **в `.env`
живёт то, без чего процесс не поднимется** (адрес базы, токен соседа, ключ
шифрования), **здесь — то, что подкручивают на ходу**. Первое меняют раз в жизни и
перезапуском, второе — по ходу работы, и лезть за этим на сервер не должно быть
нужно.

Читает значения общий модуль `py_modules/setting_/setting_.py` — тот же файл, что
у соседа. Отсюда и форма таблицы: своя схема означала бы ветку в общей библиотеке.

⚠ **Только админу.** Страница живёт в разделе «Настройки» рядом с seed и
бэкапами, и по той же причине: настройка меняет поведение панели для всех, а не
для того, кто её открыл. Право проверяется зависимостью в объявлении каждого
роута, а не в теле, — забытую проверку тогда находит чтение файла, а не разбор
происшествия.

## Что страница не показывает

Два вида строк, и оба намеренно:

* **личные** (`user_id` не пуст) — настройки одного человека, а страница про общие;
* **служебные** (`dynamic = 1`) — поставленные кодом. Это состояние, а не
  настройка: править его руками нечего, а увидеть в общем списке — повод
  попробовать.
"""

from datetime_.datetime_ import datetime_offset_hours
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from logging_.logging_ import logger_info
from mysql_.mysql_ import mysql_get_db_async
from setting_.setting_ import setting_delete, setting_get, setting_set

from src.auth.auth import AuthUser, auth_admin_required

router = APIRouter(prefix='/api/setting', tags=['setting'])

# Длина ключа — та же, что в схеме (`CHAR(50)`). Проверяем здесь, чтобы вернуть
# 422 с внятным текстом, а не 500 от обрезки на стороне MySQL.
SETTING_KEY_MAX = 50

# Ключ часового пояса. Имя то же, что у соседа: настройка одна по смыслу, и две
# панели на одном домене не должны звать её по-разному.
SETTING_TIMEZONE_KEY = 'timezone'

# Умолчание — UTC, то есть «как контейнер и живёт». Не «+3» и не пояс машины:
# сервер стоит в UTC, времена в базе лежат в UTC, и молча сдвигать показанное
# значит врать про то, чего человек не просил. Пояс ставят руками на странице
# «Настройки → Значения», и до этого панель показывает ровно то, что в базе.
SETTING_TIMEZONE_DEFAULT = '+0'


class SettingSave(BaseModel):
    """Что сохраняем. Пустое значение — удалить строку, а не записать пустоту."""

    key: str = Field(..., min_length=1, max_length=SETTING_KEY_MAX)
    value: str = ''


async def setting_timezone_hours() -> int:
    """Часовой пояс панели в часах от UTC. Не задан или мусор — ноль.

    Зовётся на каждую отрисовку страницы, поэтому не бросает: сбой чтения
    настройки не повод не показать панель. Ноль при этом означает «показываем
    как есть, в UTC» — то же, что было до появления настройки, и человек увидит
    не то время, но и не пустую страницу.

    Живёт здесь, а не в `dashboard.py`: страница про пояс ничего не знает, она
    только передаёт число в разметку. Знание о том, каким ключом он зовётся и
    чему равен по умолчанию, принадлежит настройкам.
    """
    try:
        raw = await setting_get(SETTING_TIMEZONE_KEY, SETTING_TIMEZONE_DEFAULT)
    except Exception as e:
        logger_info(f'[setting] часовой пояс не прочитался: {e}')

        return 0

    return datetime_offset_hours(raw)


@router.get('')
async def setting_list(user: AuthUser = Depends(auth_admin_required)):
    """Общие настройки: ключ, значение, номер строки.

    Личные и служебные не показываются — почему, написано в шапке модуля.
    Сортировка по ключу: список читают глазами, и порядок вставки в нём не значит
    ничего.
    """
    async with mysql_get_db_async() as db:
        await db.execute('SELECT id, `key`, value FROM setting '
                         ' WHERE user_id IS NULL AND dynamic = 0 ORDER BY `key` ASC')
        rows = await db.fetchall()

    return {'result': {'settings': [dict(row) for row in rows]}}


@router.post('')
async def setting_save(payload: SettingSave, user: AuthUser = Depends(auth_admin_required)):
    """Заводит настройку или переписывает её значение.

    ⚠ **Пустое значение удаляет строку.** Не записывает пустоту: настройки нет и
    настройка со значением «ничего» — разные вещи. Первое означает «действует
    умолчание кода», второе — «действует пустая строка», и путать их значит
    получать необъяснимое поведение там, где умолчание было осмысленным.

    Отдельной ручки «удалить значение» поэтому и нет: очистка поля на странице —
    то же действие, и заводить под него второй путь незачем.
    """
    key = payload.key.strip()
    if not key:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                            detail='ключ не может быть пустым')

    if payload.value == '':
        await setting_delete(key)
    else:
        await setting_set(key, payload.value)

    return {'result': 'ok'}


@router.delete('/{key}')
async def setting_drop(key: str, user: AuthUser = Depends(auth_admin_required)):
    """Убирает настройку целиком.

    Повторный вызов не ошибка: результат тот же — настройки нет. Отвечать 404 на
    удаление уже удалённого значило бы пугать человека тем, чего он и добивался.
    """
    await setting_delete(str(key).strip())

    return {'result': 'ok'}
