"""Дверь планировщика `POST /cronjob`: кого пускаем и что разрешаем запускать.

## ⚠⚠ Что здесь проверяется

Не «задание запустилось» — это видно и в журнале. Проверяются два запрета, и
у каждого есть цена:

| Запрет | Цена нарушения |
|--------|----------------|
| без входа — отказ | ручка исполняет код, а панель открыта на публичном домене |
| имя только из списка | «запусти что назову» это не ручка, а приглашение |

У соседа такая же дверь стоит **без входа** и принимает путь к файлу
(`cronjob_path`). Повторять это нельзя, и тесты держат различие.

⚠ Сами задания здесь не гоняются: они ходят на Диск и к соседу, то есть
наружу, а сюите наружу нельзя (`testing.md` §1.2). Запуск проверяется на
живой панели руками — это записано в `worker.md`, §3.1.
"""

import pytest

from src.cronjob.cronjob import CRONJOB_JOBS, CRONJOB_TIMEOUT_MAX, cronjob_run

pytestmark = pytest.mark.core


async def test_closed_without_login(client):
    """⚠⚠ Без входа дверь не открывается.

    У соседа она публичная, и это терпимо там, где панель не торчит наружу.
    Наша торчит: `/cronjob` без входа означал бы «исполни код, кто угодно».
    """
    answer = await client.post('/cronjob', json={'job': 'dating_chat'})

    assert answer.status_code == 401


async def test_unknown_job_is_refused_by_name(auth_client):
    """Имя не из списка — отказ, и в нём названо, что вообще бывает."""
    answer = await auth_client.post('/cronjob', json={'job': 'rm -rf /'})

    assert answer.status_code == 500
    detail = answer.json()['detail']
    assert 'не из списка' in detail
    assert 'gallery_round' in detail, 'отказ обязан подсказать, что бывает'


async def test_path_is_not_accepted_at_all(auth_client):
    """⚠⚠ Путь к файлу дверью не принимается — только имя.

    Поле `cronjob_path`, как у соседа, у нас неизвестно: pydantic его
    отбросит, а обязательного `job` не окажется — `422`. Проверяем именно
    это, потому что соблазн «принять оба» возникнет при первой же попытке
    свести расписания.
    """
    answer = await auth_client.post(
        '/cronjob', json={'cronjob_path': 'src/cronjob/gallery_round.py'})

    assert answer.status_code == 422


async def test_jobs_listed_point_at_real_files():
    """Каждое имя из списка ведёт к существующему файлу.

    ⚠ Иначе отказ приходит только в момент запуска — то есть ночью, в журнале
    Cronicle, который никто не читает. Ровно так пять месяцев и падало
    `dating_chat`.
    """
    from pathlib import Path

    for job, path in CRONJOB_JOBS.items():
        assert Path(path).is_file(), f'задание «{job}» указывает в пустоту: {path}'


async def test_missing_job_says_so_without_raising():
    """Неизвестное имя — словами в ответе, а не исключением.

    Решать, каким кодом ответить, — дело ручки; бросок спрятал бы причину в
    трейсбек, а она нужна Cronicle в теле ответа.
    """
    got = await cronjob_run('такого-нет')

    assert got['ok'] is False
    assert 'не из списка' in got['why']


async def test_timeout_is_capped():
    """⚠ Срок обрезается потолком: ошибка в единицах не должна занимать час."""
    got = await cronjob_run('такого-нет', timeout=99_999)

    # До запуска дело не дойдёт — проверяем, что потолок вообще объявлен и
    # разумен: сутки ожидания означали бы висящий процесс до перезапуска.
    assert got['ok'] is False
    assert CRONJOB_TIMEOUT_MAX <= 3600
