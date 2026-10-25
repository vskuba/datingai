"""Задержка ответа по длине текста: столько, сколько его писал бы человек.

⚠⚠ **Повод живой.** Olena ↔ Benoit, 25.10.2026: его письмо и наш ответ в четыреста
слов стоят в ящике с разницей в четыре минуты. Столько за это время не печатает
никто — и собеседнику видно ровно это, без всякого разбора текста.

Здесь проверяется арифметика срока, его отсчёт **от входящего письма** и то, что
круг отправки ждёт назначенное, а не уносит готовое немедленно.
"""

import uuid

import pytest

from src.dating import reply_delay as delay
from src.dating.reply_delay import (REPLY_DELAY_DEFAULT, REPLY_DELAY_SETTING,
                                    reply_delay_hold, reply_delay_hold_sql,
                                    reply_delay_left, reply_delay_left_word,
                                    reply_delay_sec, reply_delay_why,
                                    reply_delay_words)
from src.dating.site_setting import SITE_KNOWN

pytestmark = pytest.mark.mail

# Сайт стенда — тот же, на котором фикстура заводит анкеты.
SITE = 999_343


def _coefficient(monkeypatch, value: int) -> None:
    """Коэффициент сайта — подменой чтения настройки.

    ⚠ Подменяется **чтение реестра**, а не сам `reply_delay_per10`: так в счёт
    идёт и перевод «настройки нет → умолчание», то есть проверяется та самая
    дорога, которой коэффициент приходит к кругу.
    """
    async def _said(site_id, key):
        return int(value) if key == REPLY_DELAY_SETTING else 0

    monkeypatch.setattr('src.dating.reply_delay.site_seconds', _said)


class TestTheWordsAreCounted:
    """⚠ Слово — всё, что разделено пробелами, и разметка в счёт не идёт."""

    def test_plain_words(self):
        assert reply_delay_words('one two three') == 3

    def test_markup_is_not_a_word(self):
        """⚠⚠ `<br/>` между абзацами — не слово. У писем из панели тегов больше,
        и каждый из них добавлял бы ответу секунды из ничего."""
        assert reply_delay_words('one<br/>two <b>three</b>') == 3

    def test_empty_is_zero(self):
        assert reply_delay_words('') == 0
        assert reply_delay_words(None) == 0


class TestTheTermFollowsTheLength:
    """⚠ Сколько слов, столько времени: срок считается длиной письма."""

    def test_ten_words_cost_the_coefficient(self):
        assert reply_delay_sec(' '.join(['x'] * 10), 30) == 30

    def test_three_hundred_words_at_thirty(self):
        """Тот самый случай: триста слов при тридцати секундах — четверть часа."""
        assert reply_delay_sec(' '.join(['x'] * 300), 30) == 900

    def test_short_letters_round_up(self):
        """⚠⚠ Округление **вверх**: три слова при тридцати дают девять секунд, а
        не ноль. Ноль здесь означал бы «задержки нет», то есть настройку, которая
        на коротких ответах молча не работает."""
        assert reply_delay_sec('one two three', 30) == 9

    def test_zero_coefficient_means_no_delay(self):
        """⚠ Ноль — законное «выключено», а не «настройки нет»."""
        assert reply_delay_sec(' '.join(['x'] * 300), 0) == 0


class TestThePastIsCounted:
    """⚠⚠ Отсчёт идёт от **его письма**, и прошедшее засчитывается.

    Круг ответов сочиняет черновик через `reply_every` — обычно пять минут. Эти
    минуты человек потратил бы на чтение и печать, и складывать их с задержкой
    незачем: собеседник увидел бы двадцать минут там, где назначено пятнадцать.
    """

    def test_elapsed_time_is_subtracted(self):
        assert reply_delay_left(' '.join(['x'] * 300), 300, 30) == 600

    def test_a_long_lying_reply_goes_at_once(self):
        """Ответ, пролежавший дольше срока, уходит сразу — и это тот же ответ на
        вопрос «не пора ли», а не особый случай."""
        assert reply_delay_left(' '.join(['x'] * 300), 5000, 30) == 0

    def test_a_fresh_long_reply_waits_the_whole_term(self):
        assert reply_delay_left(' '.join(['x'] * 300), 0, 30) == 900


class TestTheSettingIsRegistered:
    """⚠ Реестр — источник границ и умолчания: значение читает код, а не форма."""

    def test_the_setting_is_a_number_with_zero_allowed(self):
        known = SITE_KNOWN[REPLY_DELAY_SETTING]

        assert known.get('kind') == 'number'
        assert known.get('min') == 0, 'ноль — это «задержки нет», и он законен'
        assert not known.get('options'), 'у числового поля не бывает вариантов'

    def test_the_default_matches_the_code(self):
        """⚠ Два умолчания об одном сроке — в реестре и в коде. Разойдись они,
        сайт без настройки вёл бы себя не так, как показывает форма."""
        assert int(SITE_KNOWN[REPLY_DELAY_SETTING]['default']) == \
            REPLY_DELAY_DEFAULT

    def test_the_title_names_the_unit(self):
        """⚠ «10 слов в секундах» в заголовке: число без подписи не прочитать —
        это секунды на слово, на письмо или на десяток?"""
        title = SITE_KNOWN[REPLY_DELAY_SETTING]['title']

        assert '10 слов' in title and 'секунд' in title

    async def test_the_reader_falls_back_to_the_default(self, monkeypatch):
        """⚠ Настройки нет — берём умолчание кода, а не ноль: ноль означал бы
        «задержки нет», то есть пустая база молча выключила бы занятие."""
        async def _nothing(site_id, key):
            return 0

        monkeypatch.setattr('src.dating.reply_delay.site_seconds', _nothing)

        assert await delay.reply_delay_per10(SITE) == 0, \
            'ноль в настройке — это «выключено», и подменять его нельзя'

        async def _broken(site_id, key):
            raise RuntimeError('база молчит')

        monkeypatch.setattr('src.dating.reply_delay.site_seconds', _broken)

        assert await delay.reply_delay_per10(SITE) == REPLY_DELAY_DEFAULT


class TestTheQueueWaits:
    """⚠⚠ Правило держит **очередь отправки**, а не только карточку на странице.

    Набор пула про задержку не знает вовсе: ответ сочиняется, когда пришло письмо.
    Решает круг отправки — он и обязан ждать.
    """

    async def _reply(self, profile_id: int, *, words: int, ago_min: int) -> int:
        """Черновик ответа заданной длины на письмо заданной давности."""
        from mysql_.mysql_ import mysql_get_db_async

        uid = f'rd-{uuid.uuid4().hex[:8]}'

        async with mysql_get_db_async() as db:
            await db.execute(
                'INSERT INTO mail_in (site_id, profile_id, message_id, uid,'
                " name, body, date_post) VALUES (%s, %s, %s, %s, 'Тест', 'Письмо',"
                ' NOW() - INTERVAL %s MINUTE)',
                (SITE, int(profile_id), int(uuid.uuid4().int % 1_000_000_000),
                 uid, int(ago_min)))
            await db.connection.commit()
            await db.execute('SELECT LAST_INSERT_ID() AS id')
            mail_in_id = int((await db.fetchone())['id'])

            await db.execute(
                'INSERT INTO mail_reply (site_id, profile_id, mail_in_id, uid,'
                ' name, body, status, error)'
                " VALUES (%s, %s, %s, %s, 'Тест', %s, 'draft', '')",
                (SITE, int(profile_id), mail_in_id, uid,
                 ' '.join(['word'] * int(words))))
            await db.connection.commit()
            await db.execute('SELECT LAST_INSERT_ID() AS id')

            return int((await db.fetchone())['id'])

    async def test_a_fresh_long_reply_is_held(self, girl, monkeypatch):
        """Длинный ответ на свежее письмо круг не берёт: его ещё «пишут»."""
        _coefficient(monkeypatch, 30)
        reply_id = await self._reply(girl, words=300, ago_min=1)

        hold = await reply_delay_hold(SITE)

        assert reply_id in hold, 'длинный свежий ответ не задержан'
        assert hold[reply_id] > 600, 'срок вышел короче назначенного'
        assert str(reply_id) in await reply_delay_hold_sql(SITE), \
            'условие очереди задержанный ответ не называет'

    async def test_an_old_letter_lets_the_reply_go(self, girl, monkeypatch):
        """⚠ Прошедшее засчитывается: письмо часовой давности снимает задержку."""
        _coefficient(monkeypatch, 30)
        reply_id = await self._reply(girl, words=300, ago_min=60)

        assert reply_id not in await reply_delay_hold(SITE), \
            'ответ держат, хотя время на него давно вышло'

    async def test_zero_coefficient_holds_nothing(self, girl, monkeypatch):
        """⚠ Ноль — «задержки нет»: условие не строится вовсе, и запрос очереди
        остаётся таким, каким был до настройки."""
        _coefficient(monkeypatch, 0)
        await self._reply(girl, words=300, ago_min=0)

        assert await reply_delay_hold(SITE) == {}
        assert await reply_delay_hold_sql(SITE) == ''

    async def test_a_short_reply_goes_while_a_long_one_waits(self, girl,
                                                             monkeypatch):
        """⚠⚠ И это главное: задержка **не запирает очередь**. Короткий ответ
        уходит, пока длинный дописывается, — иначе одно длинное письмо держало бы
        всю почту сайта."""
        _coefficient(monkeypatch, 30)
        long_id = await self._reply(girl, words=300, ago_min=0)
        short_id = await self._reply(girl, words=2, ago_min=30)

        hold = await reply_delay_hold(SITE)

        assert long_id in hold and short_id not in hold

    async def test_a_failed_reply_is_not_held(self, girl, monkeypatch):
        """⚠ Ответ с прежней ошибкой круг не берёт и так: держать его значило бы
        обещать отправку, которой не будет, — и таймер на карточке врал бы."""
        from mysql_.mysql_ import mysql_get_db_async

        _coefficient(monkeypatch, 30)
        reply_id = await self._reply(girl, words=300, ago_min=0)

        async with mysql_get_db_async() as db:
            await db.execute("UPDATE mail_reply SET error = 'сайт отказал'"
                             ' WHERE id = %s', (reply_id,))
            await db.connection.commit()

        assert reply_id not in await reply_delay_hold(SITE)


class TestTheIdleIsExplained:
    """⚠ Пул полон, ошибок нет, отправка включена — и тишина. Без строки словами
    это состояние читается как поломка: так уже было с паузой и с остудой."""

    def test_the_reason_names_the_term(self):
        said = reply_delay_why(900)

        assert 'ещё пишется' in said and '15 мин' in said

    def test_nothing_to_say_when_nothing_waits(self):
        assert reply_delay_why(0) == ''


class TestTheWordsOfTheTermAgree:
    """⚠⚠ Срок называют **двое**: сервер в строке простоя и таймер на карточке.

    Таймер тикает раз в секунду, и ходить за словами на сервер каждую секунду
    нельзя — значит на странице живёт копия. Копия вынужденная, но молча
    разойтись она не должна: тест вырезает её из разметки и прогоняет `node`-ом
    против питоновской. Тот же приём, что у подписи паузы между письмами.
    """

    def test_the_page_says_the_same_as_the_server(self):
        import json
        import os
        import re
        import shutil
        import subprocess
        import tempfile

        node = shutil.which('node')
        if not node:
            pytest.skip('node не установлен')

        page = open('data/uvicorn/template/admin/dating/profile.html',
                    encoding='utf-8').read()

        found = re.search(r'function replyWaitWords\(seconds\) \{.*?\n    \}',
                          page, re.S)
        assert found, 'функция таймера не нашлась на странице'

        cases = [0, 5, 59, 60, 90, 900, 3600, 4200, 7260]
        want = [reply_delay_left_word(one) for one in cases]

        probe = (found.group(0) + '\n'
                 + f'const cases = {json.dumps(cases)};\n'
                 + f'const want = {json.dumps(want, ensure_ascii=False)};\n'
                 + 'for (let i = 0; i < cases.length; i++) {\n'
                 + '  const got = replyWaitWords(cases[i]);\n'
                 + '  if (got !== want[i]) {\n'
                 + '    console.log(`${cases[i]} → ${got}, сервер говорит '
                 + '${want[i]}`);\n'
                 + '    process.exit(1);\n'
                 + '  }\n'
                 + '}\n')

        fd, path = tempfile.mkstemp(suffix='.mjs')
        try:
            with os.fdopen(fd, 'w', encoding='utf-8') as f:
                f.write(probe)

            result = subprocess.run([node, path], capture_output=True, text=True)

            assert result.returncode == 0, \
                f'таймер и сервер говорят разное: {result.stdout.strip()}'
        finally:
            os.remove(path)
