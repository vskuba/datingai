"""Собеседники с сайта: разбор карточек, снятых сценарием.

Юнит-тесты без сети и браузера. Правила вынесены в чистые функции
(`src/dating/people.py`) именно затем: чтобы проверять их, не входя на
живой сайт под анкетой — каждый такой вход стоит десятков секунд и приближает
блокировку.

Записи в `ROWS` **сняты с живого прогона**, а не выдуманы: имя приходит склеенным
с возрастом через перевод строки и неразрывный пробел, номер анкеты живёт только в
ссылке, а фотография у первых карточек успевает быть заглушкой ленивой загрузки.
Придумать такую форму нельзя — её можно только увидеть.
"""

import pytest

from src.dating.people import people_rows

pytestmark = pytest.mark.profile
# Ответ сценария «избранные собеседники: список», как он пришёл с LadaDate.
ROWS = {'favorites': [
    {'name': 'Vincent pitchen\n,\xa0\n49',
     'link': 'https://ladadate.com/profile/812886-men-from-france',
     'photo': 'https://ladadate.com/content/photos/7f23711d.jpg',
     'status_class': 'people__item-content people__item-offline',
     'is_new': 'new'},
    {'name': 'Mot\n,\xa0\n65',
     'link': 'https://ladadate.com/profile/102348-men-from-australia',
     'photo': '/images/placeholder-loading.svg',
     'status_class': 'people__item-content people__item-online',
     'is_new': ''},
]}


class TestPeopleRows:
    """Разбор списка целиком."""

    def test_rows_parsed(self):
        rows = people_rows(ROWS, 'favorites')

        assert [row['uid'] for row in rows] == ['812886', '102348']
        assert rows[0]['name'] == 'Vincent Pitchen'
        assert rows[0]['age'] == 49

    def test_uid_comes_from_link(self):
        """Номер анкеты берётся **из ссылки**: отдельного поля под него сайт не
        отдаёт, а без номера человека нечем отличить."""
        rows = people_rows(ROWS, 'favorites')

        assert rows[1]['uid'] == '102348'

    def test_online_read_from_class(self):
        """«В сети» — по подстройке в классе карточки, а не по точному значению:
        рядом стоит `people__item-content`, и порядок сайт не гарантирует."""
        rows = people_rows(ROWS, 'favorites')

        assert rows[0]['online'] is False
        assert rows[1]['online'] is True

    def test_new_mark(self):
        rows = people_rows(ROWS, 'favorites')

        assert rows[0]['is_new'] is True
        assert rows[1]['is_new'] is False

    def test_lazy_photo_dropped(self):
        """Заглушка ленивой загрузки — не фотография.

        Сайт грузит картинки лениво, и у первых карточек в `src` успевает попасть
        `placeholder-loading.svg`. Оставь мы её — страница показала бы сломанную
        картинку вместо буквы имени.
        """
        rows = people_rows(ROWS, 'favorites')

        assert rows[0]['photo'].endswith('7f23711d.jpg')
        assert rows[1]['photo'] == '', 'заглушка должна отбрасываться'

    def test_row_without_link_falls_back_to_name(self):
        """Без ссылки и фотографии ключом становится имя.

        Так приходит список диалогов: ссылка там голая (`/chat#`), а фотография у
        части строк не успевает догрузиться. Отбрось мы такие записи — пропали бы
        именно те диалоги, ради которых кнопку и нажимают; поймано на живом
        кабинете, где из тринадцати терялись двое.
        """
        rows = people_rows({'favorites': [{'name': 'Без ссылки', 'link': ''}]},
                                  'favorites')

        assert len(rows) == 1
        assert rows[0]['uid'] == 'name:без ссылки'

    def test_row_without_anything_skipped(self):
        """Ни ссылки, ни фото, ни имени — записывать нечего."""
        assert people_rows({'favorites': [{'name': '', 'link': ''}]}, 'favorites') == []

    def test_template_row_skipped(self):
        """Скрытая строка-образец не человек.

        Сайт держит её в разметке и заполняет скриптом; своего признака у неё нет,
        и отсекается она по тексту. Без этого в списке диалогов появлялся
        собеседник по имени «Name, age».
        """
        rows = people_rows(
            {'favorites': [{'name': 'Name, age', 'link': '/chat#'}]}, 'favorites')

        assert rows == []

    def test_photo_uid_matches_across_pages(self):
        """Ключ по фотографии сводит человека из кабинета с ним же из чата.

        В чате фотография приходит миниатюрой (`…-s.jpg`), в кабинете — полной.
        Суффикс размера не часть номера: не отсеки его, и один человек показался
        бы в колонках дважды, а значок «есть переписка» не нашёл бы себе пару.
        """
        cabinet = people_rows({'x': [
            {'name': 'Semen', 'link': '/profile/181685-men',
             'photo': 'https://ladadate.com/content/photos/5fd22e8755dc4a8199d3570e9406bc4a.jpg'}]}, 'x')
        chat = people_rows({'x': [
            {'name': 'Semen', 'link': '/chat#',
             'photo': 'https://ladadate.com/content/photos/5fd22e8755dc4a8199d3570e9406bc4a-s.jpg'}]}, 'x')

        assert cabinet[0]['photo_uid'] == chat[0]['photo_uid']
        assert cabinet[0]['uid'] == '181685', 'из кабинета берётся настоящий номер'

    def test_duplicate_uid_skipped(self):
        """Повтор в одном списке — следствие ленивой подгрузки, а не двух людей.

        Карточка попадает в съёмку дважды, и показать одного человека двумя
        строками хуже, чем потерять дубль.
        """
        one = ROWS['favorites'][0]
        rows = people_rows({'favorites': [one, dict(one)]}, 'favorites')

        assert len(rows) == 1

    def test_garbage_is_empty_list(self):
        """Мусор вместо списка — пустой результат, а не исключение: ответ приходит
        из сети, и одна кривая запись не должна ронять колонку целиком."""
        assert people_rows({}, 'favorites') == []
        assert people_rows({'favorites': 'строка'}, 'favorites') == []
        assert people_rows({'favorites': [None, 42]}, 'favorites') == []
        assert people_rows(ROWS, 'не-тот-шаг') == []


class TestNameAge:
    """Имя и возраст из склейки, которую отдаёт разметка сайта."""

    def _one(self, name: str) -> dict:
        rows = people_rows(
            {'x': [{'name': name, 'link': '/profile/1-men-from-x'}]}, 'x')

        return rows[0]

    def test_name_with_space_survives(self):
        """Делим по **запятой**, а не по пробелу: у имён бывает пробел
        («Vincent pitchen»), и по нему фамилия ушла бы в возраст.

        ⚠ Фамилия возвращается **с большой буквы**: регистр имени правится тут же,
        в `people_rows` (`people_name_case`). Проверяется здесь именно деление —
        то, что в имени два слова, а не одно."""
        assert self._one('Vincent pitchen\n,\xa0\n49')['name'] == 'Vincent Pitchen'

    def test_age_parsed(self):
        assert self._one('Zahir\n,\xa0\n51')['age'] == 51

    def test_no_age(self):
        """Без возраста — ноль, а не догадка: показать чужой возраст хуже, чем не
        показать никакого."""
        found = self._one('Просто Имя')
        assert found['name'] == 'Просто Имя'
        assert found['age'] == 0

    def test_garbage_age(self):
        assert self._one('Имя\n,\xa0\nнет')['age'] == 0

    def test_empty_name(self):
        assert self._one('')['name'] == ''


class TestTheOldNamesStillWork:
    """⚠⚠ Сторож переходного периода: живые сценарии ещё шлют старые имена.

    Переезд трёхзаходный — сперва выкладывается этот код, потом миграция
    переименовывает шаги у соседа. Между двумя выкладками приходит **старое**
    написание, и не пойми мы его, на это время встали бы все списки собеседников
    разом.

    ⚠ Этот класс уходит вместе с третьим заходом — когда из `SCENARIO_FIELD_WAS`
    уберут старые имена. Пока таблица жива, жив и он.
    """

    def test_a_row_with_old_names_is_parsed(self):
        got = people_rows(
            {'избранные': [{'имя': 'Semen, 44',
                            'ссылка': '/profile/181685-men-from-france',
                            'фото': 'https://site.test/p.jpg',
                            'статус_класс': 'people__item-online',
                            'новый': 'NEW'}]},
            'favorites')

        assert len(got) == 1
        assert got[0]['uid'] == '181685'
        assert got[0]['name'] == 'Semen'
        assert got[0]['age'] == 44
        assert got[0]['online'] is True
        assert got[0]['is_new'] is True

    def test_the_old_field_name_is_found_too(self):
        """⚠ Старым бывает и имя **шага**, а не только полей внутри записи."""
        got = people_rows({'мужчины': [{'ссылка': '/profile/1-men'}]}, 'men')

        assert len(got) == 1
