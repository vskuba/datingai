"""Разбор снимков моделью: пятёрка за заход, один раз на файл, руки сильнее.

## ⚠⚠ Что здесь проверяется

Четыре правила, и у каждого цена в деньгах или в доверии:

| Правило | Цена нарушения |
|---------|----------------|
| не больше пяти за заход | фоновая работа выигрывает гонку у чата |
| разбор один раз на файл | сорок анкет по сорок снимков = 1600 вызовов **на каждом** заходе |
| правленое руками не берётся | затёртая правка, и человек перестаёт править вовсе |
| неудача не метится разбором | строка выпадает из очереди навсегда |

Агент подменяется точечно (как в `test_invite`), Диск — заглушкой транспорта.
"""

import json
import uuid

import pytest

from mysql_.mysql_ import mysql_get_db_async

from src.dating import gallery_vision
from src.dating.gallery_vision import (GALLERY_VISION_BATCH, gallery_vision_due,
                                       gallery_vision_one, gallery_vision_run)
from src.uvicorn.routers.dating.profile_ import profile_delete

pytestmark = pytest.mark.profile

STAND_SITE = 999_343

ANSWER = json.dumps({'description': 'девушка в кружевном платье в гостиной',
                     'tags': ['lace', 'indoor', 'smiling']}, ensure_ascii=False)


async def _mine(girl: int, limit: int = 50) -> list[dict]:
    """Очередь разбора **этой** анкеты.

    ⚠⚠ Прямой `gallery_vision_due(1)` отдаёт первую строку по **всей** базе, и
    `gallery_vision_one` на ней записал бы фальшивое описание живой девушке.
    Так и случилось на первом прогоне с анкетой 849.
    """
    return [row for row in await gallery_vision_due(limit)
            if row['profile_id'] == girl]


@pytest.fixture
def only_mine(monkeypatch, girl):
    """Отбор, суженный до **своей** анкеты.

    ⚠⚠ **Без этого тест правит чужие данные.** `gallery_vision_due` по природе
    глобальна — это задание, обходящее всю базу, — и `gallery_vision_run()` в
    тесте разобрал бы заодно снимки живых анкет, записав им фальшивое описание
    от заглушки агента. Так и случилось на первом прогоне: у настоящей анкеты
    849 появилось «девушка в кружевном платье в гостиной» из этого файла.

    Что именно отбирать, проверяют тесты `gallery_vision_due` отдельно и без
    записи. Здесь проверяется другое — как `run` делит пачку и считает исход.
    """
    import src.dating.gallery_vision as module

    real = module.gallery_vision_due

    async def _due(limit=GALLERY_VISION_BATCH):
        return [row for row in await real(limit * 10)
                if row['profile_id'] == girl][:limit]

    monkeypatch.setattr(module, 'gallery_vision_due', _due)

    return _due


@pytest.fixture
def agent(monkeypatch):
    """Агент соседа: отвечает заготовленным и считает, о чём спрашивали."""
    asked = []

    async def _ask(agent_id, question, variables=None):
        asked.append({'question': question, 'variables': variables or {}})

        return agent.answer

    async def _agent_id(profile_id):
        return 501

    monkeypatch.setattr(gallery_vision, 'rhapsody_api_chat_ask', _ask)
    monkeypatch.setattr(gallery_vision, '_agent_id', _agent_id)
    agent.asked = asked
    agent.answer = ANSWER

    return agent


@pytest.fixture
def tree(drive):
    """Папка с семью снимками — больше, чем берётся за заход."""
    drive.add_folder('Dating', 'root-1')
    drive.add_folder('girl@example.com', 'girl-1', parent='root-1')
    drive.add_folder('public', 'public-1', parent='girl-1')
    for number in range(7):
        drive.add_file(f'{number}.png', f'file-{number}', parent='public-1',
                       md5=f'md5-{number}')

    return drive


@pytest.fixture
async def girl(auth_client):
    """Анкета стенда с разложенным индексом."""
    profile_id = int((await auth_client.post('/api/dating/profile', json={
        'site_id': STAND_SITE,
        'login': f'gallery-{uuid.uuid4().hex[:8]}@example.com',
        'password': 'x'})).json()['result'])

    async with mysql_get_db_async() as db:
        for number in range(7):
            await db.execute(
                'INSERT INTO gallery_file (profile_id, file_id, path, name, mime, '
                '                          kind, md5) '
                "VALUES (%s, %s, %s, %s, 'image/png', 'image', %s)",
                (profile_id, f'file-{number}', f'public/{number}.png',
                 f'{number}.png', f'md5-{number}'))
        await db.connection.commit()

    yield profile_id

    async with mysql_get_db_async() as db:
        await db.execute('DELETE FROM gallery_file WHERE profile_id = %s', (profile_id,))
        await db.connection.commit()
    await profile_delete(profile_id, user=None)


async def test_batch_is_five(tree, agent, girl, only_mine):
    """⚠ За заход ровно пять, хотя лежит семь."""
    got = await gallery_vision_run()

    assert got == {'took': GALLERY_VISION_BATCH, 'done': GALLERY_VISION_BATCH,
                   'failed': 0}
    assert len(agent.asked) == GALLERY_VISION_BATCH


async def test_described_is_not_taken_again(tree, agent, girl, only_mine):
    """⚠⚠ Разобранное не берётся повторно: второй заход добирает остаток."""
    await gallery_vision_run()
    second = await gallery_vision_run()

    assert second['took'] == 2, 'во втором заходе должны остаться только два'

    third = await gallery_vision_run()
    assert third['took'] == 0, 'третьему заходу брать нечего'


async def test_changed_file_is_taken_again(tree, agent, girl, only_mine):
    """Снимок подменили — сумма разошлась, разбираем заново."""
    await gallery_vision_run()
    async with mysql_get_db_async() as db:
        await db.execute("UPDATE gallery_file SET md5 = 'совсем-другая' "
                         " WHERE file_id = 'file-0'")
        await db.connection.commit()

    due = [row['file_id'] for row in await _mine(girl)]

    assert 'file-0' in due, 'сменившийся снимок обязан вернуться в очередь'


async def test_edited_by_hand_is_never_taken(tree, agent, girl):
    """⚠⚠ Правленое руками не берётся никогда — ни первым заходом, ни после."""
    async with mysql_get_db_async() as db:
        await db.execute("UPDATE gallery_file SET edited_at = NOW(), "
                         "description = 'правлено' WHERE file_id = 'file-0'")
        await db.connection.commit()

    due = [row['file_id'] for row in await _mine(girl)]

    assert 'file-0' not in due


async def test_image_goes_compressed_not_raw(tree, agent, girl):
    """⚠ Модели уходит сжатый JPEG, а не PNG с Диска: RGBA она не читает."""
    await gallery_vision_one((await _mine(girl))[0])

    image = agent.asked[0]['variables']['image']

    assert image.startswith('data:image/jpeg;base64,'), 'ушёл не JPEG'


async def test_fields_are_named_in_the_prompt(tree, agent, girl):
    """Поля названы в просьбе: не назовёшь — модель придумает свои."""
    await gallery_vision_one((await _mine(girl))[0])

    question = agent.asked[0]['question']

    assert 'description' in question and 'tags' in question


async def test_answer_lands_in_columns(tree, agent, girl):
    """Описание и теги ложатся в индекс, вместе с разобранной суммой."""
    row = (await _mine(girl))[0]
    await gallery_vision_one(row)

    async with mysql_get_db_async() as db:
        await db.execute('SELECT description, tags, described_at, described_md5, '
                         '       described_error FROM gallery_file WHERE id = %s',
                         (row['id'],))
        got = dict((await db.fetchone()) or {})

    assert got['description'] == 'девушка в кружевном платье в гостиной'
    assert json.loads(got['tags']) == ['lace', 'indoor', 'smiling']
    assert got['described_at'] is not None
    assert got['described_md5'] == row['md5'], 'разобранная сумма обязана записаться'
    assert got['described_error'] == ''


async def test_bad_json_is_an_error_not_a_description(tree, agent, girl, only_mine):
    """⚠ Невнятный ответ — ошибка, а не пустое описание.

    И `described_at` не ставится: строка обязана вернуться в очередь. Проставь
    мы отметку, неудача выглядела бы как разбор, и снимок остался бы без
    описания навсегда.
    """
    agent.answer = 'я подумал и решил ответить прозой'

    got = await gallery_vision_run(1)

    assert got == {'took': 1, 'done': 0, 'failed': 1}

    async with mysql_get_db_async() as db:
        await db.execute("SELECT described_at, described_error, description "
                         " FROM gallery_file WHERE file_id = 'file-0'")
        row = dict((await db.fetchone()) or {})

    assert row['described_at'] is None, 'неудача не должна выглядеть разбором'
    assert 'JSON' in row['described_error']
    assert not row['description']


async def test_one_failure_does_not_take_the_batch(tree, agent, girl, only_mine):
    """Отказ по одному снимку не уносит остальную пачку."""
    calls = {'n': 0}
    good = agent.answer

    async def _ask(agent_id, question, variables=None):
        calls['n'] += 1

        return 'не JSON' if calls['n'] == 1 else good

    gallery_vision.rhapsody_api_chat_ask = _ask
    try:
        got = await gallery_vision_run()
    finally:
        pass

    assert got['took'] == GALLERY_VISION_BATCH
    assert got['failed'] == 1 and got['done'] == GALLERY_VISION_BATCH - 1


async def test_prompt_goes_as_a_variable(tree, agent, girl):
    """⚠⚠ Просьба уходит **переменной** `prompt`, а не только вопросом.

    Workflow соседа подставляет её в шаг `tool_invoke` (`inputs_map`), и без
    этой переменной инструмент получил бы пустую просьбу — модель придумала бы
    свои поля, и колонки заполнять было бы нечем.

    ⚠ Переменной `workflow` здесь нет и быть не должно: workflow задаётся
    агенту при заведении, и переключить его вызовом нельзя. Первая редакция
    слала его переменной — это выглядело бы работающим ровно до первого
    прогона.
    """
    await gallery_vision_one((await _mine(girl))[0])

    sent = agent.asked[0]['variables']

    assert 'image' in sent and 'prompt' in sent
    assert sent['prompt'] == sent['prompt'].strip() and 'description' in sent['prompt']
    assert 'workflow' not in sent, 'workflow задаётся агенту, а не вызовом'
