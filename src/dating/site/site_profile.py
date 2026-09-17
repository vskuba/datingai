"""Разбор анкеты и собеседниц: что из ответа сайта становится карточкой.

Вторая половина драйвера сайта — рядом с `site_base.py`, но отдельным классом:
чат и анкета это разные разговоры. Чат идёт кадрами по сокету и различается
формой, анкета приходит обычным JSON и различается **именами полей**. Держать их
в одном классе значило бы файл на пятьсот строк, в котором половина методов
никогда не зовётся вместе со второй.

⚠ **`profile` и `card` — разные вещи, и путать их нельзя.** `profile` — учётка в
нашей базе (`profile_`), `card` — анкета, прочитанная с сайта. Номера у них
разные: у первой номер нашей строки, у второй номер на сайте. Так они зовутся и в
коде страниц (`.profile*` против `.card*`), и в `agent_ensure`. Смешение
этих двух уже стоило нам пустого кэша анкет: см. `profile_cache.py`.

## Настройка

Имена полей задаются сайтом, а не кодом. Список — перебор по порядку, первое
непустое побеждает; строка — одно имя:

    "profile": {
      "path": "/api/me", "field": "user",
      "fields": {"id": "id", "name": ["firstName", "name"], "age": "age",
                 "city": ["city", "from"], "about": ["about", "description"],
                 "photo": ["url", "photo"]}
    },
    "contacts": {
      "path": "/api/contacts", "field": "itemArray",
      "fields": {"uid": ["id", "ladyId"], "name": "firstName", "age": "age",
                 "city": "from", "online": "isOnline", "photo": ["url", "photo"]}
    }

Раздела `fields` нет — берутся умолчания драйвера.
"""

# Длины колонок `dating_contact`, под которые режется разобранное. Здесь, а не в
# драйвере сайта: ширина колонки — наше свойство, а не его.
DATING_SITE_CONTACT_UID_MAX = 32
DATING_SITE_CONTACT_NAME_MAX = 190
DATING_SITE_CONTACT_PHOTO_MAX = 500
DATING_SITE_CONTACT_CITY_MAX = 190


class DatingSiteProfile:
    """Сайт, отдающий анкету и собеседниц обычным JSON."""

    name = 'base'

    def __init__(self, presence: dict | None = None):
        presence = presence or {}
        self.presence = presence
        self.base = str(presence.get('base') or '')
        self.profile_fields = dict((presence.get('profile') or {}).get('fields')
                                   or self._default_profile_fields())
        self.contact_fields = dict((presence.get('contacts') or {}).get('fields')
                                   or self._default_contact_fields())

    # ── Умолчания. Подкласс сайта переопределяет их, а не разбор ────────────

    def _default_profile_fields(self) -> dict:
        return {}

    def _default_contact_fields(self) -> dict:
        return {}

    # ── Разбор ──────────────────────────────────────────────────────────────

    def card_parse(self, user: dict, login: str = '') -> dict:
        """Анкета учётки из ответа сайта.

        Args:
            user: то, что сайт отдал под своим полем анкеты.
            login: логин учётки — им подменяется номер, если сайт его не дал.
                Без него анкета осталась бы без опознавательного знака.

        Returns:
            dict: `id`, `name`, `photo`, `age`, `city`, `about`. Номер здесь —
            **с сайта**, и в нашу строку по нему не пишут (см. предупреждение
            вверху файла).
        """
        rule = self.profile_fields

        return {'id': self._pick(user, rule.get('id')) or login,
                'name': str(self._pick(user, rule.get('name')) or ''),
                'photo': self.photo_url(user),
                'age': self._pick(user, rule.get('age')) or 0,
                'city': str(self._pick(user, rule.get('city')) or ''),
                'about': str(self._pick(user, rule.get('about')) or '')}

    def contact_parse(self, item: dict) -> dict | None:
        """Собеседница из строки списка. `None` — записывать нечего.

        Без номера запись пропускается: ключ таблицы — пара `profile_id, uid`, и
        пустой `uid` склеил бы всех безымянных в одну строку.
        """
        rule = self.contact_fields

        uid = str(self._pick(item, rule.get('uid')) or '').strip()
        if not uid:
            return None

        return {'uid': uid[:DATING_SITE_CONTACT_UID_MAX],
                'name': str(self._pick(item, rule.get('name')) or '')[:DATING_SITE_CONTACT_NAME_MAX],
                'photo': self.photo_url(item)[:DATING_SITE_CONTACT_PHOTO_MAX],
                'age': int(self._pick(item, rule.get('age')) or 0),
                'city': str(self._pick(item, rule.get('city')) or '')[:DATING_SITE_CONTACT_CITY_MAX],
                'online': 1 if self._pick(item, rule.get('online')) else 0}

    def photo_url(self, user: dict) -> str:
        """Адрес фотографии: сайт отдаёт то полный, то путь от своего корня.

        Второй достраиваем хостом **при разборе**, а не при показе: страница
        подставляет значение в `<img src>` как есть, и путь без хоста браузер
        достроил бы до адреса панели, где такой картинки нет.
        """
        rule = self.profile_fields.get('photo') or self.contact_fields.get('photo')
        photo = str(self._pick(user, rule) or '').strip()
        if not photo or photo.startswith('http'):
            return photo

        return self.base.rstrip('/') + '/' + photo.lstrip('/')

    # ── Приватное ───────────────────────────────────────────────────────────

    @staticmethod
    def _pick(data: dict, names):
        """Первое непустое значение из перечисленных полей.

        Перебор, а не одно имя, потому что сайты зовут одно и то же по-разному —
        `firstName` и `name`, `about` и `description`. Порядок значим: первым
        стоит то имя, которое сайт использует чаще.
        """
        if not names:
            return None

        for name in ([names] if isinstance(names, str) else names):
            value = data.get(name)
            if value not in (None, '', 0, False):
                return value

        # Ни одно не подошло — возвращаем значение первого имени, а не None:
        # у поля мог быть законный ноль (возраст не указан, офлайн).
        first = names if isinstance(names, str) else (names[0] if names else '')

        return data.get(first)
