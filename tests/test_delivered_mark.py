"""Доставка отмечается только у реплики, которая вправду ушла.

## ⚠⚠ Зачем

Сосед пишет обмен в `companion_session` **внутри прогона** — до того, как мы
постучимся к сайту. Не отметь мы доставку отдельно, агент увидел бы в истории
свою реплику, которой собеседник не получал, и построил бы следующий ответ как
продолжение несказанного.

⚠ **Наружу сюита не ходит** (`testing.md`, §1.2): и агент, и отправка —
заглушки, проверяется то, что мы им передали.
"""

import uuid

import pytest

from mysql_.mysql_ import mysql_get_db_async

from src.dating import answer as answer_mod

pytestmark = pytest.mark.chat

STAND_SITE = 999_343


@pytest.fixture
async def girl(auth_client):
    """Анкета сюиты. Логин на `@example.com` — метка, по ней идёт уборка."""
    profile_id = int((await auth_client.post('/api/dating/profile', json={
        'site_id': STAND_SITE,
        'login': f'deliver-{uuid.uuid4().hex[:8]}@example.com',
        'password': 'x'})).json()['result'])

    yield profile_id

    async with mysql_get_db_async() as db:
        await db.execute('DELETE FROM chat_reply WHERE profile_id = %s', (profile_id,))
        await db.execute('DELETE FROM message WHERE profile_id = %s', (profile_id,))
        await db.execute('DELETE FROM profile WHERE id = %s', (profile_id,))
        await db.connection.commit()


async def _said(profile_id: int, uid: str = 'u-1') -> None:
    """Входящая реплика: без неё отвечать нечего и агент не зовётся."""
    async with mysql_get_db_async() as db:
        await db.execute(
            'INSERT INTO message (profile_id, sender_uid, sender_name, text,'
            "  message_uid, direction) VALUES (%s, %s, %s, %s, %s, 'in')",
            (int(profile_id), uid, 'Brian', 'привет',
             f'dl-{uuid.uuid4().hex[:12]}'))
        await db.connection.commit()


async def _profile(profile_id: int) -> dict:
    async with mysql_get_db_async() as db:
        await db.execute('SELECT * FROM profile WHERE id = %s', (profile_id,))

        return dict(await db.fetchone())


@pytest.fixture
def marked(monkeypatch):
    """Номера, которые панель отметила доставленными."""
    got = []

    async def _delivered(message_id):
        got.append(int(message_id))

        return True

    monkeypatch.setattr(answer_mod, 'rhapsody_api_chat_delivered', _delivered)

    return got


def _agent(monkeypatch, text: str, message_id: int):
    async def _talk(agent_id, question, variables=None):
        return {'text': text, 'message_id': message_id}

    monkeypatch.setattr(answer_mod, 'rhapsody_api_chat_talk', _talk)


def _sending(monkeypatch, ok: bool):
    async def _send(profile, presence, uid, text, **kwargs):
        return {'ok': ok, 'error': '' if ok else 'сайт отказал',
                'offline': False, 'response': '',
                'message_uid': 'u1' if ok else ''}

    monkeypatch.setattr(answer_mod, 'send_message', _send)


class TestChatMarksDelivery:
    async def test_sent_reply_is_marked(self, monkeypatch, marked, girl):
        """Ушедшая реплика помечается доставленной."""
        await _said(girl)
        _agent(monkeypatch, 'hello', 777)
        _sending(monkeypatch, ok=True)

        await answer_mod.answer_run(await _profile(girl), {}, agent_id=7)

        assert marked == [777], 'ушедшая реплика обязана быть отмечена'

    async def test_failed_send_is_not_marked(self, monkeypatch, marked, girl):
        """⚠⚠ Неудачная отправка отметки не ставит — ради этого всё и затеяно."""
        await _said(girl)
        _agent(monkeypatch, 'hello', 778)
        _sending(monkeypatch, ok=False)

        await answer_mod.answer_run(await _profile(girl), {}, agent_id=7)

        assert marked == [], 'не ушедшую реплику отмечать нельзя'

    async def test_silence_marks_nothing(self, monkeypatch, marked, girl):
        """Агент промолчал — номера нет, звать нечего."""
        await _said(girl)
        _agent(monkeypatch, '', 0)
        _sending(monkeypatch, ok=True)

        await answer_mod.answer_run(await _profile(girl), {}, agent_id=7)

        assert marked == []


class TestDraftKeepsTheNumber:
    async def test_draft_carries_the_number_to_the_next_round(self, monkeypatch, marked, girl):
        """⚠⚠ Черновик, ушедший следующим кругом, помечается своим номером.

        Собеседника не было в сети — текст лёг в пул; круг спустя он вернулся, и
        реплика ушла. Номер обмена к этому времени живёт только в строке пула —
        агента во втором заходе не спрашивали.
        """
        await _said(girl)
        _agent(monkeypatch, 'hello', 991)
        _sending(monkeypatch, ok=True)

        # Первый заход: собеседника нет в сети — черновик лёг, отправки не было.
        monkeypatch.setattr(answer_mod, 'online_check',
                            lambda site_id, uid: {'online': False})
        await answer_mod.answer_run(await _profile(girl), {}, agent_id=7)

        assert marked == [], 'неотправленное отмечать нельзя'

        # Второй заход: он в сети, агента не спрашиваем — текст уже лежит.
        async def _no_agent(*args, **kwargs):
            raise AssertionError('агента во втором заходе спрашивать не должны')

        monkeypatch.setattr(answer_mod, 'rhapsody_api_chat_talk', _no_agent)
        monkeypatch.setattr(answer_mod, 'online_check',
                            lambda site_id, uid: {'online': True})
        await answer_mod.answer_run(await _profile(girl), {}, agent_id=7)

        assert marked == [991], 'номер обязан дожить до отправки в строке пула'


@pytest.fixture
def initiative_marked(monkeypatch):
    """Номера, которые круг инициативы отметил доставленными.

    ⚠ Своя подмена, не `marked`: тот патчит `rhapsody_api_chat_delivered` в
    `answer.py` (чат), а здесь зовёт тот же сосед, но из `initiative_round.py`.
    """
    import src.dating.initiative_round as initiative_round_mod

    got = []

    async def _delivered(message_id):
        got.append(int(message_id))

        return True

    monkeypatch.setattr(initiative_round_mod, 'rhapsody_api_chat_delivered',
                        _delivered)

    return got


class TestInitiativeMarksDelivery:
    async def test_sent_card_is_marked(self, monkeypatch, initiative_marked, girl):
        """Ушедшая инициатива помечается своим номером обмена.

        ⚠ Карточку сочиняет один круг, уносит другой: номер к моменту отправки
        живёт только в строке пула.
        """
        import src.dating.initiative_round as initiative_round_mod
        from src.dating.chat_initiative import chat_initiative_put

        # ⚠ `chat_initiative_ready` берёт карточки только у анкет в сети —
        # обычная фикстура `girl` этой правкой не трогается, статус правим
        # здесь же.
        async with mysql_get_db_async() as db:
            await db.execute("UPDATE profile SET status = 'online' WHERE id = %s",
                             (girl,))
            await db.connection.commit()

        await chat_initiative_put(STAND_SITE, girl, 'u-1', 'Brian', 'текст',
                                  talk_message_id=771)

        async def _send_ok(profile, presence, uid, text, **kwargs):
            return {'ok': True, 'error': ''}

        monkeypatch.setattr(initiative_round_mod, 'online_check',
                            lambda site_id, uid: {'online': True})
        monkeypatch.setattr(initiative_round_mod, 'send_message', _send_ok)

        sent = await initiative_round_mod._send(STAND_SITE)

        assert sent == 1, 'реплика обязана уйти'
        assert initiative_marked == [771], \
            'ушедшая инициатива обязана быть отмечена своим номером обмена'
