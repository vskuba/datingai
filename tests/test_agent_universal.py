"""Универсальный агент девушки: один на все анкеты, имя приходит переменной.

⚠ **Смена подхода у соседа.** Прежде у каждой анкеты был свой агент
(`natalia_1430119`) со своей копией того же промпта: тринадцать анкет —
тринадцать записей, и правка поведения требовала обойти их все. Теперь отвечает
`dating_girl_universal` («Девушка»), а кто именно говорит — задаётся переменными
вызова.

⚠ **Две переменные обязательны, и вторая опаснее первой.** `girl_name`
подставляется в промпт; без неё все девушки назовутся значением по умолчанию у
агента. `girl_id` — ключ фактов (`document_id` в Qdrant); без него факты всех
анкет лягут в один документ, и Diana начнёт отвечать историями Natalia.

Сосед здесь поддельный: сюита не ходит в чужой проект. Живьём проверено на
Ladadate — один агент, два вызова: «I'm diana» и «i'm ekaterina».
"""

import pytest

from src.dating.dating_agent import (DATING_AGENT_SETTING, DATING_AGENT_UNIVERSAL,
                                     dating_agent_id, dating_agent_vars)

pytestmark = pytest.mark.profile

SITE = 999_784


class TestVars:
    """Чем агент узнаёт, кто он сейчас."""

    def test_name_comes_from_the_card(self):
        got = dating_agent_vars({'login': 'diana9021@mail.test'}, {'name': 'Diana'})

        assert got['girl_name'] == 'Diana'

    def test_no_name_leaves_the_login(self):
        """⚠ Пустое имя не значит пустую подстановку.

        Уйди в промпт пустота — агент назвался бы никак, а собеседник получил бы
        «You are a girl .». Логин хуже читается, но он есть всегда.
        """
        got = dating_agent_vars({'login': 'diana9021@mail.test'}, {})

        assert got['girl_name'] == 'diana9021@mail.test'

    def test_fact_key_is_built_from_login_not_name(self):
        """⚠ Ключ фактов — по логину, а не по имени.

        Имена на сайте повторяются десятками: три Natalia в списке обычное дело, и
        общий ключ склеил бы их факты в один документ. Логин уникален.
        """
        first = dating_agent_vars({'login': 'natalia1@mail.test'}, {'name': 'Natalia'})
        second = dating_agent_vars({'login': 'natalia2@mail.test'}, {'name': 'Natalia'})

        assert first['girl_id'] != second['girl_id']

    def test_fact_key_matches_the_old_personal_agent(self):
        """⚠ Ключ совпадает с именем прежнего персонального агента, и намеренно.

        У тех в `workflow_variable` лежало `{"girl_id": "diana_9021"}` — то же
        самое, что строит `dating_agent_name`. Совпадение переносит накопленные
        факты на общего агента: разойдись ключи, анкета начала бы с чистого листа,
        и её история осталась бы лежать мёртвой.
        """
        got = dating_agent_vars({'login': 'bogiladiana9021@bigmir.net'},
                                {'name': 'Diana'})

        assert got['girl_id'] == 'diana_9021'


class TestWhichAgentAnswers:
    """Кого зовём отвечать."""

    async def test_universal_agent_is_taken_first(self, rhapsody):
        """Все анкеты ходят к одному агенту — ради этого всё и затевалось."""
        rhapsody.agents = [{'id': 1663, 'name': DATING_AGENT_UNIVERSAL}]

        got = await dating_agent_id({'id': 1, 'site_id': SITE, 'login': 'a@b.test'},
                                    {'name': 'Diana'})

        assert got == 1663

    async def test_personal_agent_is_the_fallback(self, rhapsody):
        """⚠ Прежний агент анкеты остаётся запасным путём.

        Агенты, заведённые до перехода, живы и со своими фактами. Молча перестать
        их звать значило бы потерять историю без единого следа — а универсального
        у соседа может не оказаться вовсе (новая установка, откат).
        """
        rhapsody.agents = [{'id': 726, 'name': 'diana_9021'}]

        got = await dating_agent_id(
            {'id': 1, 'site_id': SITE, 'login': 'bogiladiana9021@bigmir.net'},
            {'name': 'Diana'})

        assert got == 726

    async def test_nobody_found_is_zero_not_a_crash(self, rhapsody):
        """Ни того ни другого — ноль. Круг автоответа пропустит анкету и скажет
        об этом в журнал; падать здесь нельзя, обход идёт по всем анкетам."""
        rhapsody.agents = []

        got = await dating_agent_id({'id': 1, 'site_id': SITE, 'login': 'a@b.test'},
                                    {'name': 'Diana'})

        assert got == 0


class TestSiteMayChooseAnother:
    """Настройка сайта сильнее умолчания."""

    async def test_default_is_the_universal_one(self, rhapsody):
        from src.dating.dating_agent import dating_agent_universal_name

        assert await dating_agent_universal_name(SITE) == DATING_AGENT_UNIVERSAL

    async def test_site_setting_wins(self, auth_client, rhapsody):
        """⚠ Настройка, а не константа: подход новый, и вернуться к прежним
        агентам должно быть можно правкой строки, а не выкладкой."""
        from src.dating.dating_agent import dating_agent_universal_name

        before = ((await auth_client.get(f'/api/dating/site/{SITE}')).json()
                  ['result']['settings'])
        try:
            await auth_client.put(f'/api/dating/site/{SITE}',
                                  json={'settings': {DATING_AGENT_SETTING: 'свой_агент'}})

            assert await dating_agent_universal_name(SITE) == 'свой_агент'
        finally:
            await auth_client.put(f'/api/dating/site/{SITE}',
                                  json={'settings': before})
