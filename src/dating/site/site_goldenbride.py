"""Goldenbride: чем этот сайт отличается от прочих.

Два класса — по числу разговоров с сайтом: `DatingSiteChatGoldenbride` про кадры
сокета, `DatingSiteProfileGoldenbride` про имена полей анкеты. Оба тонкие: разбор
достался от базовых классов, здесь только умолчания и то единственное, чего
настройкой не описать, — формат идентификатора сообщения.

Всё, что здесь записано, снято с живой страницы и стоило времени: каждая
константа — след поломки, которая **не выглядела поломкой**. Сайт принимал кадр,
показывал его в переписке отправителя и не доставлял никому; анкета при этом
работала, чат шёл, ошибок не было.

Поэтому менять что-либо в этом файле по соображениям красоты нельзя. Форму задаёт
сайт, а не мы, и проверяется она записанными кадрами
(`tests/frames/goldenbride_chat.json`), а не рассуждением.
"""

from src.dating.site.site_base import DatingSiteChat, dating_site_ulid
from src.dating.site.site_profile import DatingSiteProfile


class DatingSiteChatGoldenbride(DatingSiteChat):
    """Goldenbride: плоские JSON-кадры, тело — строка с JSON внутри."""

    name = 'goldenbride'

    def _default_subprotocol(self) -> str:
        """Браузер согласует `chat` заголовком `Sec-WebSocket-Protocol`.

        **Без него соединение устанавливается и входящие приходят, а всё, что
        шлёт клиент, сервер игнорирует молча**: ни ответа, ни ошибки. Из-за
        одного этого заголовка все отправки уходили в никуда, тогда как те же
        кадры со страницы доходили; на поиск ушёл день, потому что ошибка не
        выглядела ошибкой.

        Проверка простая: пошлите `getHistoryByContactIds` — с подпротоколом
        сервер ответит `notSendMessages`, без него промолчит.
        """
        return 'chat'

    def _default_hello(self) -> tuple:
        """Сервер здоровается первым и до этого кадра присланное игнорирует.

        Второе имя — для случая, когда соединение по этой учётке уже было.

        Формально оно избыточно: приветствие узнаётся вхождением подстроки, а
        `connectFromServer` входит в `reconnectFromServer`. Оставлено намеренно —
        совпадение случайное, держаться за него нельзя, и читающий не должен
        гадать, ловится ли переподключение.
        """
        return ('connectFromServer', 'reconnectFromServer')

    def _default_ask(self) -> tuple:
        """Что спросить после приветствия — ровно это шлёт страница чата.

        **Номера собеседников обязательны в запросе истории.** С пустым `content`
        сервер отвечает `notSendMessages: []` — как будто ничего и не было; те же
        сообщения он отдаёт, стоит перечислить собеседников через запятую. Именно
        так терялось всё, что приходило между подключениями: очередь на сервере
        была, а мы считали её пустой.
        """
        return ({'method': 'getHistoryByContactIds', 'to': 'content'},
                {'method': 'getChatInfo'})

    def _default_incoming(self) -> dict:
        """Живое входящее. Тело приходит строкой с JSON внутри — так шлёт сайт."""
        return {'method': 'receivePrivateMessage', 'body': 'message', 'body_json': True,
                'uid': 'senderUid', 'name': 'senderName', 'text': 'message',
                'confirm_id': 'messageIdForConfirm'}

    def _default_pending(self) -> dict:
        """Накопившееся: другая форма — без вложенного тела, поля `id` и `text`."""
        return {'method': 'notSendMessages', 'body': 'message', 'body_json': True,
                'uid': 'id', 'text': 'text', 'confirm_id': 'messageIdForConfirm'}

    def _default_confirm(self) -> dict:
        """Чем подтверждаем приём. Без этого сайт пришлёт сообщение снова."""
        return {'method': 'confirmFromClient'}

    def _default_send_frame(self) -> dict:
        return {'toId': '{{to}}', 'content': '{{text}}',
                'method': 'sendPrivateMessage', 'messageIdForConfirm': '{{uid}}'}

    def _default_before(self) -> tuple:
        """Прелюдия — ровно та, что шлёт страница сайта. Без неё сервер принимает
        кадр и не доставляет его никому.

        У каждого кадра своё место для номера собеседника, и это не придирка к
        форме: `getHistoryByContactIds` именно полем `content` открывает диалог, а
        `startTyping` ждёт номер в `toId`. С пустым `content` сервер принимал
        сообщение и не доставлял никому — так пропали все наши прежние отправки.
        """
        return ({'method': 'getHistoryByContactIds', 'to': 'content', 'suffix': ','},
                {'method': 'getChatInfo'},
                {'method': 'startTyping', 'to': 'toId'},
                {'method': 'stopTyping', 'to': 'toId'})

    def _default_ack(self) -> dict:
        """Как читается ответ сайта на отправленное.

        `ok_mark` — **собственное подтверждение сервера**. На принятое сообщение
        он шлёт два кадра: сначала эхо клиенту (`confirmFromClient`), потом
        `confirmFromServer`. Остановиться на первом значит принять эхо за
        доставку — так и было, и сообщения не доходили ни до кого.

        `refuse_mark` — по общей фразе, а не по перечню методов: методов у отказа
        несколько (`limit`, `limitSecond`, …), а пишет он всегда «Message not
        delivered» и объясняет причину. Перечень назавтра пополнится, фраза — нет.

        `offline_mark` — собеседника нет в чате. Не поломка и не отказ по
        правилам: в чате говорят с тем, кто в чате.
        """
        return {'ok_mark': 'confirmFromServer',
                'refuse_mark': 'not delivered',
                'offline_mark': 'offlineUser'}

    def message_uid(self) -> str:
        """Идентификатор в том же виде, в каком его делает страница, — ULID.

        Это не косметика. ULID несёт внутри время отправки; наш прежний
        идентификатор был шестнадцатеричной строкой, и время в нём читалось как
        14554 год. Разбор живого обмена: сообщения с настоящей страницы доходят,
        а точно такие же кадры от нашего клиента — нет. Единственным различием
        был идентификатор.
        """
        return dating_site_ulid()


class DatingSiteProfileGoldenbride(DatingSiteProfile):
    """Goldenbride: как называются поля анкеты и собеседницы.

    Перебор имён здесь не «на всякий случай»: сайт отдаёт анкету двумя разными
    ручками — кабинета и публичного профиля, — и поля в них зовутся по-разному.
    Порядок значим: первым стоит то имя, которое приходит чаще.
    """

    name = 'goldenbride'

    def _default_profile_fields(self) -> dict:
        return {'id': 'id',
                'name': ['firstName', 'name'],
                'age': 'age',
                'city': ['city', 'from'],
                'about': ['about', 'description'],
                'photo': ['url', 'photo']}

    def _default_contact_fields(self) -> dict:
        """У собеседницы номер приходит то `id`, то `ladyId` — второе в списках
        женского кабинета, где `id` занято номером диалога."""
        return {'uid': ['id', 'ladyId'],
                'name': 'firstName',
                'age': 'age',
                'city': 'from',
                'online': 'isOnline',
                'photo': ['url', 'photo']}
