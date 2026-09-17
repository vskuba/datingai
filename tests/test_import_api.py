"""Ручки импорта: права, отказы и признак готовности.

Сам импорт сюита **не гоняет**: он водит браузер соседа, ходит на живой сайт
знакомств под учёткой агентства и заводит анкеты в базу. Это то же правило, по
которому не трогают `/login` и `/check` (`testing.md`, §2.2) — минуты работы и
чужие данные.

Проверяется то, что можно проверить без сайта: закрыта ли ручка без входа, что
отвечает признак готовности и как ручка отказывает на неверном вводе. Правила
слияния лежат отдельно и проверяются начисто — `test_import_merge.py`.

⚠ **Сосед здесь поддельный** (`rhapsody_fake.py`). Прежде эти тесты спрашивали
живого и опирались на то, что у сайта №19 «сценария импорта заведомо нет», — то
есть красили бы сюиту в красный в день, когда сценарий там заведут. Теперь «есть
сценарий» и «нет сценария» задаются в самом тесте, и обе ветки проверяются на
одном и том же сайте.
"""

import pytest
from fastapi import HTTPException

from src.uvicorn.routers.dating.import_ import (import_profile,
                                                      import_ready)

pytestmark = pytest.mark.profile

# Сайт, который есть у поддельного соседа, и сценарий импорта у него заведён.
SITE_WITH_IMPORT = 343

# Сайт, который у соседа есть, а сценария импорта у него нет.
SITE_WITHOUT_IMPORT = 19

# Номера, которого нет в закладках вовсе.
SITE_MISSING = 999_999


class TestImportReady:
    """Признак «импорт настроен» — им страница решает, гасить ли кнопку."""

    async def test_site_with_scenario_is_ready(self, rhapsody):
        """Признак берётся у соседа, а не из своей настройки: сценарий заводят там,
        и своя галочка разъехалась бы с ним в день переименования."""
        result = (await import_ready(SITE_WITH_IMPORT, user=None))['result']

        assert result['ready'] is True, result

    async def test_no_site_is_not_ready(self, rhapsody):
        """Сайт не выбран — «не готово» с причиной, а не отказ ручки: страница
        открывается до всякого выбора, и 4xx на ровном месте выглядел бы поломкой.
        """
        result = (await import_ready(user=None))['result']

        assert result['ready'] is False
        assert result['why']
        assert not rhapsody.calls, 'к соседу пошли, не зная сайта'

    async def test_site_without_scenario_is_not_ready(self, rhapsody):
        """У сайта нет сценария импорта — кнопка гаснет, и причина сказана словами."""
        result = (await import_ready(SITE_WITHOUT_IMPORT, user=None))['result']

        assert result['ready'] is False
        assert 'сценар' in result['why'].lower(), result['why']

    async def test_unknown_site_is_not_ready(self, rhapsody):
        """Несуществующий сайт — тоже «не готово», а не 502: у соседа его просто
        нет, и это ответ, а не сбой связи."""
        result = (await import_ready(SITE_MISSING, user=None))['result']

        assert result['ready'] is False

    async def test_busy_neighbour_is_not_ready_either(self, rhapsody):
        """⚠ Отказ соседа — не «не настроено», но кнопку гасит тоже.

        Подсказка при этом другая, и человеку важна разница: чинить ему настройку
        или ждать соседа. С живым соседом этот случай не проверить — занятым пул
        бывает не по расписанию.
        """
        rhapsody.refuse(409, 'предел сессий исчерпан: 25')

        result = (await import_ready(SITE_WITH_IMPORT, user=None))['result']

        assert result['ready'] is False
        assert 'браузер соседа занят' in result['why'], result['why']


class TestImportProfile:
    """Сам импорт — только отказы: удачный путь водит браузер и трогает живой сайт."""

    async def test_no_site_is_422(self, rhapsody):
        with pytest.raises(HTTPException) as refusal:
            await import_profile(user=None)

        assert refusal.value.status_code == 422

    async def test_site_without_scenario_is_422(self, rhapsody):
        """Нет сценария — 422 с тем, что делать человеку, а не 500.

        Сайт выбран правильный, просто импортировать его пока нечем; чинить это в
        соседней панели, и текст отказа должен так и говорить.
        """
        with pytest.raises(HTTPException) as refusal:
            await import_profile(SITE_WITHOUT_IMPORT, user=None)

        assert refusal.value.status_code == 422
        assert 'сценар' in str(refusal.value.detail).lower()


class TestImportRoute:
    """То, что решается до входа в обработчик, — по HTTP, как и вся сюита."""

    async def test_ready_closed_without_session(self, client):
        """Без входа не пускает: настройка чужого сайта — не общее сведение."""
        assert (await client.get('/api/dating/import/ready?site_id=1')).status_code == 401

    async def test_profile_closed_without_session(self, client):
        answer = await client.post('/api/dating/import/profile?site_id=1', json={})

        assert answer.status_code == 401
