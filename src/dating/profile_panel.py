"""Параметры анкеты со страницы админ-панели агентства.

Страница `…/partner/agency/userprofile/edit/{ID}` — обычная серверная форма, и
сессия панели у нас уже есть: её держит круг (`panel_round.py`). Значит браузер не
нужен вовсе — один GET по cookie, доли секунды.

## ⚠ Кто зовёт и когда

Только кнопка в окне правки карточки. Круга, который обходил бы анкеты сам, нет
намеренно: анкета меняется раз в месяцы, и сторож, будящийся ради «ничего нового»,
— сторож без предмета. Признак пересмотреть: параметры у половины анкет не сняты ни
разу.

## ⚠⚠ Сменившаяся разметка — отказ, а не пустой результат

Форма разобралась, а знакомых имён в ней нет — это перевёрстка у сайта. Запиши мы в
этот миг пустоту, и параметры обнулились бы у **всех** анкет сразу, по одному
нажатию кнопки. Порог — «ни одного знакомого имени», а не «меньше половины»: сайт
вправе убрать одно поле, и половинчатый порог отказывал бы на законных изменениях.

⚠ Считаются **имена полей, а не значения**: у новой анкеты все поля законно пусты,
и порог по значениям отказывал бы ровно там, где снимать и надо.

## ⚠ Наружу не пишем ничего

Страница панели только читается. Правка параметров руками меняет нашу карточку и
больше ничего — на сайте она не появится.
"""

from datetime_.datetime_ import datetime_utc_now

from src.dating.json_column import json_column_load
from src.dating.panel_fetch import panel_fetch_form, panel_fetch_html
from src.dating.profile_card import profile_card_merge
from src.dating.site.site_registry import site_registry_panel


class DatingProfilePanelError(RuntimeError):
    """Снимать нечем: у анкеты нет номера либо у сайта нет панели.

    Текст годен и для журнала, и для ответа ручки. Это **ошибка вызывающего** —
    состояние, которое человек поправит у себя.
    """


class DatingProfilePanelMarkupError(DatingProfilePanelError):
    """Страница пришла, но знакомых полей в ней нет: сайт перевёрстан.

    ⚠⚠ **Свой тип, а не текст в общем исключении.** Исходы различаются по
    существу: «нет номера» правит человек у нас, перевёрстку — сайт у себя, и
    ручка отвечает на них разными кодами. Разбирай она их подстрокой в сообщении
    (`'разметка' in str(e)`), и перефразировка текста молча сменила бы код
    ответа — домену при этом не пришлось бы знать ни слова про HTTP.

    ⚠ Наследник, а не сосед: кому разница не нужна, ловит родителя и получает оба.
    """


# Вид отметки о чтении. ⚠ Тот же, что понимает `timeShift` на странице: она ждёт
# ровно `YYYY-MM-DD HH:MM:SS` и строку короче возвращает как есть, не сдвигая.
PROFILE_PANEL_AT_FORMAT = '%Y-%m-%d %H:%M:%S'


async def profile_panel_read(profile: dict, presence: dict) -> dict:
    """Снять параметры анкеты с панели и положить в карточку.

    Args:
        profile: строка `profile` — нужны `id`, `site_id`, `profile` и
            `profile_id`.
        presence: настройка сайта (`browser_site.presence`) — по ней выбирается
            драйвер панели.

    Returns:
        dict: `parameters` — что легло, `parameters_at` — когда снято.

    Raises:
        DatingProfilePanelError: у анкеты нет номера на сайте либо у сайта нет
            драйвера панели — это поправимо у нас.
        DatingProfilePanelMarkupError: страница пришла, но знакомых полей в ней
            нет — сайт перевёрстан. Наследник первого.
        DatingPanelFetchError: панель не ответила, увела на вход или ответила
            кодом ошибки. ⚠ Наверх идёт **как есть**: она уже разобрана словами, и
            заворачивать её в своё исключение значило бы потерять разбор.
    """
    card = json_column_load(profile.get('profile'))
    uid = str(card.get('id') or profile.get('profile_id') or '').strip()
    if not uid:
        raise DatingProfilePanelError('у анкеты нет номера на сайте')

    driver = site_registry_panel(presence)
    if not driver.path:
        raise DatingProfilePanelError('панель этого сайта читать нечем')

    html = await panel_fetch_html(int(profile['site_id']),
                                  driver.path.format(uid=uid))
    form = panel_fetch_form(html)

    if not driver.known(form):
        raise DatingProfilePanelMarkupError(
            'на странице панели не нашлось ни одного поля — '
            'похоже, разметка сменилась')

    at = datetime_utc_now().strftime(PROFILE_PANEL_AT_FORMAT)
    # ⚠ Отметка уезжает **тем же** `UPDATE`, что и значения: разойтись им нечем.
    stored = await profile_card_merge(
        int(profile['id']), {'parameters': driver.params(form),
                             'parameters_at': at},
        'параметры анкеты не записались')

    return {'parameters': stored.get('parameters') or {},
            'parameters_at': str(stored.get('parameters_at') or '')}
