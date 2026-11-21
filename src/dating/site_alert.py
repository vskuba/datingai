"""Тревога сайта: что сломано у него прямо сейчас — мигающий значок на вкладке.

Внешние беды сайта приходят молча. 20.11.2026 Ladadate сменил кнопку входа со
`<button>` на `<a>`, и сутки не входила ни одна из 21 анкеты: вкладка писала
`0/21`, а это же число бывает и обычной ночью. Беду заметили по скриншоту.

Значок отвечает на вопрос **«сломано ли тут что-то не по нашей воле»** и
показывает причину подсказкой. Правил три, и каждое — про сайт целиком, а не
про одну анкету: беда одной девушки видна у её строки (⚠ в списке анкет).

1. **Вход не проходит у большинства анкет по расписанию.** Анкета с
   включённым расписанием, не в сети и с ошибкой входа — это не ночь, а отказ.
   Порог — больше половины и не меньше двух: одна анкета с протухшим паролем
   сайт не роняет.
2. **Сессия админ-панели не держится.** Статус проверки не `online` либо
   проверки давно не было — круг панели встал (`panel_round.py`).
3. **Отказы сайта со страницы «Ошибки»** (`error` с `site_id`). ⚠ Без
   `idle_*` — «работа стоит, никого нет в сети» это простой, а не поломка, — и
   без сбоев процессов `crash:*`: они про наш код, а не про сайт, и у них
   `site_id` ноль.

⚠ Тревогу считает **сервер**, одной функцией: своя арифметика на странице
разошлась бы с ним на первой правке. Едет она тем же тиком, что и счётчики
вкладок (`site_live.py`), — новой ручки и второго опроса не заводится.
"""

# Сколько минут без проверки панели считается «круг панели встал». Круг ходит
# раз в несколько минут (`panel_every`), и четверть часа тишины уже не задержка.
SITE_ALERT_PANEL_STALE_MIN = 15

# Порог тревоги по входам: доля анкет по расписанию, у которых вход не проходит,
# и наименьшее число таких анкет.
SITE_ALERT_LOGIN_SHARE = 0.5
SITE_ALERT_LOGIN_MIN = 2

# Длина причины в подсказке: подсказка браузера длинное не переносит.
SITE_ALERT_TEXT_MAX = 160


async def site_alert_rows(db) -> dict:
    """Тревоги всех сайтов: `{номер сайта: [причина словами, …]}`.

    Сайта без тревоги в ответе нет. Порядок причин — по правилам модуля:
    входы, панель, отказы.
    """
    found: dict = {}

    def add(site_id, text: str) -> None:
        text = ' '.join(str(text or '').split())
        if len(text) > SITE_ALERT_TEXT_MAX:
            text = text[:SITE_ALERT_TEXT_MAX - 1] + '…'
        found.setdefault(int(site_id), []).append(text)

    await db.execute(
        'SELECT site_id, COUNT(*) AS planned,'
        "       SUM(status <> 'online' AND last_error <> '') AS failing"
        '  FROM profile WHERE enabled = 1 AND schedule_on = 1'
        ' GROUP BY site_id')
    for row in await db.fetchall() or []:
        planned, failing = int(row['planned'] or 0), int(row['failing'] or 0)
        if failing >= SITE_ALERT_LOGIN_MIN and failing > planned * SITE_ALERT_LOGIN_SHARE:
            why = await _login_why(db, int(row['site_id']))
            add(row['site_id'], f'Вход не проходит у {failing} из {planned} анкет'
                                + (f': {why}' if why else ''))

    await db.execute(
        'SELECT site_id, panel_status, panel_error,'
        '       TIMESTAMPDIFF(MINUTE, panel_checked_at, NOW()) AS idle_min'
        '  FROM site WHERE is_gone = 0 AND panel_checked_at IS NOT NULL')
    for row in await db.fetchall() or []:
        if str(row['panel_status'] or '') != 'online':
            add(row['site_id'], 'Админ-панель: '
                                + (str(row['panel_error'] or '') or
                                   f"статус «{row['panel_status'] or 'нет'}»"))
        elif int(row['idle_min'] or 0) > SITE_ALERT_PANEL_STALE_MIN:
            add(row['site_id'], f"Админ-панель не проверялась {int(row['idle_min'])} мин"
                                ' — круг панели встал?')

    from src.dating.error import error_title

    await db.execute(
        "SELECT site_id, source, message FROM error"
        " WHERE site_id <> 0 AND source NOT LIKE 'idle\\_%'"
        "   AND source NOT LIKE 'crash:%' ORDER BY last_at DESC")
    for row in await db.fetchall() or []:
        add(row['site_id'], f"{error_title(str(row['source']))}: {row['message']}")

    return found


async def _login_why(db, site_id: int) -> str:
    """Чем кончаются входы: самый свежий отказ входа в журналах анкет сайта.

    ⚠ Из журнала, а не из `profile.last_error`: там у всех одно «вход не
    выполнен: нет cookie», а настоящая причина — строка сценария в журнале
    («Вход не прошёл на шаге 4: … header-auth__btn»).
    """
    await db.execute(
        'SELECT l.text FROM log l JOIN profile p ON p.id = l.profile_id'
        " WHERE p.site_id = %s AND l.level = 'fail' AND l.text LIKE 'Вход не прошёл%%'"
        '   AND l.created_at > NOW() - INTERVAL 1 DAY'
        ' ORDER BY l.id DESC LIMIT 1', (int(site_id),))
    row = await db.fetchone()

    return str((row or {}).get('text') or '').split('\n', 1)[0]
