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
ROWS = {'избранные': [
    {'имя': 'Vincent pitchen\n,\xa0\n49',
     'ссылка': 'https://ladadate.com/profile/812886-men-from-france',
     'фото': 'https://ladadate.com/content/photos/7f23711d.jpg',
     'статус_класс': 'people__item-content people__item-offline',
     'новый': 'new'},
    {'имя': 'Mot\n,\xa0\n65',
     'ссылка': 'https://ladadate.com/profile/102348-men-from-australia',
     'фото': '/images/placeholder-loading.svg',
     'статус_класс': 'people__item-content people__item-online',
     'новый': ''},
]}


class TestPeopleRows:
    """Разбор списка целиком."""

    def test_rows_parsed(self):
        rows = people_rows(ROWS, 'избранные')

        assert [row['uid'] for row in rows] == ['812886', '102348']
        assert rows[0]['name'] == 'Vincent Pitchen'
        assert rows[0]['age'] == 49

    def test_uid_comes_from_link(self):
        """Номер анкеты берётся **из ссылки**: отдельного поля под него сайт не
        отдаёт, а без номера человека нечем отличить."""
        rows = people_rows(ROWS, 'избранные')

        assert rows[1]['uid'] == '102348'

    def test_online_read_from_class(self):
        """«В сети» — по подстройке в классе карточки, а не по точному значению:
        рядом стоит `people__item-content`, и порядок сайт не гарантирует."""
        rows = people_rows(ROWS, 'избранные')

        assert rows[0]['online'] is False
        assert rows[1]['online'] is True

    def test_new_mark(self):
        rows = people_rows(ROWS, 'избранные')

        assert rows[0]['is_new'] is True
        assert rows[1]['is_new'] is False

    def test_lazy_photo_dropped(self):
        """Заглушка ленивой загрузки — не фотография.

        Сайт грузит картинки лениво, и у первых карточек в `src` успевает попасть
        `placeholder-loading.svg`. Оставь мы её — страница показала бы сломанную
        картинку вместо буквы имени.
        """
        rows = people_rows(ROWS, 'избранные')

        assert rows[0]['photo'].endswith('7f23711d.jpg')
        assert rows[1]['photo'] == '', 'заглушка должна отбрасываться'

    def test_row_without_link_falls_back_to_name(self):
        """Без ссылки и фотографии ключом становится имя.

        Так приходит список диалогов: ссылка там голая (`/chat#`), а фотография у
        части строк не успевает догрузиться. Отбрось мы такие записи — пропали бы
        именно те диалоги, ради которых кнопку и нажимают; поймано на живом
        кабинете, где из тринадцати терялись двое.
        """
        rows = people_rows({'избранные': [{'имя': 'Без ссылки', 'ссылка': ''}]},
                                  'избранные')

        assert len(rows) == 1
        assert rows[0]['uid'] == 'name:без ссылки'

    def test_row_without_anything_skipped(self):
        """Ни ссылки, ни фото, ни имени — записывать нечего."""
        assert people_rows({'избранные': [{'имя': '', 'ссылка': ''}]}, 'избранные') == []

    def test_template_row_skipped(self):
        """Скрытая строка-образец не человек.

        Сайт держит её в разметке и заполняет скриптом; своего признака у неё нет,
        и отсекается она по тексту. Без этого в списке диалогов появлялся
        собеседник по имени «Name, age».
        """
        rows = people_rows(
            {'избранные': [{'имя': 'Name, age', 'ссылка': '/chat#'}]}, 'избранные')

        assert rows == []

    def test_photo_uid_matches_across_pages(self):
        """Ключ по фотографии сводит человека из кабинета с ним же из чата.

        В чате фотография приходит миниатюрой (`…-s.jpg`), в кабинете — полной.
        Суффикс размера не часть номера: не отсеки его, и один человек показался
        бы в колонках дважды, а значок «есть переписка» не нашёл бы себе пару.
        """
        cabinet = people_rows({'x': [
            {'имя': 'Semen', 'ссылка': '/profile/181685-men',
             'фото': 'https://ladadate.com/content/photos/5fd22e8755dc4a8199d3570e9406bc4a.jpg'}]}, 'x')
        chat = people_rows({'x': [
            {'имя': 'Semen', 'ссылка': '/chat#',
             'фото': 'https://ladadate.com/content/photos/5fd22e8755dc4a8199d3570e9406bc4a-s.jpg'}]}, 'x')

        assert cabinet[0]['photo_uid'] == chat[0]['photo_uid']
        assert cabinet[0]['uid'] == '181685', 'из кабинета берётся настоящий номер'

    def test_duplicate_uid_skipped(self):
        """Повтор в одном списке — следствие ленивой подгрузки, а не двух людей.

        Карточка попадает в съёмку дважды, и показать одного человека двумя
        строками хуже, чем потерять дубль.
        """
        one = ROWS['избранные'][0]
        rows = people_rows({'избранные': [one, dict(one)]}, 'избранные')

        assert len(rows) == 1

    def test_garbage_is_empty_list(self):
        """Мусор вместо списка — пустой результат, а не исключение: ответ приходит
        из сети, и одна кривая запись не должна ронять колонку целиком."""
        assert people_rows({}, 'избранные') == []
        assert people_rows({'избранные': 'строка'}, 'избранные') == []
        assert people_rows({'избранные': [None, 42]}, 'избранные') == []
        assert people_rows(ROWS, 'не-тот-шаг') == []


class TestNameAge:
    """Имя и возраст из склейки, которую отдаёт разметка сайта."""

    def _one(self, name: str) -> dict:
        rows = people_rows(
            {'x': [{'имя': name, 'ссылка': '/profile/1-men-from-x'}]}, 'x')

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
