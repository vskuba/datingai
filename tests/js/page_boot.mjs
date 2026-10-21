// Исполняет модуль страницы в пустом DOM.
//
// ⚠⚠ **Ловит то, чего не видит разбор.** `node --check` только парсит: чтение
// `const` до объявления (временная мёртвая зона), вызов несуществующей функции на
// верхнем уровне, опечатка в имени — всё это синтаксически безупречно и валит
// страницу при загрузке. Поймано живьём: `let statsHours` читал
// `STATS_HOURS_KEY` семью строками выше объявления, модуль падал целиком, и
// «Профили» открывались пустыми — а сюита была зелёной.
//
// ⚠ **Заглушки ровно те, что модуль трогает на верхнем уровне.** Полного DOM тут
// не будет и не нужно: проверяется загрузка, а не поведение. Упал стенд на
// незнакомом глобальном имени — допиши его сюда, это дешевле браузера.
//
// Запускается из `tests/test_page_boot.py`; сам по себе — `node
// tests/js/page_boot.mjs <файл.html>`.
import fs from 'node:fs';

const src = fs.readFileSync(process.argv[2], 'utf8');
const js = src.match(/<script type="module">([\s\S]*?)<\/script>/)[1]
  .replace(/'\{\{.*?\}\}'/gs, "'x'").replace(/"\{\{.*?\}\}"/gs, '"x"')
  .replace(/\{\{.*?\}\}/gs, '0').replace(/\{%.*?%\}/gs, '');

// Заглушки ровно того, что модуль трогает на верхнем уровне.
const el = new Proxy(function () {}, {
  get: (t, k) => k === 'classList' ? {toggle() {}, add() {}, remove() {}, contains: () => false}
    : k === 'dataset' ? {}
    : k === 'style' ? {}
    : k === 'value' ? ''
    : k === 'length' ? 0
    : k === Symbol.iterator ? [][Symbol.iterator].bind([])
    : el,
  apply: () => el,
  set: () => true,
});
globalThis.window = el;
globalThis.document = el;
globalThis.localStorage = {getItem: () => null, setItem() {}, removeItem() {}};
globalThis.location = {href: '', search: '', replace() {}};
globalThis.history = {replaceState() {}};
globalThis.innerWidth = 1280;
globalThis.innerHeight = 900;
globalThis.matchMedia = () => ({matches: false, addEventListener() {}});
globalThis.requestAnimationFrame = () => 0;
globalThis.setInterval = () => 0;
globalThis.setTimeout = () => 0;
globalThis.fetch = async () => ({ok: true, json: async () => ({result: []})});

const stub = `
const getData = async () => ({result: []});
const postData = getData, putData = getData, deleteData = getData;
const esc = (s) => String(s ?? '');
const onlineDot = esc, warnBadge = esc, seenText = esc, timeShort = esc;
const timeFull = esc, logRender = () => '', pagerRender = () => {};
// Селект сайта и помощники ленты: страницы «Журналы» и «Ошибки» читают их на
// верхнем уровне. datingSiteFill — первый же await стартового IIFE: без заглушки
// вызов не доходит до await, и модуль падает с «tab before initialization».
const DATING_SITE_KEY = 'x', datingSiteFill = async () => {}, face = () => '';
// Заглушка лиц: обе страницы раздела ставят перехват НА ВЕРХНЕМ УРОВНЕ, рядом с
// layoutRestore, — иначе первые же картинки успели бы промахнуться до него.
const faceFallback = () => {};
const datingPanelPaint = () => {};
const timeShift = esc, logStamp = esc;
// Возраст словами из dating_time.js. ⚠ Нужны здесь потому, что страница берёт
// timeAgoSec НА ВЕРХНЕМ УРОВНЕ: лента событий меряет возраст тем же помощником,
// что и колонки собеседников, и делает это присваиванием, а не вызовом внутри
// функции. Ровно это стенд и поймал при первой правке.
// ⚠ Обратных кавычек здесь быть не может — заглушка сама лежит в шаблонной
// строке, и первая же кавычка обрывает её (поймано этой же правкой).
const timeAgo = esc, timeAgoSec = esc, timeLeftSec = esc;
const showNotification = () => {};
const textTranslateSection = () => ({});
`.trim();

// Импорты модуля заменяем заглушками: настоящих файлов в node нет.
const body = js.replace(/^\s*import\s.*?;\s*$/gms, '');

try {
  await import('data:text/javascript;base64,' +
    Buffer.from(stub + '\n' + body).toString('base64'));
  console.log('МОДУЛЬ ИСПОЛНИЛСЯ');
} catch (e) {
  console.error('ПАДЕНИЕ ПРИ ЗАГРУЗКЕ:', e.constructor.name + ':', e.message);
  process.exit(1);
}
