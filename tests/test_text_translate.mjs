// Сторож общего модуля перевода: чистые функции каркаса под node.
//
// ⚠ Правило «метка до модели не доходит и возвращается на место» — то
// единственное, что здесь обязано держаться, и проверять его надо прямо.
import {textTranslateMask, textTranslateUnmask, textTranslatePeel,
        textTranslateAccept, textTranslateGuess}
    from '../dashboard/js/text_translate.js';

const MARKS = () => /\[\[[^\]]+\]\]/g;
let bad = 0;
const ok = (what, got, want) => {
    const same = JSON.stringify(got) === JSON.stringify(want);
    if (!same) { bad++; console.log(`✗ ${what}\n   ждали ${JSON.stringify(want)}\n   пришло ${JSON.stringify(got)}`); }
};

// Заслонка: метка до модели не доходит.
const m = textTranslateMask('Привет, [[girl_name]] и [[companion_name]]!', MARKS());
ok('метки заслонены', m.text, 'Привет, ⟦0⟧ и ⟦1⟧!');
ok('метки сняты в список', m.marks, ['[[girl_name]]', '[[companion_name]]']);

// Возврат на место.
ok('метки вернулись', textTranslateUnmask('Hi, ⟦0⟧ and ⟦1⟧!', m.marks),
   'Hi, [[girl_name]] and [[companion_name]]!');

// ⚠ Пробелы внутри заслонки модель добавляет — прощаем.
ok('пробелы в заслонке прощены', textTranslateUnmask('Hi, ⟦ 0 ⟧!', ['[[a]]']), 'Hi, [[a]]!');

// ⚠⚠ Потерянная метка — отказ, а не подстановка чего-нибудь.
ok('потерянная метка = null', textTranslateUnmask('Hi there!', ['[[a]]']), null);
const lost = textTranslateAccept('Hi there!', ['[[a]]']);
ok('отказ при потере метки', !!lost.error, true);
ok('при отказе текста нет', lost.text, undefined);

// ⚠⚠ Пустой ответ — отказ: иначе выделенное просто стёрлось бы.
ok('пустой ответ = отказ', !!textTranslateAccept('', []).error, true);
ok('пробелы — тоже пусто', !!textTranslateAccept('   ', []).error, true);

// Годный ответ проходит.
ok('годный ответ принят', textTranslateAccept('Hi, ⟦0⟧!', ['[[a]]']), {text: 'Hi, [[a]]!'});

// Края возвращаются своими.
ok('края отделены', textTranslatePeel('  Пиши коротко.\n'),
   {head: '  ', core: 'Пиши коротко.', tail: '\n'});
ok('текст из пробелов', textTranslatePeel('   '), {head: '   ', core: '', tail: ''});

// Язык — по буквам.
ok('русский', textTranslateGuess('Привет, как дела'), 'ru');
ok('украинский', textTranslateGuess('Привіт, як справи'), 'uk');
ok('английский', textTranslateGuess('Hello there'), 'en');
ok('метки языка не меняют', textTranslateGuess('[[girl_name]] hello'), 'en');

// ⚠ Без меток модуль не ломается: `marks` — довод, и его может не быть.
const none = textTranslateMask('Просто текст', null);
ok('без меток текст целый', none.text, 'Просто текст');
ok('без меток список пуст', none.marks, []);

console.log(bad ? `ПРОВАЛОВ: ${bad}` : 'ВСЁ ЗЕЛЁНОЕ');
process.exit(bad ? 1 : 0);
