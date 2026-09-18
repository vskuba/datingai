"""Анкета собеседника снимается обычным GET, а не браузером.

## ⚠⚠ Зачем переехали

Сценарий «анкета мужчины: карточка» — 16 шагов, из них девять это **вход заново**,
включая `wait_ms 12000` после кнопки. На проде он давал 12,4 секунды в среднем и
**143 неудачи из 286** — половина прогонов сгорала впустую. Отсюда и лимит «пять
человек за заход», из-за которого за неделю обошли 143 карточки из 376.

Сессия же у анкеты **уже есть**: cookie лежат в `storage_state`, присутствие ходит
с ними каждую минуту. Один GET с ними — доли секунды, и лимит стал не нужен.

Живая проверка на проде показала заодно, что GET отдаёт **больше**: сценарий
приносил обрезанное «I am Honest, dependable, loyal,…», а в HTML лежит вся фраза,
плюс поля, до которых браузер не добирался.

⚠ **Наружу сюита не ходит** (`testing.md`): разбор проверяется на куске живой
разметки, снятом однажды, — придумать такую вёрстку нельзя, её можно только
увидеть.
"""

import pytest

from src.dating.companion_about import (COMPANION_ABOUT_EVERY_DEFAULT,
                                        companion_about_head,
                                        companion_about_parse)
from src.dating.site.site_about import DatingSiteAbout, DatingSiteAboutLadadate
from src.dating.site.site_registry import site_registry_about

pytestmark = pytest.mark.mail

# Кусок страницы LadaDate — **снят с живой анкеты**, а не выдуман. Числовые
# сущности, кнопка «More» после описания и подписи кнопок строчными — всё это
# приметы, каждая из которых однажды сломала разбор.
PAGE = '''<html><body>
<div class="top__profle-wr">
  <h1>Edison Gustavo Sol&#237;s Villacis</h1><span>, 66</span>
  <span class="new">new</span><span>Live 3</span>
  <a>Send message</a><a>Start live chat</a><a>Add to Favorites</a>
</div>
<div class="user__profile-info">
  <div class="row"><span>Id:</span><span>706079</span></div>
  <div class="row"><span>Location</span><span>Ambato, Ecuador</span></div>
  <div class="row"><span>Occupation</span><span>driver</span></div>
  <div class="row"><span>About Myself</span>
    <p>Es dif&#237;cil pero har&#233; el mejor esfuerzo.</p><a>More</a></div>
</div>
</body></html>'''


class TestBlocks:
    """Вырезание блоков по классу."""

    def test_both_blocks_come_out(self):
        got = DatingSiteAbout().parse(PAGE)

        assert 'Occupation' in got['анкета']
        assert 'Villacis' in got['шапка']

    def test_a_missing_block_is_empty_not_a_crash(self):
        """Блока нет — пустая строка: сайт сменил вёрстку либо отдал форму входа."""
        assert DatingSiteAbout().parse('<html><body>ничего</body></html>') \
            == {'анкета': '', 'шапка': ''}

    def test_nesting_is_counted_not_guessed(self):
        """⚠⚠ Блок берётся по балансу тегов, а не «до конца строки».

        Первая версия резала полторы тысячи знаков наугад и обрывала описание на
        полуслове — на живой странице это давало «Id: 102327 Location limoges,
        France» и всё.
        """
        got = DatingSiteAbout().block(PAGE, 'user__profile-info')

        assert 'About Myself' in got, 'вложенные блоки потерялись'
        assert 'Villacis' not in got, 'захватили соседний блок'


class TestText:
    """Что становится текстом."""

    def test_entities_unfold(self):
        """⚠ Числовые сущности тоже: `&#237;` → `í`. Браузер показывал знаками."""
        got = DatingSiteAbout().text('<p>Es dif&#237;cil &amp; har&#233;</p>')

        assert got == 'Es difícil & haré'

    def test_tags_become_spaces(self):
        """⚠ Без пробела `<p>Occupation</p><p>plumber</p>` слипается в одно слово,
        и разбор по подписям не находит ничего."""
        got = DatingSiteAbout().text('<p>Occupation</p><p>plumber</p>')

        assert 'Occupation plumber' in got

    def test_the_more_button_is_dropped(self):
        """⚠⚠ «More» стоит сразу за описанием и уехало бы агенту, а оттуда в письмо."""
        got = DatingSiteAbout().text('<p>good sense of humor.</p><a>More</a>')

        assert got == 'good sense of humor.'

    def test_more_inside_a_phrase_survives(self):
        """⚠ «More than anything» — слова человека, а не кнопка."""
        assert DatingSiteAbout().text(
            '<p>More than anything I want peace</p>') \
            == 'More than anything I want peace'


class TestHeader:
    """Шапка: имя и возраст."""

    def test_name_and_age(self):
        name, age = companion_about_head(DatingSiteAbout().parse(PAGE)['шапка'])

        assert (name, age) == ('Edison Gustavo Solís Villacis', 66)

    def test_button_labels_are_cut_in_any_case(self):
        """⚠⚠ Браузер отдавал подписи заглавными — их так рисует CSS
        (`text-transform`), — а в HTML они как есть: «Send message».

        Пока резали только по заглавным, имя приходило с хвостом «, 59 new Live 3
        Send message Start live chat Add to Favorites».
        """
        name, age = companion_about_head(
            'Donald Rhodes , 59 new Live 3 Send message Add to Favorites')

        assert (name, age) == ('Donald Rhodes', 59)


class TestFields:
    """Поля анкеты — разбор общий с прежним, сценарным."""

    def test_the_same_parser_reads_http_text(self):
        fields = companion_about_parse(DatingSiteAbout().parse(PAGE)['анкета'])

        assert fields.get('Id:') == '706079'
        assert fields.get('Occupation') == 'driver'
        assert 'Es difícil' in str(fields.get('About Myself'))


class TestDriverRegistry:
    """⚠ Разбор — драйвером по сайту: вёрстка у каждого своя."""

    def test_a_named_site_gets_its_driver(self):
        assert site_registry_about({'driver': 'ladadate'}).name \
            == DatingSiteAboutLadadate.name

    def test_an_unknown_site_falls_back_to_base(self):
        """⚠ Умолчание — базовый драйвер: он работает у всякого сайта с
        постоянными классами, и падать в чужой протокол незачем."""
        assert site_registry_about({'driver': 'нет-такого'}).name == 'base'
        assert site_registry_about({}).name == 'base'
        assert site_registry_about(None).name == 'base'

    def test_settings_win_over_the_class(self):
        """Сайту с другой вёрсткой класса не нужно — хватит `presence.about`."""
        driver = site_registry_about({'about': {
            'path': '/men/{uid}',
            'blocks': {'анкета': 'x-info'},
            'drop': ['Показать ещё']}})

        assert driver.url('https://site.tld/', '77') == 'https://site.tld/men/77'
        assert driver.blocks == {'анкета': 'x-info'}
        assert driver.text('<p>текст</p><a>Показать ещё</a>') == 'текст'


class TestWhenWeGo:
    """⚠⚠ Когда идём на сайт — правило одно, и живёт оно здесь."""

    async def test_a_new_person_is_always_taken(self, monkeypatch):
        from src.dating import companion_about

        assert await companion_about.companion_about_due(1, {}) is True

    async def test_a_never_asked_card_is_taken(self):
        from src.dating.companion_about import companion_about_due

        assert await companion_about_due(1, {'about_state': 'none'}) is True

    async def test_a_failed_one_is_not_repeated(self):
        """⚠ Причина отказа обычно не рассасывается сама, а поход не бесплатный."""
        from src.dating.companion_about import companion_about_due

        assert await companion_about_due(1, {'about_state': 'fail'}) is False

    @pytest.mark.parametrize('state', ['done', 'empty'])
    async def test_a_fresh_one_waits(self, state, monkeypatch):
        """⚠ `empty` ждёт наравне с `done`: «ничего не написал» — состояние дня."""
        from datetime_.datetime_ import datetime_utc_now

        from src.dating import companion_about

        monkeypatch.setattr(companion_about, 'COMPANION_ABOUT_EVERY_DEFAULT',
                            COMPANION_ABOUT_EVERY_DEFAULT)

        assert await companion_about.companion_about_due(
            999_343, {'about_state': state, 'about_at': datetime_utc_now()}) is False

    async def test_an_old_one_is_refreshed(self):
        """Срок вышел — идём снова: человек мог заполнить анкету."""
        from datetime import timedelta

        from datetime_.datetime_ import datetime_utc_now

        from src.dating.companion_about import companion_about_due

        long_ago = datetime_utc_now() - timedelta(days=60)

        assert await companion_about_due(
            999_343, {'about_state': 'done', 'about_at': long_ago}) is True


class TestTheScenarioStaysAsBackup:
    """⚠ Браузер остаётся запасным: войти заново умеет только он."""

    def test_http_is_tried_first(self):
        import inspect

        from src.dating.companion_about import companion_about_collect

        code = inspect.getsource(companion_about_collect)
        http = code.index('companion_about_http(')
        scenario = code.index('companion_about_fetch(')

        assert http < scenario, 'сценарий снова идёт первым — это 12 секунд на человека'

    def test_the_scenario_is_still_reachable(self):
        import inspect

        from src.dating.companion_about import companion_about_collect

        assert 'companion_about_fetch(' in inspect.getsource(companion_about_collect), \
            'запасной путь пропал — протухшую сессию будет нечем починить'
