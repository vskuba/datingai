"""Разбор снимка моделью: что изображено и какими тегами это назвать.

Описание и теги добываются **один раз на файл** и ложатся в индекс
(`gallery_file`). Повтор — только когда снимок на Диске сменился: контрольная
сумма разошлась с записанной.

**Зрение живёт у соседа**, как и все прочие обращения к модели: панель зовёт
`POST /chat/ask` с workflow `dating_gallery_describe`, а тот — инструмент над
`ai_vision_json`. Своей дороги к модели здесь нет и быть не должно.

⚠⚠ **Правленое человеком не берётся никогда.** Непустой `edited_at` значит
«здесь были руки», и следующая пачка его не тронет. Иначе человек, однажды
увидев, что его правку затёрли, перестал бы править вовсе.

⚠ **За заход не больше пяти снимков.** Предел назван человеком и имеет смысл:
вызов зрения стоит секунд, а разбор — работа фоновая, у неё нет причин
выигрывать гонку у чата.

⚠⚠ **Модели уходит сжатый кадр, а не оригинал.** С Диска просим 800 пикселей
по длинной стороне и приводим к RGB-JPEG (`ai_vision_normalize`): 8,5 МБ PNG
становятся 53 КБ.
Заодно снимается чужая грабля — локальные vision-серверы **не читают RGBA-PNG**
и отвечают `400 Failed to load image`, а на Диске лежат именно PNG.
"""

import base64
import json

from ai.vision.ai_vision import ai_vision_normalize
from google_drive_.google_drive_bytes import google_drive_bytes
from json_.json_ import json_from_string
from mysql_.mysql_ import mysql_get_db_async

from src.dating.agent import AGENT_NODE, agent_site
from src.rhapsody.rhapsody_api import (RhapsodyError, rhapsody_api_agent_create,
                                       rhapsody_api_agent_find,
                                       rhapsody_api_chat_ask)

# Сколько снимков разбираем за один заход. Решение человека.
GALLERY_VISION_BATCH = 5

# Какой кадр просим у Диска под разбор. Не оригинал: 800 по длинной стороне —
# это 53 КБ JPEG против 8,5 МБ, а модели для описания платья и обстановки
# большего не нужно. Полный кадр существует для человека, а не для неё.
#
# ⚠ Кадр едет к соседу переменной workflow, то есть **ложится в трассу
# прогона**: 800 — это ~70 КБ base64 на вызов, 1600 было бы 367 КБ.
GALLERY_VISION_WIDTH = 800

# Имя workflow у соседа. ⚠ Точное, с приставкой `dating_`: сосед ищет его по
# совпадению имени, и расхождение выглядит как «агент молчит».
#
# ⚠⚠ **Workflow выбирает агент, а не вызов.** Он задаётся один раз, при
# заведении агента (`rhapsody_api_agent_create(workflow=…)`), и переменной его
# не переключить: первая редакция слала `{'workflow': …}` в `chat_ask` и
# работала бы ровно до первого прогона — агент гонял бы свой, не глядя.
GALLERY_VISION_WORKFLOW = 'dating_gallery_describe'

# Имя агента: одно на сайт, как у писем и инвайтов. Приставка нужна, чтобы его
# было видно среди сотен чужих у соседа.
GALLERY_VISION_AGENT = 'gallery_describe_{site}'

# ⚠⚠ **Словарь тегов закрытый, и в этом вся его польза.** Свободные теги модель
# придумывала каждый раз заново: `lace dress`, `silver dress`, `white sweater` —
# по таким снимок не отберёшь. Три слова об одном платье не сходятся между собой,
# а отбор — единственное, ради чего теги вообще существуют: описание читает
# человек, теги читает код.
#
# ⚠ Группы здесь для человека, модели уходит плоский список: разложи мы его по
# полям JSON, пришлось бы держать соответствие «поле → колонка» в двух местах.
#
# ⚠ Теги английские, как и прежде: ими отбирают, а не показывают. Перевод для
# глаза — дело страницы, если понадобится.
GALLERY_VISION_TAGS = {
    'одежда': ('dress', 'lingerie', 'swimsuit', 'casual', 'sportswear',
               'evening-wear', 'coat', 'nude'),
    'план': ('portrait', 'half-body', 'full-body', 'close-up', 'selfie'),
    'место': ('indoor', 'outdoor', 'studio', 'bedroom', 'kitchen', 'bathroom',
              'beach', 'nature', 'city', 'car', 'gym', 'cafe'),
    'настроение': ('smiling', 'serious', 'sensual', 'playful', 'confident',
                   'shy', 'dreamy'),
    'свет': ('daylight', 'evening', 'night', 'studio-light'),
    'прочее': ('group', 'with-pet', 'mirror', 'sitting', 'standing', 'lying'),
}

# Плоский список — он и уходит модели, и сверяется при записи.
GALLERY_VISION_TAGS_ALL = tuple(
    tag for group in GALLERY_VISION_TAGS.values() for tag in group)

# Сколько тегов просим на снимок. ⚠ Не «сколько подойдёт»: без числа модель
# ставит два-три и считает работу сделанной.
GALLERY_VISION_TAGS_WANT = 8

# Что просим у модели. ⚠ **Поля названы прямо здесь**: не назовёшь — модель
# придумает свои, и каждый раз другие, а колонки заполнять будет нечем.
GALLERY_VISION_PROMPT = (
    'Это фотография девушки с сайта знакомств. Опиши её для картотеки агентства.\n'
    'Верни объект JSON с полями:\n'
    '  description — один абзац: кто в кадре, во что одета, где находится, '
    'какое настроение и план съёмки;\n'
    f'  tags — до {GALLERY_VISION_TAGS_WANT} тегов, и **только** из этого '
    'списка, слово в слово:\n'
    '    ' + ', '.join(GALLERY_VISION_TAGS_ALL) + '.\n'
    'Тегов не из списка не придумывай — их отбросят. Подходящего нет — не ставь.\n'
    'Нечитаемого и неизвестного не выдумывай.'
)


def gallery_vision_tags_keep(tags) -> list:
    """Оставляет только теги из словаря. Чужие и повторы выбрасывает.

    Args:
        tags: что прислала модель (или ввёл человек) — список чего угодно.

    Returns:
        list: теги словаря в порядке словаря, без повторов.

    ## ⚠⚠ Промпта мало, и это проверено на живых ответах

    Модель держит список, пока он короткий, и начинает дополнять его «близкими
    по смыслу», как только снимок непривычный: на список из тридцати восьми слов
    прилетало `lace dress` при наличии `dress`. Один такой тег не ломает ничего
    заметно — он просто никогда не совпадёт в отборе, и снимок молча выпадет из
    выборки. Поэтому сверка стоит **на записи**, а не на доверии.

    ⚠ Порядок — словаря, а не ответа: так теги одного снимка сравнимы глазом с
    тегами соседнего, и «одежда» всегда идёт раньше «света».

    ⚠ Регистр и пробелы приводятся: модель иногда отвечает `Dress`.
    """
    seen = {str(tag).strip().lower() for tag in (tags or []) if str(tag).strip()}

    return [tag for tag in GALLERY_VISION_TAGS_ALL if tag in seen]


async def gallery_vision_due(limit: int = GALLERY_VISION_BATCH) -> list[dict]:
    """Снимки, которые пора разобрать: ещё не разобранные либо сменившиеся.

    Args:
        limit: сколько строк вернуть.

    Returns:
        Строки индекса с номером анкеты и файла.

    ⚠ Условие **исключает правленое руками** и ушедшее. Видео тоже не берётся:
    у него разбирать нечего — превью это первый кадр, и описывать по нему
    видеозапись значило бы выдавать догадку за знание.
    """
    async with mysql_get_db_async() as db:
        await db.execute(
            'SELECT id, profile_id, file_id, name, md5, described_md5 '
            '  FROM gallery_file '
            ' WHERE is_gone = 0 AND kind = %s AND edited_at IS NULL '
            '   AND (described_at IS NULL OR described_md5 <> md5) '
            ' ORDER BY described_at IS NOT NULL, id LIMIT %s', ('image', int(limit)))

        return [dict(row) for row in (await db.fetchall() or [])]


async def gallery_vision_run(limit: int = GALLERY_VISION_BATCH) -> dict:
    """Разобрать очередную пачку снимков. Итог — числами для журнала задания.

    Returns:
        `{'took': int, 'done': int, 'failed': int}`.

    Не бросает: отказ по одному снимку не должен уносить остальную пачку —
    причина ложится в `described_error` и строка остаётся на следующий заход.
    """
    rows = await gallery_vision_due(limit)
    done = failed = 0

    for row in rows:
        try:
            await gallery_vision_one(row)
            done += 1
        except (RuntimeError, RhapsodyError, ValueError) as err:
            await _error_save(row['id'], str(err))
            failed += 1

    return {'took': len(rows), 'done': done, 'failed': failed}


async def gallery_vision_one(row: dict) -> dict:
    """Разобрать один снимок и записать описание с тегами.

    Args:
        row: строка индекса — нужны `id`, `profile_id`, `file_id`, `md5`.

    Returns:
        `{'description': str, 'tags': list}` — то, что записали.

    Raises:
        RuntimeError: Диск не отдал кадр, либо ответ агента не разобрался.
        RhapsodyError: сосед молчит или отказал.

    ⚠ Записывается и `described_md5` — тот снимок, который разобрали. По нему
    и решается, пора ли разбирать заново: имя файла может не меняться годами,
    а содержимое — смениться.
    """
    raw, _ = await google_drive_bytes(row['file_id'], GALLERY_VISION_WIDTH)
    frame = ai_vision_normalize(raw)

    answer = await rhapsody_api_chat_ask(
        await _agent_id(row['profile_id']), GALLERY_VISION_PROMPT,
        {'image': 'data:image/jpeg;base64,' + base64.b64encode(frame).decode(),
         'prompt': GALLERY_VISION_PROMPT})

    got = json_from_string(answer)
    if not got:
        raise RuntimeError(f'агент ответил не объектом JSON: {answer[:200]}')

    description = str(got.get('description') or '').strip()
    tags = gallery_vision_tags_keep(got.get('tags'))
    if not description:
        raise RuntimeError('агент вернул объект без описания')

    await _result_save(row['id'], description, tags, row['md5'])

    return {'description': description, 'tags': tags}


async def _agent_id(profile_id: int) -> int:
    """Агент разбора для сайта анкеты; нет такого — заводим.

    Агент один на сайт, как у писем и инвайтов: метка собирается из имени
    сайта-закладки (`agent_site`), своего агента на каждую девушку не заводится.

    ⚠ **Заводим при первом обращении, а не заранее.** Сайтов у соседа десятки, и
    агент впрок — это агент тем, кто галереей не пользуется вовсе.

    ⚠ Метка обязательна и подставить общую вместо неё нельзя: агент приписался
    бы к чужому сайту (`agent_site`).
    """
    async with mysql_get_db_async() as db:
        await db.execute('SELECT site_id FROM profile WHERE id = %s', (profile_id,))
        site_id = int(((await db.fetchone()) or {}).get('site_id') or 0)

    slug = await agent_site(site_id)
    if not slug:
        raise RuntimeError(f'у сайта #{site_id} нет метки для агента: имя сайта '
                           'у соседа не сводится к латинице')

    name = GALLERY_VISION_AGENT.format(site=slug)
    found = await rhapsody_api_agent_find(name)
    if found.get('id'):
        return _with_workflow(found)

    made = await rhapsody_api_agent_create(
        node=AGENT_NODE, workflow=GALLERY_VISION_WORKFLOW, name=name,
        title=f'Галерея: {name}', variables={}, site=slug)
    if not int(made.get('id') or 0):
        raise RuntimeError('сосед завёл агента разбора, но не вернул его номер')

    # ⚠⚠ **Свежезаведённого агента перечитываем, а не верим ответу.** Заведение
    # идёт прогоном его же workflow, и `create` отвечает раньше, чем привязка
    # дописана: в тот зазор `chat_ask` бьёт в агента без workflow, а сосед
    # отвечает `500 Внутренняя ошибка сервера` — без намёка на причину.
    # Измерено: первый заход после заведения падал ровно так, второй проходил.
    return _with_workflow(await rhapsody_api_agent_find(name))


async def _result_save(row_id: int, description: str, tags: list, md5: str) -> None:
    """Записать разбор: описание, теги, отметку времени и разобранную сумму."""
    async with mysql_get_db_async() as db:
        await db.execute(
            'UPDATE gallery_file SET description = %s, tags = CAST(%s AS JSON), '
            '       described_at = NOW(), described_md5 = %s, described_error = %s '
            ' WHERE id = %s',
            (description, json.dumps(tags, ensure_ascii=False), md5, '', row_id))
        await db.connection.commit()


async def _error_save(row_id: int, why: str) -> None:
    """Записать причину неудачи, не трогая прежнее описание.

    ⚠ `described_at` не ставится: строка обязана вернуться в очередь следующим
    заходом. Проставь мы отметку, неудача выглядела бы как разбор.
    """
    async with mysql_get_db_async() as db:
        await db.execute('UPDATE gallery_file SET described_error = %s WHERE id = %s',
                         (why[:500], row_id))
        await db.connection.commit()


def _with_workflow(agent: dict) -> int:
    """Номер агента, у которого есть workflow; иначе — внятный отказ.

    ⚠ Агент без привязки существует и находится по имени, но работать не
    может: `chat_ask` к нему отвечает `500` без объяснения. Лучше сказать это
    словом и оставить строку в очереди — следующий заход застанет привязку
    дописанной.
    """
    agent_id = int(agent.get('id') or 0)
    if not agent_id:
        raise RuntimeError('агента разбора у соседа нет')

    if not int(agent.get('workflow_id') or 0):
        raise RuntimeError(
            f'агент «{agent.get("name")}» заведён, но workflow к нему ещё не '
            f'привязан — разберём на следующем заходе')

    return agent_id
