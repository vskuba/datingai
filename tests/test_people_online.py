"""«В сети» в списках собеседников берётся из ленты, а не из самих списков.

⚠ **Поймано заказчиком на живом проде.** В виджете «Мои фавориты» Semen Gorbunkov
светился серым, а в ленте «Все онлайн» — зелёным, в ту же минуту. Оба списка были
по-своему правы: снимок избранных сняли часы назад (`favorites_keep`), и `online` в
нём означал «был ли он в сети **в момент съёмки**».

Проверено на проде до правки, вот та самая строка:

    181685 Semen Gorbunkov | в списке: False | по ленте: True known True

Ответ на «кто в сети прямо сейчас» в панели один — снимок ленты
(`dating_online.py`): по нему решает отправка, по нему светятся точки в диалогах.
Теперь по нему же светятся карточки любого списка.
"""

import pytest

from src.dating import dating_online
from src.uvicorn.routers.dating.dating_people import _online_now

pytestmark = pytest.mark.core

SITE = 343

# Как приходит запись списка: `online` в ней — слово самого сайта на момент съёмки.
HIM = {'uid': '181685', 'name': 'Semen Gorbunkov', 'online': False}
HER_PICK = {'uid': '763268', 'name': 'Tony', 'online': True}


@pytest.fixture
def feed(monkeypatch):
    """Подменяет ленту: тест сам говорит, кого она знает и кто в сети."""
    state = {}

    def check(site_id, uid):
        return state.get(str(uid), {'online': False, 'known': False, 'age_sec': -1})

    monkeypatch.setattr(dating_online, 'dating_online_check', check)

    return state


class TestTheFeedDecides:
    """Свежая лента важнее того, что сказал список при съёмке."""

    def test_the_stale_offline_lights_up(self, feed):
        """⚠ Та самая жалоба: в избранных серый, в ленте зелёный."""
        feed['181685'] = {'online': True, 'known': True}

        assert _online_now([HIM], SITE, 'favorite')[0]['online'] is True

    def test_the_stale_online_goes_out(self, feed):
        """И обратно: «был в сети два часа назад» — не «в сети»."""
        feed['763268'] = {'online': False, 'known': True}

        assert _online_now([HER_PICK], SITE, 'favorite')[0]['online'] is False

    def test_the_record_itself_is_not_touched(self, feed):
        """Правим копию: снимок в кэше остаётся тем, что сняли с сайта."""
        feed['181685'] = {'online': True, 'known': True}
        _online_now([HIM], SITE, 'favorite')

        assert HIM['online'] is False, 'запись снимка изменили на месте'

    def test_other_fields_survive(self, feed):
        feed['181685'] = {'online': True, 'known': True}
        got = _online_now([HIM], SITE, 'favorite')[0]

        assert got['name'] == 'Semen Gorbunkov' and got['uid'] == '181685'


class TestThePageKeepsUpWithTheFeed:
    """⚠⚠ Сервер проставляет «в сети» **в момент выдачи списка**, и этого мало.

    Избранных снимают раз в несколько часов (`favorites_keep`), ленту — каждые
    пару минут. Между выдачами точка снова отстаёт от ленты, и человек видит серый
    кружок у того, кто минуту назад зашёл. Страница догоняет ленту сама
    (`stripDotsPaint`), не дожидаясь новой съёмки.

    ⚠ Проверяем по исходному тексту страницы: сюита браузера не имеет, а поведение
    это её, а не сервера.
    """

    async def test_the_page_repaints_dots_after_the_feed(self, auth_client):
        page = (await auth_client.get('/admin/dating/profile')).text

        assert 'function stripDotsPaint' in page, \
            'точки лент больше не догоняют ленту онлайна'
        assert 'stripDotsPaint();' in page, 'перекраску никто не зовёт'

    async def test_the_dot_carries_its_own_tooltip(self, auth_client):
        """⚠ Подпись — **у точки**, а не у карточки: так её обновляют вместе с
        цветом. Пока статус жил в подписи карточки, обновить его значило собрать
        подпись заново из имени и возраста, которых у точки нет."""
        page = (await auth_client.get('/admin/dating/profile')).text

        assert "title=\"${p.online ? 'в сети' : 'не в сети'}\"" in page, \
            'у точки пропала своя подпись — она разойдётся с цветом'

    async def test_the_feed_strip_is_left_alone(self, auth_client):
        """Лента онлайна сама и есть ответ: перекрашивать её по себе незачем."""
        page = (await auth_client.get('/admin/dating/profile')).text
        start = page.index('function stripDotsPaint')
        block = page[start:start + 900]

        assert "dataset.strip === 'online'" in block


class TestWhenWeDoNotKnow:
    """⚠ «Не знаем» — не повод гасить точку."""

    def test_an_unknown_person_keeps_what_the_list_said(self, feed):
        """Ленты нет вовсе или она протухла: соврать «офлайн» там, где мы просто
        не смотрели, хуже устаревшей правды."""
        got = _online_now([HIM, HER_PICK], SITE, 'favorite')

        assert [one['online'] for one in got] == [False, True]

    def test_no_site_means_no_stamping(self, feed):
        feed['181685'] = {'online': True, 'known': True}

        assert _online_now([HIM], 0, 'favorite')[0]['online'] is False

    def test_the_feed_itself_is_left_alone(self, feed):
        """⚠ Принадлежность ленте и означает «в сети» — спрашивать её о себе незачем.

        Заодно это защита от круга: лента снимается по сайту, и опрос её же
        снимка на каждую запись был бы работой ни за чем.
        """
        feed['181685'] = {'online': False, 'known': True}

        assert _online_now([{**HIM, 'online': True}], SITE, 'online')[0]['online'] is True

    def test_an_empty_list_is_returned_as_is(self, feed):
        assert _online_now([], SITE, 'favorite') == []
