"""Дверь планировщика: `POST /cronjob` — запустить задание по имени.

Cronicle живёт у соседа и умеет только сходить по HTTP. Раньше он и ходил —
**в ручку соседа**, прося исполнить файл из нашего репозитория. У соседа этого
файла нет и быть не может, поэтому задание `dating_chat` падало каждые пять
минут с `400 Файл src/cronjob/dating_chat.py не найден` — и падало незаметно,
потому что смотреть в журнал Cronicle некому.

Теперь дверь своя: он стучит к нам, и мы запускаем своё.

⚠⚠ **Ручка закрыта токеном, в отличие от такой же у соседа.** Там она в списке
публичных и исполняет файл по присланному пути. Наша панель открыта на
публичном домене, и «исполни что назову, без входа» — это не ручка, а дыра.
Cronicle шлёт заголовок (`urlplug` их умеет), а имя задания сверяется с
закрытым списком `CRONJOB_JOBS`.

⚠ **Код ответа — не только вежливость.** Cronicle помечает прогон неудачным
по коду, и `200` на упавшем задании означал бы зелёную ленту при мёртвой
работе. Поэтому отказ отдаётся `500`, а причина — текстом в теле: её он
кладёт в журнал прогона, и это единственное место, где человек её прочтёт.
"""

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from src.auth.auth import AuthUser, auth_required
from src.cronjob.cronjob import CRONJOB_TIMEOUT, cronjob_run

router = APIRouter(tags=['cronjob'])


class CronjobRun(BaseModel):
    """Что запустить. ⚠ Имя, а не путь: путь означал бы «исполни что назову»."""

    job: str = Field(..., min_length=1, max_length=64)
    timeout: int = Field(CRONJOB_TIMEOUT, ge=1, le=3600)


@router.post('/cronjob', include_in_schema=False)
async def cronjob_start(payload: CronjobRun, user: AuthUser = Depends(auth_required)):
    """Запустить задание планировщика и дождаться его.

    Отвечает `200` с тем, что задание напечатало, либо `500` с причиной —
    по коду Cronicle и красит прогон.
    """
    got = await cronjob_run(payload.job, payload.timeout)

    if not got['ok']:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                            detail=got['why'])

    return {'result': got}
