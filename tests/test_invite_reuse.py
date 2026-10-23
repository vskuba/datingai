"""Тексты инвайтов сочиняются один раз, а выдаются по сроку.

На проде при наборе каждые три минуты движок сочинял заново то, что никто не
читает уже неделю: инвайт живёт у мужчины ~15 минут. Таблица `invite_draft`
собирает уникальные тексты сайта; заход (`invite._templates`) сперва берёт из
неё всё, что отлежало `invite_reuse_hours`, а к агенту идёт только за
нехваткой. Дорастёт коллекция — вызовы LLM сойдут на нет.

## ⚠⚠ Что здесь стережётся

* **срок меряется выдачей, а не сочинением**: две выдачи одного текста
  разделяет ровно настройка, меньше — не возьмёт ни один заход (`_draft_take`
  закрепляет строку тем же условием, каким отбирала);
* **пустая или выключенная база не мешает набору**: нулевая настройка — прежняя
  дорога, ровно один вызов агенту на весь заход;
* **отказ соседа не отменяет заход, если база уже дала**: без базы прежнее
  поведение — исключение вызывающему.

⚠ База пишется и при выключенном переиспользовании: коллекция копится с первого
запуска, и включение возврата застает её наполненной, а не пустой.
"""

import pytest

from mysql_.mysql_ import mysql_get_db_async

from src.dating import invite
from src.dating.mail_agent import DatingMailAgentError

pytestmark = pytest.mark.profile

STAND_SITE = 999_343


@pytest.fixture
async def hours():
    """Ставит срок переиспользования у сайта стенда и убирает за собой."""
    async def _set(value):
        async with mysql_get_db_async() as db:
            await db.execute(
                'UPDATE site SET settings = JSON_SET(COALESCE(settings, JSON_OBJECT()),'
                ' %s, %s) WHERE site_id = %s',
                ('$.invite_reuse_hours', str(value), STAND_SITE))
            await db.connection.commit()

    yield _set

    async with mysql_get_db_async() as db:
        await db.execute('UPDATE site SET settings = JSON_REMOVE(settings, %s)'
                         ' WHERE site_id = %s',
                         ('$.invite_reuse_hours', STAND_SITE))
        await db.connection.commit()


@pytest.fixture(autouse=True)
async def clean():
    """База текстов стенда пустая до теста и после: чужие строки не нужны."""
    async def _wipe():
        async with mysql_get_db_async() as db:
            await db.execute('DELETE FROM invite_draft WHERE site_id = %s',
                             (STAND_SITE,))
            await db.connection.commit()

    await _wipe()
    yield
    await _wipe()


async def _put(*texts, lang: str = 'en') -> list[str]:
    """Кладёт тексты в базу **самой дорогой записи** (`_draft_save`).

    ⚠ Не `INSERT` руками: ключ `body_key` считает код, и своя копия ключа в
    тесте разошлась бы с боевым правилом — база начала бы копить видимые дубли,
    а тест этого не увидел бы.
    """
    return await invite._draft_save(STAND_SITE, lang, list(texts))


async def _age(text: str, hours_ago: int):
    """Ставит тексту давность выдачи.

    ⚠ Время считает **база** (`NOW() - INTERVAL`), а не Python: MySQL живёт по
    UTC, и `datetime.now()` положил бы строку в будущее — на этом уже ловились.
    """
    async with mysql_get_db_async() as db:
        await db.execute(
            'UPDATE invite_draft SET used_at = NOW() - INTERVAL %s HOUR'
            ' WHERE site_id = %s AND body = %s',
            (int(hours_ago), STAND_SITE, text))
        await db.connection.commit()


def _agent(monkeypatch, *texts, raises: Exception = None):
    """Подменяет агента: считает вызовы, запоминает сколько просили.

    Отдаёт **всё**, что задано, не ровняясь на заказ: лишний ответ — законный
    случай, его разбирает предохранитель, и тесту важно видеть обе ветки.
    """
    calls = []

    async def _template(site_id, language, avoid=None, name_percent='0',
                        girl_name_percent='0', how_many=1):
        calls.append({'how_many': int(how_many), 'avoid': avoid})
        if raises:
            raise raises

        return list(texts)

    monkeypatch.setattr(invite, 'invite_template', _template)

    return calls


async def _count() -> int:
    async with mysql_get_db_async() as db:
        await db.execute('SELECT COUNT(*) AS n FROM invite_draft'
                         ' WHERE site_id = %s', (STAND_SITE,))
        row = await db.fetchone()

    return int(row['n'])


class TestTake:
    """Отбор из базы: что годно и что делает текст взятым."""

    async def test_fresh_texts_are_not_taken(self, hours):
        """Меньше срока со дня выдачи — текст не берёт ни один заход."""
        await hours(24)
        await _put('свежий', 'тоже свежий')

        assert await invite._draft_take(STAND_SITE, 'en', 5) == []

    async def test_ripe_texts_go_oldest_first(self, hours):
        """Порядок `used_at ASC` — и он же гарантия: две выдачи разделены сроком."""
        await hours(24)
        await _put('давний', 'средний')
        await _age('давний', 48)
        await _age('средний', 30)

        assert await invite._draft_take(STAND_SITE, 'en', 5) == ['давний', 'средний']

    async def test_taken_text_is_not_taken_twice(self, hours):
        """Взятие отмечает `used_at = NOW()` — второй заход уже ничего не видит."""
        await hours(24)
        await _put('один')
        await _age('один', 48)

        assert await invite._draft_take(STAND_SITE, 'en', 5) == ['один']
        assert await invite._draft_take(STAND_SITE, 'en', 5) == []

    async def test_zero_setting_takes_nothing(self, hours):
        """⚠ Умолчание — «не переиспользовать»: прежняя дорога, агент каждый заход."""
        await hours(0)
        await _put('давний')
        await _age('давний', 999)

        assert await invite._draft_take(STAND_SITE, 'en', 5) == []

    async def test_an_absent_setting_takes_nothing(self):
        """Настройки нет вовсе — поведение прежнее, база молчит."""
        await _put('давний')
        await _age('давний', 999)

        assert await invite._draft_take(STAND_SITE, 'en', 5) == []

    async def test_changing_the_language_stops_the_collection(self, hours):
        """⚠ Тексты другой языковой ветки не выдаются, но и не выбрасываются."""
        await hours(24)
        await _put('привет', lang='ru')
        await _age('привет', 48)

        assert await invite._draft_take(STAND_SITE, 'en', 5) == []


class TestRound:
    """Заход: база целиком, нехватка — агенту, отказ соседа — не приговор."""

    async def test_the_cache_answers_without_the_agent(self, hours, monkeypatch):
        """База накормила весь заход — вызова движка нет вовсе."""
        await hours(24)
        await _put('раз', 'два', 'три')
        for text in ('раз', 'два', 'три'):
            await _age(text, 48)
        calls = _agent(monkeypatch)

        got = await invite._templates(STAND_SITE, 'en', 3)

        assert sorted(got) == ['два', 'раз', 'три']
        assert not calls, 'ради текстов, лежащих в базе, агент не зовётся'

    async def test_only_the_gap_is_generated(self, hours, monkeypatch):
        """⚠ Агенту уезжает **нехватка**, а не весь заказ."""
        await hours(24)
        await _put('раз', 'два')
        await _age('раз', 48)
        await _age('два', 30)
        calls = _agent(monkeypatch, 'новый')

        got = await invite._templates(STAND_SITE, 'en', 3)

        assert calls[0]['how_many'] == 1, 'агенту ушёл весь заказ, а не нехватка'
        assert len(got) == 3 and len(set(got)) == 3

    async def test_generated_texts_land_in_the_collection(self, hours, monkeypatch):
        """⚠ База пишется всегда: сочинённое дописывается с текущим временем."""
        await hours(0)
        _agent(monkeypatch, 'новый один', 'новый два')

        await invite._templates(STAND_SITE, 'en', 2)

        assert await _count() == 2

    async def test_duplicate_from_the_agent_is_dropped_from_the_round(
            self, hours, monkeypatch):
        """⚠ Тот же текст, что уже роздан этим заходом, вторым экземпляром не уйдёт.

        Агент вернул то, что уже лежит в базе (он его и не знал — `avoid`
        подсказка, а не запрет). Вставка такой дубль не пустит, и заходу его
        отдавать нельзя: раньше срока тот же текст прозвучал бы дважды.
        """
        await hours(24)
        await _put('тот же')
        await _age('тот же', 48)
        _agent(monkeypatch, 'ТОТ   ЖЕ', 'новый')

        got = await invite._templates(STAND_SITE, 'en', 2)

        assert got == ['тот же', 'новый'], got

    async def test_agent_down_goes_with_the_cache(self, hours, monkeypatch):
        """⚠ Сбой соседа не отменяет заход, если база уже дала."""
        await hours(24)
        await _put('из базы')
        await _age('из базы', 48)
        _agent(monkeypatch, raises=DatingMailAgentError('сосед лежит'))

        assert await invite._templates(STAND_SITE, 'en', 2) == ['из базы']

    async def test_agent_down_without_cache_raises(self, monkeypatch):
        """Без базы — прежнее поведение: исключение вызывающему, заход пуст."""
        _agent(monkeypatch, raises=DatingMailAgentError('сосед лежит'))

        with pytest.raises(DatingMailAgentError):
            await invite._templates(STAND_SITE, 'en', 2)

    async def test_zero_setting_asks_the_agent_for_everything(self, hours,
                                                              monkeypatch):
        """Настройка выключена — заход ровно прежний: весь заказ агенту."""
        await hours(0)
        await _put('лёжащий')
        await _age('лёжащий', 999)
        calls = _agent(monkeypatch, 'сочинённый')

        got = await invite._templates(STAND_SITE, 'en', 1)

        assert calls[0]['how_many'] == 1
        assert got == ['сочинённый']

    async def test_disabled_reuse_never_shortens_the_round(self, hours,
                                                          monkeypatch):
        """⚠ При нулевой настройке дубль в базе заход не укорачивает.

        Отбора из базы нет, и фильтрация по нему была бы наказанием за текст,
        который заход всё равно намеренно раздал бы заново: заход при нуле
        обязан идти **ровно прежней дорогой**.
        """
        await hours(0)
        await _put('тот же')
        await _age('тот же', 999)
        _agent(monkeypatch, 'тот же')

        assert await invite._templates(STAND_SITE, 'en', 1) == ['тот же']


class TestAvoid:
    """Подсказка «так уже было» — из базы текстов, свежая на выдачу."""

    async def test_the_avoid_list_reads_the_collection(self, hours):
        """Порядок по `used_at DESC`: в подсказке то, что ходит сейчас."""
        await hours(24)
        await _put('давний', 'свежий')
        await _age('давний', 100)

        assert await invite._avoid(STAND_SITE, 'en') == ['свежий', 'давний']

    async def test_just_taken_texts_are_in_the_hint(self, hours):
        """⚠ Взятые этим заходом — первые в подсказке: агент не сочиняет то,
        что мы держим в обороте."""
        await hours(24)
        await _put('взяли только что')
        await _age('взяли только что', 48)

        assert await invite._draft_take(STAND_SITE, 'en', 1) == ['взяли только что']

        assert (await invite._avoid(STAND_SITE, 'en'))[0] == 'взяли только что'

    async def test_other_language_stays_out_of_the_hint(self):
        await _put('по-русски', lang='ru')

        assert await invite._avoid(STAND_SITE, 'en') == []
