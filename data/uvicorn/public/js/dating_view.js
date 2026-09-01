// Как показывают учётку: значки, бейджи, подсказки, время.
//
// Общее для «Анкет» и «Профилей». Обе страницы рисуют одни и те же строки одной
// и той же ручки (`GET /api/dating/profile`), и две копии этой логики разошлись
// бы в мелочах — а мелочи здесь и есть смысл: пульсирующая точка отличает живую
// анкету от замершего экрана, мигающий ⚠ отличает «ещё пробуем» от «сдались».
//
// Разметка тут же, а не в шаблонах, по той же причине: классы (`dot`, `pill`,
// `profile-warn`) живут в `dating_page.css`, общем для обеих страниц, и менять
// их надо в одном месте.

import {timeShift, timeToday} from './dating_time.js';

export function esc(value) {
    const el = document.createElement('div');
    el.textContent = value == null ? '' : value;
    return el.innerHTML.replace(/"/g, '&quot;').replace(/'/g, '&#39;');
}

export function statusTitle(row) {
    if (row.status === 'online') return 'вход подтверждён ' + String(row.last_seen_at || '').slice(0, 19).replace('T', ' ');
    if (row.status === 'error') return row.last_error || 'опрос не прошёл';
    return row.has_state ? 'вход протух — залогиньтесь заново' : 'входа ещё не было';
}

// Точка «в сети или нет» — **два** состояния, а не три: зелёная пульсирует,
// красная нет. Отдельно от `statusDot`, где состояний три (серое «не входили»,
// зелёное, красное «опрос не прошёл»).
//
// Разница по смыслу вопроса. `statusDot` отвечает «что с учёткой» — и «входа ещё
// не было» там честно серое, потому что это не поломка. Здесь вопрос другой:
// «работает ли анкета прямо сейчас». Ответа на него ровно два, и серый в колонке
// с двумя состояниями читался бы как третье, которого нет.
export function onlineDot(row) {
    const on = row.status === 'online';

    return `<span class="dot ${on ? 'online' : 'off'}" title="${esc(statusTitle(row))}"></span>`;
}

export function statusDot(row) {
    return `<span class="dot ${esc(row.status)}" title="${esc(statusTitle(row))}"></span>`;
}

// Значок «не входит». Текст подсказки собираем здесь, а состояние считает
// сервер: арифметика суток в двух местах разошлась бы.
export function warnBadge(row) {
    if (!row.revive_state || row.revive_state === 'ok') return '';

    const when = row.relogin_next_at ? timeShift(row.relogin_next_at).slice(11, 16) : '';
    const title = row.revive_state === 'given_up'
        ? `не удаётся войти: ${row.last_error || 'причина неизвестна'} — попытки прекращены, нужен вход вручную`
        : `не удаётся войти: ${row.last_error || 'причина неизвестна'}`
          + (when ? `; следующая попытка в ${when}` : '');

    return `<span class="profile-warn ${esc(row.revive_state)}" title="${esc(title)}">⚠</span>`;
}

// Работа, которая на учётке висит: непрочитанные в чате, собеседники, письма.
// Ноли не показываются вовсе — бейдж «0» занимает место и ничего не сообщает.
export function workPills(row) {
    return (row.unread_chat ? `<span class="pill chat" title="новых сообщений в чате">💬 ${row.unread_chat}</span>` : '')
        + (row.contacts ? `<span class="pill" title="собеседников в чате">Собеседников: ${row.contacts}</span>` : '')
        + (row.unread > 0 ? `<span class="pill mail" title="непрочитанных писем в почте">✉ ${row.unread}</span>` : '');
}

// Время — абсолютное, `26/08 13:59`, и это не лень.
//
// «Вчера» и «2 часа назад» пришлось бы считать по часам браузера, а времена
// анкеты живут по одному общему поясу — тому, что стоит в настройке `timezone`.
// Оператор может сидеть в другом; «вчера» тогда показывалось бы у записи,
// сделанной час назад. День и месяц перед временем нужны по той же причине, по
// какой они стоят в журнале: записи лежат за много дней, и голое время не
// отличает вчерашнюю попытку входа от сегодняшней. Год опущен — смотрят
// последние дни, а строка повторяется у каждой записи.
//
// Сдвиг из UTC делает `timeShift` (`dating_time.js`): с сервера метка приходит в
// UTC, а человек сверяет её с часами на стене.
export function seenText(value) {
    const text = timeShift(value);
    if (text.length < 16) return '';

    return `${text.slice(8, 10)}/${text.slice(5, 7)} ${text.slice(11, 16)}`;
}

// Отметка времени в журнале: «19/08 11:39:23» — день и месяц перед временем.
//
// Дата нужна потому, что журнал хранит записи за много дней, а одно голое время
// не отличает вчерашнюю попытку входа от сегодняшней — при разборе «почему анкета
// не входит» это первое, что нужно знать.
//
// Год не показываем: строка стоит перед каждой записью, а разбирают журнал за
// последние дни. Резать по индексам можно, потому что формат фиксирован
// (`YYYY-MM-DDTHH:MM:SS.ffffff` от FastAPI); разъедется он — здесь и будет видно,
// а не в тихо неверной дате.
//
// Секунды здесь есть, а в `seenText` их нет, и это не расхождение: в журнале
// подряд идущие шаги укладываются в одну минуту, и без секунд не видно, что за
// чем шло; в колонке «Вход» секунда события месячной давности не значит ничего.
// Короткая метка для тесных мест: сегодняшнее — часами, старше — датой.
//
// ⚠ **Одними часами показывать нельзя.** «16:25» у сообщения недельной давности
// читается как сегодняшнее, и человек отвечает на письмо, которое считает свежим.
// Место при этом тесное — строка диалога и шапка колонки, — поэтому дата вместо
// часов, а не вместе с ними.
//
// «Сегодня» считается по часам панели, а не браузера: оператор может сидеть в
// другом поясе, и его полночь не должна объявлять вчерашним то, что для панели
// сегодняшнее.
export function timeShort(value) {
    const stamp = timeShift(value);
    if (stamp.length < 16) return '';

    return timeToday() === stamp.slice(0, 10)
        ? stamp.slice(11, 16)
        : `${stamp.slice(8, 10)}/${stamp.slice(5, 7)}`;
}

export function logStamp(value) {
    const raw = timeShift(value);
    if (raw.length < 19) return raw;

    return `${raw.slice(8, 10)}/${raw.slice(5, 7)} ${raw.slice(11, 19)}`;
}

// Журнал учётки в панель. Общий для «Анкет» и «Профилей»: строки одной и той же
// ручки, и две копии разошлись бы в цвете уровня — а по нему в ленте и ищут.
//
// Прокрутку сохраняем сами: журнал перерисовывается своим таймером, и без этого
// он прыгал бы наверх у человека, который отлистал к нужной строке.
export function logRender(el, rows, emptyText) {
    const scroll = el.scrollTop;

    el.innerHTML = rows.length
        ? rows.map(row => `<div class="log-row ${esc(row.level)}">`
            + `<span class="log-time">${esc(logStamp(row.created_at))}</span>`
            + `<span class="log-text">${esc(row.text)}</span></div>`).join('')
        : `<div class="log-empty">${esc(emptyText)}</div>`;

    el.scrollTop = scroll;
}

// Кнопки страниц. Одна функция на все списки панели — анкеты, собеседницы,
// профили: разъехавшись, они дали бы разные пейджеры на соседних страницах.
//
// Одной страницы мало, чтобы её показывать: пейджер над списком из восьми строк
// только занимает место. Поэтому подвал раскрывается классом `open`, а не живёт
// всегда.
//
// `current` — номер с нуля, как индекс среза, которым режут список.
export function pagerRender(el, pages, current, pick) {
    el.classList.toggle('open', pages > 1);
    if (pages <= 1) {
        el.innerHTML = '';
        return;
    }

    const button = (label, page, disabled, active) =>
        `<button type="button" class="page-btn${active ? ' active' : ''}" data-page="${page}"`
        + `${disabled ? ' disabled' : ''}>${label}</button>`;

    let html = button('‹', current - 1, current === 0, false);
    for (let i = 0; i < pages; i++) html += button(i + 1, i, false, i === current);
    html += button('›', current + 1, current === pages - 1, false);

    el.innerHTML = html;
    el.querySelectorAll('.page-btn').forEach(btn => {
        btn.addEventListener('click', () => pick(Number(btn.dataset.page)));
    });
}
