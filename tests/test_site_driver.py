"""Выбор драйвера сайта и разговор по настройке, без своего файла.

Ради этого протокол и выносили: **новый сайт не должен требовать правки Python.**
Если его кадры укладываются в плоский JSON с именем метода, хватает разделов
`chat` и `send` в настройке (`browser_site.presence`); если нет — заводится файл
рядом с `site_goldenbride.py`, и вот тогда это уже код.

Проверяется здесь именно первое: выдуманный сайт, описанный одной настройкой,
разбирает свои кадры и собирает свои. Ни одной строки под него не написано.
"""

import json

import pytest

from src.dating.site.site_base import DatingSiteChat
from src.dating.site.site_goldenbride import DatingSiteChatGoldenbride
from src.dating.site.site_registry import (DATING_SITE_DRIVER_DEFAULT, site_registry_chat)

pytestmark = pytest.mark.profile
# Выдуманный сайт: те же понятия, другие имена и другая форма. Тело кадра лежит
# прямо в нём, а не строкой с JSON внутри, — так делает большинство.
OTHER_SITE = {
    'driver': 'base',
    'chat': {
        'subprotocol': 'messaging',
        'hello': ['ready'],
        'ask': [{'method': 'history', 'to': 'peers', 'suffix': ';'}],
        'incoming': {'method': 'msg.new', 'body': 'data', 'body_json': False,
                     'uid': 'from', 'name': 'fromName', 'text': 'body',
                     'confirm_id': 'ackId'},
        'pending': {'method': 'msg.queued', 'body': 'items', 'body_json': False,
                    'uid': 'from', 'text': 'body', 'confirm_id': 'ackId'},
        'confirm': {'method': 'msg.ack'},
    },
    'send': {
        'transport': 'ws',
        'frame': {'to': '{{to}}', 'body': '{{text}}', 'op': 'msg.send', 'id': '{{uid}}'},
        'before': [{'method': 'typing', 'to': 'to'}],
        'ack': {'ok_mark': 'msg.delivered', 'refuse_mark': 'rejected',
                'offline_mark': 'peer.away'},
    },
}


class TestRegistry:
    """Кто с кем разговаривает."""

    def test_named_driver_wins(self):
        assert isinstance(site_registry_chat({'driver': 'goldenbride'}),
                          DatingSiteChatGoldenbride)
        assert type(site_registry_chat({'driver': 'base'})) is DatingSiteChat

    def test_unknown_name_falls_back(self):
        """Опечатка в закладке — не отказ.

        Разбираться с ней посреди живого чата поздно: сообщения идут сейчас, и
        молчащий сокет хуже, чем сокет, говорящий умолчанием.
        """
        driver = site_registry_chat({'driver': 'нет-такого'})

        assert driver.name == DATING_SITE_DRIVER_DEFAULT

    def test_default_is_goldenbride_while_bookmarks_are_unmarked(self):
        """Умолчание — Goldenbride, и это **переходная мера**, а не идеал.

        На день переноса в закладках был один сайт, и его настройка не содержала
        ни `driver`, ни описания кадров: протокол жил константами в коде. Возьми
        реестр базовый драйвер — приём и отправка замолчали бы ровно тем
        способом, каким этот протокол ломался всегда: без ошибки, без отказа.

        Тест сторожит именно это. Менять его следует вместе с простановкой
        `driver` во всех закладках — и не раньше.
        """
        assert DATING_SITE_DRIVER_DEFAULT == 'goldenbride'
        assert isinstance(site_registry_chat({}), DatingSiteChatGoldenbride)
        assert isinstance(site_registry_chat(None), DatingSiteChatGoldenbride)


class TestSiteByConfigOnly:
    """Сайт, описанный одной настройкой: ни строки Python под него."""

    @pytest.fixture
    def site(self):
        return site_registry_chat(OTHER_SITE)

    def test_subprotocol_from_config(self, site):
        assert site.subprotocol() == 'messaging'

    def test_hello_from_config(self, site):
        assert site.is_hello('{"op":"ready"}') is True
        assert site.is_hello('{"op":"connectFromServer"}') is False

    def test_incoming_parsed_by_config(self, site):
        frame = json.dumps({'method': 'msg.new',
                            'data': {'from': '77', 'fromName': 'Lena',
                                     'body': 'привет', 'ackId': 'a-1'}})

        assert site.incoming_parse(frame, 'we@example.com') == {
            'sender_uid': '77', 'sender_name': 'Lena',
            'text': 'привет', 'message_uid': 'a-1'}

    def test_foreign_method_ignored(self, site):
        """Кадры Goldenbride для этого сайта — чужие, и наоборот."""
        goldenbride = json.dumps({'method': 'receivePrivateMessage',
                                  'message': json.dumps({'senderUid': '1', 'message': 'x'})})

        assert site.incoming_parse(goldenbride, '') is None

    def test_own_echo_dropped_by_config_field(self, site):
        frame = json.dumps({'method': 'msg.new',
                            'data': {'from': 'we@example.com', 'body': 'наше'}})

        assert site.incoming_parse(frame, 'we@example.com') is None

    def test_pending_parsed_by_config(self, site):
        frame = json.dumps({'method': 'msg.queued',
                            'items': [{'from': '77', 'body': 'первое', 'ackId': 'a-1'},
                                      {'from': '', 'body': 'без отправителя'},
                                      {'from': '88', 'body': 'второе', 'ackId': 'a-2'}]})
        found = site.pending_parse(frame)

        assert [m['sender_uid'] for m in found] == ['77', '88']
        assert site.is_pending(frame) is True

    def test_confirm_frame_by_config(self, site):
        frame = site.confirm_frame({'sender_uid': '77', 'message_uid': 'a-1'})

        assert frame['method'] == 'msg.ack'
        assert frame['messageIdForConfirm'] == 'a-1'

    def test_ask_frames_by_config(self, site):
        frames = site.ask_frames('77,88')

        assert frames == [{'toId': '', 'content': '', 'method': 'history', 'peers': '77,88;'}]

    def test_send_frame_by_config(self, site):
        assert site.send_frame('77', 'привет', 'u-1') == {
            'to': '77', 'body': 'привет', 'op': 'msg.send', 'id': 'u-1'}

    def test_prelude_by_config(self, site):
        assert site.prelude_frames('77') == [{'toId': '', 'content': '',
                                              'method': 'typing', 'to': '77'}]

    @pytest.mark.parametrize('answer,ok,offline', [
        ('{"op":"msg.delivered","id":"u-1"}', True, False),
        ('{"op":"peer.away"}', False, True),
        ('{"op":"rejected","why":"no credits"}', False, False),
        ('', False, False),
    ])
    def test_ack_read_by_config(self, site, answer, ok, offline):
        read = site.ack_read(answer, 'u-1')

        assert read['ok'] is ok
        assert bool(read.get('offline')) is offline

    def test_message_uid_is_not_ulid_without_asking(self, site):
        """ULID — свойство Goldenbride, а не всех сайтов.

        Формат идентификатора настройкой не описать: он либо важен сайту, либо
        нет, и знает об этом только драйвер. Базовый даёт обычную случайную
        строку — сайту, которому нужен ULID, полагается свой файл.
        """
        assert len(site.message_uid()) != len(site_registry_chat({}).message_uid())
