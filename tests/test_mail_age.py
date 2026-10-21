"""Возрастное окно получателя: отказ, который виден до сочинения письма.

## ⚠⚠ Что чинится

Сайт отказывает холодному письму:

    403 "You do not match the criteria of age to send messages to this user
         (ERROR_MESSAGE_AGE_CRITERIA)"

Отказ вечный — пара закрывается навсегда (`block.py`). Но узнавали мы о нём
**после** сочинения: 205 строк `failed` в пуле рассылки и 208 записей запрета за
три недели, каждая ценой письма и похода к сайту.

А окно возраста приезжает **в том же списке**, по которому набирается пул:
`GET /users/{state}` отдаёт `AgeFrom` и `AgeTo` на каждого человека. Панель читала
из записи четыре поля из десяти и эти два выбрасывала.

## Что здесь проверяется

**Что окно доезжает** от сайта до записи человека.

**Что правило с запасом.** Отсекаем при промахе от двух лет: сверка 203 пар из
журнала с живыми окнами показала, что отказов внутри окна нет ни одного, но семь
разрешённых пар промахнулись ровно на год.

**Что окно отнимает анкету, а не адресата.** Ему сорок, нашей девушке тридцать —
письмо напишет та, чей возраст в окно попадает, а не ни одна.

**Что начатый разговор не отсекается.** Шесть из девятнадцати разрешённых пар с
промахом — те, где мужчина писал первым.
"""

import inspect

import pytest

from src.dating.mail_age import MAIL_AGE_SLACK, mail_age_fits, mail_age_nobody

pytestmark = pytest.mark.mail


def _girl(age: int, girl_id: int = 1) -> dict:
    """Анкета так, как её видит набор: возраст лежит в карточке кабинета."""
    return {'id': girl_id, 'profile': {'age': age} if age else {}}


def _man(low: int, high: int, uid: str = '1') -> dict:
    return {'uid': uid, 'name': 'Vincent', 'age_from': low, 'age_to': high}


class TestTheWindowDecides:
    """Основное правило: попадает ли возраст девушки в окно получателя."""

    def test_inside_the_window_is_fine(self):
        assert mail_age_fits(_girl(36), _man(30, 40))

    def test_on_the_edge_is_inside(self):
        """⚠ Границы включительно: «ищет 35–45» означает, что 35 подходит."""
        assert mail_age_fits(_girl(35), _man(35, 45))
        assert mail_age_fits(_girl(45), _man(35, 45))

    def test_a_year_off_still_passes(self):
        """⚠⚠ Запас в два года — не осторожность, а замер: у семи разрешённых
        сайтом пар промах был ровно год."""
        assert mail_age_fits(_girl(34), _man(35, 50))
        assert mail_age_fits(_girl(56), _man(40, 55))

    def test_two_years_off_is_refused(self):
        """Ровно та пара, на которой сайт отказывал: промах два года — минимум
        среди 63 отказов журнала."""
        assert not mail_age_fits(_girl(28), _man(30, 40))
        assert not mail_age_fits(_girl(33), _man(35, 70))

    def test_far_off_is_refused(self):
        assert not mail_age_fits(_girl(31), _man(45, 70))
        assert not mail_age_fits(_girl(36), _man(45, 50))

    def test_the_slack_is_the_measured_one(self):
        assert MAIL_AGE_SLACK == 2, \
            'запас меняли — проверь замер в заголовке mail_age.py'


class TestUnknownMeansWrite:
    """⚠ «Не знаем» значит «пишем»: ошибка в эту сторону стоит одного отказа."""

    def test_no_window_means_write(self):
        assert mail_age_fits(_girl(36), _man(0, 0))

    def test_half_a_window_still_works(self):
        """Сайт вправе отдать только одну границу — вторая ноль, и это «любой»."""
        assert mail_age_fits(_girl(60), _man(30, 0))
        assert not mail_age_fits(_girl(20), _man(30, 0))

    def test_no_girl_age_means_write(self):
        """⚠ Карточка кабинета бывает пустой, и возраст в ней — не всегда.

        Молчать по незнанию значило бы остановить рассылку у анкеты, которой
        просто не снимали страницу.
        """
        assert mail_age_fits(_girl(0), _man(45, 70))
        assert mail_age_fits({'id': 1}, _man(45, 70))

    def test_a_broken_window_does_not_break_the_pass(self):
        """Сайт вправе прислать мусор; двести человек не должны пропасть из-за
        одного поля."""
        assert mail_age_fits(_girl(36), {'uid': '1', 'age_from': 'сорок',
                                         'age_to': None})


class TestTheStartedTalkIsExempt:
    """⚠⚠ Он писал первым — пишем всегда."""

    def test_a_talked_pair_passes_any_window(self):
        assert mail_age_fits(_girl(31), _man(45, 70), talked=True)

    def test_without_the_mark_the_same_pair_is_refused(self):
        assert not mail_age_fits(_girl(31), _man(45, 70), talked=False)


class TestNobodyFits:
    """Причина словами — для случая, когда не подходит ни одна наша анкета."""

    def test_silence_while_someone_fits(self):
        girls = [_girl(31, 1), _girl(42, 2)]

        assert mail_age_nobody(girls, _man(40, 50)) == '', \
            'причина выдумана там, где письмо напишет вторая анкета'

    def test_the_reason_names_both_numbers(self):
        """⚠ «Не подходит по возрасту» читается как наша выдумка, а «он ищет
        45–70, нашим анкетам 28–37» — как правило сайта."""
        why = mail_age_nobody([_girl(28, 1), _girl(37, 2)], _man(45, 70))

        assert '45' in why and '70' in why and '28' in why and '37' in why, why

    def test_a_talked_pair_keeps_the_person(self):
        talked = {(1, '1')}

        assert mail_age_nobody([_girl(31, 1)], _man(45, 70), talked) == ''

    def test_one_girl_is_named_without_a_range(self):
        why = mail_age_nobody([_girl(31, 1)], _man(45, 70))

        assert '31' in why and '–31' not in why, why


class TestTheWindowTravels:
    """⚠ Окно обязано доехать от сайта до записи человека."""

    def test_the_api_rows_carry_the_window(self):
        from src.dating.people_api import _rows

        rows = _rows([{'AccountId': '1', 'Name': 'Vincent', 'Age': 49,
                       'AgeFrom': 25, 'AgeTo': 45}], online=True)

        assert rows[0]['age_from'] == 25 and rows[0]['age_to'] == 45

    def test_a_missing_window_is_zero_not_absent(self):
        """⚠ Поля обязаны быть всегда: `row['age_from']` читает правило отбора, и
        отсутствующий ключ уронил бы набор целиком."""
        from src.dating.people_api import _rows

        rows = _rows([{'AccountId': '1', 'Name': 'X'}], online=True)

        assert rows[0]['age_from'] == 0 and rows[0]['age_to'] == 0

    def test_a_string_window_is_understood(self):
        from src.dating.people_api import _rows

        rows = _rows([{'AccountId': '1', 'Name': 'X', 'AgeFrom': '25',
                       'AgeTo': None}], online=True)

        assert rows[0]['age_from'] == 25 and rows[0]['age_to'] == 0


class TestTheSelectionTakesTheFittingGirl:
    """⚠⚠ Окно отнимает **анкету**, а не адресата: письмо напишет подходящая."""

    @staticmethod
    def _people():
        return [{'uid': '1', 'name': 'Vincent', 'age_from': 40, 'age_to': 70}]

    def test_the_fitting_girl_gets_the_pair(self):
        from src.dating.mailing import _pairs

        girls = [_girl(31, 1), _girl(45, 2)]
        # ⚠ У каждой своя копия шаблона (2026-10-16): чужим текстом анкета не пишет.
        got = list(_pairs(girls, self._people(),
                          [{'id': 7, 'profile_id': 1}, {'id': 7, 'profile_id': 2}],
                          set()))

        assert len(got) == 1, 'адресат выпал, хотя одна анкета подходит'
        assert got[0][0]['id'] == 2, 'письмо досталось анкете вне окна'

    def test_nobody_fitting_drops_the_person(self):
        from src.dating.mailing import _pairs

        got = list(_pairs([_girl(31, 1)], self._people(),
                          [{'id': 7, 'profile_id': 1}], set()))

        assert got == [], 'письмо готовится тому, кто его не получит'

    def test_the_talked_pair_survives(self):
        from src.dating.mailing import _pairs

        got = list(_pairs([_girl(31, 1)], self._people(),
                          [{'id': 7, 'profile_id': 1}], set(), talked={(1, '1')}))

        assert len(got) == 1, 'начатый разговор отсечён возрастом'

    def test_the_make_reports_who_was_dropped(self):
        """⚠ Молчаливое исчезновение адресатов — то самое «круг работает, писем
        нет», за которое уже платили временем."""
        from src.dating.mailing import mailing_make

        code = inspect.getsource(mailing_make)

        assert 'mail_age_nobody' in code, 'набор снова теряет адресатов молча'


class TestCoverageCountsOnlyWriters:
    """⚠ Покрытие возрастом считают те, **кому есть чем писать**.

    Анкету без шаблона `_pairs` не берёт вовсе — её возраст не строит письмо.
    Учтём его — и человек, подходящий лишь безшаблонной, исчезнет из набора
    молча, а «никому не подошёл» наврёт числами.
    """

    @staticmethod
    def _stub(value):
        """Корутина, отдающая `value`: базы и походов наружу здесь нет ни одного."""
        async def one(*args, **kwargs):
            return value

        return one

    async def test_the_only_fitting_girl_without_templates_is_named(self,
                                                                    monkeypatch):
        from src.dating import mailing
        from src.uvicorn.routers.dating import mail_var

        # Диане 45, она под окно 40–70 — но шаблонов нет. Виктории 31, шаблон
        # есть — но она мимо окна. Прежний код видел покрытие в Диане и молчал.
        monkeypatch.setattr(mailing, '_girls', self._stub([_girl(45, 1), _girl(31, 2)]))
        monkeypatch.setattr(mailing, '_people', self._stub([_man(40, 70)]))
        monkeypatch.setattr(mailing, 'mailing_people_first',
                            lambda people, first: people)
        monkeypatch.setattr(mailing, '_taken', self._stub(set()))
        monkeypatch.setattr(mailing, '_talked', self._stub(set()))
        monkeypatch.setattr(mailing, '_mail_answered', self._stub(set()))
        monkeypatch.setattr(mailing, 'block_pairs', self._stub(set()))
        monkeypatch.setattr(mailing, 'mail_wait_uids', lambda site_id: [])
        monkeypatch.setattr(mail_var, 'mail_var_rows', self._stub([]))

        done = await mailing.mailing_make(
            999_777, [{'id': 7, 'profile_id': 2}], 50)

        assert done['made'] == 0
        assert done['skipped'] == 1, 'безшаблонная анкета снова притворилась покрытием'
        assert '40' in done['reasons'][0], done['reasons']
        assert '45' not in done['reasons'][0], \
            'в причине светится возраст той, кто писать не станет'
