"""Luxee: переписка чата сокетом панели агентства — все диалоги, все сообщения.

У Luxee кабинета под девушкой нет: одна панель агентства («Models admin») на всех,
и чат в ней — страница `/chats/`, которая сама ничего не несёт, а грузит диалоги
событиями socket.io 2 (`/chat/socket.io`). Ходим тем же сокетом, без браузера:
GET страницы под сессией круга панели даёт токен, дальше — текстовые кадры
(протокол — `site_socket_luxee.py`, общий с отправкой ответа).

    cm_get_profiles {uids}             → внешние анкеты девушек (по одной на проект)
    cl_ch_all / cl_ch_more {ownerUid}  → диалоги девушки страницами
    cl_c_messages {ownerUid, identity} → последние 20 сообщений диалога
    cl_cm_messages {…, token: index}   → двадцать раньше указанного номера

## ⚠⚠ Номер девушки — внутренний, автор сообщения — внешний

У девушки один номер панели (`622180`, он же `id` карточки у нас) и по анкете на
каждый проект агентства (`3161872`, `3161888`, …). Диалог зовётся
`{мужчина}_{внешняя анкета}`, и автор её реплики — **внешняя** анкета. Карту
«внешняя → внутренняя» отдаёт `cm_get_profiles` (`import_uid`); без этого запроса
сервер вдобавок отвечает пустым списком диалогов — замер 19.11.2026.

## ⚠⚠ «Прочитано» на выдачу не влияет — и мы его не ставим

Прочитанность живёт в сообщении отдельно (`memberStatus`: `status` 1/2,
`readAt`), и прочитанное девушкой руками приезжает так же, как непрочитанное.
Отметку ставит событие `cl_consume` — его мы **не шлём никогда**: чтение панели
не должно гасить счётчик, по которому оператор видит новое.

## ⚠⚠ Пропусков не бывает: номера в диалоге сквозные

У каждого диалога номер последнего сообщения (`index`), и сообщения нумерованы
подряд с единицы (замер: 437 из 437 без дыр). Помним по диалогу последний
**сохранённый** номер (Redis, `LUXEE_CHAT_MARKS`) и, когда он вырос, листаем
назад до него — сколько бы ни проспал круг. Диалог без отметки (первый заход,
пустой Redis) читается **целиком**, как давно бы он ни жил. Отметка ставится **после записи**
(`journal_saved`): упади запись — следующий заход перечитает то же самое.
"""

from datetime import datetime, timezone

from redis_.redis_ import redis_conn_get

from src.dating.panel_fetch import DatingPanelFetchError
from src.dating.site.site_import import DatingSiteImport
from src.dating.site.site_socket_luxee import (site_socket_luxee_channels,
                                               site_socket_luxee_listen,
                                               site_socket_luxee_messages,
                                               site_socket_luxee_open,
                                               site_socket_luxee_outer)

# Отметки «последний сохранённый номер» по диалогам: хэш на сайт.
LUXEE_CHAT_MARKS = 'dating:luxee:chat:{site_id}'

# Сообщение без текста — тоже событие разговора, и показать его надо словом:
# пустой текст общий разбор выбрасывает. Вид — по `type` сообщения.
LUXEE_CHAT_KINDS = {3: '[wink]', 5: '[gift]', 6: '[gift request]', 8: '[sticker]'}


class DatingSiteImportLuxee(DatingSiteImport):
    """Чат панели Luxee сокетом (см. заголовок файла)."""

    name = 'luxee'

    def __init__(self, presence: dict | None = None):
        super().__init__(presence)
        self._pending = {}
        self._site_id = 0

    def date_fmt(self) -> str:
        return '%Y-%m-%d'

    def has_journal(self) -> bool:
        return True

    async def journal_rows(self, site_id: int, profiles: dict, date_from: str,
                           date_to: str) -> tuple[int, list[dict], str]:
        """Все новые сообщения всех диалогов наших девушек.

        Raises:
            DatingPanelFetchError: страница чата не отдала токен, сокет не
                открылся или не пустил. Беда одной девушки — строка в `why`.
        """
        self._site_id = int(site_id)
        self._pending = {}

        girls = sorted(int(uid) for uid in profiles if str(uid).isdigit())
        if not girls:
            return 0, [], ''

        marks = _marks_get(site_id)

        async with site_socket_luxee_open(site_id, self.presence, girls[0]) as sock:
            return await self._read(sock, girls, marks)

    def live(self) -> bool:
        return True

    async def journal_live(self, site_id: int, on_change, stop_at: float) -> None:
        """Держать сокет панели открытым и звать `on_change()` на новом в чате.

        ⚠⚠ Запрос профилей (`cm_get_profiles`) — до слушания, как у страницы:
        без него сервер не считает девушек «открытыми» в этом соединении.

        Raises:
            DatingPanelFetchError: сокет не открылся или закрылся — переоткрывает
                вызывающий (`chat_live.py`) со своей паузой.
        """
        from src.dating.chat_panel import chat_panel_profiles

        girls = sorted(int(uid) for uid in await chat_panel_profiles(site_id)
                       if str(uid).isdigit())
        if not girls:
            return

        async with site_socket_luxee_open(site_id, self.presence, girls[0]) as sock:
            await site_socket_luxee_outer(sock, girls)
            await site_socket_luxee_listen(sock, on_change, stop_at)

    async def journal_saved(self, site_id: int) -> None:
        """Строки записаны — запоминаем, докуда прочитан каждый диалог."""
        if self._pending and int(site_id) == self._site_id:
            redis_conn_get().hset(LUXEE_CHAT_MARKS.format(site_id=int(site_id)),
                                  mapping=self._pending)
        self._pending = {}

    async def _read(self, sock, girls: list,
                    marks: dict) -> tuple[int, list[dict], str]:
        """Обход по девушкам: диалоги, затем новые сообщения в каждом.

        ⚠⚠ Диалог без отметки читается **целиком и всегда**, как давно бы он
        ни жил: окно дат здесь не спрашивается вовсе. Прежде впервые брались
        только живые в окне круга (сутки) — и диалог, затихший 17.11, не
        подтянулся, хотя панель уже выдала по нему задание Catch Up
        (19.11.2026, Olena ↔ David). Цена — одно чтение на диалог: дальше
        работает отметка.
        """
        inner = await site_socket_luxee_outer(sock, girls)

        total, rows, why = 0, [], []
        for girl in girls:
            try:
                channels = await site_socket_luxee_channels(sock, girl)
            except DatingPanelFetchError as e:
                why.append(f'{girl}: {e}')
                continue

            for channel in channels:
                identity = str(channel.get('identity') or '')
                index = int(channel.get('index') or 0)
                mark = marks.get(identity)

                if not identity or (mark is not None and index <= mark):
                    continue

                try:
                    messages = await site_socket_luxee_messages(sock, girl,
                                                                identity, mark)
                except DatingPanelFetchError as e:
                    why.append(f'{girl}/{identity}: {e}')
                    continue

                total += len(messages)
                rows += [row for row in (_row(m, inner) for m in messages) if row]
                self._pending[identity] = index

        return total, rows, '; '.join(why)


def site_import_luxee_text(message: dict) -> str:
    """Текст сообщения Luxee для нашей переписки; без текста — словом о том,
    что пришло. ⚠ Наружу — ради отправки: реплика девушки, увиденная до импорта,
    уходит соседу тем же текстом, каким её потом привезёт импорт."""
    body = str(message.get('body') or message.get('bodyOrigin') or '').strip()
    extra = []
    if message.get('media'):
        extra.append('[photo]')
    if message.get('sticker') and not body:
        extra.append('[sticker]')

    kind = LUXEE_CHAT_KINDS.get(int(message.get('type') or 0))
    if not body and not extra and kind:
        extra.append(kind)

    return ' '.join([body, *extra]).strip()


def site_import_luxee_line(message: dict) -> dict:
    """Сообщение сокета — ключом, текстом и временем, как их запишет импорт.

    Зовёт отправка: реплика её стороны, увиденная до импорта, уходит соседу
    под тем же ключом (`uid` → `panel:{uid}`), тем же текстом и временем.
    """
    return {'uid': str(message.get('sid') or ''),
            'text': site_import_luxee_text(message),
            'when': _when(message.get('created'))}


def _row(message: dict, inner: dict) -> dict | None:
    """Сообщение сокета — строкой общего разбора (`chat_panel_rows`)."""
    author = str((message.get('author') or {}).get('uid') or '')
    members = (message.get('channel') or {}).get('members') or []
    names = {str(m.get('uid')): str(m.get('username') or m.get('first_name') or '')
             for m in members if isinstance(m, dict)}

    girl_outer = next((uid for uid in names if uid in inner), '')
    man = next((uid for uid in names if uid not in inner), '')
    girl = inner.get(girl_outer, '')
    if not girl or not man or not author:
        return None

    man_cell = f'{man} {names.get(man, "").strip()}'
    if author == man:
        side = {'from_uid': man, 'from': man_cell, 'to_uid': girl, 'to': girl}
    else:
        side = {'from_uid': girl, 'from': girl, 'to_uid': man, 'to': man_cell}

    return {'uid': str(message.get('sid') or ''), **side,
            'text': site_import_luxee_text(message),
            'when': _when(message.get('created'))}


def _when(created) -> str:
    """Метка сервера в миллисекундах — `гггг-мм-дд чч:мм:сс` UTC."""
    try:
        moment = datetime.fromtimestamp(int(created) / 1000, timezone.utc)
    except (TypeError, ValueError, OverflowError, OSError):
        return ''

    return moment.strftime('%Y-%m-%d %H:%M:%S')


def _marks_get(site_id: int) -> dict:
    """Отметки диалогов сайта: `identity → последний сохранённый номер`."""
    raw = redis_conn_get().hgetall(LUXEE_CHAT_MARKS.format(site_id=int(site_id))) or {}

    found = {}
    for key, value in raw.items():
        key = key.decode() if isinstance(key, bytes) else str(key)
        try:
            found[key] = int(value)
        except (TypeError, ValueError):
            continue

    return found
