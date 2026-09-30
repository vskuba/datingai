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

from src.dating import revive_login, revive_round
from src.dating.revive import revive_plan
from src.dating.revive_round import (REVIVE_PROBE_PARALLEL,
                                            revive_round_run)

pytestmark = pytest.mark.profile

NOW = datetime(2026, 8, 18, 12, 0, 0)


def _profile(**kw) -> dict:
    """Строка учётки такой, какой её отдаёт `revive_db_profiles_kept`.

    Возраст входа приходит числом секунд, а не моментом времени: `logged_in_at`
    пишет `NOW()` базы, и вычитать его из `datetime.now()` контейнера значило бы
    сравнивать двое разных часов. Считает возраст сама база (`TIMESTAMPDIFF`).
    """
    row = {'id': 1, 'login': 'test', 'keep_online': 1, 'login_age_sec': 0,
           'relogin_since': None, 'relogin_tries': 0, 'relogin_next_at': None}
    row.update(kw)

    return row


def _profiles(count: int, **kw) -> list[dict]:
    """Пачка анкет одного сайта — там, где важно не решение, а порядок работы."""
    return [_profile(id=n, login=f'g{n}', site_id=1, **kw) for n in range(1, count + 1)]


def _login_stub(state: dict, pause=timedelta(0), ok: bool = True, opens: tuple = (),
                raises: BaseException = None, session_id: str = ''):
    """Вход-заглушка: считает, сколько их идёт разом, и двигает часы, как настоящий.

    Настоящий вход занимает минуту-две живого времени, и именно из-за этого круг не
    имеет права держать одно `now` на всех.

    Args:
        opens: вкладки, которые вход успевает открыть, парами `(номер, сайт)` —
            настоящий `login_run` заводит свою до того, как упасть.
        raises: чем вход обрывается. `None` — доходит до конца.
        session_id: номер вкладки в ответе: вход отдаёт её живой, когда сценарий
            вернул ошибку.
    """
    async def login_run(profile_id: int) -> dict:
        state['login_live'] += 1
        state['login_peak'] = max(state['login_peak'], state['login_live'])
        state['logins'].append(int(profile_id))
        for opened, site_id in opens:
            state['sessions'].append({'session_id': opened, 'name': f'g{profile_id}',
                                      'site_id': site_id})
        try:
            # Уступаем управление: будь входы параллельны, здесь бы они и наложились.
            await asyncio.sleep(0.01)
            state['clock'] += pause
        finally:
            state['login_live'] -= 1

        if raises is not None:
            raise raises

        # `ok`, а не `success`: именно так отвечает прогон сценария у соседа, и
        # заглушка обязана врать ровно как настоящий вход. Пока здесь стояло
        # `success`, круг считал провалом каждый удачный вход — а тесты этого не
        # видели, потому что повторяли ту же ошибку.
        return {'status': 'ok' if ok else 'error', 'session_id': session_id, 'error': ''}

    return login_run


@pytest.fixture
def round_stub(monkeypatch):
    """Круг с подменёнными краями: без базы, без соседа, без живого сайта.

    Подменяются соседи круга — тонкие функции из `revive_db`,
    `revive_site` и `revive_login` (они для того и выделены: тяжёлые
    импорты живут внутри них, и сюита запускается без зависимостей проекта).
    Порядок работы, счётчики и решения при этом настоящие — беды, ради которых
    написаны эти тесты, живут именно в них.
    """
    state = {'profiles': [], 'presence': {}, 'status': {}, 'plan_saved': True,
             'probe_pause': 0.0, 'clock': NOW, 'probe_raise': {},
             'probe_live': 0, 'probe_peak': 0, 'login_live': 0, 'login_peak': 0,
             'probed': [], 'saved': [], 'marked': [], 'errors': [], 'logins': [],
             # Анкеты выпавшего сайта: их не проверить, и они обязаны получить
             # пометку в карточку, а не остаться с прежним «всё хорошо».
             'missing': [], 'presence_why': 'у сайта не настроен опрос присутствия',
             # Живые вкладки соседа и те, что круг закрыл. Уборка — единственное,
             # что автомат делает с чужим пулом Chromium, и промах здесь виден
             # человеку: погашенная вкладка, в которой он работал.
             'sessions': [], 'closed': [], 'close_raises': None, 'logs': []}

    async def profiles_kept():
        return state['profiles']

    async def presence_of(site_id):
        presence = state['presence'].get(int(site_id), {'base': 'https://site'})

        return presence, ('' if presence else state['presence_why'])

    async def presence_missing_mark(profile, why):
        state['missing'].append((int(profile['id']), why))

    async def db_now():
        return state['clock']

    async def probe_one(profile, presence):
        state['probe_live'] += 1
        state['probe_peak'] = max(state['probe_peak'], state['probe_live'])
        try:
            await asyncio.sleep(state['probe_pause'])
        finally:
            state['probe_live'] -= 1
        state['probed'].append(int(profile['id']))
        broken = state['probe_raise'].get(int(profile['id']))
        if broken:
            raise broken

        return {'status': state['status'].get(int(profile['id']), 'online'),
                'unread': 0, 'error': '', 'ms': 1}

    async def probe_save(profile_id, probe):
        state['marked'].append((int(profile_id), probe))

    async def plan_save(profile_id, plan):
        state['saved'].append((int(profile_id), plan))

        return state['plan_saved']

    async def log(profile_id, kind, text):
        state['logs'].append((int(profile_id), kind, text))

    async def error_write(profile, why, **kw):
        state['errors'].append((int(profile['id']), why))

    async def sessions_list():
        # Отбор по имени и сайту — дело самой функции, а не заглушки: заглушка
        # изображает соседа, а он отдаёт все живые вкладки установки разом.
        return [dict(session) for session in state['sessions']]

    async def session_close(session_id):
        if not session_id:
            return
        if state['close_raises'] is not None:
            raise state['close_raises']
        state['closed'].append(str(session_id))
        state['sessions'] = [item for item in state['sessions']
                             if str(item.get('session_id')) != str(session_id)]

    # Подменяем **там, где имя связано**, а не там, где функция объявлена: круг
    # зовёт соседей по именам своего модуля, и патч по месту объявления до него бы
    # не дошёл. Уборка вкладок живёт в модуле входа и подменяется в нём же.
    for module, name, func in (
            (revive_round, 'revive_db_profiles_kept', profiles_kept),
            (revive_round, 'revive_site_presence', presence_of),
            (revive_round, 'revive_db_now', db_now),
            (revive_round, '_probe_one', probe_one),
            (revive_round, 'revive_db_probe_save', probe_save),
            (revive_round, 'revive_db_plan_save', plan_save),
            (revive_round, 'revive_db_log', log),
            (revive_round, 'revive_site_error_write', error_write),
            (revive_round, 'revive_db_site_missing', presence_missing_mark),
            (revive_login, 'revive_db_log', log),
            (revive_login, '_sessions_list', sessions_list),
            (revive_login, '_session_close', session_close)):
        monkeypatch.setattr(module, name, func)

    return state


def _apply(profile: dict, plan: dict) -> dict:
    """Учётка после круга: ровно то, что записал бы `revive_db_plan_save`.

    Нужна там, где проверяется не одно решение, а цепочка кругов: беда с лестницей
    видна только на последовательности, по одному вызову она выглядит безобидно.
    """
    profile.update({field: plan[field]
                    for field in ('relogin_since', 'relogin_tries', 'relogin_next_at')})

    return profile


class TestRevivePlan:

    def test_online_and_fresh_needs_nothing(self):
        """Вход жив и свеж — трогать нечего."""
        assert revive_plan(_profile(), online=True, now=NOW)['action'] == 'none'

    def test_online_but_old_is_refreshed(self):
        """Профилактика: входу больше двадцати часов — обновляем заранее.

        Откат заводим сразу: плановый вход тоже может не выйти, а без него круг лез
        бы за ним в браузер соседа каждую минуту. А вот тревогу не зажигаем — анкета
        в сети, человеку тут делать нечего.
        """
        profile = _profile(login_age_sec=21 * 3600)
        plan = revive_plan(profile, online=True, now=NOW)

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
        profile = _profile(login_age_sec=3600, logged_in_at=NOW - timedelta(hours=30))

        assert revive_plan(profile, online=True, now=NOW)['action'] == 'none'

    def test_no_age_means_old(self):
        """Возраст неизвестен (входа не было) — считаем вход протухшим и обновляем."""
        assert revive_plan(_profile(login_age_sec=None),
                                  online=True, now=NOW)['action'] == 'login'

    def test_planned_login_failure_waits_on_ladder(self):
        """Плановый вход не вышел, опрос всё ещё online — откат не стираем.

        Опрос идёт по старым cookie и говорит «жив», но `logged_in_at` не обновился:
        значит вход через браузер не прошёл, и повторять его надо по лестнице.
        Тревоги при этом по-прежнему нет: карточке мигать не о чем.
        """
        profile = _profile(login_age_sec=21 * 3600, relogin_tries=1,
                           relogin_next_at=NOW + timedelta(minutes=4))
        plan = revive_plan(profile, online=True, now=NOW)

        assert plan['action'] == 'none'
        assert plan['relogin_since'] is None
        assert plan['relogin_tries'] == 1
        assert plan['relogin_next_at'] == NOW + timedelta(minutes=4)

    def test_planned_login_retries_when_due(self):
        """Срок повторного планового входа настал — входим, лестница едет дальше."""
        profile = _profile(login_age_sec=21 * 3600, relogin_tries=1,
                           relogin_next_at=NOW - timedelta(seconds=1))
        plan = revive_plan(profile, online=True, now=NOW)

        assert plan['action'] == 'login'
        assert plan['relogin_since'] is None
        assert plan['relogin_tries'] == 2
        assert plan['relogin_next_at'] == NOW + timedelta(minutes=15)

    def test_offline_starts_chain(self):
        """Первый отказ начинает череду: попытка через минуту, а не сейчас."""
        plan = revive_plan(_profile(), online=False, now=NOW)

        assert plan['action'] == 'wait'
        assert plan['relogin_since'] == NOW
        assert plan['relogin_tries'] == 0
        assert plan['relogin_next_at'] == NOW + timedelta(minutes=1)

    def test_offline_waits_until_due(self):
        """Срок не настал — молчим, попытку не двигаем."""
        profile = _profile(relogin_since=NOW - timedelta(minutes=1),
                           relogin_next_at=NOW + timedelta(minutes=4), relogin_tries=1)
        plan = revive_plan(profile, online=False, now=NOW)

        assert plan['action'] == 'none'
        assert plan['relogin_since'] == NOW - timedelta(minutes=1)
        assert plan['relogin_tries'] == 1
        assert plan['relogin_next_at'] == NOW + timedelta(minutes=4)

    def test_offline_logs_in_when_due(self):
        """Срок настал — входим, и следующая попытка назначается по лестнице."""
        profile = _profile(relogin_since=NOW - timedelta(minutes=6),
                           relogin_next_at=NOW - timedelta(seconds=1), relogin_tries=1)
        plan = revive_plan(profile, online=False, now=NOW)

        assert plan['action'] == 'login'
        assert plan['relogin_tries'] == 2
        assert plan['relogin_next_at'] == NOW + timedelta(minutes=15)

    def test_gives_up_after_a_day(self):
        """Сутки прошли — попытки прекращаются, срок следующей стирается."""
        profile = _profile(relogin_since=NOW - timedelta(hours=25),
                           relogin_next_at=NOW - timedelta(minutes=1), relogin_tries=9)
        plan = revive_plan(profile, online=False, now=NOW)

        assert plan['action'] == 'give_up'
        assert plan['relogin_next_at'] is None

    def test_gives_up_only_once(self):
        """Сдача объявляется один раз: череда в базе останется, круг придёт ещё
        тысячу раз, а сосед и журнал должны увидеть только переход."""
        profile = _profile(relogin_since=NOW - timedelta(hours=30), relogin_next_at=None,
                           relogin_tries=12)
        plan = revive_plan(profile, online=False, now=NOW)

        assert plan['action'] == 'none'
        assert plan['relogin_since'] == NOW - timedelta(hours=30)
        assert plan['relogin_tries'] == 12
        assert plan['relogin_next_at'] is None

    def test_recovery_clears_alarm_but_keeps_ladder(self):
        """Анкета снова в сети — тревога гаснет, откат остаётся.

        Откат обнуляет только удавшийся вход (`login_run`). Стереть его здесь
        значило бы: сайт полежал минуту, встал — и круг снова волен ходить в браузер
        соседа каждую минуту, потому что паузу забыли.
        """
        profile = _profile(relogin_since=NOW - timedelta(hours=2), relogin_tries=4,
                           relogin_next_at=NOW + timedelta(minutes=90))
        plan = revive_plan(profile, online=True, now=NOW)

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
        profile = _profile(login_age_sec=23 * 3600,
                           relogin_since=NOW - timedelta(hours=3), relogin_tries=5,
                           relogin_next_at=NOW + timedelta(minutes=30))
        plan = revive_plan(profile, online=True, now=NOW)

        assert plan['action'] == 'clear'
        assert plan['relogin_since'] is None
        assert plan['relogin_tries'] == 5
        assert plan['relogin_next_at'] == NOW + timedelta(minutes=30)

    def test_online_never_gives_up(self):
        """Сутки тревоги, но опрос отвечает — сдачи нет, тревога гаснет.

        Сдача — про «нужен вход вручную». Пока анкета в сети, дёргать человека не о чем.
        """
        profile = _profile(login_age_sec=30 * 3600,
                           relogin_since=NOW - timedelta(hours=26), relogin_tries=14,
                           relogin_next_at=NOW + timedelta(hours=1))
        plan = revive_plan(profile, online=True, now=NOW)

        assert plan['action'] == 'clear'
        assert plan['relogin_since'] is None

    def test_online_and_due_clears_alarm_and_logs_in(self):
        """Тревога шла, анкета вернулась, вход протух и пауза вышла — оба сразу:
        тревогу гасим, вход обновляем, лестница едет дальше."""
        profile = _profile(login_age_sec=23 * 3600,
                           relogin_since=NOW - timedelta(hours=3), relogin_tries=3,
                           relogin_next_at=NOW - timedelta(seconds=1))
        plan = revive_plan(profile, online=True, now=NOW)

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
        profile = _profile(relogin_tries=4, relogin_next_at=NOW - timedelta(minutes=30))
        plan = revive_plan(profile, online=False, now=NOW)

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
        profile = _profile(relogin_tries=3, relogin_next_at=NOW + timedelta(minutes=40))
        plan = revive_plan(profile, online=False, now=NOW)

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
        profile = _profile()

        down = revive_plan(profile, online=False, now=NOW)
        assert down['action'] == 'wait'
        assert down['relogin_next_at'] == NOW + timedelta(minutes=1)

        first = revive_plan(_apply(profile, down), online=False,
                                   now=NOW + timedelta(minutes=1))
        assert first['action'] == 'login'
        assert first['relogin_tries'] == 1
        assert first['relogin_next_at'] == NOW + timedelta(minutes=6)

        blink = revive_plan(_apply(profile, first), online=True,
                                   now=NOW + timedelta(minutes=2))
        assert blink['action'] == 'clear'
        assert blink['relogin_since'] is None
        assert blink['relogin_next_at'] == NOW + timedelta(minutes=6)

        again = revive_plan(_apply(profile, blink), online=False,
                                   now=NOW + timedelta(minutes=3))
        assert again['action'] == 'wait'
        assert again['relogin_since'] == NOW + timedelta(minutes=3)
        assert again['relogin_tries'] == 1
        assert again['relogin_next_at'] == NOW + timedelta(minutes=6)

        # Второй вход — не раньше срока, назначенного первым: лестница цела.
        early = revive_plan(_apply(profile, again), online=False,
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
        round_stub['profiles'] = _profiles(8)
        round_stub['probe_pause'] = 0.05

        counts = await revive_round_run(login_run=_login_stub(round_stub))

        assert counts['checked'] == 8
        assert counts['online'] == 8
        assert round_stub['probe_peak'] == 8

    async def test_probes_do_not_flood(self, round_stub):
        """Разом — но не все: у соседа и у сайта есть предел, отсюда ограничитель."""
        round_stub['profiles'] = _profiles(REVIVE_PROBE_PARALLEL + 5)
        round_stub['probe_pause'] = 0.02

        await revive_round_run(login_run=_login_stub(round_stub))

        assert round_stub['probe_peak'] == REVIVE_PROBE_PARALLEL

    async def test_logins_go_one_at_a_time(self, round_stub):
        """Входы — строго по одному, и это требование не обсуждается.

        Каждый вход открывает вкладку Chromium (~143 МБ) в пуле, общем с соседом.
        Сайт лёг, все анкеты пошли входить разом — и пул падает вместе с чужой работой.
        """
        round_stub['profiles'] = _profiles(4, login_age_sec=21 * 3600)

        counts = await revive_round_run(login_run=_login_stub(round_stub))

        assert counts['tried'] == 4
        assert counts['revived'] == 4
        assert round_stub['login_peak'] == 1

    async def test_now_is_taken_for_every_profile(self, round_stub):
        """Время берётся на каждую анкету, а не одно на весь круг.

        Перед решением по второй анкете проходит вход первой — минута-две живого
        времени. С одним `now` на круг срок второй, наступивший за это время,
        читался бы как ненаступивший, и анкета ждала бы ещё круг — и так каждый раз,
        пока в круге есть хоть один вход.
        """
        round_stub['profiles'] = [
            _profile(id=1, login='g1', site_id=1, login_age_sec=21 * 3600),
            _profile(id=2, login='g2', site_id=1, relogin_since=NOW - timedelta(minutes=1),
                     relogin_tries=1, relogin_next_at=NOW + timedelta(minutes=2))]
        round_stub['status'] = {2: 'offline'}

        await revive_round_run(
            login_run=_login_stub(round_stub, pause=timedelta(minutes=5)))

        assert round_stub['logins'] == [1, 2]


class TestReviveRoundCleanup:
    """Уборка вкладок: автомат гасит ровно то, что открыл сам.

    Вкладка Chromium стоит ~143 МБ в пуле, общем с соседом, и смотреть на неё после
    автоматического входа некому — значит бросать её нельзя. Но и хватать лишнего
    нельзя тем более: закрытая чужая вкладка видна человеку сразу — это оборванные
    «Диалоги» или потерянный ручной вход.
    """

    def _profile_due(self):
        """Анкета, которой по плану положен вход прямо сейчас (вход протух по сроку)."""
        return _profiles(1, login_age_sec=21 * 3600)

    async def test_failed_login_closes_the_tab_it_opened(self, round_stub):
        """Сосед не ответил посреди входа — вкладку за собой закрываем.

        Номер её до нас не дошёл: исключение оборвало вход до ответа. Найти вкладку
        можно только сравнением с тем, что жило до входа.
        """
        round_stub['profiles'] = self._profile_due()

        counts = await revive_round_run(
            login_run=_login_stub(round_stub, opens=(('new', 1),),
                                  raises=RuntimeError('сосед не ответил')))

        assert round_stub['closed'] == ['new']
        assert counts['tried'] == 1
        assert counts['revived'] == 0

    async def test_cancelled_login_closes_the_tab_and_reraises(self, round_stub):
        """Смена кончилась посреди входа — убираем и пропускаем отмену наружу.

        `CancelledError` не наследник `Exception`, и без своей ветки вкладка пережила
        бы саму смену. А проглоченная отмена — это смена, которая не заканчивается.
        """
        round_stub['profiles'] = self._profile_due()

        with pytest.raises(asyncio.CancelledError):
            await revive_round_run(
                login_run=_login_stub(round_stub, opens=(('new', 1),),
                                      raises=asyncio.CancelledError()))

        assert round_stub['closed'] == ['new']

    async def test_returned_session_is_closed(self, round_stub):
        """Вход дошёл до конца и отдал живую вкладку — закрываем именно её.

        Так бывает, когда сценарий вернул ошибку: свои пути вход закрывает сам, а
        этот оставляет вызывающему — ручке вкладка нужна, автомату нет.
        """
        round_stub['profiles'] = self._profile_due()

        await revive_round_run(
            login_run=_login_stub(round_stub, ok=False, opens=(('kept', 1),),
                                  session_id='kept'))

        assert round_stub['closed'] == ['kept']

    async def test_tab_that_lived_before_the_login_is_not_touched(self, round_stub):
        """Чужую вкладку не трогаем — даже когда вход упал, не открыв ни одной.

        Исключение бывает и до вкладки: у сайта нет сценария входа, у анкеты нет
        пароля, сосед не ответил на запрос сайта, база отвалилась при чтении анкеты.
        Гася «всё под этим логином», автомат убивал бы вкладку, которую человек
        оставил себе после ручного входа или в которой прямо сейчас открыты
        «Диалоги». Случай «нет сценария входа» повторяется каждую попытку до самой
        сдачи — то есть держать вкладку открытой было нельзя вообще.
        """
        round_stub['profiles'] = self._profile_due()
        round_stub['sessions'] = [{'session_id': 'human', 'name': 'g1', 'site_id': 1}]

        await revive_round_run(
            login_run=_login_stub(round_stub,
                                  raises=RuntimeError('для сайта нет сценария входа')))

        assert round_stub['closed'] == []

    async def test_only_the_new_tab_is_closed(self, round_stub):
        """Своё закрываем, чужое оставляем — даже когда они рядом."""
        round_stub['profiles'] = self._profile_due()
        round_stub['sessions'] = [{'session_id': 'human', 'name': 'g1', 'site_id': 1}]

        await revive_round_run(
            login_run=_login_stub(round_stub, opens=(('new', 1),),
                                  raises=RuntimeError('сосед не ответил')))

        assert round_stub['closed'] == ['new']

    async def test_same_login_on_another_site_is_not_touched(self, round_stub):
        """Одинаковый логин на двух сайтах — это две анкеты, а не одна.

        Ища вкладки по одному имени, автомат гасил бы вкладку соседней анкеты, и обе
        такие анкеты вечно закрывали бы вход друг другу.
        """
        round_stub['profiles'] = self._profile_due()

        await revive_round_run(
            login_run=_login_stub(round_stub, opens=(('new', 1), ('twin', 2)),
                                  raises=RuntimeError('сосед не ответил')))

        assert round_stub['closed'] == ['new']

    async def test_broken_cleanup_does_not_hide_the_login_error(self, round_stub):
        """Уборка упала — настоящая ошибка входа всё равно доходит до журнала.

        Правило проекта: уборка работу не роняет. Без обёртки исключение из неё
        подменяло бы собой причину неудачи (строки «Войти не вышло» человек бы не
        увидел) и обрывало круг для всех остальных анкет.
        """
        round_stub['profiles'] = self._profile_due()
        round_stub['close_raises'] = RuntimeError('сосед не ответил на закрытие')

        counts = await revive_round_run(
            login_run=_login_stub(round_stub, opens=(('new', 1),),
                                  raises=RuntimeError('пароль не подошёл')))

        assert counts['tried'] == 1
        assert any(kind == 'fail' and 'Войти не вышло' in text
                   for _, kind, text in round_stub['logs'])


class TestReviveRoundProbeFailure:
    """Упавший опрос — беда одной анкеты, а не конец круга для всех остальных."""

    async def test_broken_probe_does_not_break_the_round(self, round_stub):
        """Опрос бросил — анкета считается не в сети, остальные проверяются дальше.

        Сейчас круг держится только на том, что `presence_probe` глушит своё
        внутри. Гарантии нет: cookie из `storage_state` читаются **до** его `try`, и
        кривое состояние (`cookies` списком строк) бросит наружу. Без перехвата круг
        обрывался бы целиком — и не разово: следующий круг споткнулся бы о ту же
        строку, и анкеты после сломанной перестали бы проверяться совсем.
        """
        round_stub['profiles'] = _profiles(3)
        round_stub['probe_raise'] = {2: TypeError('cookies списком строк')}

        counts = await revive_round_run(login_run=_login_stub(round_stub))

        assert counts['checked'] == 3
        # Две живые анкеты посчитаны, сломанная — нет, но круг дошёл до конца.
        assert counts['online'] == 2
        assert [profile_id for profile_id, _ in round_stub['saved']] == [1, 2, 3]

    async def test_broken_probe_counts_as_offline(self, round_stub):
        """Отказ опроса зажигает тревогу — как и любое другое «не в сети».

        Проглотить его тихо было бы хуже поломки: анкета висела бы в панели
        здоровой, потому что круг её просто не коснулся.
        """
        round_stub['profiles'] = _profiles(1)
        round_stub['probe_raise'] = {1: TypeError('cookies списком строк')}

        await revive_round_run(login_run=_login_stub(round_stub))

        profile_id, plan = round_stub['saved'][0]
        assert (profile_id, plan['action']) == (1, 'wait')
        assert plan['relogin_since'] == NOW
        assert 'TypeError' in round_stub['errors'][0][1]


class TestReviveRoundLoginResult:
    """Как круг читает ответ входа. Поймано на живой анкете прода, не тестами."""

    async def test_neighbour_ok_counts_as_success(self, round_stub):
        """Прогон сценария отвечает `ok` — это удача, а не провал.

        Проверка «status == success» считала неудачей каждый удачный вход: в
        журнале сразу после «Вход признан сайтом» появлялась строка «Войти не
        вышло» с пустой причиной, а счётчик восстановленных стоял на нуле.
        Неудача — это `error`; тем же правилом живут ручка входа и страница.
        """
        # Тревога уже горит, срок попытки настал — круг пойдёт входить.
        round_stub['profiles'] = [_profile(site_id=1, relogin_since=NOW - timedelta(minutes=6),
                                           relogin_tries=1,
                                           relogin_next_at=NOW - timedelta(seconds=1))]
        round_stub['status'] = {1: 'error'}

        counts = await revive_round_run(login_run=_login_stub(round_stub))

        assert round_stub['logins'] == [1]
        assert counts['revived'] == 1
        assert (1, 'ok', 'Вход восстановлен') in round_stub['logs']
        assert not [text for _, _, text in round_stub['logs'] if 'не вышло' in text]

    async def test_error_still_counts_as_failure(self, round_stub):
        """Настоящий провал остаётся провалом — иначе правило бесполезно."""
        round_stub['profiles'] = [_profile(site_id=1, relogin_since=NOW - timedelta(minutes=6),
                                           relogin_tries=1,
                                           relogin_next_at=NOW - timedelta(seconds=1))]
        round_stub['status'] = {1: 'error'}

        counts = await revive_round_run(login_run=_login_stub(round_stub, ok=False))

        assert counts['revived'] == 0
        assert [text for _, _, text in round_stub['logs'] if 'не вышло' in text]


class TestReviveRoundSiteMissing:
    """Сайт без настройки опроса: анкеты не проверить — но и молчать про них нельзя."""

    async def test_profiles_of_a_broken_site_are_marked(self, round_stub):
        """Каждая анкета выпавшего сайта получает пометку в карточку.

        Раньше круг просто пропускал их, а след оставался в логе контейнера,
        который никто не читает. Анкета висела в панели с прежним «всё хорошо» —
        хотя её не трогали неделю. Это ровно та беда «никто не замечает», ради
        которой всё и делалось.
        """
        round_stub['profiles'] = _profiles(3)
        round_stub['presence'] = {1: {}}

        counts = await revive_round_run(login_run=_login_stub(round_stub))

        assert [profile_id for profile_id, _ in round_stub['missing']] == [1, 2, 3]
        assert all('опрос' in why for _, why in round_stub['missing'])
        # Проверить их нечем — значит и опрашивать нечего, и входить незачем.
        assert (counts['online'], counts['tried']) == (0, 0)
        assert round_stub['probed'] == []

    async def test_marked_profiles_keep_their_ladder(self, round_stub):
        """Пометка — не тревога: лестницу она не двигает и вход не назначает.

        Анкета не объявлена вне сети, её просто нечем проверить. Зажечь тревогу
        значило бы гонять браузер соседа впустую: вход всё равно кончается тем же
        опросом, которого нет.
        """
        round_stub['profiles'] = _profiles(2)
        round_stub['presence'] = {1: {}}

        await revive_round_run(login_run=_login_stub(round_stub))

        assert round_stub['saved'] == []
        assert round_stub['logins'] == []

    async def test_healthy_site_marks_nobody(self, round_stub):
        """У настроенного сайта пометок нет вовсе — иначе она обесценится."""
        round_stub['profiles'] = _profiles(2)

        await revive_round_run(login_run=_login_stub(round_stub))

        assert round_stub['missing'] == []


class TestReviveRoundBackoff:
    """Откат — единственная защита соседа от входа раз в минуту, и она хрупкая."""

    async def test_lost_backoff_cancels_the_login(self, round_stub):
        """Запись отката не легла — входить в этом круге нельзя.

        `revive_db_plan_save` гасит исключение, как и положено журналам. Но если строка не
        записалась, а вход всё равно состоялся, следующий круг увидит прежний, уже
        наступивший срок и войдёт снова через минуту — и так до бесконечности. Это
        единственный путь, где лестницу можно превысить; пропущенная попытка
        дешевле вкладки Chromium раз в минуту.
        """
        round_stub['profiles'] = _profiles(2, login_age_sec=21 * 3600)
        round_stub['plan_saved'] = False

        counts = await revive_round_run(login_run=_login_stub(round_stub))

        assert round_stub['logins'] == []
        assert counts['tried'] == 0

    async def test_saved_backoff_lets_the_login_through(self, round_stub):
        """Откат записан — попытка идёт как обычно: пропускаем только потерю записи."""
        round_stub['profiles'] = _profiles(2, login_age_sec=21 * 3600)

        counts = await revive_round_run(login_run=_login_stub(round_stub))

        assert round_stub['logins'] == [1, 2]
        assert counts['tried'] == 2


class TestGiveUpTellsWhichSilenceItIs:
    """⚠⚠ «Сутки не выходит войти» и «сутки не пробовали» — разные беды.

    Прод, анкета #40 (Ladadate), 30.09 06:00. Строка «сутки не выходит войти,
    автовход остановлен» звала искать беду на сайте — а искать надо было у себя: за
    те сутки не было ни одной настоящей попытки. Все двенадцать остановил потолок
    неудач входа у своей же двери, защёлкнутый тремя браузерными таймаутами
    (`login_retry.py`).

    Разница не в вежливости формулировки, а в том, куда человек пойдёт смотреть:
    в журнал сайта или в карточку анкеты.
    """

    def _dead(self, **kw) -> list[dict]:
        """Анкета, которую сутки не удаётся вернуть, и срок попытки ещё не стёрт.

        Именно непустой `relogin_next_at` и делает круг «тем самым, который
        сдался»: следующий сотрёт срок, и сдача станет давней.
        """
        return [_profile(id=1, site_id=1,
                         relogin_since=NOW - timedelta(hours=25),
                         relogin_next_at=NOW - timedelta(minutes=1), **kw)]

    async def test_a_plain_give_up_keeps_its_words(self, round_stub):
        """Попытки шли и не вышли — говорим про сайт, как и раньше."""
        round_stub['profiles'] = self._dead()
        round_stub['status'][1] = 'offline'

        counts = await revive_round_run(login_run=_login_stub(round_stub))

        assert counts['given_up'] == 1
        assert any('сутки не выходит войти' in why
                   for _, why in round_stub['errors']), round_stub['errors']

    async def test_a_latched_ceiling_says_the_automation_never_tried(self, round_stub):
        """⚠⚠ Потолок исчерпан — значит попыток не было, и строка обязана это сказать."""
        round_stub['profiles'] = self._dead(login_fail_tries=3)
        round_stub['status'][1] = 'offline'

        counts = await revive_round_run(login_run=_login_stub(round_stub))

        assert counts['given_up'] == 1
        said = ' '.join(why for _, why in round_stub['errors'])
        assert 'потолок' in said, said
        assert 'не пробовал' in said, said
        # Журнал анкеты говорит то же: человек смотрит в него, а не в чужую вкладку.
        logged = ' '.join(text for _, kind, text in round_stub['logs'] if kind == 'fail')
        assert 'потолок' in logged, logged

    async def test_the_row_carries_the_counter(self):
        """⚠ Счётчик неудач обязан приезжать в круг: без него он не различит беды.

        Отбор анкет — единственное место, где это читается, и забытый столбец
        выглядел бы как «потолок никогда не исчерпан».
        """
        import inspect

        from src.dating import revive_db

        code = inspect.getsource(revive_db.revive_db_profiles_kept)

        assert 'login_fail_tries' in code, 'круг снова не видит потолка неудач'
