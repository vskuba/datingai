"""Две роли: что видит оператор анкет и чего не видит.

Роль решает один вопрос — пускать ли к устройству установки. Оператор работает с
анкетами, администратор дополнительно правит и выносит базу (seed, бэкапы,
phpMyAdmin в шапке).

Проверяется здесь не «скрыта ли кнопка», а **закрыт ли доступ**: скрытая вкладка
это удобство, а защита — отказ на самой ручке. Поэтому каждая админская точка
опрашивается напрямую, включая изменяющие.
"""

import pytest

from tests.conftest import ADMIN_USERNAME, DATING_USERNAME

# Точки, до которых оператора пускать нельзя. Метод указан рядом: `GET` на
# изменяющей ручке отдал бы 405 и «прошёл» бы проверку, ничего не проверив.
ROLE_ADMIN_ONLY = [
    ('GET', '/admin/seed'),
    ('GET', '/admin/backup'),
    ('GET', '/api/seed/table'),
    ('GET', '/api/seed/file'),
    ('POST', '/api/seed/generate'),
    ('POST', '/api/seed/apply/whatever.sql'),
    ('GET', '/api/seed/download/whatever.sql'),
    ('DELETE', '/api/seed/whatever.sql'),
    ('GET', '/api/backup/file'),
    ('POST', '/api/backup/create'),
    ('POST', '/api/backup/restore/whatever.sql.gz'),
    ('GET', '/api/backup/download/whatever.sql.gz'),
    ('DELETE', '/api/backup/whatever.sql.gz'),
    # Раздача прав на саму панель. Заведомо несуществующий номер не случайно: тот
    # же список прогоняется под администратором, и настоящий id здесь означал бы
    # проверку прав ценой удалённой учётки.
    ('GET', '/admin/users'),
    ('GET', '/api/users'),
    ('POST', '/api/users'),
    ('PUT', '/api/users/99999999'),
    ('DELETE', '/api/users/99999999'),
    ('POST', '/api/users/99999999/token'),
    ('DELETE', '/api/users/token/99999999'),
]

# То, ради чего оператор и заходит. Открывается — иначе роль бессмысленна.
ROLE_OPERATOR_ALLOWED = ['/', '/admin/dating', '/api/site']


class TestRole:

    async def test_roles_are_what_they_claim(self, auth_client, dating_client):
        """`/auth/me` называет роль: по ней страница решает, что рисовать."""
        admin = (await auth_client.get('/auth/me')).json()['result']
        operator = (await dating_client.get('/auth/me')).json()['result']

        assert admin['username'] == ADMIN_USERNAME and admin['role'] == 'admin'
        assert operator['username'] == DATING_USERNAME and operator['role'] == 'dating'

    @pytest.mark.parametrize('method,path', ROLE_ADMIN_ONLY)
    async def test_operator_is_refused(self, dating_client, method, path):
        """Оператору отказывают на каждой админской точке.

        403, а не 404: страница существует, и человек видит её у коллеги —
        «нельзя» понятнее, чем «нет такой».
        """
        response = await dating_client.request(method, path, json={})

        assert response.status_code == 403, f'{method} {path} ответил {response.status_code}'

    @pytest.mark.parametrize('method,path', ROLE_ADMIN_ONLY)
    async def test_admin_is_not_refused(self, auth_client, method, path):
        """Админа те же точки пускают.

        Сверяется именно отсутствие 403: несуществующий файл в пути даёт 404 или
        400, и это нормально — проверяется право, а не работа самой ручки.
        """
        response = await auth_client.request(method, path, json={})

        assert response.status_code != 403, f'{method} {path} отказал админу'

    @pytest.mark.parametrize('path', ROLE_OPERATOR_ALLOWED)
    async def test_operator_works_with_girls(self, dating_client, path):
        """Анкеты и сайты оператору открыты — ради этого роль и заведена."""
        response = await dating_client.get(path)

        assert response.status_code == 200, f'{path} закрыт оператору'

    async def test_navbar_hides_admin_sections(self, auth_client, dating_client):
        """В шапке оператора нет разделов «Настройки» и «Инструменты».

        Отдельно от проверки прав: отказ после нажатия читается как поломка
        панели, поэтому недоступное не показывается вовсе.
        """
        admin_page = (await auth_client.get('/admin/dating')).text
        operator_page = (await dating_client.get('/admin/dating')).text

        assert 'data-group="setting"' in admin_page
        assert 'data-group="tool"' in admin_page

        assert 'data-group="dating"' in operator_page, 'у оператора пропала и его вкладка'
        assert 'data-group="setting"' not in operator_page
        assert 'data-group="tool"' not in operator_page

    async def test_refusal_speaks_html_to_browser(self, dating_client):
        """Браузеру отказ приходит страницей, скрипту — прежним `{"detail": …}`.

        Голый JSON в окне браузера человек читает как поломку панели и идёт
        выяснять, почему она сломалась, вместо того чтобы попросить доступ.
        """
        page = await dating_client.get('/admin/backup', headers={'Accept': 'text/html'})
        api = await dating_client.get('/api/backup/file')

        assert page.status_code == 403
        assert 'text/html' in page.headers['content-type']
        assert 'администратора' in page.text

        assert api.status_code == 403
        assert api.json()['detail']

    async def test_other_codes_are_untouched(self, dating_client):
        """Обработчик отказа подменяет только 403.

        Он висит на всех `HTTPException`, и легко было бы завернуть в страницу
        отказа заодно 404 и 422 — тогда клиент API вместо причины получал бы
        разметку.
        """
        missing = await dating_client.get('/api/dating/girl/999999/log',
                                          headers={'Accept': 'text/html'})

        assert missing.status_code == 404
        assert 'application/json' in missing.headers['content-type']
