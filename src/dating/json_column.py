"""Чтение JSON-столбцов базы одним местом на весь проект.

Отдельным модулем ради одной функции, потому что копий её было четыре — по одной
в держателе сокетов, в опросе присутствия и в роутере анкет, и четвёртая
напрашивалась в модуле входа. Функция крошечная, но чинить её пришлось бы во всех
копиях разом, а найти все четыре в тот день никто бы не взялся.
"""

import json


def json_column_load(value) -> dict:
    """JSON-столбец приезжает то структурой, то строкой — зависит от драйвера."""
    if isinstance(value, str):
        try:
            return json.loads(value or '{}')
        except Exception:
            return {}

    return value if isinstance(value, dict) else {}


def json_column_list(value) -> list:
    """То же, но столбец держит **массив**: теги снимка, перечень имён.

    Отдельной функцией, а не признаком у соседней: та объявлена отдающей
    словарь, и её зовут четыре места, разбирающие ответ как словарь. Расширь мы
    её возврат — проверка `if not данные:` осталась бы верной, а `данные.get(…)`
    упала бы на списке, и упала бы не здесь, а у вызывающего.
    """
    if isinstance(value, str):
        try:
            got = json.loads(value or '[]')
        except Exception:
            return []
        return got if isinstance(got, list) else []

    return value if isinstance(value, list) else []
