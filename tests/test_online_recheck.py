"""Статус анкеты перечитывается перед самим походом, а не только при отборе.

## ⚠⚠ Живая беда: список устаревает за время обхода

Круги отбирают анкеты запросом `status = 'online'`, а потом обходят их по одной
**с паузой** — сайт считает частые заходы подозрением, и заходы разведены
намеренно. Между отбором и походом проходят секунды, и снимок успевает устареть:

    20:00:41  анкета 43 ушла в офлайн — смена кончилась
    20:00:43  GET /messages под ней    ← через две секунды
    20:00:42  анкета 46 ушла в офлайн
    20:00:46  GET /messages под ней    ← через четыре секунды

Обход начался, когда обе были в сети. Смена кончилась на второй секунде, и круг
доработал по снимку, снятому до неё.

⚠ **Вреда в тот раз не вышло** — письма забирались по живому токену, входа не
было, счётчик неудач сайта не пополнился. Но правило «под спящей анкетой наружу
не ходим» держится не на везении: окажись токен протухшим, круг пошёл бы входить
под спящей — ровно то, чем мы однажды заработали суточную блокировку.
"""

import uuid

import pytest
from mysql_.mysql_ import mysql_get_db_async

pytestmark = pytest.mark.profile

# Сайт стенда — тот же, что у соседних сюит.
STAND_SITE = 999_343


@pytest.fixture
async def girl(auth_client):
    """Анкета стенда в сети — её статус и двигают проверки ниже."""
    profile_id = int((await auth_client.post('/api/dating/profile', json={
        'site_id': STAND_SITE,
        'login': f'chk-{uuid.uuid4().hex[:8]}@example.com',
        'password': 'x'})).json()['result'])

    await _status(profile_id, 'online')

    yield profile_id

    async with mysql_get_db_async() as db:
        await db.execute('DELETE FROM dating_profile WHERE id = %s', (profile_id,))
        await db.connection.commit()


class TestTheCheckItself:
    """Вопрос «всё ещё в сети?» — к своей базе и по одной анкете."""

    async def test_an_online_profile_passes(self, girl):
        from src.dating.dating_ready import dating_ready_online

        await _status(girl, 'online')

        assert await dating_ready_online(girl) is True

    async def test_a_profile_gone_offline_is_stopped(self, girl):
        from src.dating.dating_ready import dating_ready_online

        await _status(girl, 'offline')

        assert await dating_ready_online(girl) is False

    async def test_a_disabled_profile_is_stopped_too(self, girl):
        """⚠ Выключенную анкету не ведут вовсе — статус тут ни при чём."""
        from src.dating.dating_ready import dating_ready_online

        await _status(girl, 'online', enabled=0)
        try:
            assert await dating_ready_online(girl) is False
        finally:
            await _status(girl, 'online', enabled=1)

    async def test_an_unknown_profile_is_stopped(self):
        """Анкеты нет — идти не под кем."""
        from src.dating.dating_ready import dating_ready_online

        assert await dating_ready_online(999_999_999) is False

    async def test_a_silent_database_reads_as_online(self, monkeypatch):
        """⚠⚠ Молчание базы читается как «в сети», а не как «стой».

        Ошибка чтения не повод отменять работу: круг и так обходит анкеты раз в
        две минуты, а лишний пропуск тише, но вреднее — письма не заберутся, и
        понять почему будет нечем.
        """
        from src.dating import dating_ready

        def _broken():
            raise RuntimeError('база отвалилась')

        monkeypatch.setattr('mysql_.mysql_.mysql_get_db_async', _broken)

        assert await dating_ready.dating_ready_online(1) is True


class TestRoundsAskBeforeGoing:
    """⚠ Спрашивают те круги, у которых между отбором и походом есть пауза."""

    def test_the_mail_round_asks(self):
        import inspect

        from src.dating import dating_mail_in_round

        code = inspect.getsource(dating_mail_in_round.dating_mail_in_round_run)

        assert 'dating_ready_online(' in code, \
            'круг писем снова ходит по устаревшему списку'
        assert code.index('sleep(') < code.index('dating_ready_online('), \
            'проверка стоит до паузы — тогда она проверяет то же, что и отбор'

    def test_the_favorites_round_asks(self):
        """Звёздочка — это вход под девушкой, и заходы так же разведены паузой."""
        import inspect

        from src.dating import dating_favorite_round

        assert 'dating_ready_online(' in inspect.getsource(
            dating_favorite_round.dating_favorite_round_run)

    def test_the_check_goes_to_our_own_base(self):
        """⚠ Запрос к **своей** базе: он не идёт наружу и сайтом не считается.

        Иначе лекарство было бы хуже болезни — проверка «не стучимся ли мы
        лишний раз» сама стала бы лишним стуком.
        """
        import inspect

        from src.dating.dating_ready import dating_ready_online

        code = inspect.getsource(dating_ready_online)

        assert 'FROM dating_profile' in code
        assert 'httpx' not in code and 'rhapsody' not in code


async def _status(profile_id: int, status: str, enabled: int = 1) -> None:
    async with mysql_get_db_async() as db:
        await db.execute('UPDATE dating_profile SET status = %s, enabled = %s'
                         ' WHERE id = %s', (status, int(enabled), int(profile_id)))
        await db.connection.commit()
