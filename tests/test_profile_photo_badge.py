"""Значок «отправка фотографий» строкой списка анкет.

## ⚠⚠⚠ Что здесь стережётся

Вопрос «кому из девятнадцати анкет можно слать снимки» задают по всему списку
сразу. Без значка на него отвечали бы открытием каждой карточки по очереди —
девятнадцать переходов ради четырёх ответов.

Проверяется и **дорога данных**: поле, не объявленное в `response_model`
ручки, наружу не уходит — молча, без единой ошибки. Та же грабля уже стоила
чипса `chat_on` (CLAUDE.md, «анкета в чате»), и ловится она только проверкой
самого ответа, а не разметки.
"""

from pathlib import Path

import pytest

pytestmark = pytest.mark.page

PAGE_FILE = Path('data/uvicorn/template/admin/dating/index.html')


@pytest.fixture(scope='module')
def page() -> str:
    """Шаблон страницы «Анкеты» как его поставляет ветка."""
    assert PAGE_FILE.exists(), f'шаблона нет: {PAGE_FILE}'

    return PAGE_FILE.read_text(encoding='utf-8')


def _badge(page: str) -> str:
    """Тело рисовальщика значка."""
    box = page[page.index('function photoBadge'):]

    return box[:box.index('function galleryBadge')]


class TestTheBadgeIsAlwaysThere:
    """⚠⚠ Показывается у **каждой** строки, в отличие от значка галереи.

    Там отсутствие и есть ответ («фотографий нет»), а здесь пропуск читался бы
    как «про эту анкету неизвестно» — и счёт по списку сбивался бы.
    """

    def test_it_never_returns_empty(self, page):
        body = _badge(page)

        assert "return ''" not in body, (
            'значок пропадает у части строк — вопрос «кому можно» перестаёт '
            'иметь ответ по всему списку')

    def test_it_is_painted_for_every_row(self, page):
        assert '+ photoBadge(row)' in page, 'значок не попал в строку списка'


class TestTheColourCarriesTheAnswer:
    """⚠⚠ Разрешение читается **яркостью**, а не вторым рисунком.

    Два разных значка пришлось бы запоминать; «серое — нельзя, цветное —
    можно» понятно сразу и без подсказки.
    """

    def test_the_state_rides_on_a_class(self, page):
        body = _badge(page)

        assert "' on'" in body, 'у разрешённой анкеты значок ничем не отличается'
        assert 'row.mail_photo_on' in body, (
            'значок не смотрит на флаг анкеты')

    def test_grey_is_the_default_and_colour_is_the_exception(self, page):
        css = page[page.index('.prof-photo {'):]
        css = css[:css.index('.profile > .prof-photo')]

        assert 'grayscale(1)' in css, 'запрещённая анкета не обесцвечена'
        assert '.prof-photo.on { filter: none;' in css, (
            'разрешённая анкета не становится цветной')

    def test_refusal_is_not_painted_as_trouble(self, page):
        """⚠ Серый, а не красный: запрет здесь обычное состояние.

        Красный на девятнадцати строках кричал бы о беде, которой нет.
        """
        css = page[page.index('.prof-photo {'):]
        css = css[:css.index('.profile > .prof-photo')]

        for alarm in ('#dc2626', '#b91c1c', '#ef4444', 'red'):
            assert alarm not in css, f'запрет покрашен тревожным ({alarm})'


class TestTheHintSaysWhatToDo:
    """⚠ Подсказка называет не только состояние, но и где его менять."""

    def test_both_states_are_explained(self, page):
        body = _badge(page)

        assert 'разрешена' in body and 'запрещена' in body
        assert 'галочка в карточке' in body, (
            'человек видит запрет и не знает, где его снять')


class TestTheFlagReachesThePage:
    """⚠⚠⚠ Поле обязано быть **в `response_model`**, иначе не уйдёт молча.

    Обработчик его отдаёт, FastAPI вырезает, страница не видит — и ни одной
    ошибки. Так уже терялся чипс `chat_on`; ловится это только проверкой
    самой модели ответа.
    """

    def test_the_model_declares_it(self):
        from src.uvicorn.routers.dating.profile_ import DatingProfileResponse

        assert 'mail_photo_on' in DatingProfileResponse.model_fields, (
            'поля нет в `response_model` — страница его не увидит, и молча')

    def test_the_query_selects_it(self):
        import inspect

        import src.uvicorn.routers.dating.profile_ as mod

        body = inspect.getsource(mod)

        assert 'g.mail_photo_on' in body, (
            'запрос списка не выбирает флаг — в ответе будет умолчание, '
            'одинаковое у всех анкет')

    async def test_the_handle_answers_with_it(self, girl, auth_client):
        """И живьём: ручка списка отдаёт флаг."""
        got = await auth_client.get('/api/dating/profile',
                                    params={'site_id': 999_343})

        assert got.status_code == 200
        rows = (got.json() or {}).get('result') or []
        mine = [one for one in rows if int(one.get('id') or 0) == int(girl)]

        assert mine, 'анкета сюиты не нашлась в списке'
        assert 'mail_photo_on' in mine[0], (
            'флаг не доехал до страницы — проверьте `response_model`')
