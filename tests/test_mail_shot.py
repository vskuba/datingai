"""Снимок к письму по жребию: когда прикладывается и что с ним дальше.

## ⚠⚠ Что здесь стережётся

Занятие редкое — пять писем из ста, — и поэтому его поломка **не видна на
глаз**: разницу между «жребий сломался» и «просто не выпало» нельзя заметить,
глядя на пул. Отсюда и проверки: доля меряется счётом, а не наблюдением, а
дорога письма проверяется с назначенным снимком, без жребия вовсе.

⚠ Три молчаливых способа сломаться, каждый проверяется отдельно:

* снимок назначен, а запрос круга его **не выбирает** — письмо уходит текстом
  (та же грабля, что уже стоила `reply_to`, `message_id` и столбцов
  исполнения);
* снимок ушёл, а **не отметился показанным** — и тот же кадр уйдёт второй раз;
* снимок не достался, и письмо **падает** вместо того, чтобы уйти текстом.
"""

import uuid

import pytest

from src.dating.mail_shot import (MAIL_SHOT_GROUP, MAIL_SHOT_KEY,
                                  mail_shot_pick, mail_shot_want)

pytestmark = pytest.mark.mail


class TestTheDiceLivesAtTheNeighbour:
    """⚠⚠⚠ Жребия в панели **нет**: решает сосед, мы читаем исход.

    Доля жила настройкой панели, а бросок — здешним `random.random()`. Это
    повторяло уже исправленную ошибку: доли стояли в двух местах разом и
    складывались — приём «писать первой неловко» выпадал девяти заготовкам из
    двадцати при ожидаемых четырёх. Теперь решение приезжает в `extra`
    прогона ключом `img_attach`.
    """

    def test_there_is_no_dice_in_the_code(self):
        """Главный сторож: вернись бросок сюда — правило нарушено молча."""
        import inspect

        from src.dating import mail_shot

        # ⚠ Смотрим на **код**, а не на весь файл: шапка объясняет, почему
        # жребия здесь нет, и поминает `random.random()` словами. Запрети мы
        # упоминание — пропала бы причина, по которой правило существует.
        code = ''.join(line for line in inspect.getsource(mail_shot).splitlines(True)
                       if not line.lstrip().startswith('#'))
        code = code[code.index('MAIL_SHOT_KEY'):]

        assert 'random' not in code, (
            'жребий вернулся в панель — доля опять живёт в двух местах')
        assert 'percent_share' not in code, (
            'доля читается из настройки панели, а должна жить у соседа')

    def test_the_setting_is_gone_from_the_registry(self):
        """⚠ Настройка снята: оставь мы её, человек крутил бы число, которое
        ни на что не влияет — хуже отсутствия, потому что обещает."""
        from src.dating.site_setting import SITE_KNOWN

        assert 'mail_shot_percent' not in SITE_KNOWN, (
            'снятая настройка осталась в реестре и обещает несуществующее')

    def test_the_key_matches_the_workflow_step(self):
        """⚠ Слово — то самое, что в шаге соседа. Опечатка здесь означала бы
        вечно пустой отбор без единой ошибки."""
        assert MAIL_SHOT_KEY == 'img_attach'

    def test_one_means_one_photo(self):
        assert mail_shot_want({'img_attach': '1'}) == 1

    def test_zero_and_absence_are_the_same(self):
        """⚠ «Не просили» и «ключа нет» — один ответ: читающий смотрит на своё
        имя и не падает, не найдя его."""
        assert mail_shot_want({'img_attach': '0'}) == 0
        assert mail_shot_want({}) == 0
        assert mail_shot_want(None) == 0

    def test_a_number_is_ready_for_many_photos(self):
        """⚠ Форма уже готова к «сколько снимков»: ключ придёт числом, и
        менять здесь будет нечего."""
        assert mail_shot_want({'img_attach': '3'}) == 3

    def test_a_broken_value_does_not_break_the_pass(self):
        """⚠ Опечатка в шаге workflow (`'yes'` вместо `'1'`) читается как ноль:
        письмо уйдёт текстом. Это лучше упавшего захода — текст дойдёт."""
        assert mail_shot_want({'img_attach': 'yes'}) == 0
        assert mail_shot_want({'img_attach': '-2'}) == 0


class TestOnlyPublicPhotos:
    """⚠⚠ Жребием уходит только `public`, и это решение, а не умолчание.

    Отбор умеет брать из любой папки, включая `intim`. Прислать интимный
    снимок тому, кто ни о чём не просил, нельзя: такие уходят лишь по обещанию
    агента, где о снимке попросили словами.
    """

    def test_the_group_is_public(self):
        assert MAIL_SHOT_GROUP == 'public'

    async def test_the_picker_is_asked_for_that_group(self, girl, monkeypatch):
        asked = {}

        async def spy(profile_id, site_id, uid, limit=0, group=''):
            asked['group'] = group

            return []

        monkeypatch.setattr('src.dating.mail_shot.companion_gallery_fresh', spy)

        await mail_shot_pick(girl, 343, 'someone', {'img_attach': '1'})

        assert asked.get('group') == 'public', (
            f"жребий полез в папку {asked.get('group')!r} — оттуда снимки "
            'уходят только по просьбе')


class TestNothingBreaksTheLetter:
    """⚠ Письмо дороже снимка: ни одна помеха не отменяет его отправки."""

    async def test_an_empty_gallery_is_not_an_error(self, girl, monkeypatch):
        assert await mail_shot_pick(girl, 343, _uid(),
                                    {'img_attach': '1'}) == 0, (
            'пустая галерея должна давать ноль, а не отказ')


class TestTheSendKnowsAboutIt:
    """⚠⚠ Дорога письма со снимком — там, где ломается молча."""

    def test_the_pools_are_listed(self):
        """Рассылки в списке нет: её текст уходит полусотне людей, и снимок к
        нему был бы одним кадром, разосланным веером."""
        from src.dating.mail_send import MAIL_SEND_SHOT_TABLES

        assert set(MAIL_SEND_SHOT_TABLES) == {'intention_mail', 'mail_reply',
                                              'favorite_mail'}
        assert 'mailing' not in MAIL_SEND_SHOT_TABLES

    def test_the_query_selects_the_photo_for_all_of_them(self):
        """⚠⚠ Не выбранный столбец — письмо уходит текстом при назначенном
        кадре, молча. Четвёртый случай той же грабли."""
        import inspect

        from src.dating import send_round

        code = inspect.getsource(send_round)

        assert "if table in MAIL_SEND_SHOT_TABLES else ''" in code, (
            'номер снимка выбирается не для всех пулов, которым он положен')

    def test_a_lost_photo_does_not_kill_an_ordinary_letter(self):
        """⚠⚠ Исход неудачи **разный**, и это главное различие пулов: у
        обещания снимок суть дела, у ответа — украшение."""
        import inspect

        from src.dating import send_round

        code = inspect.getsource(send_round._send)

        assert "if table == 'intention_mail':" in code, (
            'неудача снимка обрабатывается одинаково — обычное письмо пропадёт '
            'из-за украшения')
        assert "{**letter, 'gallery_file_id': 0}" in code, (
            'номер снимка не снят со строки — отправка отметит кадр показанным,'
            ' хотя он никуда не ушёл')

    def test_the_photo_is_remembered_for_every_pool(self):
        """⚠⚠ Память о показанном — общая: иначе один кадр уйдёт дважды, сперва
        ответом, потом обещанием."""
        import inspect

        from src.dating import mail_send

        code = inspect.getsource(mail_send.mail_send_mark)

        assert 'companion_gallery_sent' in code, (
            'снимок не отмечается показанным в общей двери отправки')
        assert "sent and int(letter.get('gallery_file_id') or 0)" in code, (
            'отметка привязана к роду письма, а не к наличию снимка')


def _uid() -> str:
    return f'id-{uuid.uuid4().hex[:8]}'
