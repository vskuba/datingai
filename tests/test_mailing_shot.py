"""Снимок у письма рассылки: кладётся при сборке, виден в карточке.

## ⚠⚠⚠ Что здесь стережётся

Рассылка была единственным пулом писем без фотографий. Решением владельца
снимок прикладывается к **каждому** её письму. Правило от 08.11.2026 названо
без исключений кроме пустой галереи: «Снимок должен доставаться каждому,
каждой карточке, каждому письму. Единственное исключение, если у профиля нету
в галерее изображений». Исчерпанная галерея — не повод для текста: показ
прокручивается по давности (`companion_gallery_repeat`), кадр уходит повторно.

⚠⚠ **Жребия здесь нет**, и это отличие от ответов с приставанием. Там долю
бросает шаг `dice` у соседа и кладёт `img_attach` в `extra` прогона — письмо
сочиняется на пару, и вызов агента всё равно идёт. Рассылка собирается одним
шаблоном на всех, агента на письмо не зовёт, и второе место с долей
разошлось бы с первым молча.

⚠⚠ **Прежний довод против снимков у рассылки записан в коде и отпал.** Он
звучал так: «её письмо уходит полусотне людей одним текстом, и снимок к нему —
один и тот же кадр, разосланный веером». Верно ровно тогда, когда снимок
выбирается к **шаблону**. Здесь он выбирается **на пару**, той же памятью, что
у прочих пулов, — веера не выходит.
"""

from pathlib import Path

import pytest

pytestmark = pytest.mark.mail

PAGE_FILE = Path('data/uvicorn/template/admin/dating/profile.html')


@pytest.fixture(scope='module')
def page() -> str:
    return PAGE_FILE.read_text(encoding='utf-8')


class TestTheLetterCanCarryAShot:
    """⚠ Рассылка — полноправный пул со снимком, как три соседних."""

    def test_the_pool_is_in_the_list(self):
        from src.dating.mail_send import MAIL_SEND_SHOT_TABLES

        assert 'mailing' in MAIL_SEND_SHOT_TABLES, (
            'рассылка не считается пулом со снимком — круг отправки не '
            'выберет колонку, и кадр не доедет до письма молча')

    async def test_the_column_exists(self):
        from mysql_.mysql_ import mysql_get_db_async

        async with mysql_get_db_async() as db:
            await db.execute(
                'SELECT COUNT(*) AS n FROM information_schema.COLUMNS'
                ' WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s'
                '   AND COLUMN_NAME = %s', ('mailing', 'gallery_file_id'))
            got = int(((await db.fetchone()) or {}).get('n') or 0)

        assert got == 1, 'колонки снимка у рассылки нет'


class TestTheShotIsPickedPerPair:
    """⚠⚠⚠ Снимок выбирается **получателю**, а не шаблону.

    На этом и держится отмена прежнего довода («веер»): память общая со всеми
    прочими письмами — пара «сайт + человек». Первыми идут кадры, которых он
    не видел; на исчерпанной галерее рассылка прокручивает показ (ниже), а не
    заводит свой счёт — веера не выходит и там.
    """

    def test_it_asks_the_shared_memory(self):
        import inspect

        from src.dating import mailing
        from src.dating.companion_gallery import companion_gallery_repeat

        body = inspect.getsource(mailing._shot)

        assert 'companion_gallery_repeat' in body, (
            'рассылка завела свой счёт показанного — один кадр уйдёт '
            'человеку дважды, разными занятиями')
        assert 'companion_gallery_fresh' in inspect.getsource(
            companion_gallery_repeat), (
            'прокрутка перестала спрашивать общую память невиданного — '
            'невиданный кадр уступил бы место показанному')
        assert 'uid' in body, 'снимок выбирается не на получателя'

    def test_it_takes_the_public_folder(self):
        """⚠ Интимные уходят только по обещанию агента, где о них просили."""
        import inspect

        from src.dating import mailing
        from src.dating.mail_shot import MAIL_SHOT_GROUP

        assert MAIL_SHOT_GROUP == 'public'
        assert 'MAIL_SHOT_GROUP' in inspect.getsource(mailing._shot), (
            'папка задана своей строкой — разойдётся с прочими письмами')

    def test_no_dice_is_thrown(self):
        """⚠⚠ Доли здесь нет: снимок кладётся каждому письму, у кого он есть."""
        from tests_.tests_guard import tests_guard_code

        from src.dating import mailing

        # ⚠ Смотрим **код**, а не исходник: в докстрингах жребий упомянут
        # нарочно — там объяснено, почему его здесь нет. Чистит общий сторож
        # слоя, а не свой разбор: `ast` сам по себе докстринг не отбрасывает —
        # он такая же строковая константа.
        text = (tests_guard_code(mailing._shot)
                + tests_guard_code(mailing._put)).lower()

        for word in ('dice', 'percent', 'random', 'img_attach'):
            assert word not in text, (
                f'у рассылки завёлся жребий ({word}) — это второе место с '
                f'долей, оно разойдётся с первым')

    async def test_no_free_shot_is_not_an_error(self, girl):
        """⚠⚠ Нет галереи — единственное законное письмо без снимка.

        Снимка может не быть, только если славать нечем: галереи нет вовсе,
        в `public` нет кадров, Диск не обошли. «Всё уже показано» причиной быть
        перестало — там прокрутка (`TestTheShownGalleryGoesRound`).
        """
        from src.dating.mailing import _shot

        assert await _shot(int(girl), 999_343, 'nobody-here') == 0


def _uid() -> str:
    """Номер, занятый только этим прогоном: память бьёт по паре, тесты не
    должны задевать друг друга отметками."""
    import uuid

    return f'shot-{uuid.uuid4().hex[:10]}'


async def _seed_shot(girl, name: str) -> int:
    """Заводит анкете один снимок в `public`, возвращает номер строки."""
    import uuid as _uuid

    from mysql_.mysql_ import mysql_get_db_async

    async with mysql_get_db_async() as db:
        await db.execute(
            'INSERT INTO gallery_file'
            '  (profile_id, file_id, name, path, mime, kind, is_gone)'
            " VALUES (%s, %s, %s, %s, 'image/png', 'image', 0)",
            (int(girl), _uuid.uuid4().hex, f'{name}.jpg',
             f'public/{name}-{_uuid.uuid4().hex[:8]}.png'))
        file_id = int(db.lastrowid)
        await db.connection.commit()

    return file_id


class TestTheShownGalleryGoesRound:
    """⚠⚠⚠ Всё показано — письмо всё равно со снимком: показ по кругу.

    Прямое требование владельца от 08.11.2026: исключение одно — пустая
    галерея. Прежняя дорога «исчерпали — ушёл текст» здесь запрещена, и
    сторожится живым показом, а не словами в докстринге.
    """

    async def test_shown_still_sends(self, girl):
        """Единственный кадр уже ушёл этому человеку — второй letter берёт его."""
        from src.dating.companion_gallery import companion_gallery_sent
        from src.dating.mailing import _shot

        uid = _uid()
        one = await _seed_shot(girl, 'one')
        await companion_gallery_sent(int(girl), 999_343, uid, one)

        assert await _shot(int(girl), 999_343, uid) == one, (
            'исчерпанная галерея отправила письмо текстом')

    async def test_the_longer_unshown_goes_first(self, girl):
        """⚠ По кругу первым идёт тот, что не слался **дольше**."""
        from mysql_.mysql_ import mysql_get_db_async
        from src.dating.companion_gallery import companion_gallery_sent
        from src.dating.mailing import _shot

        uid = _uid()
        a = await _seed_shot(girl, 'a')
        b = await _seed_shot(girl, 'b')
        await companion_gallery_sent(int(girl), 999_343, uid, a)
        await companion_gallery_sent(int(girl), 999_343, uid, b)

        async with mysql_get_db_async() as db:
            await db.execute(
                'UPDATE companion_gallery_file'
                '   SET sent_at = NOW() - INTERVAL 2 DAY'
                ' WHERE site_id = %s AND uid = %s AND gallery_file_id = %s',
                (999_343, uid, b))
            await db.connection.commit()

        assert await _shot(int(girl), 999_343, uid) == b

        # ⚠ Пересылка передвигает отметку — иначе тот же кадр ходил бы вечно.
        await companion_gallery_sent(int(girl), 999_343, uid, b)

        assert await _shot(int(girl), 999_343, uid) == a, (
            'пересланный кадр не уехал в конец очереди')

    async def test_the_pool_letters_pick_up_their_shots(self, girl):
        """⚠⚠ Письма, собранные без снимка, добирают его до отправки.

        Пул набирался и до правила, и на пустой тогда галерее; не добирай
        круг кадры, человек видел бы в карточке текст, а ушёл бы снимок.
        """
        from mysql_.mysql_ import mysql_get_db_async

        from src.dating import mailing
        from src.dating.mailing import mailing_shots_fill

        uid = _uid()
        one = await _seed_shot(girl, 'draft')

        async with mysql_get_db_async() as db:
            await db.execute(
                "INSERT INTO mailing (site_id, profile_id, uid, name, body,"
                "  status, gallery_file_id)"
                " VALUES (%s, %s, %s, 'Ben', 'Hello', 'draft', 0)",
                (999_343, int(girl), uid))
            letter = int(db.lastrowid)
            await db.connection.commit()

        assert await mailing_shots_fill(999_343) >= 1

        async with mysql_get_db_async() as db:
            await db.execute('SELECT gallery_file_id FROM mailing'
                             ' WHERE id = %s', (letter,))
            got = int(((await db.fetchone()) or {}).get('gallery_file_id') or 0)

        assert got == one, 'письмо пула осталось без снимка'

    def test_the_make_tops_the_pool_up(self):
        """Добор живёт в наборе — его будит и кнопка, и круг."""
        import inspect

        from src.dating import mailing

        assert 'mailing_shots_fill' in inspect.getsource(mailing.mailing_make)


class TestTheCardShowsIt:
    """⚠ Человек видит, что именно уйдёт, не открывая письмо."""

    def test_every_reader_joins_the_shot(self):
        """⚠⚠ Каждый читатель строк рассылки джойнит кадр **у себя**.

        Скан по модулю находил `mail_shot_join` и после того, как джойн был
        только в ручке пула: окно письма и журнал доставки теряли кадр молча.
        Поэтому смотрим каждую из трёх дорог отдельно.
        """
        import inspect

        import src.uvicorn.routers.dating.mailing as mod

        for handle in (mod.mailing_list, mod.mailing_delivery, mod._row):
            assert 'mail_shot_join' in inspect.getsource(handle), (
                f'{handle.__name__} не джойнит кадр — карточка или строка '
                f'журнала покажет письмо без фотографии, хотя она уйдёт')

    def test_the_answer_keeps_the_shot(self):
        """⚠⚠ Выбранный запросом столбец обязан переехать в ответ.

        `_answer` собирает свой словарь по списку полей, а не правит строку,
        и поле, не попавшее в список, до страницы не доедает молча — та же
        грабля, что у журнала ответов (`mail_reply.py`), только с этого конца.
        """
        import inspect

        import src.uvicorn.routers.dating.mailing as mod

        body = inspect.getsource(mod._answer)

        for field in ('gallery_file', 'gallery_path'):
            assert field in body, (
                f'_answer теряет {field} — карточка не увидит снимок, даже '
                f'когда запрос его выбрал')

    def test_the_card_draws_the_thumb(self, page):
        assert 'mailShotThumb(row)' in page, 'карточка не рисует снимок'
        assert 'function mailShotThumb' in page, 'выкройки нет'

    def test_no_shot_means_no_node(self, page):
        """⚠⚠ Нет кадра — узла нет вовсе, а не пустой квадрат.

        Письма без фотографии законны, и у большинства карточек рамка
        читалась бы как «кадр не загрузился».
        """
        box = page[page.index('function mailShotThumb'):]
        box = box[:box.index('// ⚠ Снимок **готового письма**')]

        assert "if (!file) return '';" in box, (
            'у письма без снимка рисуется пустая рамка')

    def test_it_asks_the_file_id_not_the_row(self, page):
        """⚠⚠ Превью отдаётся по номеру файла **на Диске**, а не строки.

        Спутать легко — оба числа, — и ошибка видна лишь пустым квадратом.
        """
        box = page[page.index('function mailShotThumb'):]
        box = box[:box.index('// ⚠ Снимок **готового письма**')]

        assert 'gallery_file' in box
        assert 'gallery_file_id' not in box, (
            'в адрес превью уехал номер строки индекса вместо номера файла')

    def test_the_thumb_has_its_look(self, page):
        assert '.mail-card-thumb {' in page, 'у снимка нет своего вида'


class TestTheAnswerCarriesTheShot:
    """⚠⚠⚠ Кадр, выбранный запросом, обязан доехать до страницы через ответ.

    Замер 08.11.2026: в пуле рассылки ни одна карточка не показывала снимка.
    Запрос джойнил кадр (`mail_shot_join`), страница умела его рисовать
    (`mailShotThumb`), но между ними стоит `_answer`, который собирает свой
    словарь по списку полей, — `gallery_file` в список не попал, и карточка
    молчала о фотографии, которая уйдёт живому человеку.

    ⚠ Сторож прогоняет **живой запрос** через все три двери чтения: список
    пула, журнал доставки и правку письма. Скан исходника этого не стоял:
    `mail_shot_join` нашёлся бы в модуле, даже когда джойн живёт только в
    одной ручке, а поле теряется на другой.
    """

    async def test_all_three_doors_keep_the_shot(self, auth_client, girl):
        """Три двери чтения: список пула, журнал доставки, правка в пуле.

        ⚠ Правка — дверь не косметическая: `letterSave` перечеркивает карточку
        присланным рядом, и потерянный там кадр снимал бы фотографию с
        карточки сразу после «Сохранить в пуле».
        """
        import uuid
        from datetime import datetime

        from mysql_.mysql_ import mysql_get_db_async

        from tests.conftest import TEST_STAND_SITE

        disk_id = f'keeper-{uuid.uuid4().hex[:8]}'

        async with mysql_get_db_async() as db:
            await db.execute(
                'INSERT INTO gallery_file (profile_id, file_id, path, name,'
                '  mime, kind, is_gone, tags)'
                " VALUES (%s, %s, 'public/keeper.png', 'keeper',"
                " 'image/png', 'image', 0, '[]')", (girl, disk_id))
            file_row = int(db.lastrowid)

            # Два письма: черновик читают пул и правка, ушедшее — журнал
            # доставки. Уборка за фикстурой анкеты: у `mailing` каскад.
            made = []

            for status_word, when in (('draft', None),
                                      ('sent', datetime.now())):
                await db.execute(
                    'INSERT INTO mailing (site_id, profile_id, mail_id, uid,'
                    '  name, body, status, sent_at, gallery_file_id)'
                    " VALUES (%s, %s, NULL, %s, 'Сторожевой', 'Текст',"
                    '  %s, %s, %s)',
                    (TEST_STAND_SITE, girl,
                     f'keeper-{uuid.uuid4().hex[:8]}', status_word, when,
                     file_row))
                made.append(int(db.lastrowid))
            await db.connection.commit()

        draft, sent = made

        def keep(where: str, rows: list, want: int) -> None:
            ours = [one for one in rows if int(one['id']) == want]
            assert ours, f'{where}: письма нет в ответе — карточка его не видит'

            for one in ours:
                assert one['gallery_file'] == disk_id, (
                    f'{where}: кадр потерян на сборке ответа — карточка'
                    f' покажет письмо без фотографии, хотя она уйдёт/ушла')
                assert one['gallery_path'] == 'public/keeper.png', (
                    f'{where}: пути кадра нет — у превью не будет подсказки')

        pool = await auth_client.get('/api/dating/mailing',
                                     params={'site_id': TEST_STAND_SITE})
        assert pool.status_code == 200, pool.text[:200]
        keep('пул', pool.json()['result'], draft)

        journal = await auth_client.get(
            '/api/dating/mailing/delivery', params={'site_id': TEST_STAND_SITE})
        assert journal.status_code == 200, journal.text[:200]
        keep('доставка', [row for row in journal.json()['result']
                          if row.get('kind') == 'mailing'], sent)

        edited = await auth_client.put(
            f'/api/dating/mailing/{draft}', json={'body': 'Текст правленый'})
        assert edited.status_code == 200, edited.text[:200]
        keep('правка', [edited.json()['result']], draft)
