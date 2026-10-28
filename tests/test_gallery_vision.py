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

from tests.drive_fake import DRIVE_FAKE_ROOT

from src.dating import gallery_vision
from src.dating.gallery_index import gallery_index_profile
from src.dating.gallery_vision import (GALLERY_VISION_BATCH, gallery_vision_due,
                                       gallery_vision_one, gallery_vision_run)
from src.uvicorn.routers.dating.profile_ import profile_delete

pytestmark = pytest.mark.profile

STAND_SITE = 999_343

ANSWER = json.dumps({'description': 'девушка в кружевном платье в гостиной',
                     'tags': ['dress', 'indoor', 'smiling']}, ensure_ascii=False)


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

    async def _due(limit=GALLERY_VISION_BATCH, site_id=0):
        return [row for row in await real(limit * 10, site_id)
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
    drive.add_folder(DRIVE_FAKE_ROOT, 'root-1')
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


async def test_the_prompt_is_not_sent_from_here(tree, agent, girl):
    """⚠⚠ Промпт **не шлётся**: он живёт у соседа умолчанием переменной.

    Присланный отсюда перебил бы его молча (умолчание не перезаписывает
    переданное), и текст, который человек видит на странице workflow, перестал
    бы быть тем, по которому работает модель, — а понять это по журналу
    нельзя.
    """
    await gallery_vision_one((await _mine(girl))[0])

    sent = agent.asked[0]['variables']

    assert 'prompt' not in sent, 'промпт уехал из кода и перебил сторону соседа'
    assert 'image' in sent, 'кадр обязан уехать — его у соседа взять неоткуда'


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
    assert json.loads(got['tags']) == ['dress', 'indoor', 'smiling']
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


async def test_only_the_frame_goes_as_a_variable(tree, agent, girl):
    """Переменной уходит **кадр**, и только он.

    ⚠ Переменной `workflow` здесь нет и быть не должно: workflow задаётся
    агенту при заведении, и переключить его вызовом нельзя. Первая редакция
    слала его переменной — это выглядело бы работающим ровно до первого
    прогона.

    ⚠ Вопрос ручке всё равно нужен: `/chat/ask` требует его, а журнал прогонов
    показывает строкой — пустой читался бы как сорванный вызов.
    """
    await gallery_vision_one((await _mine(girl))[0])

    sent = agent.asked[0]['variables']

    assert list(sent) == ['image'], f'уехало лишнее: {sorted(sent)}'
    assert agent.asked[0]['question'].strip(), 'вопрос пустой'


def test_agent_without_workflow_is_refused_with_words():
    """⚠⚠ Агент без привязанного workflow — отказ словом, а не `500` от соседа.

    Заведение агента идёт прогоном его же workflow, и `create` отвечает
    раньше, чем привязка дописана. В этот зазор `chat_ask` бьёт в агента без
    workflow, а сосед отвечает «Внутренняя ошибка сервера» — без намёка на
    причину. Измерено живьём: первый заход после заведения падал ровно так,
    второй проходил.

    Отказ ловит `gallery_vision_run` как любой другой: строка получает
    `described_error` и остаётся в очереди — следующий заход застанет
    привязку дописанной.
    """
    from src.dating.gallery_vision import _with_workflow

    assert _with_workflow({'id': 777, 'name': 'x', 'workflow_id': 75}) == 777

    with pytest.raises(RuntimeError) as refused:
        _with_workflow({'id': 777, 'name': 'gallery_describe_ladadate'})
    assert 'workflow' in str(refused.value)
    assert 'следующем заходе' in str(refused.value), 'отказ обязан сказать, что дальше'

    with pytest.raises(RuntimeError):
        _with_workflow({})


class TestTheTagDictionary:
    """⚠⚠ Словарь тегов закрытый — на том и держится отбор снимков."""

    def test_it_is_big_enough_to_describe_a_photo(self):
        """Тегов десятки, а не единицы: иначе снимки неразличимы."""
        from src.dating.gallery_vision import (GALLERY_VISION_TAGS,
                                               GALLERY_VISION_TAGS_ALL)

        assert 30 <= len(GALLERY_VISION_TAGS_ALL) <= 60, \
            f'тегов {len(GALLERY_VISION_TAGS_ALL)} — словарь вышел из берегов'
        assert len(GALLERY_VISION_TAGS) >= 4, 'группы нужны человеку для чтения'

    def test_group_keys_are_latin(self):
        """⚠ Ключ группы — ключ в коде, значит латиницей (`code_rules.md` §1.2).

        Прежде он был русским и уезжал **подписью** прямо в ответ ручки: одно
        значение работало и ключом, и текстом для глаза. Подпись теперь своя.
        """
        import re

        from src.dating.gallery_vision import GALLERY_VISION_TAGS

        for key in GALLERY_VISION_TAGS:
            assert re.fullmatch(r'[a-z][a-z0-9_-]*', key), \
                f'ключ группы «{key}» не латиницей'

    def test_every_group_has_a_title_and_no_stranger_has_one(self):
        """⚠⚠ Сторож согласия: два словаря про одно и то же расходятся молча.

        Забыл подпись — окно покажет человеку `clothes`; забыл группу — подпись
        лежит мёртвой и никто об этом не узнает.
        """
        from src.dating.gallery_vision import (GALLERY_VISION_GROUP_TITLES,
                                               GALLERY_VISION_TAGS)

        assert set(GALLERY_VISION_GROUP_TITLES) == set(GALLERY_VISION_TAGS), \
            'подписи групп разошлись с самими группами'
        assert list(GALLERY_VISION_GROUP_TITLES) == list(GALLERY_VISION_TAGS), \
            'порядок подписей разошёлся с порядком групп'
        assert all(one.strip() for one in GALLERY_VISION_GROUP_TITLES.values()), \
            'у группы пустая подпись'

    def test_no_tag_repeats_between_groups(self):
        """⚠ Повтор тега в двух группах — молчаливая беда.

        Отбор его найдёт дважды, а страница покажет дважды, и человек решит, что
        выбрал один, а горят оба.
        """
        from src.dating.gallery_vision import (GALLERY_VISION_TAGS,
                                               GALLERY_VISION_TAGS_ALL)

        assert len(GALLERY_VISION_TAGS_ALL) == len(set(GALLERY_VISION_TAGS_ALL)), \
            'тег повторяется в двух группах'
        assert sum(len(one) for one in GALLERY_VISION_TAGS.values()) \
            == len(GALLERY_VISION_TAGS_ALL), 'плоский список разошёлся с группами'

    def test_the_dictionary_has_no_twin_in_our_code(self):
        """⚠⚠ Промпт со списком тегов у нас не живёт — он у соседа.

        Держи мы свою копию текста, она разошлась бы с его страницей молча, и
        человек правил бы тот текст, по которому модель не работает. Отсюда и
        сторож: в коде не должно быть строки, перечисляющей теги.

        ⚠ Что список всё равно соблюдается, держит сверка **на записи**
        (`gallery_vision_tags_keep`) и строка в журнале, когда модель прислала
        теги мимо словаря, — см. `gallery_vision_one`.
        """
        import src.dating.gallery_vision as module

        assert not hasattr(module, 'GALLERY_VISION_PROMPT'), \
            'промпт вернулся в код — он разойдётся со страницей workflow молча'

        # ⚠ Сам словарь остаётся здесь и это правильно: по нему идёт сверка на
        # записи. Нельзя лишь **текста просьбы**, который его перечисляет.
        assert hasattr(module, 'GALLERY_VISION_TAGS'), 'словарь тегов потерялся'

    def test_a_stranger_tag_is_dropped(self):
        """⚠⚠ Промпта мало: сверка стоит **на записи**.

        Модель держит список, пока он короткий, и дополняет его «близкими по
        смыслу», как только снимок непривычный. Такой тег не ломает ничего
        заметно — он просто никогда не совпадёт в отборе.
        """
        from src.dating.gallery_vision import gallery_vision_tags_keep

        assert gallery_vision_tags_keep(['lace dress', 'indoor']) == ['indoor']
        assert gallery_vision_tags_keep(['выдумка']) == []
        assert gallery_vision_tags_keep(None) == []

    def test_case_and_spaces_do_not_lose_a_tag(self):
        """⚠ Модель отвечает то `Dress`, то ` dress ` — тег один и тот же."""
        from src.dating.gallery_vision import gallery_vision_tags_keep

        assert gallery_vision_tags_keep([' Dress ', 'INDOOR']) == ['dress', 'indoor']

    def test_the_order_is_the_dictionary_one(self):
        """⚠ Порядок словаря, а не ответа: так теги снимков сравнимы глазом."""
        from src.dating.gallery_vision import gallery_vision_tags_keep

        assert gallery_vision_tags_keep(['night', 'indoor', 'dress']) \
            == ['dress', 'indoor', 'night']

    def test_repeats_collapse(self):
        """Один тег дважды — это один тег."""
        from src.dating.gallery_vision import gallery_vision_tags_keep

        assert gallery_vision_tags_keep(['dress', 'dress']) == ['dress']


class TestTheQueueTurns:
    """⚠⚠ Очередь проворачивается: неудача не запирает её на одних и тех же.

    Поймано на проде 28.10.2026. Неудача `described_at` не ставит — строка
    обязана вернуться, — а порядок был `described_at IS NOT NULL, id`. Пока
    причина неудачи жила (у соседа зрение ходило на мёртвый адрес), пачка брала
    **одни и те же** первые пять строк час за часом: 80 прогонов подряд, а 117
    остальных снимков не пробовали ни разу.
    """

    async def test_a_failed_photo_goes_behind_the_untried(self, tree, girl):
        """Снимок с неудачей уступает место тем, кого ещё не пробовали."""
        await gallery_index_profile(girl, 'girl@example.com')
        first = (await _mine(girl, 1))[0]

        await gallery_vision._error_save(first['id'], 'сосед молчит')

        assert (await _mine(girl, 1))[0]['id'] != first['id'], \
            'очередь снова упёрлась в ту же строку'

    async def test_all_failed_still_rotates(self, tree, girl):
        """⚠⚠ Главное: когда неудача у **всех**, очередь всё равно крутится.

        Это и отличает отметку времени от простого «строки с ошибкой в конец»:
        такой ключ перестаёт различать строки ровно тогда, когда он нужен, и
        порядок снова упирается в `id`.
        """
        await gallery_index_profile(girl, 'girl@example.com')
        everyone = await _mine(girl, 50)
        assert len(everyone) >= 3, 'для проверки нужно хотя бы три снимка'

        # Все пробованы, и самый старый — первый из списка.
        for number, row in enumerate(reversed(everyone)):
            await gallery_vision._error_save(row['id'], 'сосед молчит')
            await _try_at(row['id'], f'2026-10-28 0{number}:00:00')

        order = [row['id'] for row in await _mine(girl, 50)]

        assert order[0] == everyone[-1]['id'], \
            'взята не самая давняя попытка — очередь не проворачивается'

    async def test_an_untried_photo_outranks_an_old_failure(self, tree, girl):
        """Непробованный (`NULL`) идёт раньше любой попытки, даже давней."""
        await gallery_index_profile(girl, 'girl@example.com')
        rows = await _mine(girl, 50)

        await gallery_vision._error_save(rows[0]['id'], 'сосед молчит')
        await _try_at(rows[0]['id'], '2020-01-01 00:00:00')

        assert (await _mine(girl, 1))[0]['id'] != rows[0]['id'], \
            'давняя неудача обогнала снимок, который не пробовали ни разу'


async def _try_at(row_id: int, when: str) -> None:
    """Подменить время попытки — чтобы не ждать часов ради проверки порядка."""
    async with mysql_get_db_async() as db:
        await db.execute('UPDATE gallery_file SET described_try_at = %s WHERE id = %s',
                         (when, row_id))
        await db.connection.commit()
