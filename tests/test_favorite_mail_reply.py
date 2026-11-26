"""Ответ руками под разговором: `POST /api/dating/favorite-mail/reply`.

Форма в «Фаворитах → Диалоги» кладёт письмо приставания и уносит его сразу.
Решение Хозяина от 26.11.2026: **не ушло — не потерялось**, письмо остаётся в
пуле с причиной; здесь это решение и стережено.

## Что здесь стережётся

* ⚠⚠ **одна дверь с остальными**: ручка зовёт те же `favorite_mail_manual` и
  `favorite_mail_send`, что форма «Письмо руками» и кнопка «отправить сейчас»
  на «Приставании»; своей проверки и своей отправки у неё нет и быть не может —
  иначе кнопка человека и кнопка под разговором расходились бы в правилах;
* успех — `{'status': 'sent'}`, строка пула `sent`, письмо дошло до сайта;
* ⚠⚠ **отказ сайта — не 409 и не потерянное письмо**: ответ
  `{'status': 'draft', 'error': <причина>}`, строка остаётся `draft` с причиной.
  Человек видит слова «не ушло сразу» и ту же карточку в пулу, где можно
  отправить снова; черновик пережидал бы конец смены (`pool._keep`) даже без
  его глаз;
* отказ по адресату — **сквозной 409**: письмо не легло вовсе, в пуле пусто,
  поле страницы сохраняет набранное — причину правят, а не набирают заново;
* анкета письма — **анкета пары**, а не та, что выбрана в списке слева.
"""

import uuid

import pytest
from fastapi import HTTPException

from tests.conftest import TEST_STAND_SITE
from tests.site_api_fake import SITE_API_FAKE_BASE, SITE_API_FAKE_PASSWORD

pytestmark = pytest.mark.mail


async def _sql(text: str, args: tuple = ()):
    from mysql_.mysql_ import mysql_get_db_async

    async with mysql_get_db_async() as db:
        await db.execute(text, tuple(args))
        rows = await db.fetchall()
        await db.connection.commit()

    return [dict(row) for row in (rows or [])]


async def _star(girl: int, uid: str) -> None:
    await _sql('INSERT INTO favorite (site_id, profile_id, uid, state)'
               " VALUES (%s, %s, %s, 'done')", (TEST_STAND_SITE, girl, uid))


@pytest.fixture
async def online_girl(girl_make):
    girl = await girl_make(online=True)
    yield girl
    await _sql('DELETE FROM favorite WHERE profile_id = %s', (girl,))


@pytest.fixture
def site_door(monkeypatch):
    """Сайт с адресом и паролем: отправка идёт ими, как в панели.

    ⚠ Подменяем `site_setting` **в модуле-ручке**: `favorite_mail_send` берёт
    адрес и пароль от того `site_setting`, что лежит в его модуле, — подмена
    же в `src.dating.site_setting` мимо него прошла бы.
    """
    from src.uvicorn.routers.dating import favorite_mail as mail_router

    async def fake_setting(site_id: int, key: str, default: str = '') -> str:
        return {'api_base': SITE_API_FAKE_BASE,
                'api_password': SITE_API_FAKE_PASSWORD}.get(key, default)

    monkeypatch.setattr(mail_router, 'site_setting', fake_setting)


async def _reply(girl: int, uid: str, body: str, shot: int = 0):
    """Зовёт ручку напрямую, как позвал бы FastAPI после проверки прав.

    ⚠ `user=None` — по образцу соседей (`profile_view_messages`): ручка
    пользователя не читает, всю её защиту держат `auth_required` на пути и
    проверки `favorite_mail_manual`. Живого HTTP до сайта тесты не делают:
    он под запретом, а сайт отвечает заглушкой.
    """
    from src.uvicorn.routers.dating.favorite_mail import (
        DatingFavoriteMailReply, favorite_mail_reply)

    return await favorite_mail_reply(
        DatingFavoriteMailReply(profile_id=int(girl), uid=uid, body=body,
                                gallery_file_id=int(shot)), None)


async def test_reply_goes_at_once(online_girl, site_door, site_api):
    uid = f'man-{uuid.uuid4().hex[:8]}'
    await _star(online_girl, uid)

    got = await _reply(online_girl, uid, ' Hello, I miss you ')

    assert got['status'] == 'sent'
    assert got['error'] == ''

    rows = await _sql('SELECT status, profile_id, uid, body, error'
                      ' FROM favorite_mail WHERE id = %s', (got['mail_id'],))
    assert rows and rows[0]['status'] == 'sent', 'письмо не стало ушедшим'
    assert rows[0]['profile_id'] == online_girl
    assert rows[0]['uid'] == uid
    assert rows[0]['body'] == 'Hello, I miss you', 'пробелы не срезаны'
    assert not rows[0]['error'], 'ушедшему письмо причину не пишут'

    assert len(site_api.sent) == 1, 'письмо до сайта не дошло'
    sent_uid, sent_body, _ = site_api.sent[0]
    assert sent_uid == uid and sent_body == 'Hello, I miss you'


async def test_refusal_leaves_draft_with_reason(online_girl, site_door, site_api):
    """⚠⚠ Сайт не даёт писать — **не 409**: слова человека живы в пулу."""
    uid = f'man-{uuid.uuid4().hex[:8]}'
    await _star(online_girl, uid)
    site_api.forbid_send(uid)

    got = await _reply(online_girl, uid, 'My dear, are you there?')

    assert got['status'] == 'draft'
    assert got['error'], 'без причины человек не поймёт, что делать дальше'

    rows = await _sql('SELECT status, error FROM favorite_mail WHERE id = %s',
                      (got['mail_id'],))
    assert rows and rows[0]['status'] == 'draft', 'черновик не должен умирать'
    assert rows[0]['error'], 'причина обязана лежать на карточке пула'

    assert not site_api.sent, 'письмо ушло, хотя разрешения не было'


async def test_not_favorite_refuses_wholly(online_girl, site_door):
    """Не его избранный — 409 как есть: не легло, значит нечего и слать."""
    uid = f'stranger-{uuid.uuid4().hex[:8]}'

    with pytest.raises(HTTPException) as e:
        await _reply(online_girl, uid, 'Hey, you!')

    assert e.value.status_code == 409
    assert uid in str(e.value.detail), 'отказ обязан назвать адресата'

    rows = await _sql('SELECT id FROM favorite_mail WHERE profile_id = %s'
                      ' AND uid = %s', (online_girl, uid))
    assert not rows, 'отказанное письмо не должно лежать в пуле'
