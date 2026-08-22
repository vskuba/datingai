"""Фотография собеседницы: в базу ложится адрес, который откроет браузер.

Сайт отдаёт то полный адрес на CDN, то путь от своего корня. Второй вариант
обязан дописываться хостом **при записи**, ровно как это делается для фотографии
самой анкеты: страница подставляет `photo` в `<img src>` как есть, и путь без
хоста браузер достроит до адреса панели, где такой картинки нет.

Проверяется без сети и базы: правило — чистая функция, а цена ошибки — молча
битые аватарки у всех собеседниц разом.
"""

from src.dating.dating_profile import _contact_values

# Настройка сайта из RhapsodyAi: интересен только корень.
PRESENCE = {'base': 'https://goldenbride.net/'}

GIRL_ID = 7

# Порядок полей строки `dating_contact` — как в INSERT.
PHOTO = 3


class TestContactPhoto:

    def test_relative_photo_gets_host(self):
        """Путь от корня сайта дописывается хостом.

        Тот самый случай: сайт отдаёт `/images/...` для собеседницы без фото, и
        без хоста браузер просил эту картинку у панели.
        """
        values = _contact_values({'id': '42', 'url': '/images/designNew/Default-image-07-Man.png'},
                                 GIRL_ID, PRESENCE)

        assert values[PHOTO] == 'https://goldenbride.net/images/designNew/Default-image-07-Man.png'

    def test_full_url_is_untouched(self):
        """Адрес на CDN уже полный — трогать его нечем."""
        photo = 'https://ddev6uq7v65vs.cloudfront.net/lady/42/thumbnailMedium.jpg'
        values = _contact_values({'id': '42', 'url': photo}, GIRL_ID, PRESENCE)

        assert values[PHOTO] == photo

    def test_missing_photo_stays_empty(self):
        """Без фотографии остаётся пусто, а не голый хост.

        Иначе `<img src="https://goldenbride.net/">` попросил бы у сайта главную
        страницу вместо картинки.
        """
        values = _contact_values({'id': '42'}, GIRL_ID, PRESENCE)

        assert values[PHOTO] == ''

    def test_row_without_uid_is_skipped(self):
        """Собеседница без идентификатора не записывается.

        Ключ таблицы — пара `girl_id, uid`, и пустой `uid` склеил бы всех
        безымянных в одну строку.
        """
        assert _contact_values({'firstName': 'Semen'}, GIRL_ID, PRESENCE) is None
