"""Круг восстановления: кого трогаем, кого нет, и что пишем в базу.

Настоящий вход сюда не заходит — он водит браузер соседа и стучится на живой сайт.
Поэтому вход подменяется заглушкой, а проверяется решение: пора входить или рано.

Главное, что здесь стерегут тесты, — тревога (`relogin_since`) и откат
(`relogin_tries` + `relogin_next_at`) живут порознь. Тревога зажигается только когда
анкеты нет в сети, а откат держит паузы между входами при любом поводе и переживает
гашение тревоги. Слить их обратно в одно — вернуть браузер соседа раз в минуту.

Второе, что здесь стерегут, — порядок работы круга: опросы разом, входы по одному.
Ошибка в любую сторону не видна глазом и стоит дорого: последовательные опросы
растягивают круг с минуты до десяти, параллельные входы кладут пул Chromium соседа.
"""

import asyncio
from datetime import datetime, timedelta

import pytest

from src.dating import dating_revive
from src.dating.dating_revive import (DATING_REVIVE_PROBE_PARALLEL, dating_revive_plan,
                                      dating_revive_round)

NOW = datetime(2026, 8, 18, 12, 0, 0)


def _girl(**kw) -> dict:
    """Строка учётки такой, какой её отдаёт `_girls_kept`.

    Возраст входа приходит числом секунд, а не моментом времени: `logged_in_at`
    пишет `NOW()` базы, и вычитать его из `datetime.now()` контейнера значило бы
    сравнивать двое разных часов. Считает возраст сама база (`TIMESTAMPDIFF`).
    """
    row = {'id': 1, 'login': 'test', 'keep_online': 1, 'login_age_sec': 0,
           'relogin_since': None, 'relogin_tries': 0, 'relogin_next_at': None}
    row.update(kw)

    return row


def _girls(count: int, **kw) -> list[dict]:
    """Пачка анкет одного сайта — там, где важно не решение, а порядок работы."""
    return [_girl(id=n, login=f'g{n}', site_id=1, **kw) for n in range(1, count + 1)]


def _login_stub(state: dict, pause=timedelta(0), ok: bool = True):
    """Вход-заглушка: считает, сколько их идёт разом, и двигает часы, как настоящий.

    Настоящий вход занимает минуту-две живого времени, и именно из-за этого круг не
    имеет права держать одно `now` на всех.
    """
    async def login_run(girl_id: int) -> dict:
        state['login_live'] += 1
        state['login_peak'] = max(state['login_peak'], state['login_live'])
        state['logins'].append(int(girl_id))
        # Уступаем управление: будь входы параллельны, здесь бы они и наложились.
        await asyncio.sleep(0.01)
        state['clock'] += pause
        state['login_live'] -= 1

        return {'status': 'success' if ok else 'error', 'session_id': '', 'error': ''}

    return login_run


@pytest.fixture
def round_stub(monkeypatch):
    """Круг с подменёнными краями: без базы, без соседа, без живого сайта.

    Подменяется ровно то, чем модуль выходит наружу, — несколько маленьких обёрток
    (они для того и выделены: тяжёлые импорты живут внутри функций, и сюита
    запускается без зависимостей проекта). Порядок работы, счётчики и решения при
    этом настоящие — беды, ради которых написаны эти тесты, живут именно в них.
    """
    state = {'girls': [], 'presence': {}, 'status': {}, 'plan_saved': True,
             'probe_pause': 0.0, 'clock': NOW,
             'probe_live': 0, 'probe_peak': 0, 'login_live': 0, 'login_peak': 0,
             'probed': [], 'saved': [], 'marked': [], 'errors': [], 'logins': []}

    async def girls_kept():
        return state['girls']

    async def presence_of(site_id):
        return state['presence'].get(int(site_id), {'base': 'https://site'})

    async def db_now():
        return state['clock']

    async def probe_one(girl, presence):
        state['probe_live'] += 1
        state['probe_peak'] = max(state['probe_peak'], state['probe_live'])
        try:
            await asyncio.sleep(state['probe_pause'])
        finally:
            state['probe_live'] -= 1
        state['probed'].append(int(girl['id']))

        return {'status': state['status'].get(int(girl['id']), 'online'),
                'unread': 0, 'error': '', 'ms': 1}

    async def probe_save(girl_id, probe):
        state['marked'].append((int(girl_id), probe))

    async def plan_save(girl_id, plan):
        state['saved'].append((int(girl_id), plan))

        return state['plan_saved']

    async def log(girl_id, kind, text):
        pass

    async def error_write(girl, why, **kw):
        state['errors'].append((int(girl['id']), why))

    for name, func in (('_girls_kept', girls_kept), ('_presence_of', presence_of),
                       ('_db_now', db_now), ('_probe_one', probe_one),
                       ('_probe_save', probe_save), ('_plan_save', plan_save),
                       ('_log', log), ('_error_write', error_write)):
        monkeypatch.setattr(dating_revive, name, func)

    return state


def _apply(girl: dict, plan: dict) -> dict:
    """Учётка после круга: ровно то, что записал бы `_plan_save`.

    Нужна там, где проверяется не одно решение, а цепочка кругов: беда с лестницей
    видна только на последовательности, по одному вызову она выглядит безобидно.
    """
    girl.update({field: plan[field]
                 for field in ('relogin_since', 'relogin_tries', 'relogin_next_at')})

    return girl


class TestRevivePlan:

    def test_online_and_fresh_needs_nothing(self):
        """Вход жив и свеж — трогать нечего."""
        assert dating_revive_plan(_girl(), online=True, now=NOW)['action'] == 'none'

    def test_online_but_old_is_refreshed(self):
        """Профилактика: входу больше двадцати часов — обновляем заранее.

        Откат заводим сразу: плановый вход тоже может не выйти, а без него круг лез
        бы за ним в браузер соседа каждую минуту. А вот тревогу не зажигаем — анкета
        в сети, человеку тут делать нечего.
        """
        girl = _girl(login_age_sec=21 * 3600)
        plan = dating_revive_plan(girl, online=True, now=NOW)

        assert plan['action'] == 'login'
        assert plan['relogin_since'] is None
        assert plan['relogin_tries'] == 1
        assert plan['relogin_next_at'] == NOW + timedelta(minutes=5)

    def test_age_is_taken_from_the_database_not_from_the_row(self):
        """Возраст входа — только число секунд из SQL; момент времени игнорируется.

        `logged_in_at` пишет `NOW()` базы, а вычитали его из `datetime.now()`
        контейнера. `TZ` не задан ни в одном compose, прод-MySQL живёт на чужом
        сервере со своими настройками — и профилактика «при двадцати часах»
        молча превращалась в «двадцать плюс-минус расхождение поясов».
        """
        girl = _girl(login_age_sec=3600, logged_in_at=NOW - timedelta(hours=30))

        assert dating_revive_plan(girl, online=True, now=NOW)['action'] == 'none'

    def test_no_age_means_old(self):
        """Возраст неизвестен (входа не было) — считаем вход протухшим и обновляем."""
        assert dating_revive_plan(_girl(login_age_sec=None),
                                  online=True, now=NOW)['action'] == 'login'

    def test_planned_login_failure_waits_on_ladder(self):
        """Плановый вход не вышел, опрос всё ещё online — откат не стираем.

        Опрос идёт по старым cookie и говорит «жив», но `logged_in_at` не обновился:
        значит вход через браузер не прошёл, и повторять его надо по лестнице.
        Тревоги при этом по-прежнему нет: карточке мигать не о чем.
        """
        girl = _girl(login_age_sec=21 * 3600, relogin_tries=1,
                     relogin_next_at=NOW + timedelta(minutes=4))
        plan = dating_revive_plan(girl, online=True, now=NOW)

        assert plan['action'] == 'none'
        assert plan['relogin_since'] is None
        assert plan['relogin_tries'] == 1
        assert plan['relogin_next_at'] == NOW + timedelta(minutes=4)

    def test_planned_login_retries_when_due(self):
        """Срок повторного планового входа настал — входим, лестница едет дальше."""
        girl = _girl(login_age_sec=21 * 3600, relogin_tries=1,
                     relogin_next_at=NOW - timedelta(seconds=1))
        plan = dating_revive_plan(girl, online=True, now=NOW)

        assert plan['action'] == 'login'
        assert plan['relogin_since'] is None
        assert plan['relogin_tries'] == 2
        assert plan['relogin_next_at'] == NOW + timedelta(minutes=15)

    def test_offline_starts_chain(self):
        """Первый отказ начинает череду: попытка через минуту, а не сейчас."""
        plan = dating_revive_plan(_girl(), online=False, now=NOW)

        assert plan['action'] == 'wait'
        assert plan['relogin_since'] == NOW
        assert plan['relogin_tries'] == 0
        assert plan['relogin_next_at'] == NOW + timedelta(minutes=1)

    def test_offline_waits_until_due(self):
        """Срок не настал — молчим, попытку не двигаем."""
        girl = _girl(relogin_since=NOW - timedelta(minutes=1),
                     relogin_next_at=NOW + timedelta(minutes=4), relogin_tries=1)
        plan = dating_revive_plan(girl, online=False, now=NOW)

        assert plan['action'] == 'none'
        assert plan['relogin_since'] == NOW - timedelta(minutes=1)
        assert plan['relogin_tries'] == 1
        assert plan['relogin_next_at'] == NOW + timedelta(minutes=4)

    def test_offline_logs_in_when_due(self):
        """Срок настал — входим, и следующая попытка назначается по лестнице."""
        girl = _girl(relogin_since=NOW - timedelta(minutes=6),
                     relogin_next_at=NOW - timedelta(seconds=1), relogin_tries=1)
        plan = dating_revive_plan(girl, online=False, now=NOW)

        assert plan['action'] == 'login'
        assert plan['relogin_tries'] == 2
        assert plan['relogin_next_at'] == NOW + timedelta(minutes=15)

    def test_gives_up_after_a_day(self):
        """Сутки прошли — попытки прекращаются, срок следующей стирается."""
        girl = _girl(relogin_since=NOW - timedelta(hours=25),
                     relogin_next_at=NOW - timedelta(minutes=1), relogin_tries=9)
        plan = dating_revive_plan(girl, online=False, now=NOW)

        assert plan['action'] == 'give_up'
        assert plan['relogin_next_at'] is None

    def test_gives_up_only_once(self):
        """Сдача объявляется один раз: череда в базе останется, круг придёт ещё
        тысячу раз, а сосед и журнал должны увидеть только переход."""
        girl = _girl(relogin_since=NOW - timedelta(hours=30), relogin_next_at=None,
                     relogin_tries=12)
        plan = dating_revive_plan(girl, online=False, now=NOW)

        assert plan['action'] == 'none'
        assert plan['relogin_since'] == NOW - timedelta(hours=30)
        assert plan['relogin_tries'] == 12
        assert plan['relogin_next_at'] is None

    def test_recovery_clears_alarm_but_keeps_ladder(self):
        """Анкета снова в сети — тревога гаснет, откат остаётся.

        Откат обнуляет только удавшийся вход (`dating_login_run`). Стереть его здесь
        значило бы: сайт полежал минуту, встал — и круг снова волен ходить в браузер
        соседа каждую минуту, потому что паузу забыли.
        """
        girl = _girl(relogin_since=NOW - timedelta(hours=2), relogin_tries=4,
                     relogin_next_at=NOW + timedelta(minutes=90))
        plan = dating_revive_plan(girl, online=True, now=NOW)

        assert plan['action'] == 'clear'
        assert plan['relogin_since'] is None
        assert plan['relogin_tries'] == 4
        assert plan['relogin_next_at'] == NOW + timedelta(minutes=90)

    def test_online_clears_alarm_even_when_login_is_old(self):
        """Ради этого всё и затевалось: сайт лежал, тревога шла, вход протух.

        Сайт встал, опрос отвечает — тревога обязана погаснуть, хотя `logged_in_at`
        так и остался старым. Прежде возраст входа держал тревогу зажжённой, и через
        сутки такой анкете выставлялось «сдались» при живом опросе.
        """
        girl = _girl(login_age_sec=23 * 3600,
                     relogin_since=NOW - timedelta(hours=3), relogin_tries=5,
                     relogin_next_at=NOW + timedelta(minutes=30))
        plan = dating_revive_plan(girl, online=True, now=NOW)

        assert plan['action'] == 'clear'
        assert plan['relogin_since'] is None
        assert plan['relogin_tries'] == 5
        assert plan['relogin_next_at'] == NOW + timedelta(minutes=30)

    def test_online_never_gives_up(self):
        """Сутки тревоги, но опрос отвечает — сдачи нет, тревога гаснет.

        Сдача — про «нужен вход вручную». Пока анкета в сети, дёргать человека не о чем.
        """
        girl = _girl(login_age_sec=30 * 3600,
                     relogin_since=NOW - timedelta(hours=26), relogin_tries=14,
                     relogin_next_at=NOW + timedelta(hours=1))
        plan = dating_revive_plan(girl, online=True, now=NOW)

        assert plan['action'] == 'clear'
        assert plan['relogin_since'] is None

    def test_online_and_due_clears_alarm_and_logs_in(self):
        """Тревога шла, анкета вернулась, вход протух и пауза вышла — оба сразу:
        тревогу гасим, вход обновляем, лестница едет дальше."""
        girl = _girl(login_age_sec=23 * 3600,
                     relogin_since=NOW - timedelta(hours=3), relogin_tries=3,
                     relogin_next_at=NOW - timedelta(seconds=1))
        plan = dating_revive_plan(girl, online=True, now=NOW)

        assert plan['action'] == 'login'
        assert plan['relogin_since'] is None
        assert plan['relogin_tries'] == 4
        assert plan['relogin_next_at'] == NOW + timedelta(minutes=90)

    def test_offline_chain_starts_ladder_from_scratch(self):
        """Откат исчерпан — лестница начинается с нуля: пауза давно вышла, никто её
        не занял, и наследовать от прошлой, закрытой беды нечего.

        Признак тут — прошедший срок попытки, а не пустая тревога: тревога гаснет от
        одного удачного опроса, и по ней «повод новый» ловилось бы посреди живой паузы.
        """
        girl = _girl(relogin_tries=4, relogin_next_at=NOW - timedelta(minutes=30))
        plan = dating_revive_plan(girl, online=False, now=NOW)

        assert plan['action'] == 'wait'
        assert plan['relogin_since'] == NOW
        assert plan['relogin_tries'] == 0
        assert plan['relogin_next_at'] == NOW + timedelta(minutes=1)

    def test_offline_keeps_live_backoff_when_alarm_lights_again(self):
        """Тревога погасла, откат ещё идёт, сайт снова упал — пауза обязана уцелеть.

        Тревога зажигается заново (анкеты нет в сети, карточке есть о чём мигать), но
        лестница остаётся на своей ступени: в браузер соседа мы уже сходили, и до
        назначенного срока туда ходить нельзя независимо от тревоги.
        """
        girl = _girl(relogin_tries=3, relogin_next_at=NOW + timedelta(minutes=40))
        plan = dating_revive_plan(girl, online=False, now=NOW)

        assert plan['action'] == 'wait'
        assert plan['relogin_since'] == NOW
        assert plan['relogin_tries'] == 3
        assert plan['relogin_next_at'] == NOW + timedelta(minutes=40)

    def test_flapping_site_does_not_shorten_the_ladder(self):
        """Сценарий, ради которого признак и переписан: сайт моргает раз в минуту.

        Один удачный опрос гасит тревогу, но не откат. Пока «повод новый» решался по
        пустой тревоге, следующая же строка «не online» обнуляла лестницу, и вход
        через браузер выходил через три минуты вместо предписанных пяти. С большими
        ступенями это схлопывало до минуты и двухчасовую паузу.
        """
        girl = _girl()

        down = dating_revive_plan(girl, online=False, now=NOW)
        assert down['action'] == 'wait'
        assert down['relogin_next_at'] == NOW + timedelta(minutes=1)

        first = dating_revive_plan(_apply(girl, down), online=False,
                                   now=NOW + timedelta(minutes=1))
        assert first['action'] == 'login'
        assert first['relogin_tries'] == 1
        assert first['relogin_next_at'] == NOW + timedelta(minutes=6)

        blink = dating_revive_plan(_apply(girl, first), online=True,
                                   now=NOW + timedelta(minutes=2))
        assert blink['action'] == 'clear'
        assert blink['relogin_since'] is None
        assert blink['relogin_next_at'] == NOW + timedelta(minutes=6)

        again = dating_revive_plan(_apply(girl, blink), online=False,
                                   now=NOW + timedelta(minutes=3))
        assert again['action'] == 'wait'
        assert again['relogin_since'] == NOW + timedelta(minutes=3)
        assert again['relogin_tries'] == 1
        assert again['relogin_next_at'] == NOW + timedelta(minutes=6)

        # Второй вход — не раньше срока, назначенного первым: лестница цела.
        early = dating_revive_plan(_apply(girl, again), online=False,
                                   now=NOW + timedelta(minutes=4))
        assert early['action'] == 'none'
        assert early['relogin_next_at'] == NOW + timedelta(minutes=6)


class TestReviveRoundOrder:
    """Порядок работы круга: опросы разом, входы по одному, время — на каждую анкету."""

    async def test_probes_go_at_once(self, round_stub):
        """Опросы идут разом: подряд они не укладываются в свою минуту.

        Опрос — три-четыре HTTP-запроса с таймаутом в десять секунд. У двухсот
        анкет, о которых говорит документация, последовательный обход занял бы минут
        семь: круг «раз в минуту» превратился бы в «раз в семь-десять», а задержка
        обнаружения росла бы вместе с числом анкет.
        """
        round_stub['girls'] = _girls(8)
        round_stub['probe_pause'] = 0.05

        counts = await dating_revive_round(login_run=_login_stub(round_stub))

        assert counts['checked'] == 8
        assert counts['online'] == 8
        assert round_stub['probe_peak'] == 8

    async def test_probes_do_not_flood(self, round_stub):
        """Разом — но не все: у соседа и у сайта есть предел, отсюда ограничитель."""
        round_stub['girls'] = _girls(DATING_REVIVE_PROBE_PARALLEL + 5)
        round_stub['probe_pause'] = 0.02

        await dating_revive_round(login_run=_login_stub(round_stub))

        assert round_stub['probe_peak'] == DATING_REVIVE_PROBE_PARALLEL

    async def test_logins_go_one_at_a_time(self, round_stub):
        """Входы — строго по одному, и это требование не обсуждается.

        Каждый вход открывает вкладку Chromium (~143 МБ) в пуле, общем с соседом.
        Сайт лёг, все анкеты пошли входить разом — и пул падает вместе с чужой работой.
        """
        round_stub['girls'] = _girls(4, login_age_sec=21 * 3600)

        counts = await dating_revive_round(login_run=_login_stub(round_stub))

        assert counts['tried'] == 4
        assert counts['revived'] == 4
        assert round_stub['login_peak'] == 1

    async def test_now_is_taken_for_every_girl(self, round_stub):
        """Время берётся на каждую анкету, а не одно на весь круг.

        Перед решением по второй анкете проходит вход первой — минута-две живого
        времени. С одним `now` на круг срок второй, наступивший за это время,
        читался бы как ненаступивший, и анкета ждала бы ещё круг — и так каждый раз,
        пока в круге есть хоть один вход.
        """
        round_stub['girls'] = [
            _girl(id=1, login='g1', site_id=1, login_age_sec=21 * 3600),
            _girl(id=2, login='g2', site_id=1, relogin_since=NOW - timedelta(minutes=1),
                  relogin_tries=1, relogin_next_at=NOW + timedelta(minutes=2))]
        round_stub['status'] = {2: 'offline'}

        await dating_revive_round(
            login_run=_login_stub(round_stub, pause=timedelta(minutes=5)))

        assert round_stub['logins'] == [1, 2]


class TestReviveRoundBackoff:
    """Откат — единственная защита соседа от входа раз в минуту, и она хрупкая."""

    async def test_lost_backoff_cancels_the_login(self, round_stub):
        """Запись отката не легла — входить в этом круге нельзя.

        `_plan_save` гасит исключение, как и положено журналам. Но если строка не
        записалась, а вход всё равно состоялся, следующий круг увидит прежний, уже
        наступивший срок и войдёт снова через минуту — и так до бесконечности. Это
        единственный путь, где лестницу можно превысить; пропущенная попытка
        дешевле вкладки Chromium раз в минуту.
        """
        round_stub['girls'] = _girls(2, login_age_sec=21 * 3600)
        round_stub['plan_saved'] = False

        counts = await dating_revive_round(login_run=_login_stub(round_stub))

        assert round_stub['logins'] == []
        assert counts['tried'] == 0

    async def test_saved_backoff_lets_the_login_through(self, round_stub):
        """Откат записан — попытка идёт как обычно: пропускаем только потерю записи."""
        round_stub['girls'] = _girls(2, login_age_sec=21 * 3600)

        counts = await dating_revive_round(login_run=_login_stub(round_stub))

        assert round_stub['logins'] == [1, 2]
        assert counts['tried'] == 2
