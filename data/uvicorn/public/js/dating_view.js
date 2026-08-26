// Как показывают учётку: значки, бейджи, подсказки, время.
//
// Общее для «Чатов» и «Профилей». Обе страницы рисуют одни и те же строки одной
// и той же ручки (`GET /api/dating/profile`), и две копии этой логики разошлись
// бы в мелочах — а мелочи здесь и есть смысл: пульсирующая точка отличает живую
// анкету от замершего экрана, мигающий ⚠ отличает «ещё пробуем» от «сдались».
//
// Разметка тут же, а не в шаблонах, по той же причине: классы (`dot`, `pill`,
// `profile-warn`) живут в `dating_page.css`, общем для обеих страниц, и менять
// их надо в одном месте.

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

export function statusDot(row) {
    return `<span class="dot ${esc(row.status)}" title="${esc(statusTitle(row))}"></span>`;
}

// Значок «не входит». Текст подсказки собираем здесь, а состояние считает
// сервер: арифметика суток в двух местах разошлась бы.
export function warnBadge(row) {
    if (!row.revive_state || row.revive_state === 'ok') return '';

    const when = row.relogin_next_at ? String(row.relogin_next_at).slice(11, 16) : '';
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
// анкеты живут по часам базы. `TZ` не задан ни в одном compose, прод-MySQL стоит
// на чужом сервере, и часы расходятся молча — «вчера» показывалось бы у записи,
// сделанной час назад. День и месяц перед временем нужны по той же причине, по
// какой они стоят в журнале: записи лежат за много дней, и голое время не
// отличает вчерашнюю попытку входа от сегодняшней. Год опущен — смотрят
// последние дни, а строка повторяется у каждой записи.
export function seenText(value) {
    const text = String(value || '').replace('T', ' ');
    if (text.length < 16) return '';

    return `${text.slice(8, 10)}/${text.slice(5, 7)} ${text.slice(11, 16)}`;
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
