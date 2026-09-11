"""Пейджер: правило «первая, последняя, соседи, между ними многоточие».

⚠ **Проверяется настоящий модуль** `data/uvicorn/public/js/dating_view.js`, а не
его пересказ на Python. Пересказ разошёлся бы с оригиналом в первую же правку и
продолжал бы при этом проходить — то есть врал бы ровно тогда, когда нужен.

Сам тест написан на JS (`test_pager.js`) и запускается `node`; здесь — мост:
собрать временную папку, подложить заглушку зависимости и показать вывод человеку,
если что-то не сошлось. Тот же приём и тот же мост, что у ленты расписания
(`test_schedule_band.py`).

## Почему не браузером

Ряд страниц — это арифметика над двумя числами. Поднимать ради неё Chromium
значило бы менять секунды на минуты, ничего не добавляя. Как он выглядит на экране,
проверено глазами и headless-браузером отдельно.

## Что здесь ловится

Ряд рос вместе с числом страниц: сорок страниц — сорок кнопок, подвал переносился
на вторую строку и отодвигал список. Человек это и увидел на вкладке собеседников.

И обратная сторона: разрыв не должен прятать **одну** страницу — «…» вместо «3» той
же ширины, а щелчка стоит одного.
"""

import pathlib
import shutil
import subprocess
import tempfile

import pytest

pytestmark = pytest.mark.page
JS_DIR = pathlib.Path(__file__).resolve().parents[1] / 'data/uvicorn/public/js'
MODULE = JS_DIR / 'dating_view.js'
TEST_JS = pathlib.Path(__file__).with_suffix('.js')

# Единственное, что тянет модуль ради `pagerPages`, — времена. Подкладываем
# заглушку: `pagerPages` из неё не берёт ничего, а настоящий файл читает
# `window`, которого в `node` нет.
TIME_STUB = ('export const timeShift = (value) => String(value ?? "");\n'
             'export const timeToday = () => "";\n')


def test_pager_pages_rules():
    """Какие страницы попадают в ряд — прогоном настоящего модуля."""
    node = shutil.which('node')
    if not node:
        pytest.skip('node не найден в PATH')

    with tempfile.TemporaryDirectory() as tmp:
        room = pathlib.Path(tmp)
        shutil.copy(MODULE, room / MODULE.name)
        shutil.copy(TEST_JS, room / TEST_JS.name)
        (room / 'dating_time.js').write_text(TIME_STUB, encoding='utf-8')

        result = subprocess.run([node, str(room / TEST_JS.name)],
                                capture_output=True, text=True, timeout=60)

    # ⚠ Вывод показываем **целиком и всегда при отказе**: тест на JS сам называет,
    # какая проверка не сошлась, и прятать это за «returncode != 0» значило бы
    # заставлять человека повторять прогон руками.
    assert result.returncode == 0, (
        f'проверки пейджера не прошли:\n{result.stdout}\n{result.stderr}')
