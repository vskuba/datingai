"""Память об отправленных снимках: что этот собеседник уже видел.

## ⚠⚠ Зачем она заведена раньше самой отправки

Отправить снимок в чат сегодня нечем — галерея это картотека. Память нужна в тот
же миг, когда отправка появится: заводить её потом значило бы иметь первые сотни
отправок без памяти, то есть ровно те дубли, ради которых всё и затеяно.

Поэтому здесь проверяется **дверь**, а не отправка: ляжет ли отметка, не ляжет ли
дважды, и увидит ли отбор то, чего человек ещё не видел.
"""

import uuid

import pytest

from mysql_.mysql_ import mysql_get_db_async

from src.dating.companion_gallery import (companion_gallery_fresh,
                                          companion_gallery_pick,
                                          companion_gallery_seen,
                                          companion_gallery_sent)
from src.uvicorn.routers.dating.profile_ import profile_delete

pytestmark = pytest.mark.profile

STAND_SITE = 999_343


@pytest.fixture
async def girl(auth_client):
    """Анкета стенда с разложенным индексом снимков. Убирается за собой."""
    login = f'cg-{uuid.uuid4().hex[:8]}@example.com'
    profile_id = int((await auth_client.post('/api/dating/profile', json={
        'site_id': STAND_SITE, 'login': login, 'password': 'x'})).json()['result'])

    files = {}
    async with mysql_get_db_async() as db:
        # ⚠ Теги у `a` — словарные, у `b` — одно словарное из трёх, у `c` —
        # свободные, какими размечали **до** закрытого словаря. На этой тройке
        # и стоит отбор по приметам.
        for name, path, kind, gone, tags in (
                ('a', 'photo/public/a.png', 'image', 0,
                 '["outdoor", "evening", "smiling"]'),
                ('b', 'photo/public/b.png', 'image', 0,
                 '["outdoor", "studio", "serious"]'),
                ('c', 'photo/intim/c.png', 'image', 0,
                 '["lace dress", "sofa"]'),
                ('gone', 'photo/public/gone.png', 'image', 1, None),
                ('film', 'photo/public/film.mp4', 'video', 0, None)):
            await db.execute(
                'INSERT INTO gallery_file (profile_id, file_id, path, name,'
                '  mime, kind, is_gone, tags)'
                ' VALUES (%s, %s, %s, %s, %s, %s, %s, %s)',
                (profile_id, f'{name}-{uuid.uuid4().hex[:8]}', path, name,
                 'image/png' if kind == 'image' else 'video/mp4', kind, gone,
                 tags))
            files[name] = int(db.lastrowid)
        await db.connection.commit()

    yield {'id': profile_id, 'files': files}

    async with mysql_get_db_async() as db:
        await db.execute('DELETE FROM gallery_file WHERE profile_id = %s',
                         (profile_id,))
        await db.connection.commit()
    await profile_delete(profile_id, user=None)


class TestTheMark:
    """Отметка об отправке."""

    async def test_it_lands(self, girl):
        assert await companion_gallery_sent(
            girl['id'], STAND_SITE, 'u-1', girl['files']['a']) is True

    async def test_twice_is_not_an_error(self, girl):
        """⚠⚠ Повтор — обычный исход, а не поломка.

        Отправку могут отметить дважды: круг и кнопка, две попытки подряд. Ключ
        базы такую вставку отвергает сам, и вызывающему довольно знать, новая
        это отметка или нет.
        """
        await companion_gallery_sent(girl['id'], STAND_SITE, 'u-1',
                                     girl['files']['a'])

        assert await companion_gallery_sent(
            girl['id'], STAND_SITE, 'u-1', girl['files']['a']) is False

    async def test_another_site_is_another_person(self, girl):
        """⚠⚠ Номер человека уникален **в пределах сайта**.

        Два сайта вправе выдать один и тот же номер. Не будь сайта в ключе,
        отметка с одного глушила бы снимок на другом — человек не увидел бы его
        никогда, и понять почему было бы нечем.
        """
        await companion_gallery_sent(girl['id'], STAND_SITE, 'u-1',
                                     girl['files']['a'])

        assert await companion_gallery_sent(
            girl['id'], STAND_SITE + 1, 'u-1', girl['files']['a']) is True

    async def test_junk_is_refused_quietly(self, girl):
        """Пустой собеседник или нулевой файл — ложь, а не исключение."""
        assert await companion_gallery_sent(girl['id'], STAND_SITE, '',
                                            girl['files']['a']) is False
        assert await companion_gallery_sent(girl['id'], STAND_SITE, 'u-1',
                                            0) is False


class TestWhatHeHasNotSeen:
    """Отбор: чего этот собеседник ещё не видел."""

    async def test_everything_at_first(self, girl):
        """Ничего не слали — свободны все снимки, кроме негодных."""
        got = await companion_gallery_fresh(girl['id'], STAND_SITE, 'u-1')

        assert {one['name'] for one in got} == {'a', 'b', 'c'}

    async def test_the_sent_one_falls_out(self, girl):
        await companion_gallery_sent(girl['id'], STAND_SITE, 'u-1',
                                     girl['files']['a'])

        got = await companion_gallery_fresh(girl['id'], STAND_SITE, 'u-1')

        assert {one['name'] for one in got} == {'b', 'c'}

    async def test_another_person_sees_it_still(self, girl):
        """⚠ Отметка на пару, а не на файл: другому человеку снимок свободен."""
        await companion_gallery_sent(girl['id'], STAND_SITE, 'u-1',
                                     girl['files']['a'])

        got = await companion_gallery_fresh(girl['id'], STAND_SITE, 'u-2')

        assert {one['name'] for one in got} == {'a', 'b', 'c'}

    async def test_gone_and_video_are_not_offered(self, girl):
        """⚠⚠ Пропавший файл послать нечем, видео — не та работа.

        `is_gone` значит «на Диске не застали». Видео живёт на Диске целиком, и
        отправка его в чат устроена иначе, чем отправка снимка.
        """
        got = await companion_gallery_fresh(girl['id'], STAND_SITE, 'u-1')
        names = {one['name'] for one in got}

        assert 'gone' not in names, 'пропавший снимок предлагать нечем'
        assert 'film' not in names, 'видео — не снимок'

    async def test_group_narrows_by_the_shared_rule(self, girl):
        """⚠⚠ Группа считается **общим правилом**, а не выражением в SQL.

        Имя группы — последняя папка над файлом (`photo/public/a.png` →
        `public`). Повтори это запросом, и `SUBSTRING_INDEX(path, '/', 1)` дал
        бы приставку `photo`: отбор молча разошёлся бы с тем, что человек видит
        на вкладке.
        """
        got = await companion_gallery_fresh(girl['id'], STAND_SITE, 'u-1',
                                            group='public')

        assert {one['name'] for one in got} == {'a', 'b'}

        got = await companion_gallery_fresh(girl['id'], STAND_SITE, 'u-1',
                                            group='intim')

        assert {one['name'] for one in got} == {'c'}

    async def test_limit_comes_after_the_group(self, girl):
        """⚠ Предел — после отбора по группе, а не до.

        Примени его запрос, в окно попали бы чужие группы, а своих не осталось
        бы вовсе.
        """
        got = await companion_gallery_fresh(girl['id'], STAND_SITE, 'u-1',
                                            limit=1, group='intim')

        assert [one['name'] for one in got] == ['c']

    async def test_an_unknown_pair_is_a_quiet_empty(self):
        """Нет анкеты — пустой список, а не исключение."""
        assert await companion_gallery_fresh(0, STAND_SITE, 'u-1') == []
        assert await companion_gallery_fresh(1, STAND_SITE, '') == []


class TestWhatHeHasSeen:
    """Обратный вопрос: что этому человеку уже показывали."""

    async def test_it_lists_the_sent(self, girl):
        await companion_gallery_sent(girl['id'], STAND_SITE, 'u-1',
                                     girl['files']['a'])
        await companion_gallery_sent(girl['id'], STAND_SITE, 'u-1',
                                     girl['files']['b'])

        got = await companion_gallery_seen(girl['id'], 'u-1')

        assert {one['name'] for one in got} == {'a', 'b'}
        assert all(one['sent_at'] for one in got), 'время отправки обязано быть'

    async def test_nothing_sent_is_an_empty_list(self, girl):
        assert await companion_gallery_seen(girl['id'], 'u-1') == []


class TestThePickByTags:
    """Отбор снимка по приметам намерения."""

    async def test_the_best_overlap_wins(self, girl):
        """Две приметы против одной: берётся тот, где совпало больше."""
        got = await companion_gallery_pick(girl['id'], STAND_SITE, 'u-1',
                                           'outdoor, evening, smiling')

        assert got['file'].get('name') == 'a', got['why']
        assert got['score'] == 3

    async def test_one_match_is_enough(self, girl):
        """⚠ Порога нет: одна совпавшая примета лучше случайного снимка."""
        got = await companion_gallery_pick(girl['id'], STAND_SITE, 'u-1',
                                           'studio')

        assert got['file'].get('name') == 'b'
        assert got['score'] == 1

    async def test_a_list_is_accepted_too(self, girl):
        """⚠ Контекст свободен по форме: список так же законен, как строка."""
        got = await companion_gallery_pick(girl['id'], STAND_SITE, 'u-1',
                                           ['outdoor', 'evening'])

        assert got['file'].get('name') == 'a'

    async def test_the_sent_one_is_not_offered_again(self, girl):
        """Память об отправленном сильнее совпадения примет."""
        await companion_gallery_sent(girl['id'], STAND_SITE, 'u-1',
                                     girl['files']['a'])

        got = await companion_gallery_pick(girl['id'], STAND_SITE, 'u-1',
                                           'outdoor, evening, smiling')

        assert got['file'].get('name') == 'b', 'снимок «a» уже показан'

    async def test_old_free_tags_do_not_match_but_do_not_break(self, girl):
        """⚠ Разметка до словаря («lace dress») просто не совпадает.

        Выбрасывать такие теги нельзя: `gallery_vision_tags_keep` отбросил бы
        их, и снимок остался бы вовсе без примет — а он годен, просто проигрывает
        размеченному.
        """
        got = await companion_gallery_pick(girl['id'], STAND_SITE, 'u-1',
                                           'nude, lingerie')

        assert got['file'] == {}
        assert 'не подошёл по приметам' in got['why']
        assert got['fresh'] == 3, 'выбирать было из чего — не подошли приметы'

    async def test_three_silences_are_three_different_answers(self, girl):
        """⚠⚠ «Снимка нет» бывает трёх видов, и делать с ними надо разное."""
        no_tags = await companion_gallery_pick(girl['id'], STAND_SITE, 'u-1', '')
        assert 'нет примет' in no_tags['why']

        for name in ('a', 'b', 'c'):
            await companion_gallery_sent(girl['id'], STAND_SITE, 'u-1',
                                         girl['files'][name])
        all_shown = await companion_gallery_pick(girl['id'], STAND_SITE, 'u-1',
                                                 'outdoor')
        assert 'уже видел' in all_shown['why']

        empty = await companion_gallery_pick(girl['id'] + 1_000_000, STAND_SITE,
                                             'u-1', 'outdoor')
        assert 'галереи у анкеты нет' in empty['why']

    async def test_a_word_outside_the_vocabulary_is_named_not_swallowed(
            self, girl):
        """⚠⚠ Словарь тегов лежит тремя копиями, и третья — промпт у соседа.

        Разойдись он со словарём, модель назовёт слово, которого у снимков нет,
        отбор не найдёт ничего и промолчит: «не подошло» выглядит обычным
        исходом. Поэтому чужое слово **остаётся** в наборе и попадает в причину
        отказа — человек видит его глазами, а не гадает.
        """
        got = await companion_gallery_pick(girl['id'], STAND_SITE, 'u-1',
                                           'sunset-glow')

        assert got['file'] == {}
        assert 'sunset-glow' in got['why'], \
            'чужое слово обязано быть названо, а не выброшено молча'

    async def test_the_group_narrows_the_choice(self, girl):
        """⚠ Политики папок отбор не держит — её задаёт вызывающий."""
        got = await companion_gallery_pick(girl['id'], STAND_SITE, 'u-1',
                                           'outdoor', group='intim')

        assert got['file'] == {}, 'в intim лежит только «c», а он без примет'
        assert got['fresh'] == 1
