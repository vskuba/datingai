"""Избранные: чем сайт зажигает и гасит звезду на анкете мужчины.

Седьмая грань драйвера сайта. Чат — про кадры сокета, анкета — про имена полей
JSON, панель и переписка — про чужой кабинет, описание собеседника — про
вёрстку его страницы, гости — про ленту визитов, а эта про **звёздочку**: кто
у девушки в избранных и как туда попасть.

## ⚠⚠ Дорог две, и они разной цены

| Сайт | Как ставится звезда | Сколько стоит |
|------|---------------------|---------------|
| Ladadate и прочие | сценарий у соседа, вкладка Chromium | десятки секунд |
| Step2Love | обычный POST под сессией анкеты | доли секунды |

У Step2Love сценариев избранных **нет вовсе** — ни «в избранные», ни «из
избранных» (спрошено у соседа 03.11.2026), и занятие для него не работало
никак. Зато нашлась ручка — та же, что дёргает его собственная страница чата
по щелчку на звёздочке:

    POST {сайт}/ajax.php?com=ajax
      jsonData = {"cmd": "add_favorites" | "del_favorites", "userID": N}

Ответом приходит объект с полем `status`: `OK` — сделано, иначе причина в
`reason`.

⚠⚠⚠ **Заголовок `X-Requested-With` обязателен.** Без него сайт отвечает `200`,
а в теле — отказ «look like not Ajax»: то есть неудача приезжает под кодом
успеха, и проверка по коду ответа сочла бы звезду зажжённой. Заголовок ставит
jQuery страницы сам, поэтому в перехвате сети его легко не заметить. Измерено
живой пробой: без него отказ, с ним — `Successfully added`, и номер появился в
списке избранных.

## ⚠⚠ Список избранных у Step2Love тоже свой

API у сайта нет, и общий путь через него этому сайту не годится. Зато список
приезжает **в кадре авторизации сокета** — `favoriteList`, тем же открытием
соединения, которым идут отправка и приём приглашений (`chat_socket.py`). То
есть лишнего похода на сайт он не стоит вовсе.

## ⚠⚠⚠ У сайта **два** списка избранных, и эта ручка ведёт первый

| Список | Где виден | Трогает ли его `ajax.php` |
|--------|-----------|---------------------------|
| чатовый | `favoriteList` кадра авторизации | **да** |
| «Мои Фавориты» в блоке Activity | `message.php?com=inbox&folder=favourite&favorite=my` | **нет** |

Замерено 08.11.2026 полным кругом под живой анкетой: `del_favorites` → ответ
«Successfully deleted», `favoriteList` стал пуст, счётчик «Мои Фавориты» — ноль;
`add_favorites` → «Successfully added», `favoriteList` снова с номером, счётчик
**по-прежнему ноль**. Повторный `add_favorites` отвечает «Already in
Favorites» — то есть ручка своё дело делает и помнит сделанное.

⚠⚠ **Виновата не наша редакция запроса.** Видимая звёздочка на странице мужчин
(`/ladies/all/`) висит на `onClick="addFavorites(ID)"`, а это их собственный
`js/favorites.js`, и шлёт он ровно то же: `POST /ajax.php?com=ajax` с
`jsonData={"cmd":"add_favorites","userID":N,"test":0}`. Значит нажатие рукой в
браузере счётчик тоже не сдвинет — расхождение внутри самого сайта, а не между
ним и нами.

⚠ Отсюда правило чтения: «звезда стоит» у этого сайта значит **чатовый**
список. Проверять сделанное по счётчику Activity нельзя — он отвечает на другой
вопрос (похоже, про переписку: рядом с ним «Мои контакты» и «Чей я фаворит»).

## ⚠ Почему драйвером, а не развилкой по сайту

Сайтов больше одного, и общего у них только замысел: «звезда на его анкете».
Пиши развилку внутри `favorite.py` — правка под Step2Love молча ломала бы
Ladadate, и заметно это стало бы не сразу, а на живом круге избранных. Тот же
приём, что у чата, анкеты, панели и гостей (`site_registry.py`).

## ⚠⚠ Своего клиента здесь нет

Запрос идёт общей дверью `site_session_post`: cookie анкеты, её выход наружу,
общий транспорт. Заведи свой клиент рядом — и сайт увидел бы у одной учётки
два адреса, потому что чтение ходило бы через прокси, а запись напрямую.
"""

import json

from src.dating.chat_socket import chat_socket_hello, chat_socket_open
from src.dating.site_session import site_session_post

# Путь ручки и команды — словами самого сайта (`chat_v4.min.js`, щелчок по
# `.icon-favorite`). Это не настройка: их задаёт движок сайта, а не владелец.
SITE_FAVORITE_PATH = '/ajax.php?com=ajax'
SITE_FAVORITE_ADD = 'add_favorites'
SITE_FAVORITE_DEL = 'del_favorites'

# ⚠⚠⚠ Без него отказ приезжает под кодом `200` — см. шапку файла.
SITE_FAVORITE_HEADERS = {'X-Requested-With': 'XMLHttpRequest'}

# Где в кадре авторизации лежит список избранных анкеты.
SITE_FAVORITE_LIST = 'favoriteList'

# Что сайт отвечает, когда сделал.
SITE_FAVORITE_OK = 'OK'


class DatingSiteFavorite:
    """Избранные по умолчанию: сценарием у соседа.

    ⚠ Своей работы здесь нет, и это честный ответ, а не заглушка: базовый
    драйвер говорит «ручкой не умею», и `favorite.py` идёт прежней дорогой —
    прогоном сценария. Так работали все сайты до Step2Love, и ломать это ради
    единообразия незачем.
    """

    name = 'base'

    def __init__(self, presence: dict | None = None):
        self.presence = presence or {}

    def by_handle(self) -> bool:
        """Умеет ли сайт ставить звезду ручкой. `False` — только сценарием."""
        return False

    async def add(self, profile: dict, uid: str) -> dict:
        """Зажечь звезду. Базовый драйвер этого не умеет."""
        raise NotImplementedError('у этого сайта звезда ставится сценарием')

    async def forget(self, profile: dict, uid: str) -> dict:
        """Погасить звезду. Базовый драйвер этого не умеет."""
        raise NotImplementedError('у этого сайта звезда ставится сценарием')

    async def uids(self, profile: dict) -> set:
        """Кто сейчас в избранных. Базовый драйвер этого не умеет."""
        raise NotImplementedError('у этого сайта список берётся через API')


class DatingSiteFavoriteStep2Love(DatingSiteFavorite):
    """Step2Love: звезда ручкой, список — из кадра авторизации сокета."""

    name = 'step2love'

    def by_handle(self) -> bool:
        return True

    async def add(self, profile: dict, uid: str) -> dict:
        """Зажигает звезду на его анкете.

        Returns:
            dict: `ok` и `error` словами. ⚠ Ничего не бросает: зовёт круг,
            обходящий анкеты сайта, и исключение унесло бы остальных.
        """
        return await self._cmd(profile, uid, SITE_FAVORITE_ADD)

    async def forget(self, profile: dict, uid: str) -> dict:
        """Гасит звезду на его анкете."""
        return await self._cmd(profile, uid, SITE_FAVORITE_DEL)

    async def uids(self, profile: dict) -> set:
        """Номера тех, кто у неё в избранных.

        ⚠⚠ Берётся **из кадра авторизации** сокета, а не отдельным запросом:
        список приезжает тем же открытием соединения, которым идут отправка и
        приём приглашений, и лишнего похода на сайт не стоит.

        Raises:
            ChatSocketError: соединение не открылось (нет входа, сайт молчит).
                ⚠⚠ Бросаем намеренно: «список не пришёл» и «избранных нет» —
                разные ответы, и спутать их значит погасить всех живых при
                первом же моргании сайта (`favorite.py`, сверка).
        """
        send = self.presence.get('send') or {}
        base = str(send.get('base') or '').rstrip('/')

        opened = await chat_socket_open(profile, self.presence, send, base,
                                        str(profile.get('proxy_url') or ''))

        got = chat_socket_hello(opened['frames'], SITE_FAVORITE_LIST)

        return {str(one) for one in (got or []) if str(one).strip()}

    async def _cmd(self, profile: dict, uid: str, cmd: str) -> dict:
        """Одна команда звезды: общее у «зажечь» и «погасить» — всё, кроме слова.

        ⚠⚠ Удачу решает **тело ответа**, а не код: отказ «look like not Ajax»
        приезжает под `200`, и проверка по коду сочла бы звезду зажжённой.
        """
        base = str(self.presence.get('base') or '').rstrip('/')
        if not base:
            return {'ok': False, 'error': 'у сайта не задан адрес (presence.base)'}

        try:
            answer = await site_session_post(
                profile, base + SITE_FAVORITE_PATH,
                data={'jsonData': json.dumps({'cmd': cmd, 'userID': int(uid)})},
                headers={**(self.presence.get('headers') or {}),
                         **SITE_FAVORITE_HEADERS})
        except Exception as e:
            return {'ok': False, 'error': str(e)}

        try:
            said = answer.json()
        except Exception:
            said = {}

        # ⚠ Не-объект в ответе — тоже отказ, а не падение: `said.get` на строке
        # бросает `AttributeError`, и заход круга оборвался бы на одной анкете.
        # Поймано сторожем, а не на проде.
        if not isinstance(said, dict):
            said = {}

        if str(said.get('status') or '').upper() == SITE_FAVORITE_OK:
            return {'ok': True, 'error': ''}

        return {'ok': False,
                'error': str(said.get('reason')
                             or f'сайт отказал ({answer.status_code})')}
