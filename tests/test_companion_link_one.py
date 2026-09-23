"""Имя собеседника ведёт на его карточку — одинаково во всех пулах.

## ⚠⚠ Зачем

Пулы стоят рядом, в одной полосе подвкладок: рассылка, инвайты, переписка с
избранными. Имя адресата вело на «Анкеты» только у рассылки — у соседних оно было
простым текстом, и, чтобы посмотреть, кому пишем, человек уходил искать номер
руками.

⚠ **Адрес собирают две функции на весь раздел** — `companionHref` для собеседника
и `girlHref` для анкеты. Своя сборка в каждом месте разошлась бы на первой правке:
забудь одна из копий ключ `site`, и «Анкеты» открылись бы на том сайте, что был
выбран там в прошлый раз, — молча и не каждый раз.

## ⚠ Почему проверяется вызов, а не вид ссылки

Вид — дело оформления, и он меняется. Неизменно другое: адрес собирается **одним**
местом. Поэтому тест спрашивает «зовут ли общую функцию», а не «какой получился
`href`»: второе краснело бы от каждой правки подсказки.
"""

import pathlib
import re

import pytest

pytestmark = pytest.mark.page

PAGE = (pathlib.Path(__file__).resolve().parents[1]
        / 'data/uvicorn/template/admin/dating/profile.html')


class TestTheAddressIsBuiltOnce:
    """⚠⚠ Главное: адрес «Анкет» собирают две функции, и только они."""

    def test_the_builders_exist(self):
        page = _page()

        assert 'function companionHref(' in page, 'пропала сборка адреса собеседника'
        assert 'function girlHref(' in page, 'пропала сборка адреса анкеты'

    def test_the_site_key_travels(self):
        """⚠ Без `site` «Анкеты» откроются на чужом сайте.

        `uid` уникален внутри сайта, а не вообще, и ключ выбранного сайта общий на
        весь раздел.
        """
        for name in ('companionHref', 'girlHref'):
            assert 'siteId()' in _body(name), f'{name} потеряла ключ сайта'

    def test_nobody_builds_the_address_by_hand(self):
        """⚠⚠ Сторож приёма: `?companion=` и `?girl=` пишутся в одном месте.

        ⚠ **Первая версия этого теста ловила только сборку одной строкой** — и
        пропустила `personCardMark`, где адрес собран переносом:

            `href="${BASE}/admin/dating`
            + `?companion=${encodeURIComponent(uid)}&site=${siteId()}"`

        Комментарий там прямо говорил «ровно как в пуле писем», а собиралось
        заново. Поэтому считаем **метки сами по себе**, как бы вокруг них ни
        стояли кавычки и переносы: метка в коде значит сборку.

        ⚠⚠ Строки и комментарии здесь не различить — это не Python, а разметка,
        и разбирать её ради теста дороже, чем держать пояснения без меток.
        Поэтому в комментариях рядом метка пишется словами («ключ `site`»), а не
        `?companion=`.
        """
        page = _page()

        for mark, owner in (('?companion=', 'companionHref'),
                            ('?girl=', 'girlHref')):
            found = page.count(mark)

            assert found <= 1, \
                (f'{mark} встречается {found} раз — собирать адрес должна '
                 f'только {owner}; лишнее в строках '
                 f'{[page[:m.start()].count(chr(10)) + 1 for m in re.finditer(re.escape(mark), page)]}')


class TestEveryPoolLinksTheName:
    """⚠ Три пула отвечают на вопрос «кому пишем» одинаково."""

    def test_the_favourite_pool_links(self):
        """Пул переписки с избранными — тот, ради которого всё затевалось."""
        assert 'companionLink(one)' in _body('favMailCard'), \
            'в пуле избранных имя адресата снова простой текст'

    def test_the_mailing_pool_still_links(self):
        """⚠ Обратная сторона: у рассылки ссылка была первой и осталась."""
        page = _page()

        assert 'companionLink(row)' in page, \
            'у пула рассылки пропала ссылка на карточку адресата'

    def test_the_favourite_link_does_not_open_the_letter(self):
        """⚠⚠ Щелчок по имени уводит на «Анкеты», а не открывает письмо.

        Карточка щёлкается целиком, и без гашения всплытия окно письма всплыло бы
        **за спиной** новой вкладки: человек нашёл бы его, только вернувшись.

        ⚠ Спрашивается именно про **пул избранных**: у рассылки то же правило
        проверяет `test_profiles_tabs.py`, и ссылка там появилась раньше. Два
        пула — два места, где это можно забыть.
        """
        bound = _body('favMailCardsBind')

        assert '.mail-card-to' in bound and 'stopPropagation' in bound, \
            'щелчок по имени в пуле избранных открывает ещё и окно письма'


class TestTheLetterWindowShowsBothSides:
    """⚠ Окно письма: лицо, имя-ссылка и номер — с обеих сторон.

    Прежде там стояли две строки голого текста. Кому письмо, было видно, а вот
    открыть карточку этого человека — только уйдя искать его номер руками.
    """

    def test_both_sides_use_the_common_view(self):
        """⚠⚠ Тем же `talkSide`, что «Диалоги» и журнал доставки.

        ⚠ Спрашиваем у `letterSides` — общего помощника всех окон писем: сама
        разметка переехала туда, и проверять её у одного окна значило бы
        стеречь то место, где её больше нет.
        """
        body = _body('letterSides')

        assert body.count('talkSide(') == 2, \
            'окно письма рисует стороны не общим видом'

    def test_both_names_are_links(self):
        body = _body('letterSides')

        assert 'girlHref(' in body, 'имя девушки в окне письма не ссылка'
        assert 'companionHref(' in body, 'имя собеседника в окне письма не ссылка'

    def test_the_face_comes_from_the_row(self):
        """⚠ Лицо берётся из строки пула, своей ручки под фото нет.

        Поля ручки считали и раньше (`profile_photo`, `photo`) — не хватало
        только отрисовки, и у рассылки не хватало самих полей.
        """
        body = _body('letterSides')

        assert 'row.profile_photo' in body and 'row.photo' in body, \
            'окно письма перестало показывать лица'


class TestTheSideStaysUsableWithoutALink:
    """⚠ Ссылка у стороны разговора — **необязательная**, и это намеренно.

    Мест, где сторона рисуется, дюжина, а адрес есть не у всех: у анкеты без
    номера, у собеседника из старой строки пула. Где адреса нет, остаётся простой
    текст — ссылка в никуда обещает переход, которого не будет.
    """

    def test_the_argument_is_optional(self):
        assert "href = ''" in _body('talkSide'), \
            'ссылка у стороны разговора стала обязательной'

    def test_an_empty_uid_gives_no_link(self):
        """⚠ Пустой номер — пустой адрес, а не `?companion=`."""
        body = _body('companionHref')

        assert 'id ?' in body or 'if (!id)' in body, \
            'пустой номер собеседника даёт ссылку в никуда'


class TestBothLetterWindowsShareOnePlace:
    """⚠⚠ Окна писем рисуют пару сторон **одним** помощником.

    Их три — рассылка, ответы, письма избранным, — и вопрос у них один: от какой
    анкеты письмо и какому собеседнику. Собранные порознь, они и разошлись: у
    избранных стояли лицо и ссылка, у рассылки «Diana → 803656», голый номер.
    """

    def test_the_helper_exists(self):
        assert 'function letterSides(' in _page(), \
            'пропал общий помощник пары сторон'

    def test_both_windows_call_it(self):
        """⚠ И окно избранных, и окно рассылки — через него."""
        for name in ('favMailOpen', 'letterOpen', 'inviteOpen', 'replyOpen'):
            assert 'letterSides(' in _body(name), \
                f'{name} рисует стороны своей разметкой'

    def test_nobody_draws_the_pair_by_hand(self):
        """⚠⚠ Сторож приёма: строка «От кого» пишется в одном месте.

        Появится вторая — значит кто-то снова собрал пару руками, и она
        разойдётся с первой на первой же правке.
        """
        page = _page()
        found = page.count('letter-key">От кого')

        assert found == 1, \
            f'строка «От кого» собирается {found} раз — должен только letterSides'

    def test_the_server_gives_the_same_fields(self):
        """⚠ Поля обеим ручкам даёт общий кусок запроса.

        Помощник берёт `profile_photo`, `photo`, `profile_uid` как есть; собери
        их ручки по-своему — рассылка снова осталась бы без лиц.
        """
        import inspect

        from src.dating import pool_pair
        from src.uvicorn.routers.dating import favorite_mail, mailing

        assert 'pool_pair_select' in inspect.getsource(mailing), \
            'рассылка собирает имя и лицо собеседника своим запросом'
        assert 'pool_pair_select' in inspect.getsource(favorite_mail), \
            'избранные собирают имя и лицо собеседника своим запросом'
        assert 'profile_photo' in inspect.getsource(pool_pair.pool_pair_girl), \
            'общий кусок перестал отдавать лицо анкеты'


def _page() -> str:
    return PAGE.read_text(encoding='utf-8')


def _body(name: str) -> str:
    """Тело функции страницы — от объявления до закрывающей скобки её уровня."""
    page = _page()
    got = page[page.index(f'function {name}('):]

    return got[:got.index('\n    }')]
