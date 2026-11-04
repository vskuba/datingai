"""Кадр принадлежит шаблону: разные шаблоны — разные снимки, порядок — по свежести.

## ⚠⚠⚠ Что здесь стережётся

Владельца не устраивал вид списка: «одно и то же изображение прикрепляется к
карточкам, у которых разные шаблоны». Причина была в устройстве отбора: кадр
выбирался **каждому письму заново** у одной двери без памяти, и та же дверь
каждый раз видела тот же первый подходящий файл. Отсюда три правила, каждое
стережётся отдельно — потому что каждое ломается молча:

* **кадр принадлежит шаблону** (`mail.gallery_file_id`), а не письму: письмо
  наследует его, круг заготовок закрепляет его общей дверью, удаление строки
  шаблона (суточная чистка) кадр отпускает;
* **порядок — по свежести** (`gallery_file.last_shot_at`): сначала никогда не
  бранные, самыми свежими вперёд; все браны — сброс круга и случайный порядок;
* **человек важнее шаблона**: показанный этому адресату кадр письмо не
  повторяет — берёт следующий невиданный (ответ владельца 04.11.2026).

⚠⚠ Одна дверь на все дороги — `mail_shot_for_template`. Круг заготовок, кнопка
«Новый шаблон», правка с включением и письмо-новорождённый (шаблон без кадра)
ходят через неё; своя копия отбора в любом из мест разошлась бы с остальными
на первой же правке — той же граблей, что стоила четырёх копий
`mail_send_mark`.
"""

import inspect
import pathlib
import uuid

import pytest

pytestmark = pytest.mark.mail

SRC = pathlib.Path(__file__).resolve().parents[1] / 'src'


def _uid() -> str:
    """Номер, занятый только этим прогоном: память бьёт по паре."""
    return f'tshot-{uuid.uuid4().hex[:10]}'


async def _sql(text: str, args: tuple = ()):
    from mysql_.mysql_ import mysql_get_db_async

    async with mysql_get_db_async() as db:
        await db.execute(text, tuple(args))
        rows = await db.fetchall()
        await db.connection.commit()

    return [dict(row) for row in (rows or [])]


async def _seed(girl: int, n: int) -> list:
    """Заводит анкете `n` снимков в `public`, самый свежий файл — последним."""
    from mysql_.mysql_ import mysql_get_db_async

    made = []

    for k in range(n):
        async with mysql_get_db_async() as db:
            await db.execute(
                'INSERT INTO gallery_file (profile_id, file_id, name, path,'
                " mime, kind) VALUES (%s, %s, %s, %s, 'image/png', 'image')",
                (girl, uuid.uuid4().hex, f'shot{k}.png',
                 f'public/tshot-{uuid.uuid4().hex[:8]}.png'))
            made.append(int(db.lastrowid))
            await db.connection.commit()

    return made


async def _template(girl: int, name: str, shot: int = 0,
                    enabled: int = 1) -> int:
    """Строка `mail` с владелицей — как её заводит круг или кнопка."""
    from mysql_.mysql_ import mysql_get_db_async

    async with mysql_get_db_async() as db:
        await db.execute(
            'INSERT INTO mail (site_id, profile_id, name, body, enabled,'
            ' gallery_file_id) VALUES (%s, %s, %s, %s, %s, %s)',
            (999_343, girl, name, 'Hello {{firstName}}', enabled, shot))
        mail_id = int(db.lastrowid)
        await db.connection.commit()

    return mail_id


def _bind(mail_id: int, shot: int):
    return _sql('UPDATE mail SET gallery_file_id = %s WHERE id = %s',
                (shot, mail_id))


class TestTheSchemaCarriesTheRule:
    """Колонки правила: без них решение осталось бы словами в докстринге."""

    async def test_the_template_holds_the_shot(self):
        rows = await _sql(
            'SELECT COUNT(*) AS n FROM information_schema.COLUMNS'
            ' WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s'
            '   AND COLUMN_NAME = %s', ('mail', 'gallery_file_id'))

        assert int(rows[0]['n']) == 1, 'шаблон не может держать кадр'

    async def test_the_gallery_remembers_freshness(self):
        rows = await _sql(
            'SELECT COUNT(*) AS n FROM information_schema.COLUMNS'
            ' WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s'
            '   AND COLUMN_NAME = %s', ('gallery_file', 'last_shot_at'))

        assert int(rows[0]['n']) == 1, (
            'свежесть негде помнить — выбор снова упрётся в первый файл')


class TestEveryLiveTemplateItsOwnShot:
    """⚠⚠⚠ Живые шаблоны одной анкеты не делят кадр. Это и есть жалоба
    владельца, решённая прямо."""

    async def test_two_templates_two_shots(self, girl):
        from src.dating.mail_shot import mail_shot_for_template

        frames = await _seed(girl, 3)

        first = await _template(girl, 't-a')
        one = await mail_shot_for_template(girl, first)
        await _bind(first, one)

        second = await _template(girl, 't-b')
        two = await mail_shot_for_template(girl, second)
        await _bind(second, two)

        assert one in frames and two in frames, 'кадр взят не из галереи'
        assert one != two, (
            'дверь отдала один кадр двум живым шаблонам — тот самый веер')

    async def test_text_edit_does_not_move_the_shot(self, girl):
        """Правка текста — не повод менять лицо письма: keep свободен, keep и
        остаётся, свежесть не тратится повторно."""
        from src.dating.mail_shot import mail_shot_for_template

        await _seed(girl, 2)
        own = await _template(girl, 't-keep')
        one = await mail_shot_for_template(girl, own)
        await _bind(own, one)

        assert await mail_shot_for_template(girl, own, keep=one) == one, (
            'правка текста утащила шаблон на другой кадр')

    async def test_disabled_template_frees_its_shot(self, girl):
        """Выключенный шаблон не участвует в рассылке — кадр отдаёт живым.

        ⚠ Кадр один: при двух победил бы другой, никогда не бранный, и
        проверка молчала бы о висящем грузе — здесь важно, что **освобождённый
        снова берётся**, когда он единственный.
        """
        from src.dating.mail_shot import mail_shot_for_template

        frames = await _seed(girl, 1)
        held = await _template(girl, 't-off')
        one = await mail_shot_for_template(girl, held)
        await _bind(held, one)

        await _sql('UPDATE mail SET enabled = 0 WHERE id = %s', (held,))

        fresh = await _template(girl, 't-on')
        again = await mail_shot_for_template(girl, fresh)

        assert again == one, (
            'кадр выключенного шаблона завис мёртвым грузом — галерея есть, '
            'а брать нечего')
        assert again in frames

    async def test_deleted_template_frees_its_shot(self, girl):
        """⚠ Суточная чистка круга сносит строки `mail` — кадр обязан
        освободиться вместе с ними, иначе после первой нормы галерея
        показалась бы пустой."""
        from src.dating.mail_shot import mail_shot_for_template

        await _seed(girl, 1)
        doomed = await _template(girl, 't-doomed')
        one = await mail_shot_for_template(girl, doomed)
        await _bind(doomed, one)

        await _sql('DELETE FROM mail WHERE id = %s', (doomed,))

        heir = await _template(girl, 't-heir')

        assert await mail_shot_for_template(girl, heir) == one, (
            'удалённый шаблон унёс кадр с собой')


class TestFreshnessOrder:
    """⚠ Порядок выбора: «брали бы более свежую... если все уже использовались
    — начинаем круг заново». Сказано владельцем, решается здесь."""

    async def test_never_taken_goes_first_newest_first(self, girl):
        """Никогда не бранные — первыми, самыми свежими файлами вперёд."""
        from src.dating.mail_shot import mail_shot_for_template

        frames = await _seed(girl, 2)
        # Верхний кадр помечен взятым, нижний — нет: пусть дверь не угадывает,
        # а читает память.
        await _sql('UPDATE gallery_file SET last_shot_at = NOW()'
                   ' WHERE id = %s', (frames[0],))

        got = await mail_shot_for_template(girl)

        assert got == frames[1], (
            'взяли браный кадр, когда был никогда не бранный')

    async def test_freshness_survives_the_purge(self, girl):
        """⚠⚠ Чистка сносит шаблоны, а память о свежести живёт на **файле** и
        переживает её: новый круг не должен начинаться с того же первого
        файла — иначе «одно и то же каждый раз» вернётся с точностью до
        наоборот."""
        from src.dating.mail_shot import mail_shot_for_template

        frames = await _seed(girl, 2)
        gone = await _template(girl, 't-purged')
        for frame in frames:
            await _bind(gone, frame)
            await mail_shot_for_template(girl, gone)   # списывает свежесть

        await _sql('DELETE FROM mail WHERE id = %s', (gone,))

        got = await mail_shot_for_template(girl)

        assert got in frames, 'после чистки брать стало нечем'
        rows = await _sql('SELECT id FROM gallery_file WHERE profile_id = %s'
                          ' AND last_shot_at IS NULL', (girl,))
        # Круг сброшен: выбранному вернули отметку, остальным — NULL.
        assert len(rows) == len(frames) - 1, (
            'сброс круга не прошёл по файлам — память свежести не обнулена')

    async def test_no_gallery_is_the_only_zero(self, girl):
        from src.dating.mail_shot import mail_shot_for_template

        assert await mail_shot_for_template(girl) == 0, (
            'пустая галерея обязана отвечать нулём, а не отказом')


class TestTheRecipientWinsOverTheTemplate:
    """⚠⚠ Показанному человеку шаблон не лицензия: следующий невиданный.
    (Ответ владельца 04.11.2026 на вопрос «что делать, если его кадр уже
    уходил этому адресату».)"""

    async def test_the_letter_inherits_the_template_shot(self, girl):
        from src.dating.mailing import _letter_shot

        frames = await _seed(girl, 2)
        own = await _template(girl, 't-inherit')
        await _bind(own, frames[0])

        got = await _letter_shot(girl, 999_343, _uid(), own, frames[0])

        assert got == frames[0], 'письмо не понесло шаблонный кадр'

    async def test_a_shown_shot_is_not_repeated(self, girl):
        from src.dating.companion_gallery import companion_gallery_sent
        from src.dating.mailing import _letter_shot

        frames = await _seed(girl, 2)
        own = await _template(girl, 't-shown')
        await _bind(own, frames[0])
        uid = _uid()
        await companion_gallery_sent(girl, 999_343, uid, frames[0])

        got = await _letter_shot(girl, 999_343, uid, own, frames[0])

        assert got != frames[0], (
            'человек получил бы тот же кадр вторично — память пары проиграла '
            'привязке')

    async def test_a_shotless_template_binds_through_the_door(self, girl):
        """⚠ Шаблон без кадра (наследие, сбой двери на круге) получает его
        **дорогой**, а не жребием на письмо: закрепляется — и дальше держит."""
        from src.dating.mailing import _letter_shot

        frames = await _seed(girl, 1)
        bare = await _template(girl, 't-bare')

        got = await _letter_shot(girl, 999_343, _uid(), bare, 0)

        assert got in frames, 'письмо осталось без кадра при живой галерее'
        rows = await _sql('SELECT gallery_file_id FROM mail WHERE id = %s',
                          (bare,))
        assert int(rows[0]['gallery_file_id']) == got, (
            'кадр привязали письму, но не шаблону — следующий заход выбрал бы'
            ' другой')

    async def test_a_dead_template_does_not_undress_the_letter(self, girl):
        """Шаблон умер между сборкой и отправкой — письмо берёт жребием, а не
        выходит текстом (правило 08.11.2026: снимок обязан каждому)."""
        from src.dating.mailing import _letter_shot

        frames = await _seed(girl, 1)

        assert await _letter_shot(girl, 999_343, _uid(), 0, 0) in frames


class TestOneDoorOnEveryRoad:
    """⚠⚠⚠ Все дороги — через `mail_shot_for_template`. Своё место отбора —
    второе мнение о том, какой кадр свеж и кто его держит; оно разойдётся с
    первым на первой же правке (уроком стоят четыре копии `mail_send_mark`)."""

    def test_the_round_binds_after_the_judge(self):
        """Круг закрепляет кадр **после** суда `mail_check`: бракованная
        заготовка не должна съедать свежесть галереи."""
        from src.dating import mail_round

        code = inspect.getsource(mail_round._write)

        assert 'mail_shot_for_template' in code, (
            'круг перестал привязывать кадр — заготовки снова безликие')
        assert code.index('mail_check(') < code.index('mail_shot_for_template('), (
            'кадр берётся до судьи — брак сожрёт свежесть')

    def test_the_letter_roads_agree(self):
        """Сборка и дозор зовут **одну** помощницу, а не каждая свой жребий."""
        from src.dating import mailing

        for road in (mailing._put, mailing.mailing_shots_fill):
            code = inspect.getsource(road)
            assert '_letter_shot(' in code, (
                'одна из дорог письма пошла своим отбором мимо привязки')

    def test_the_door_is_the_only_stamp(self):
        """⚠ Отметку свежести ставит только дверь — одна точка на весь код.
        Вторая означает второй порядок, и он разойдётся с первым."""
        writers = [str(path.relative_to(SRC))
                   for path in SRC.rglob('*.py')
                   if 'SET last_shot_at' in path.read_text(encoding='utf-8')]

        assert writers == ['dating/mail_shot.py'], (
            f'свежесть пишется в двух местах: {writers}')

    def test_the_templates_list_shows_the_shot(self):
        """Человек видит кадр шаблона списком — выбор превью общий с
        журналами (`mail_shot_join`), своя SQL-копия ушла бы врозь."""
        from src.uvicorn.routers.dating import mail

        code = inspect.getsource(mail)

        assert 'mail_shot_join(' in code, (
            'список шаблонов перестал выбирать превью общим куском')

    def test_the_shown_check_shares_the_memory_rule(self):
        """Сверка «показан ли этот кадр ему» — общим куском `_SEEN_MATCH`,
        тем же, чем отбор прячет показанное."""
        from src.dating.companion_gallery import companion_gallery_shown

        assert '_SEEN_MATCH' in inspect.getsource(companion_gallery_shown), (
            'у сверки завёллся свой критерий показанного — одно и то же письмо'
            ' отбор считает свежим, а сверка показанным')
