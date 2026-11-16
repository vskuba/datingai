"""Лента «Все онлайн» со страницы админ-панели: GET по сессии, без браузера.

Третья дорога ленты рядом с API сайта и сценарием у соседа. Нужна сайтам, у
которых **кабинета под девушкой нет вовсе** — одна панель агентства на всех
(Luxee): мужчины в сети видны в ней своим разделом, и снимать их сценарием
значило бы гонять Chromium ради страницы, которая отдаётся готовой разметкой.

## Как устроено

```
круг панели (panel_round)  ── держит cookie живыми, раз в admin_every
        │
лента (people.py, kind=online)
        └─ panel_online_rows ── GET раздела, страница за страницей по метке
                                  `token`, пока сайт не скажет «конец»
```

Что за раздел, каким фильтром и как разбирать плитки, знает **драйвер панели**
(`site/site_panel.py`, `ONLINE_PATH`): у сайта без такой страницы путь пуст, и
лента идёт прежней дорогой.

## ⚠ Своего входа здесь нет

Сессию держит круг панели. Не признала — отказ словами «панель увела на
вход», и лента показывает прежний снимок с причиной. Входить отсюда значило
бы получить два входа наперегонки — тот же довод, что у `panel_fetch`.

## ⚠ Анкета ленте не нужна

Список снимается **от лица агентства**, а не девушки: ни «под кем заходить»,
ни «никто не в сети» здесь не бывает. Поэтому лента таких сайтов живёт и
ночью, когда смены нет ни у кого, — так же, как круг панели.
"""

from src.dating.panel import PANEL_URL_SETTING
from src.dating.panel_fetch import DatingPanelFetchError, panel_fetch_html
from src.dating.site.site_registry import site_registry_panel
from src.dating.site_presence import site_presence
from src.dating.site_setting import site_setting

# Сколько страниц подкачки берём за один съём. По двадцать плиток — потолок
# в шестьсот человек; замер 16.11.2026: платящих в сети 103, шесть страниц.
# ⚠ Предохранитель от петли, а не обрезка: сайт, вернувший ту же метку,
# крутил бы нас бесконечно.
PANEL_ONLINE_PAGE_MAX = 30

# Подкачка отвечает JSON только на запрос «от скрипта страницы»: без заголовка
# приезжает целая страница, а на устаревшую метку — страница без списка вовсе.
PANEL_ONLINE_HEADERS = {'X-Requested-With': 'XMLHttpRequest',
                        'Accept': 'application/json, text/javascript, */*'}


async def panel_online_on(site_id: int) -> bool:
    """Снимается ли лента этого сайта со страницы панели.

    ⚠ Нужны оба условия: драйвер знает раздел **и** у сайта задан адрес
    панели. Без адреса сессии нет, и обещать дорогу, которая ответит «в
    панель ещё не входили», значило бы спрятать прежнюю, рабочую.
    """
    if not site_id:
        return False

    if not str(await site_setting(int(site_id), PANEL_URL_SETTING) or '').strip():
        return False

    return bool(site_registry_panel(await site_presence(int(site_id))).ONLINE_PATH)


async def panel_online_rows(site_id: int) -> list[dict]:
    """Все, кто сейчас в сети, — карточками ленты.

    Returns:
        list: записи того же вида, что у API сайта и у сценария. Повторы
        между страницами сняты: лента живая, и человек успевает переехать со
        страницы на страницу прямо во время съёмки.

    Raises:
        DatingPanelFetchError: сессии нет, панель увела на вход, разметка
            раздела сменилась. ⚠ Неудача **любой** страницы — отказ всего
            съёма: половина ленты погасила бы точки у второй половины.
    """
    driver = site_registry_panel(await site_presence(int(site_id)))
    if not driver.ONLINE_PATH:
        raise DatingPanelFetchError('у панели этого сайта нет страницы ленты')

    found, seen, token = [], set(), '0'
    for _ in range(PANEL_ONLINE_PAGE_MAX):
        raw = await panel_fetch_html(int(site_id), driver.ONLINE_PATH,
                                     driver.online_params(token),
                                     headers=PANEL_ONLINE_HEADERS)
        try:
            rows, after = driver.online_page(raw)
        except ValueError as e:
            raise DatingPanelFetchError(str(e))

        for row in rows:
            if row['uid'] not in seen:
                seen.add(row['uid'])
                found.append(row)

        if not after or after == token:
            break
        token = after

    return found
