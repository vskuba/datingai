"""Импорт анкет: разбор ответа сценария и правила слияния.

Юнит-тесты без сети и базы: правила вынесены в чистые функции
(`src/dating/import.py`) именно затем, чтобы проверять их не гоняя браузер.

Стерегут здесь три вещи, и все три — про молчаливые беды.

**Дубликаты.** Импорт нажимают повторно, и вторая карточка той же девушки — не
досадная мелочь: у неё своя переписка, свой журнал, и оператор отвечает в
половину диалогов. Поиск идёт по номеру сайта раньше логина, потому что логин у
анкеты меняется (номер → почта), а номер нет.

**Затирание ручной работы.** Импорт не имеет права трогать пароль, пометку,
автоответ и снятый вход: их заводил человек, а сайт про них ничего не знает.

**Пустое не считается изменением.** Сайт отдаёт не все поля и не всегда; считай
мы пустоту изменением, каждый импорт «обновлял» бы анкеты в никуда, стирая уже
известное.
"""

import pytest

from datetime import datetime

from src.dating.import_ import (import_card, import_match,
                                      import_photo, import_plan,
                                      import_rows)

pytestmark = pytest.mark.profile

NOW = datetime(2026, 8, 30, 12, 0, 0)
BASE = 'https://ladadate.com'

# Ответ сценария списка — в том виде, в каком его отдаёт `extract_rows` соседа.
# Снято с живого прогона, а не выдумано: имена полей русские, потому что так
# названы шаги сценария.
LIST_EXTRACT = {'анкеты': [
    {'имя': 'Diana', 'город': 'Kyiv, UA', 'номер': '643694',
     'ссылка': '/partner/agency/userprofile/edit/643694',
     'фото': '/content/photos/9a5770f8-s.jpg'},
    {'имя': 'Ekaterina', 'город': 'Kyiv, UA', 'номер': '622715',
     'ссылка': '/partner/agency/userprofile/edit/622715',
     'фото': '/content/photos/6b98b24b-s.jpg'},
]}

# Ответ сценария карточки — тоже с живого прогона.
CARD_EXTRACT = {'id': '643694', 'login': 'bogiladiana9021@bigmir.net', 'name': 'Diana',
                'city': 'Kyiv', 'country_id': '208', 'birth_year': '1990',
                'occupation': 'choreographer', 'about': 'Red-haired woman.',
                'photo': '/content/photos/9a5770f8.jpg'}


class TestImportRows:
    """Список анкет из ответа сценария."""

    def test_rows_parsed(self):
        rows = import_rows(LIST_EXTRACT)

        assert [row['site_uid'] for row in rows] == ['643694', '622715']
        assert rows[0]['name'] == 'Diana'
        assert rows[0]['city'] == 'Kyiv, UA'

    def test_row_without_number_skipped(self):
        """Без номера запись пропускается: по нему ищут анкету, и пустой склеил бы
        разные в одну."""
        rows = import_rows({'анкеты': [{'имя': 'Без номера', 'номер': ''}]})

        assert rows == []

    def test_garbage_is_empty_list(self):
        """Мусор вместо списка — пустой результат, а не исключение: ответ приходит
        из сети, и одна кривая строка не должна ронять импорт целиком."""
        assert import_rows({}) == []
        assert import_rows({'анкеты': 'строка'}) == []
        assert import_rows({'анкеты': [None, 42]}) == []


class TestImportCard:
    """Карточка одной анкеты."""

    def test_card_parsed(self):
        card = import_card(CARD_EXTRACT, NOW)

        assert card['login'] == 'bogiladiana9021@bigmir.net'
        # ⚠ Здесь `site_uid` — поле **разобранной записи** (`import_card`), а не
        # ключ сохраняемой карточки. Это разные вещи с разной судьбой: запись живёт
        # один заход импорта, карточка ложится в `profile.profile` и читается всеми.
        # Дубль убрали **в карточке** (`20260927140000`), см. `test_card_for_profile`.
        assert card['site_uid'] == '643694'
        assert card['age'] == 36, 'возраст считается из года рождения по текущему году'

    def test_occupation_joins_about(self):
        """Занятие дописывается к описанию: своей колонки под него нет, а человеку
        оно говорит о собеседнице больше, чем город."""
        card = import_card(CARD_EXTRACT, NOW)

        assert card['about'].startswith('choreographer. ')

    def test_bad_year_gives_zero_age(self):
        """Мусор в году рождения даёт ноль, а не возраст в две тысячи лет."""
        for year in ('', '—', '0', 'нет', '3000'):
            card = import_card({**CARD_EXTRACT, 'birth_year': year}, NOW)
            assert card['age'] == 0, f'год {year!r} дал возраст {card["age"]}'

    def test_empty_card_is_empty(self):
        assert import_card({}, NOW) == {}
        assert import_card({'name': 'Без номера и почты'}, NOW) == {}


class TestImportPhoto:
    """Фотография: страница подставляет её в `<img src>` как есть."""

    def test_relative_gets_host(self):
        assert import_photo('/content/photos/a.jpg', BASE) == f'{BASE}/content/photos/a.jpg'

    def test_full_url_untouched(self):
        full = 'https://cdn.example/a.jpg'
        assert import_photo(full, BASE) == full

    def test_empty_stays_empty(self):
        """Пусто остаётся пустым, а не голым хостом: иначе `<img>` попросил бы у
        сайта главную страницу вместо картинки."""
        assert import_photo('', BASE) == ''


class TestImportMatch:
    """Поиск своей учётки: чем считать «та же анкета»."""

    def test_found_by_site_uid_first(self):
        """**Номер сайта важнее логина.** Анкета, заведённая импортом без карточки,
        носит логин-номер; следующий импорт с карточкой приносит почту. Ищи мы по
        логину первым — та же девушка завелась бы второй раз.
        """
        by_uid = {'643694': {'id': 7, 'login': '643694', 'card': {}}}
        row = {'site_uid': '643694', 'login': 'bogiladiana9021@bigmir.net'}

        assert import_match(row, by_uid, {})['id'] == 7

    def test_found_by_login_when_no_uid(self):
        """Анкету, заведённую руками, номер сайта не знает — ищем по логину."""
        by_login = {'bogiladiana9021@bigmir.net': {'id': 9, 'login': 'x', 'card': {}}}
        row = {'site_uid': '643694', 'login': 'bogiladiana9021@bigmir.net'}

        assert import_match(row, {}, by_login)['id'] == 9

    def test_new_profile_is_none(self):
        assert import_match({'site_uid': '1'}, {}, {}) is None


class TestImportPlan:
    """Решение: завести, обновить или оставить."""

    def test_new_profile_is_created(self):
        plan = import_plan({'site_uid': '643694', 'login': 'a@b.c', 'name': 'Diana'},
                                  None, BASE)

        assert plan['action'] == 'create'
        assert plan['login'] == 'a@b.c'
        assert plan['card']['id'] == '643694'
        # ⚠⚠ Номер в сохраняемой карточке — **одним** ключом. Дубль
        # `site_uid` убран (`20260927140000`): его писал только импорт, вход
        # писал один `id`, и читатели разбрелись — кто спрашивал одно, кто
        # другое. У анкеты, заведённой входом, `pool_pair` показывал пустой
        # номер. Два имени одной величины разводят по смыслу, не заметив.
        assert 'site_uid' not in plan['card']

    def test_login_falls_back_to_site_uid(self):
        """Карточку не читали — логином становится номер: без него анкету нечем
        отличить, а колонка `login` обязательна."""
        plan = import_plan({'site_uid': '643694', 'name': 'Diana'}, None, BASE)

        assert plan['login'] == '643694'

    def test_unchanged_profile_is_same(self):
        """Повторный импорт тех же данных ничего не трогает: «обновлено 13» на
        каждом прогоне обесценивает счётчик, по которому и смотрят, что изменилось.
        """
        row = {'site_uid': '1', 'login': 'a@b.c', 'name': 'Diana', 'city': 'Kyiv'}
        found = {'id': 3, 'login': 'a@b.c',
                 'card': import_plan(row, None, BASE)['card']}

        plan = import_plan(row, found, BASE)

        assert plan['action'] == 'same'
        assert plan['changed'] == []

    def test_changed_fields_are_named(self):
        row = {'site_uid': '1', 'login': 'a@b.c', 'name': 'Diana', 'city': 'Kyiv'}
        found = {'id': 3, 'login': 'a@b.c',
                 'card': {**import_plan(row, None, BASE)['card'], 'city': 'Odesa'}}

        plan = import_plan(row, found, BASE)

        assert plan['action'] == 'update'
        assert plan['changed'] == ['city'], 'меняется город — его и называем'

    def test_empty_value_is_not_a_change(self):
        """Сайт не отдал город — это не «город стёрли». Иначе импорт без карточек
        затирал бы то, что дочитал импорт с карточками."""
        known = import_plan({'site_uid': '1', 'name': 'Diana', 'city': 'Kyiv',
                                    'about': 'текст'}, None, BASE)['card']
        found = {'id': 3, 'login': '1', 'card': known}

        plan = import_plan({'site_uid': '1', 'name': 'Diana'}, found, BASE)

        assert plan['action'] == 'same'
        assert plan['card']['city'] == 'Kyiv', 'известное осталось на месте'
        assert plan['card']['about'] == 'текст'

    def test_known_survives_a_real_update(self):
        """И то же самое, когда обновление всё-таки идёт.

        Тот случай, ради которого проверка и написана: список отдаёт новое имя, а
        города и описания в нём нет вовсе. Сливай мы карточку целиком, вместе с
        именем уехало бы в пустоту всё, что дочитал прошлый импорт, — и заметил бы
        это только человек, открывший анкету через неделю.
        """
        known = import_plan({'site_uid': '1', 'name': 'Diana', 'city': 'Kyiv',
                                    'about': 'текст'}, None, BASE)['card']
        found = {'id': 3, 'login': '1', 'card': known}

        plan = import_plan({'site_uid': '1', 'name': 'Дарья'}, found, BASE)

        assert plan['action'] == 'update' and plan['changed'] == ['name']
        assert plan['card']['name'] == 'Дарья', 'имя обновилось'
        assert plan['card']['city'] == 'Kyiv', 'город не стёрт пустотой из списка'
        assert plan['card']['about'] == 'текст'

    def test_login_of_found_profile_is_kept(self):
        """Логин найденной анкеты не переписывается **никогда**: его правит человек
        в карточке, и импорт, вернувший туда номер вместо почты, отобрал бы у него
        эту правку. Обновляется только кэш анкеты.
        """
        found = {'id': 3, 'login': 'человек@правил.сам', 'card': {}}

        plan = import_plan({'site_uid': '1', 'login': 'сайт@отдал.это'}, found, BASE)

        assert plan['login'] == 'человек@правил.сам'
