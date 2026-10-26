"""Goldenbride-образная панель: журнал на одной странице `userchatmessages`.

Такую панель знают все сайты сети Goldenbride (он сам, LadaDate и соседние):
страница `userchatmessages` рядом со страницей агентства, пять столбцов, даты
формата `дд.мм.гггг`. Прежде этот путь был **зашит** в `chat_panel.py` константами и
считался общим для всех сайтов — на нём и вырос 404 у Step2Love, у которого такой
страницы нет.

⚠ Путь строится от **корня панели**, а не от корня домена: таблицы лежат рядом с
той страницей, что стоит в настройке `admin_url` (`panel_fetch._session`). Для
Goldenbride это `/partner/agency/…`.
"""

from src.dating.panel_fetch import panel_fetch_html, panel_fetch_table
from src.dating.site.site_import import DatingSiteImport


class DatingSiteImportGoldenbride(DatingSiteImport):
    """Одна страница, пять столбцов, все анкеты разом."""

    name = 'goldenbride'

    # Страница журнала. Путь тот же, на который ходил сценарий: разметку читает
    # ручка (`panel_fetch.py`), браузер тому не товарищ.
    PATH = 'userchatmessages'

    # Ячейки таблицы по порядку. Панель отдаёт пять столбцов: ID, дата UTC, от,
    # кому, текст. ⚠ Имена те же, что у шагов сценария, — разбор строк
    # (`chat_panel_rows`) общий на обе дороги, и расходиться им нельзя.
    CELLS = ('number', 'when', 'from', 'to', 'text')

    def has_journal(self) -> bool:
        return True

    async def journal_rows(self, site_id: int, profiles: dict, date_from: str,
                           date_to: str) -> tuple[int, list[dict]]:
        # `AccountId=0` — «все анкеты»: панель так и подписывает первый пункт
        # селекта. Спрашивать по одной значило бы тринадцать запросов вместо одного.
        html = await panel_fetch_html(site_id, self.PATH, {
            'AccountId': '0',
            'DateFrom.DateValue': date_from,
            'DateTo.DateValue': date_to,
        })

        table = panel_fetch_table(html)

        return len(table), self._cells(table)

    def _cells(self, table: list) -> list[dict]:
        """Ячейки таблицы — в записи того же вида, что отдаёт сценарий.

        ⚠ Вид общий намеренно: разбор строк (`chat_panel_rows`) один на обе
        дороги, и вторая его копия под ручку разошлась бы с первой на первой же
        правке.

        Номера сторон берём из **ссылки** ячейки: панель пишет туда «181685 Semen
        Gorbunkov», где номер — ссылка на карточку, а имя стоит рядом. Текст
        сообщения — из подсказки `title`, потому что в самой ячейке он обрезан
        шириной колонки.

        ⚠ Имена полей те же, что у сценария, и оба набора латиницей: имена шагов
        переехали с кириллицы на латиницу вместе (`scenario_field.py`), и
        переводить здесь нечего.
        """
        out = []
        for row in table:
            one = {}
            for at, name in enumerate(self.CELLS):
                cell = row[at] if at < len(row) else {}
                one[name] = cell.get('text', '')
                if name in ('from', 'to'):
                    one[f'{name}_uid'] = cell.get('link', '')
                if name == 'text':
                    one['text_title'] = cell.get('title', '')

            out.append(one)

        return out
