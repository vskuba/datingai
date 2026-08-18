"""Восстановление входа анкеты: когда пробовать и когда сдаться.

Вход на сайт живёт около суток. Когда он протухает, анкета уходит из сети молча:
сообщения перестают приходить, а сайт не сообщает об этом ничем, кроме отказа в
опросе. Здесь — правила, по которым смена держателя решает, пора ли входить заново.

Лестница пауз, а не постоянный интервал: половина причин временные (сайт лёг,
сосед недоступен), и они рассасываются за минуты; вторая половина требует человека,
и долбить сайт настоящим входом через браузер раз в минуту ради них нельзя.

Сутки — рубеж, за которым автомат перестаёт пробовать. Дальше это уже не сбой, а
поломка: пароль сменили, анкету закрыли, сайт просит подтверждение.
"""

from datetime import datetime

# Как часто смена сверяет вход анкет с сайтом.
DATING_REVIVE_PERIOD_SEC = 60

# Возраст входа, после которого он обновляется профилактически. Сессия сайта живёт
# около суток, и ловить её протухание в середине разговора незачем.
DATING_REVIVE_AGE_SEC = 20 * 3600

# Сколько терпим череду неудач, прежде чем перестать пробовать.
DATING_REVIVE_GIVE_UP_SEC = 24 * 3600

# Лестница пауз в минутах и хвост после неё.
DATING_REVIVE_STEPS_MIN = (1, 5, 15, 45, 90)
DATING_REVIVE_TAIL_MIN = 120


def dating_revive_delay(tries: int) -> int:
    """Сколько ждать до попытки номер `tries` (0 — первая), в секундах."""
    step = max(0, int(tries))
    minutes = (DATING_REVIVE_STEPS_MIN[step] if step < len(DATING_REVIVE_STEPS_MIN)
               else DATING_REVIVE_TAIL_MIN)

    return minutes * 60


def dating_revive_state(relogin_since: datetime | None, now: datetime) -> str:
    """Состояние анкеты: `ok`, `failing` (череда идёт) или `given_up` (сдались).

    Считается по времени, а не по наличию назначенной попытки: так ответ один и
    тот же у страницы, у журнала и у самой смены, чем бы ни кончился её прошлый круг.
    """
    if not relogin_since:
        return 'ok'

    lasted = (now - relogin_since).total_seconds()

    return 'given_up' if lasted >= DATING_REVIVE_GIVE_UP_SEC else 'failing'


def dating_revive_due(relogin_next_at: datetime | None, now: datetime) -> bool:
    """Настал ли срок очередной попытки."""
    return bool(relogin_next_at) and relogin_next_at <= now
