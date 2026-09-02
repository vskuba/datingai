"""Настройки сайта: наши значения по номеру у соседа.

Хранение и правка идут по HTTP, в живую панель с живой базой: проверять хранение
с поддельной базой значило бы проверять подделку.

⚠ **Номер сайта берётся выдуманный, а не из списка соседа.** Ручкам правки он всё
равно: своей таблицы сайтов здесь нет, а `site_id` — просто ключ, указывающий в
чужую базу (внешнего ключа поэтому и нет). Спроси мы номер у соседа, сюита зависела
бы от того, поднят ли он, ради значения, которое ни на что здесь не влияет.

Склейка «список соседа + наши значения» — единственное, что упирается в него, и
проверяется в процессе с поддельным (`rhapsody_fake.py`).

⚠ **Ключи здесь свои, с приставкой `тест_`.** Настройки общие на сайт, и тест,
взявший осмысленное имя, однажды затрёт живое значение. Всё заведённое убирается в
`finally`; настройки соседних ключей при этом не трогаются — ручка переписывает
набор целиком, поэтому тест сперва читает его и возвращает как было.
"""

import pytest

from src.uvicorn.routers.dating import dating_site
from src.uvicorn.routers.dating.dating_site import dating_site_list

from src.uvicorn.routers.dating.dating_site import DATING_SITE_SECRET_MASK as MASK

KEY = 'тест_ключ'
OTHER = 'тест_второй'

# Номера, под которыми сюита пишет свои настройки. Выдуманные: сайт с таким
# номером у соседа не заведён, и живые значения задеть невозможно в принципе.
SITE = 999_777
SITE_OTHER = 999_778


@pytest.fixture
async def kept(auth_client):
    """Возвращает настройки обоих тестовых номеров такими, какими они были.

    Ручка переписывает набор целиком, и тест, оставивший свой, стёр бы прежние
    значения. Номера выдуманные, но правило одно на всю сюиту: что завела, то и
    убрала.
    """
    before = {site: await _settings(auth_client, site) for site in (SITE, SITE_OTHER)}

    yield

    for site, settings in before.items():
        await auth_client.put(f'/api/dating/site/{site}', json={'settings': settings})


class TestSiteSettings:
    """Завести, переписать, убрать."""

    async def test_saved_value_comes_back(self, auth_client, kept):
        await auth_client.put(f'/api/dating/site/{SITE}', json={'settings': {KEY: '30'}})

        assert (await _settings(auth_client, SITE)).get(KEY) == '30'

    async def test_save_rewrites_the_whole_set(self, auth_client, kept):
        """⚠ Набор переписывается целиком, а не по ключу.

        Страница держит его перед глазами и шлёт то, что на ней. Правка по одному
        ключу означала бы, что удаление — отдельная ручка, а порядок двух правок
        подряд начинает значить.
        """
        await auth_client.put(f'/api/dating/site/{SITE}',
                              json={'settings': {KEY: '1', OTHER: '2'}})
        await auth_client.put(f'/api/dating/site/{SITE}', json={'settings': {KEY: '1'}})

        assert await _settings(auth_client, SITE) == {KEY: '1'}, 'второй ключ пережил перезапись'

    async def test_empty_value_is_not_stored(self, auth_client, kept):
        """Пустое значение не хранится: «настройки нет» и «настройка пустая» —
        разные вещи, и путать их значит получать необъяснимое поведение там, где
        умолчание кода было осмысленным."""
        await auth_client.put(f'/api/dating/site/{SITE}',
                              json={'settings': {KEY: '30', OTHER: ''}})
        settings = await _settings(auth_client, SITE)

        assert KEY in settings and OTHER not in settings

    async def test_settings_are_kept_apart_by_site(self, auth_client, kept):
        """Настройки одного сайта не видны у другого: ключ — его номер."""
        await auth_client.put(f'/api/dating/site/{SITE}', json={'settings': {KEY: 'мой'}})

        assert KEY not in await _settings(auth_client, SITE_OTHER)

    async def test_unknown_site_has_empty_settings(self, auth_client):
        """Сайт, который ещё не настраивали, — законное состояние, а не 404:
        строка появляется при первом сохранении."""
        answer = await auth_client.get('/api/dating/site/999999')

        assert answer.status_code == 200
        assert answer.json()['result']['settings'] == {}


class TestSiteList:
    """⚠ Список ведёт сосед, значения — мы. Единственное место, где он нужен."""

    async def test_list_joins_snapshot_and_us(self, rhapsody, monkeypatch):
        """Список ведёт сосед — через снимок его ответа (`test_site_snapshot.py`);
        значения ведём мы. Сайт без сохранённых настроек виден с пустым набором —
        иначе его нельзя было бы настроить в первый раз.
        """
        monkeypatch.setattr(dating_site, '_settings_all',
                            _saved({343: {'favorites_keep': '86400'}}))
        await dating_site.dating_site_sync_now()

        sites = {site['id']: site for site in
                 (await dating_site_list(user=None))['result']['sites']}

        assert 343 in sites and 19 in sites
        assert sites[343]['name'] == 'Ladadate' and sites[343]['url']
        assert sites[343]['settings'] == {'favorites_keep': '86400'}, 'наши значения потерялись'
        assert sites[19]['settings'] == {}, 'сайт без настроек пропал из списка'

    async def test_our_settings_do_not_add_sites(self, rhapsody, monkeypatch):
        """Настройки остались, а сайта у соседа нет — показывать нечего.

        Список ведёт он: строка без имени и адреса в селекте бесполезна, а её
        появление означало бы, что свой список сайтов у нас всё-таки завёлся.
        """
        monkeypatch.setattr(dating_site, '_settings_all', _saved({777: {KEY: '1'}}))
        await dating_site.dating_site_sync_now()

        sites = (await dating_site_list(user=None))['result']['sites']

        assert 777 not in [site['id'] for site in sites]

    async def test_known_settings_come_with_their_values(self, rhapsody, monkeypatch):
        """Реестр известных настроек уезжает на страницу: набор значений — условие
        кода, и страница рисует по нему селект, а не выдумывает свой."""
        monkeypatch.setattr(dating_site, '_settings_all', _saved({}))

        known = (await dating_site_list(user=None))['result']['known']
        keep = next(row for row in known if row['key'] == 'favorites_keep')

        assert [option['value'] for option in keep['options']] == ['604800', '259200', '86400']
        assert keep['default'] == '604800'
        assert all(option['title'] for option in keep['options']), 'человеку нужны подписи'

    async def test_unreachable_neighbour_does_not_empty_the_page(self, rhapsody, monkeypatch):
        """⚠ 502 отсюда больше не приходит.

        Раньше приходил: список брали у соседа при каждом показе. Теперь отвечаем
        снимком и сверяем в фоне — лежащий сосед не повод показывать пустую
        страницу настроек. Отказ сверки виден на «Ошибках» (`test_site_snapshot.py`).
        """
        monkeypatch.setattr(dating_site, '_settings_all', _saved({}))
        rhapsody.offline()

        sites = (await dating_site_list(user=None))['result']['sites']

        assert sites, 'страница настроек опустела вместе с соседом'


class TestKnownDurations:
    """Настройки-сроки: набор значений — условие кода, а не украшение формы."""

    @pytest.mark.parametrize('key, default, values', [
        ('favorites_keep', '604800', ['604800', '259200', '86400']),
        ('chat_check_every', '300', ['300', '1800', '3600']),
        ('online_check_every', '120', ['120', '300', '600']),
    ])
    async def test_seconds_setting_offers_its_values(self, rhapsody, monkeypatch,
                                                     key, default, values):
        monkeypatch.setattr(dating_site, '_settings_all', _saved({}))

        known = {row['key']: row for row in
                 (await dating_site_list(user=None))['result']['known']}

        assert [option['value'] for option in known[key]['options']] == values
        assert known[key]['default'] == default
        assert all(option['title'] for option in known[key]['options']), 'человеку нужны подписи'

    async def test_value_outside_the_set_is_refused(self, auth_client, kept):
        """⚠ Вписанное мимо набора код не принимает — берёт умолчание.

        Набор предлагает страница, но условие это **кода**: впиши кто-нибудь в базу
        «месяц», и проверка шла бы месяц, хотя ни одна страница такого не
        предлагала. Поймать это иначе нечем — молча работало бы не то.
        """
        from src.uvicorn.routers.dating.dating_site import dating_site_seconds

        await auth_client.put(f'/api/dating/site/{SITE}',
                              json={'settings': {'chat_check_every': '99999'}})

        assert await dating_site_seconds(SITE, 'chat_check_every') == 300

    async def test_chosen_value_is_used(self, auth_client, kept):
        await auth_client.put(f'/api/dating/site/{SITE}',
                              json={'settings': {'chat_check_every': '3600'}})

        from src.uvicorn.routers.dating.dating_site import dating_site_seconds

        assert await dating_site_seconds(SITE, 'chat_check_every') == 3600


class TestSecretSettings:
    """⚠ Пароль наружу не отдаётся никому, включая администратора."""

    async def test_secret_value_never_leaves(self, auth_client, kept):
        """Из ручки уходят точки, а не пароль.

        Страница его не показывает, а значит и получать ей нечего; утечь через
        журнал запросов, скриншот или чужой глаз за плечом ему больше нечем.
        """
        await auth_client.put(f'/api/dating/site/{SITE}',
                              json={'settings': {'api_password': 'тайна'}})

        assert (await _settings(auth_client, SITE))['api_password'] == MASK

    async def test_secret_is_hidden_in_the_list_too(self, auth_client, kept):
        """Список сайтов — вторая дорога к тем же значениям, и там та же маска."""
        await auth_client.put(f'/api/dating/site/{SITE}',
                              json={'settings': {'api_password': 'тайна'}})

        answer = await auth_client.put(f'/api/dating/site/{SITE}',
                                       json={'settings': {'api_password': MASK}})

        assert answer.json()['result']['settings']['api_password'] == MASK

    async def test_mask_means_keep(self, auth_client, kept):
        """⚠ Маска вместо секрета означает «не трогать».

        Страница настоящего значения не знает и прислать его не может; прими мы
        точки как значение — первое же сохранение соседней настройки затёрло бы
        пароль строкой «••••••••», и вход перестал бы работать без единого
        сообщения.
        """
        await auth_client.put(f'/api/dating/site/{SITE}',
                              json={'settings': {'api_password': 'тайна'}})
        await auth_client.put(f'/api/dating/site/{SITE}',
                              json={'settings': {'api_password': MASK, 'api_base': 'https://x'}})

        assert await _secret_in_base(SITE) == 'тайна', 'пароль затёрт маской'

    async def test_secret_can_be_replaced(self, auth_client, kept):
        """Замена работает: прислали новое значение — оно и легло."""
        await auth_client.put(f'/api/dating/site/{SITE}',
                              json={'settings': {'api_password': 'первый'}})
        await auth_client.put(f'/api/dating/site/{SITE}',
                              json={'settings': {'api_password': 'второй'}})

        assert await _secret_in_base(SITE) == 'второй'

    async def test_secret_can_be_removed(self, auth_client, kept):
        """Пустое значение убирает пароль — то же правило, что у прочих настроек."""
        await auth_client.put(f'/api/dating/site/{SITE}',
                              json={'settings': {'api_password': 'тайна'}})
        await auth_client.put(f'/api/dating/site/{SITE}', json={'settings': {'api_password': ''}})

        assert 'api_password' not in await _settings(auth_client, SITE)

    async def test_registry_tells_the_page_it_is_secret(self, rhapsody, monkeypatch):
        """Страница рисует пароль полем-звёздочками по признаку из реестра, а не по
        догадке об имени ключа."""
        monkeypatch.setattr(dating_site, '_settings_all', _saved({}))

        known = {row['key']: row for row in
                 (await dating_site_list(user=None))['result']['known']}

        assert known['api_password']['secret'] is True
        assert known['api_base']['secret'] is False
        assert known['api_base']['options'] == [], 'адрес — свободное поле, а не набор'


class TestSecretReveal:
    """⚠ Посмотреть пароль можно — отдельной ручкой и только админу."""

    async def test_admin_sees_the_real_value(self, auth_client, kept):
        """Ради этого ручка и заведена: иначе пароль не проверить, не переписав его."""
        await auth_client.put(f'/api/dating/site/{SITE}',
                              json={'settings': {'api_password': 'тайна'}})

        answer = await auth_client.get(f'/api/dating/site/{SITE}/reveal/api_password')

        assert answer.status_code == 200
        assert answer.json()['result']['value'] == 'тайна'

    async def test_list_still_shows_the_mask(self, auth_client, kept):
        """⚠ Отдельная ручка — не формальность.

        Список перерисовывается на каждой смене сайта и оседает в журналах
        запросов; пароль в них попадать не должен. Здесь же обращение к нему видно
        как отдельное событие с именем ключа.
        """
        await auth_client.put(f'/api/dating/site/{SITE}',
                              json={'settings': {'api_password': 'тайна'}})

        assert (await _settings(auth_client, SITE))['api_password'] == MASK

    async def test_operator_cannot_reveal(self, dating_client, auth_client, kept):
        """Оператору пароль не показывается: правка настроек и так админская, а
        пароль тем более."""
        await auth_client.put(f'/api/dating/site/{SITE}',
                              json={'settings': {'api_password': 'тайна'}})

        answer = await dating_client.get(f'/api/dating/site/{SITE}/reveal/api_password')

        assert answer.status_code == 403

    async def test_closed_without_login(self, client):
        answer = await client.get(f'/api/dating/site/{SITE}/reveal/api_password')

        assert answer.status_code in (401, 307)

    async def test_missing_setting_is_404(self, auth_client, kept):
        """Нет настройки — 404, а не пустое значение: пустое читалось бы как
        «пароль пустой», и его вписали бы второй раз."""
        answer = await auth_client.get(f'/api/dating/site/{SITE}/reveal/нет_такой')

        assert answer.status_code == 404


class TestSiteRefuses:
    """Чего ручка не принимает."""

    async def test_empty_key_is_422(self, auth_client, kept):
        answer = await auth_client.put(f'/api/dating/site/{SITE}',
                                       json={'settings': {'   ': '1'}})

        assert answer.status_code == 422

    async def test_too_long_key_is_422(self, auth_client, kept):
        """Длиннее предела — отказ с внятным текстом. Предел тот же, что у настроек
        панели: человек читает их одними глазами."""
        answer = await auth_client.put(f'/api/dating/site/{SITE}',
                                       json={'settings': {'к' * 51: '1'}})

        assert answer.status_code == 422


class TestSiteRights:
    """⚠ Правка — только админу, чтение — всем."""

    async def test_operator_can_read(self, dating_client):
        """Оператору полезно видеть, с чем он работает: прятать это значило бы
        звать админа на каждый вопрос."""
        assert (await dating_client.get(f'/api/dating/site/{SITE}')).status_code == 200

    async def test_operator_cannot_write(self, dating_client):
        """Настройка сайта меняет работу всех анкет на нём, а не того, кто её
        открыл."""
        answer = await dating_client.put(f'/api/dating/site/{SITE}',
                                         json={'settings': {KEY: '1'}})

        assert answer.status_code == 403

    async def test_page_is_open_to_operator(self, dating_client):
        """Страница открыта, как остальные в разделе; форму правки она прячет
        сама — форма, которая всегда отказывает, читается как поломка."""
        assert (await dating_client.get('/admin/dating/site')).status_code == 200

    async def test_closed_without_login(self, client):
        assert (await client.get('/api/dating/site')).status_code == 401


async def _secret_in_base(site_id: int) -> str:
    """Что на самом деле лежит в базе.

    ⚠ Единственное место в сюите, где секрет читается напрямую: через ручку он не
    виден — в этом и смысл проверки. Иначе «маска означает не трогать» проверялось
    бы маской же, то есть ничем.
    """
    from src.uvicorn.routers.dating.dating_site import dating_site_setting

    return await dating_site_setting(site_id, 'api_password')


def _saved(rows: dict):
    """Подменённое чтение сохранённых настроек: номер сайта → набор."""
    async def _all():
        return dict(rows)

    return _all


async def _settings(auth_client, site_id: int) -> dict:
    return (await auth_client.get(f'/api/dating/site/{site_id}')).json()['result']['settings']
