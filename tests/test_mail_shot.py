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

from src.dating.mail_shot import (MAIL_SHOT_GROUP, MAIL_SHOT_SETTING,
                                  MAIL_SHOT_SHARE, mail_shot_pick)

pytestmark = pytest.mark.mail


class TestTheDiceIsRare:
    """Доля: пять писем из ста, и это проверяется счётом."""

    def test_the_default_is_five_percent(self):
        assert MAIL_SHOT_SHARE == 0.05, (
            'умолчание разошлось с тем, о чём договорились')

    def test_the_registry_agrees_with_the_code(self):
        """⚠⚠ Умолчаний **два** — в реестре настроек и в коде, — и разойдись
        они, поведение зависело бы от того, прочиталась настройка или нет."""
        from src.dating.site_setting import SITE_KNOWN

        known = SITE_KNOWN.get(MAIL_SHOT_SETTING) or {}

        assert known, 'настройки нет в реестре — форма её не покажет'
        assert int(known['default']) == int(MAIL_SHOT_SHARE * 100), (
            f"реестр обещает {known['default']}%, код — "
            f'{MAIL_SHOT_SHARE * 100}%')
        assert known.get('max') == 100, (
            'у доли снят потолок: 101% это не «много», а тихо выключенный '
            'жребий')

    async def test_zero_turns_it_off(self, girl, monkeypatch):
        """⚠ Ноль — законное значение: человек выбрал «никогда»."""
        async def no_share(site_id, key, fallback):
            return 0

        monkeypatch.setattr('src.dating.mail_shot.percent_share', no_share)

        assert await mail_shot_pick(girl, 343, 'someone') == 0


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

        async def always(site_id, key, fallback):
            return 1.0

        async def spy(profile_id, site_id, uid, limit=0, group=''):
            asked['group'] = group

            return []

        monkeypatch.setattr('src.dating.mail_shot.percent_share', always)
        monkeypatch.setattr('src.dating.mail_shot.companion_gallery_fresh', spy)

        await mail_shot_pick(girl, 343, 'someone')

        assert asked.get('group') == 'public', (
            f"жребий полез в папку {asked.get('group')!r} — оттуда снимки "
            'уходят только по просьбе')


class TestNothingBreaksTheLetter:
    """⚠ Письмо дороже снимка: ни одна помеха не отменяет его отправки."""

    async def test_an_empty_gallery_is_not_an_error(self, girl, monkeypatch):
        async def always(site_id, key, fallback):
            return 1.0

        monkeypatch.setattr('src.dating.mail_shot.percent_share', always)

        assert await mail_shot_pick(girl, 343, _uid()) == 0, (
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
