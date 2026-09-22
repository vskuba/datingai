"""Журнал доставки избранных: дата есть и у неудачного письма.

## ⚠⚠ Что было сломано

В колонке времени показывался только `sent_at` — «когда ушло». У неудачного
письма его нет и быть не может: отметку снимает сама неудача.

Человек видел две строки подряд, одинаковые по виду, и у красной пусто там, где
у зелёной время. Читается это как «данных не показали», хотя показывать было
нечего: времени исхода мы не хранили вовсе.

## ⚠ Почему не `created_at`

Он говорит, когда черновик **сочинили**, а не когда отправка сорвалась. Между
ними бывают часы: пул набирает один круг, уносит письма другой по своему
расписанию. Показав время сочинения под видом времени ошибки, мы отвечали бы не
на тот вопрос — и молча.

## ⚠ Так уже сделано у инвайтов

У `invite` столбец `updated_at` был с самого начала, и журнал брал
`COALESCE(sent_at, updated_at)`. Здесь заведено то же самое и тем же именем:
две таблицы с разными именами под одно понятие расходятся в первом же запросе,
который пишут по памяти.
"""

import inspect
import uuid

import pytest

from mysql_.mysql_ import mysql_get_db_async

pytestmark = pytest.mark.mail

STAND_SITE = 999_343


@pytest.fixture
async def letters(auth_client):
    """Два письма рядом: ушедшее и сорвавшееся."""
    made = (await auth_client.post('/api/dating/profile', json={
        'site_id': STAND_SITE,
        'login': f'when-{uuid.uuid4().hex[:8]}@example.com',
        'password': 'x'})).json()['result']

    async with mysql_get_db_async() as db:
        await db.execute(
            "INSERT INTO favorite_mail (site_id, profile_id, uid, name, body,"
            " status, error, sent_at)"
            " VALUES (%s, %s, '111', 'Ok', 'hi', 'sent', '', NOW()),"
            "        (%s, %s, '222', 'Bad', 'hi', 'failed', 'сайт отказал', NULL)",
            (STAND_SITE, int(made), STAND_SITE, int(made)))
        await db.connection.commit()

    yield int(made)

    await auth_client.delete(f'/api/dating/profile/{made}')


class TestEveryRowHasATime:
    """⚠⚠ Главное: у обеих строк журнала есть чем заполнить колонку."""

    async def test_the_failed_letter_carries_a_time(self, letters, auth_client):
        got = (await auth_client.get(
            f'/api/dating/favorite-mail?site_id={STAND_SITE}'
            f'&profile_id={letters}&status_=delivery')).json()['result']

        bad = next(r for r in got if r['status'] == 'failed')

        assert not bad['sent_at'], 'у неудачи не должно быть времени отправки'
        assert bad['updated_at'], \
            'времени исхода нет — колонка в журнале останется пустой'

    async def test_the_sent_letter_still_shows_its_send_time(self, letters,
                                                             auth_client):
        """⚠ Обратная сторона: у ушедшего письма показывается именно отправка,
        а не время правки строки."""
        got = (await auth_client.get(
            f'/api/dating/favorite-mail?site_id={STAND_SITE}'
            f'&profile_id={letters}&status_=delivery')).json()['result']

        ok = next(r for r in got if r['status'] == 'sent')

        assert ok['sent_at'], 'у ушедшего письма пропало время отправки'

    async def test_both_rows_are_filled(self, letters, auth_client):
        """То, что видит человек: ни одной пустой ячейки времени."""
        got = (await auth_client.get(
            f'/api/dating/favorite-mail?site_id={STAND_SITE}'
            f'&profile_id={letters}&status_=delivery')).json()['result']

        assert len(got) == 2

        for row in got:
            when = row.get('sent_at') or row.get('updated_at') \
                or row.get('created_at')

            assert when, f'строка «{row["name"]}» осталась без даты'


class TestTheJournalReadsAsATimeline:
    """⚠ Порядок — по времени исхода, а не по номеру строки.

    Журнал читают как ленту «что происходило»: неудача, случившаяся позже
    удачи, обязана стоять выше. По `id` она встала бы по времени **сочинения**.
    """

    def test_it_sorts_by_outcome_time(self):
        import src.uvicorn.routers.dating.favorite_mail as mod

        code = inspect.getsource(mod.favorite_mail_list)

        assert 'COALESCE(m.sent_at, m.updated_at, m.created_at)' in code, \
            'журнал снова сортируется по номеру строки'


class TestOnePatternForAllJournals:
    """⚠⚠ Дату во всех журналах доставки берёт одна функция страницы.

    Прежде у избранных дату брали своим выражением — только `sent_at`, — и
    красные строки стояли пустыми. Две копии правила разошлись молча.
    """

    def test_the_page_uses_the_shared_helper(self):
        page = open('data/uvicorn/template/admin/dating/profile.html',
                    encoding='utf-8').read()

        assert '_sentWhen(one)' in page, \
            'доставка избранных снова берёт дату своим выражением'

    def test_the_helper_falls_back(self):
        """⚠ Порядок запасных важен: сперва отправка, потом исход, потом
        сочинение. Поменяй местами — у ушедшего письма показалось бы время
        правки строки вместо времени отправки."""
        page = open('data/uvicorn/template/admin/dating/profile.html',
                    encoding='utf-8').read()

        assert 'row.sent_at || row.updated_at || row.created_at' in page
