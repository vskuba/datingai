"""Шаблон работы с анкетой: разметка `#work` под конкретный сайт.

Проверяется то, ради чего шаблоны и разделены: **сайты получают разную разметку**, а
имя шаблона, приходящее из базы, не пускает никуда за пределы каталога.

Разметку сверяем по контрактным признакам (`data-slot`, классы блоков), а не по
вёрстке целиком: иначе тест падал бы на каждой правке отступов.

⚠ **Ручку зовём в процессе, а не по HTTP.** Она спрашивает у соседа имя сайта, и
тест против живого зависел бы от того, поднят ли он, — а имя сайта тут и вовсе
подставляется поддельным (`rhapsody_fake.py`). Настройка сайта лежит в MySQL и
подменяется здесь же: проверяем выбор шаблона, а не чтение настройки.

По HTTP остаётся то, что решается **до** входа в обработчик: вход и отсутствие
сайта.
"""

import os

import pathlib

import pytest
from fastapi.templating import Jinja2Templates
from starlette.requests import Request

from src.uvicorn.routers.dating import work as work_module
from src.uvicorn.routers.dating.work import _slug, work_template

pytestmark = pytest.mark.page
# Сайты у поддельного соседа: у первого имя `Ladadate`, у второго `Goldenbride` —
# по ним и подбирается файл шаблона.
LADA = 343
GOLD = 19

# Сайт, которого у соседа нет вовсе: его шаблон должен быть умолчанием.
UNKNOWN = 999_777


@pytest.fixture
def work(rhapsody, monkeypatch):
    """Зовёт ручку шаблона так, как это делает браузер, но в процессе.

    Настройка `template` задаётся тестом: она из базы, а базой тут ничего не
    проверяется.
    """
    chosen = {'value': ''}

    async def _setting(site_id, key, default=''):
        return chosen['value']

    monkeypatch.setattr(work_module, 'site_setting', _setting)

    async def _call(site_id: int, template: str = '') -> str:
        chosen['value'] = template
        answer = await work_template(_request(), site_id=site_id, user=None)

        return answer.body.decode()

    _call.rhapsody = rhapsody

    return _call


class TestWorkTemplate:
    """Что отдаёт ручка."""

    async def test_template_has_slots_and_no_log(self, work):
        """⚠ Журнала в шаблоне сайта быть не должно.

        Он про анкету, а не про сайт: одна и та же лента нужна на любом, и держи мы
        её копию в каждом шаблоне — правка журнала означала бы правку всех. Место
        журнала — каркас страницы.
        """
        html = await work(LADA)

        assert 'data-slot="dialogs"' in html, 'нет слота под диалоги'
        assert 'chat-log' not in html, 'журнал попал в шаблон сайта'
        assert 'id="log"' not in html

        # ⚠ Слота `setup` здесь больше нет: правка учётки, действия и автоответ
        # переехали на «Анкеты», в настройки анкеты — туда, где выключатель,
        # расписание и удаление. Настраивать анкету в двух местах значило
        # расходиться в мелочах.
        assert 'data-slot="setup"' not in html, 'вкладка настроек вернулась в шаблон'

    async def test_every_tab_has_a_panel(self, work):
        """Панели всех вкладок приходят разом.

        Переключение вкладки тогда — показать одну и снять остальные, без запроса к
        серверу: шаблон один на сайт, и просить его заново на каждый щелчок незачем.

        ⚠ Список сверяется с полосой вкладок в `profile_.html`: кнопка без
        панели — пустое место вместо содержимого, панель без кнопки — мёртвая
        разметка, к которой не добраться.
        """
        html = await work(LADA)

        for tab in ('chat', 'fav', 'mail', 'stats'):
            assert f'data-tab="{tab}"' in html, f'нет панели вкладки «{tab}»'

    async def test_tabs_and_panels_match(self, work):
        """⚠ Кнопок вкладок ровно столько же, сколько панелей, и это одни и те же.

        Проверка появилась после переезда «Настроек»: панель убрали из шаблона
        сайта, а кнопку с полосы — из страницы, и разъехаться им было легко.
        """
        import re

        html = await work(LADA)
        page_src = pathlib.Path(
            'data/uvicorn/template/admin/dating/profile.html').read_text(encoding='utf-8')

        tabs = set(re.findall(r'class="page-tab[^"]*" data-tab="(\w+)"', page_src))
        panels = set(re.findall(r'class="work-panel" data-tab="(\w+)"', html))

        assert tabs == panels, f'кнопки {tabs}, панели {panels}'

    async def test_sites_get_different_markup(self, work):
        """⚠ Ради этого шаблоны и разделены.

        У Goldenbride не заведено сценариев ни под ленту «Все онлайн», ни под
        избранных: оба блока стояли бы навсегда с надписью «не снялось». Шаблон
        сайта убирает их, не трогая ни JS, ни другие сайты.

        ⚠ Ленты у сайта не должно остаться **ни одной**, а стоит она на двух
        вкладках: опустошить один блок из двух — значит вернуть на «Письма
        рассылка» ровно то «не снялось», ради которого блоки и опустошены.
        """
        lada = await work(LADA)
        gold = await work(GOLD)

        # ⚠ Оба списка — ряды-карусели одного макроса, и различает их `data-strip`.
        # Проверяем по нему, а не по классу: класс у них общий (`strip-row`), и
        # «есть ли лента онлайна» по нему не спросить.
        assert 'data-strip="online"' in lada and 'data-strip="favorite"' in lada
        assert 'data-strip="online"' not in gold
        assert 'data-strip="favorite"' not in gold
        # Диалоги есть у обоих: они из нашей базы и работают на любом сайте.
        assert 'data-slot="dialogs"' in gold

    async def test_favorites_stand_as_a_strip_not_a_column(self, work):
        """⚠ Избранные — **лента**, тем же макросом, что «Все онлайн».

        Колонкой они и жили: строка на человека, лицо слева, имя и номер справа.
        Читать в ней было нечего — избранных десятки, а вопрос к списку один: «кто
        там». Лицо в ленте отвечает на него взглядом, а строка требовала чтения
        сверху вниз, отдавая две трети ширины пустоте.

        Проверяем по контрактным признакам ряда: страница находит его по
        `data-strip`, наполняет через `{kind}-strip` и листает стрелками
        `{kind}-left` / `{kind}-right`. Не сойдись имена — лента молча осталась бы
        пустой.
        """
        html = await work(LADA)

        assert html.count('data-strip="favorite"') == 1, 'ряд избранных не один'
        for slot in ('favorite-strip', 'favorite-left', 'favorite-right',
                     'favorite-count', 'favorite-when'):
            assert f'data-slot="{slot}"' in html, f'нет слота {slot}'

        # ⚠ Колонки избранных не осталось: два способа показать один список
        # разошлись бы в первый же день, а страница рисует ленту.
        assert 'data-kind="favorite"' not in html or 'people-col' not in html, \
            'избранные остались и колонкой тоже'

    async def test_online_strip_stands_where_the_work_needs_it(self, work):
        """⚠ Лента «Все онлайн» стоит **в трёх местах**: «Диалоги», «Инвайты» и
        «Письма рассылка».

        Список в сети — про сайт, а не про выбранную анкету, и все три занятия
        составляют, глядя, кто на сайте сейчас: инвайт зовёт того, кто **сейчас**
        в чате, рассылка пишет тем, кто в сети, а диалоги показывают, кто из
        собеседников на месте.

        Отсюда требование к разметке: рядов три, а слоты у них **одинаковые** —
        страница наполняет все разом (`peopleOnlinePaint`), а не первый
        попавшийся.
        """
        import re

        html = await work(LADA)

        assert html.count('data-strip="online"') == 2, 'лента не в обоих местах'
        assert html.count('data-slot="online-strip"') == 2

        # ⚠ **На «Диалогах» ленты нет.** Она отвечает на вопрос «кому писать
        # первым» — и потому осталась там, где этим и заняты: на «Инвайтах» (пул
        # набирается ровно из неё) и на «Письмах рассылка». В «Диалогах» читают
        # уже начатые разговоры: список слева и лента справа, и ряд карточек
        # поверх них отнимал высоту у обоих.
        talk = re.search(r'<div class="chat-pane off" data-chat-pane="talk">.*?'
                         r'(?=<div class="chat-pane" data-chat-pane="invite">)',
                         html, re.S)
        assert talk, 'панели «Диалоги» нет'
        assert 'data-strip="online"' not in talk.group(0), \
            'лента онлайна вернулась в «Диалоги»'

        # Ряд «Инвайтов» — внутри своей подвкладки, а не рядом с ней.
        invite = re.search(r'<div class="chat-pane" data-chat-pane="invite">.*?'
                           r'(?=<div class="chat-pane off" data-chat-pane="sent">)',
                           html, re.S)
        assert invite, 'панели «Инвайты» нет'
        assert 'data-strip="online"' in invite.group(0), 'ленты на «Инвайтах» нет'
        assert invite.group(0).index('data-strip="online"') \
               < invite.group(0).index('invite-schedule'), \
            'лента должна стоять над расписанием'

        # Ряд «Письма рассылка» — внутри своей панели, а не рядом с ней: иначе он
        # виден на всех вкладках сразу.
        mail = re.search(r'<div class="work-panel" data-tab="mail">.*?'
                         r'(?=<div class="work-panel" data-tab="stats">)', html, re.S)
        assert mail, 'панели «Письма» нет'
        assert 'data-strip="online"' in mail.group(0), 'ленты на «Письмах» нет'
        assert 'data-slot="mail-soon"' in mail.group(0), 'нет слота под подпись вкладки'

    async def test_mail_panel_has_three_sub_tabs(self, work):
        """⚠ «Письма» делятся натрое — по пути письма: «Рассылка» (что уйдёт),
        «Доставка» (что ушло), «Входящие» (что пришло, и ответ на него).

        Занятия разные, и общего у них только почта: пул **правят перед
        отправкой**, журнал доставки читают, чтобы понять, почему письмо не дошло,
        входящие — чтобы прочесть и ответить.

        ⚠ Состав менялся дважды, и оба раза в сторону слияния. Сперва «Входящие» и
        «Ответы» были отдельной вкладкой верхнего уровня — две вкладки со словом
        «письма» рядом заставляли на каждый переход отвечать «а это которые
        письма?». Потом «Ответы» растворились: пул ответов встал рядом с письмами,
        на которые отвечает, а расписание уехало в шапку панели — подвкладка с
        одной галочкой это переход ради переключателя.

        Проверяем по контрактным признакам: полоса вкладок, все три панели и слоты
        под списки — по ним страница их и находит.
        """
        import re

        html = await work(LADA)
        mail = re.search(r'<div class="work-panel" data-tab="mail">.*?'
                         r'(?=<div class="work-panel" data-tab="stats">)', html, re.S)
        assert mail, 'панели «Письма» нет'
        block = mail.group(0)

        assert 'data-slot="mail-tabs"' in block, 'нет полосы подвкладок'
        for name in ('pool', 'sent', 'in'):
            assert f'data-mail-tab="{name}"' in block, f'нет кнопки «{name}»'
            assert f'data-mail-pane="{name}"' in block, f'нет панели «{name}»'

        assert 'data-mail-pane="reply"' not in block, \
            'панель «Ответы» осталась — от неё остались пул во «Входящих» и '\
            'расписание в шапке'

        assert 'data-slot="mail-sent"' in block, 'нет слота под журнал доставки'
        assert 'data-slot="in-list"' in block, 'нет слота под входящие'
        # ⚠ **Справа теперь разговор, а не пул карточек.** Пул повторял то, что уже
        # есть слева («от кого кому» и текст), а понять, годится ли ответ, всё
        # равно можно было только вместе с перепиской — и та открывалась окном,
        # поверх обоих списков. Разговор в колонке снимает этот шаг, а работа с
        # ответом переехала под ленту: черновик виден пометкой «в пуле», ниже —
        # поле правки и кнопки.
        for slot in ('in-talk-thread', 'in-talk-who', 'in-reply-edit'):
            assert f'data-slot="{slot}"' in block, f'нет слота {slot}'

        assert 'data-slot="in-reply-list"' not in block, \
            'пул ответов вернулся отдельным списком — разговор и ответ снова порознь'

    async def test_chat_panel_has_sub_tabs(self, work):
        """⚠ «Чат» делится надвое: «Инвайты» и «Диалоги» — в этом порядке.

        Разделение по занятию, а не по сущности. «Инвайты» — первый шаг к тем, с
        кем не говорят вовсе; «Диалоги» — то, что уже говорят. Общего у них только
        сайт, и держи мы их в одной панели, «позвать двадцать незнакомых» мешалось
        бы с «ответить Кевину».

        ⚠ **Порядок читается как путь разговора**: сперва зовём, потом говорим. Он
        же и порядок дня — пул инвайтов набирает круг ночью, а утром его разбирают.
        Отсюда и умолчание: открыта та панель, с которой начинают.

        Проверяем по контрактным признакам, как и у почты: полоса, кнопки, панели.
        Не сойдись `data-chat-tab` с `data-chat-pane` — переключение молча ничего не
        показывало бы.
        """
        import re

        html = await work(LADA)
        chat = re.search(r'<div class="work-panel" data-tab="chat">.*?'
                         r'(?=<div class="work-panel" data-tab="fav">)', html, re.S)
        assert chat, 'панели «Чат» нет'
        block = chat.group(0)

        assert 'data-slot="chat-tabs"' in block, 'нет полосы подвкладок'
        for name in ('invite', 'sent', 'talk'):
            assert f'data-chat-tab="{name}"' in block, f'нет кнопки «{name}»'
            assert f'data-chat-pane="{name}"' in block, f'нет панели «{name}»'

        # Порядок кнопок в разметке — он же порядок на экране. ⚠ «Доставка» между
        # ними не для красоты: она отвечает на вопрос, который возникает сразу
        # после отправки, — «ушло ли», — и до «Диалогов» человек доходит уже с
        # ответом на него.
        assert (block.index('data-chat-tab="invite"')
                < block.index('data-chat-tab="sent"')
                < block.index('data-chat-tab="talk"')), 'порядок подвкладок не тот'

        assert '<div class="chat-pane" data-chat-pane="invite">' in block, \
            'панель «Инвайты» не открыта в разметке'
        assert 'chat-pane off" data-chat-pane="talk"' in block, \
            'панель «Диалоги» не спрятана в разметке'
        assert 'chat-pane off" data-chat-pane="sent"' in block, \
            'панель «Доставка» не спрятана в разметке'

        # ⚠ Журнал доставки — **только чтение**: ушедшая реплика уже случилась.
        # Слоты под список и счётчик есть, кнопок правки и очистки нет.
        for slot in ('invite-sent', 'invite-sent-count', 'invite-sent-reload'):
            assert f'data-slot="{slot}"' in block, f'нет слота {slot}'

        # ⚠ Оба выключателя инвайтов — **в правом краю полосы**, и оба про открытую
        # подвкладку (`data-for`). Порознь человек, включивший первый, спрашивал бы,
        # почему ничего не уходит.
        assert 'data-for="invite"' in block, 'нет блока настроек подвкладки'
        for slot in ('invite-sched-on', 'invite-send-on'):
            assert f'data-slot="{slot}"' in block, f'нет выключателя {slot}'

        # ⚠ Лента онлайна и список диалогов — **внутри** панели «Диалогов», а не
        # рядом с ней: иначе они видны и на «Инвайтах».
        talk = re.search(r'<div class="chat-pane off" data-chat-pane="talk">.*?'
                         r'(?=<div class="chat-pane" data-chat-pane="invite">)',
                         block, re.S)
        assert talk, 'панели «Диалоги» нет'
        assert 'data-slot="dialogs"' in talk.group(0), 'диалоги вне своей панели'
        # ⚠ Ленты онлайна здесь **нет** — она ушла на «Инвайты» и «Рассылку», где
        # ею и заняты. Подробности — в `test_online_strip_stands_where_the_work_needs_it`.
        assert 'data-slot="chat-talk-thread"' in talk.group(0), \
            'разговор не в своей панели: он читается рядом со списком, а не окном'

    async def test_invite_pane_has_schedule_and_pool_but_no_templates(self, work):
        """⚠ «Инвайты» устроены как «Рассылка»: расписание слева, пул справа.

        Занятие то же по форме — круг готовит текст, человек читает и отправляет, —
        и второй набор соглашений на ту же работу человеку пришлось бы разгадывать.

        ⚠ **Шаблонов здесь нет, и это проверяется.** У письма есть заготовка: текст
        пишут заранее, переменные подставляют при отправке. Инвайт целиком сочиняет
        агент под конкретного человека — вся его длина одно-два предложения, и
        «общей части» в них не остаётся. Заведись здесь выбор шаблонов, двадцать
        реплик подряд читались бы как двадцать копий.
        """
        import re

        html = await work(LADA)
        pane = re.search(r'<div class="chat-pane" data-chat-pane="invite">.*?'
                         r'(?=<div class="work-panel" data-tab="fav">)', html, re.S)
        assert pane, 'панели «Инвайты» нет'
        block = pane.group(0)

        assert 'data-form="invite-schedule"' in block, 'нет расписания инвайтов'
        assert 'data-slot="invite-schedule"' in block, 'нет слота под ленту смен'
        assert 'data-slot="invite-pool"' in block, 'нет слота под пул'
        assert 'data-slot="invite-make-btn"' in block, 'нет кнопки генерации'
        assert 'data-slot="invite-pool-clear"' in block, 'нет очистки пула'

        assert 'invite-make-pick' not in block and 'mail-make-field' not in block, \
            'у инвайтов завёлся выбор шаблонов — у них шаблонов не бывает'

    async def test_delivery_pane_has_no_editing(self, work):
        """⚠ На «Доставке» нет ни очистки, ни крестиков: ушедшее письмо уже
        случилось, и править его нечестно перед тем, кто его получил.

        Кнопка, которая всегда отказывает, хуже её отсутствия — по ней человек
        решает, что панель сломалась.
        """
        import re

        html = await work(LADA)
        pane = re.search(r'<div class="mail-pane off" data-mail-pane="sent">.*?'
                         r'(?=</div>\s*(?:\{%|</div>))', html, re.S)
        assert pane, 'панели «Доставка» нет'

        assert 'mail-pool-clear' not in pane.group(0), 'на «Доставке» есть очистка'
        assert 'mail-card-drop' not in pane.group(0), 'на «Доставке» есть удаление'

    async def test_pool_has_clear_button(self, work):
        """Очистка пула — в шапке пула, там же, где его видно."""
        html = await work(LADA)

        assert 'data-slot="mail-pool-clear"' in html, 'нет кнопки очистки пула'

    async def test_make_has_reset(self, work):
        """⚠ Сброс выбора шаблонов — над списком, справа.

        Нужен потому, что снять выбор в `multiple` мышью трудно: щелчок всегда
        что-нибудь отмечает, а отпустить последнее выбранное можно лишь
        Ctrl-щелчком по нему же — и это знают не все.
        """
        html = await work(LADA)

        assert 'data-slot="mail-make-reset"' in html, 'нет сброса выбора шаблонов'

    async def test_make_has_size_select(self, work):
        """⚠ Сколько писем за нажатие — **рядом с кнопкой**, а не в настройках
        сайта: число выбирают перед каждым нажатием, и ходить за этим на другую
        страницу значило бы менять настройку по три раза за вечер."""
        html = await work(LADA)

        assert 'data-slot="mail-make-size"' in html, 'нет выбора количества'

    async def test_online_split_is_a_class_not_an_id(self, work):
        """Разделитель высоты ленты — классом, а не `id`.

        Рядов в разметке три, и `id` у них совпал бы: двух узлов с одним `id` в
        документе быть не должно, а `querySelector` нашёл бы только первый — на
        «Инвайтах» и «Письмах рассылка» ленту стало бы нечем тянуть.

        ⚠ **Число не приколочено к трём.** Копий ленты столько, сколько мест, где
        она нужна, и добавление четвёртого — обычная правка; тест сторожит другое:
        что разделитель у каждой копии свой и находится по классу.
        """
        html = await work(LADA)

        assert html.count('split-online') == html.count('data-strip="online"'), \
            'у ленты есть копия без разделителя — её нечем тянуть'
        assert 'id="splitOnline"' not in html

    async def test_site_without_own_template_gets_default(self, work):
        """Новый сайт должен открыться и работать до того, как под него напишут
        свой шаблон: иначе его нельзя было бы даже посмотреть."""
        html = await work(UNKNOWN)

        assert 'data-slot="dialogs"' in html

    async def test_unreachable_neighbour_still_gives_markup(self, work):
        """⚠ Лежащий сосед страницу не закрывает.

        Разметка `#work` от него не зависит: он нужен только чтобы узнать имя сайта
        и подобрать по нему файл. Не ответил — берём умолчание, а не 502.
        """
        work.rhapsody.offline()

        html = await work(LADA)

        assert 'data-slot="dialogs"' in html


    async def test_favorites_has_letters_and_delivery(self, work):
        """⚠ «Фавориты» — две подвкладки: «Переписка» и «Доставка».

        Порядок тот же, что у писем и инвайтов: сперва **что уйдёт**, потом **что
        ушло**. Пул читают, чтобы править перед отправкой; журнал — чтобы понять,
        дошло ли. Первое требует правки и удаления, второе неизменно вовсе.

        ⚠ **Лента избранных — внутри «Переписки»**, первым рядом, а не отдельной
        подвкладкой. Своя подвкладка у неё была и ушла: кто в звёздочках и что мы
        им пишем — про одно и то же, и переход между двумя панелями ради одного
        занятия это лишний щелчок.

        ⚠ **Оба выключателя — в правом краю полосы и только у «Переписки»** (по
        `data-for`): они про «готовим» и «шлём», а журнал ушедшего к этому решению
        отношения не имеет.

        ⚠ **Пределов частоты в разметке нет**: их правят на «Настройках сайтов».
        Два места для одного значения означали бы вопрос, какое из них
        настоящее, — здесь только подпись с действующими числами.
        """
        import re

        html = await work(LADA)
        fav = re.search(r'<div class="work-panel" data-tab="fav">.*?'
                        r'(?=<div class="work-panel" data-tab="mail">)', html, re.S)
        assert fav, 'панели «Фавориты» нет'
        block = fav.group(0)

        for name in ('talk', 'sent', 'talks'):
            assert f'data-fav-tab="{name}"' in block, f'нет кнопки «{name}»'
            assert f'data-fav-pane="{name}"' in block, f'нет панели «{name}»'

        # Порядок читается как путь: что уйдёт → что ушло → что из этого вышло.
        assert (block.index('data-fav-tab="talk"')
                < block.index('data-fav-tab="sent"')
                < block.index('data-fav-tab="talks"')), 'порядок подвкладок не тот'

        # ⚠ «Приставание», а не «Переписка»: рядом стоят «Диалоги», и оба слова
        # про письма — но занятия разные. Здесь пишут **первыми** тому, кто
        # замолчал; там читают разговор, который уже был.
        assert '>Приставание<' in block, 'подвкладка снова названа «Переписка»'

        # Диалоги устроены как «Письма → Входящие»: список слева, чтение справа.
        for slot in ('fav-talks-list', 'fav-talks-count', 'fav-talks-reload',
                     'fav-talk-thread', 'fav-talk-who'):
            assert f'data-slot="{slot}"' in block, f'нет слота {slot}'
        assert 'fav-pane off" data-fav-pane="sent"' in block, \
            'панель «Доставка» не спрятана в разметке'

        # Лента избранных — внутри «Переписки», до работы с письмами.
        talk = re.search(r'<div class="fav-pane" data-fav-pane="talk">.*?'
                         r'(?=<div class="fav-pane off")', block, re.S)
        assert talk, 'панели «Переписка» нет'
        assert 'data-strip="favorite"' in talk.group(0), 'лента не в «Переписке»'
        assert talk.group(0).index('data-strip="favorite"') \
               < talk.group(0).index('fav-mail-pool'), 'лента должна стоять над пулом'

        # Выключатели — только у «Переписки».
        assert 'data-for="talk"' in block, 'выключатели не привязаны к подвкладке'
        for slot in ('fav-mail-make-on', 'fav-mail-send-on', 'fav-mail-pool',
                     'fav-mail-make-btn', 'fav-mail-limits',
                     'fav-sent', 'fav-sent-count', 'fav-sent-reload'):
            assert f'data-slot="{slot}"' in block, f'нет слота {slot}'

        for slot in ('fav-mail-per-day', 'fav-mail-gap'):
            assert f'data-slot="{slot}"' not in block, \
                f'{slot} правится на странице — он должен жить в настройках сайта'


class TestTemplateBySetting:
    """Шаблон можно назначить сайту руками."""

    async def test_setting_wins_over_name(self, work):
        """Настройка `template` сильнее имени сайта: два сайта могут делить один
        шаблон, а имя не всегда годится в имя файла."""
        html = await work(LADA, template='goldenbride')

        assert 'online-row' not in html, 'назначенный шаблон не подхватился'

    async def test_missing_template_falls_back(self, work):
        """Назначенный шаблон могли удалить. Молча берём умолчание, а не отдаём
        500: страница откроется и будет работать, а расхождение видно в логе."""
        html = await work(LADA, template='такого-нет')

        assert 'data-slot="dialogs"' in html


class TestTemplateNameIsSafe:
    """⚠ Имя шаблона приходит из базы — значит, ему нельзя доверять."""

    @pytest.mark.parametrize('attack', ['../profile_', '../../login', '/etc/passwd',
                                        '..%2f..%2fbase', '_base'])
    async def test_traversal_does_not_read_other_files(self, work, attack):
        """Возьми ручка значение как путь — получили бы чтение чужих шаблонов, а
        дальше и любого файла, до которого дотянется Jinja.

        Проверяем итог, а не механику: что бы ни лежало в настройке, наружу уходит
        шаблон работы с анкетой.
        """
        html = await work(LADA, template=attack)

        assert 'data-slot="dialogs"' in html, f'«{attack}» подменил шаблон'
        assert '<html' not in html.lower(), f'«{attack}» отдал целую страницу'

    def test_base_is_not_a_site_template(self):
        """`_base.html` — основа, а не шаблон сайта: отдать его напрямую значит
        показать разметку с неопределёнными блоками."""
        assert '_base'.startswith(work_module.WORK_HIDDEN)

    def test_slug_of_site_name(self):
        """Имя сайта в имя файла: `Ladadate` → `ladadate`."""
        assert _slug('Ladadate') == 'ladadate'
        assert _slug('  GoldenBride ') == 'goldenbride'
        assert _slug('Two Words') == 'two-words'

    def test_slug_refuses_anything_odd(self):
        """Не годится в имя — пустая строка, а не искажённое: подобрать шаблон по
        искажённому имени всё равно не выйдет."""
        for name in ('../etc/passwd', 'сайт', 'a/b', 'a.b', '', None, 'x' * 65):
            assert _slug(name) == '', f'«{name}» прошло как имя файла'


class TestWorkRoute:
    """То, что решается до входа в обработчик, — по HTTP, как и вся сюита."""

    async def test_site_is_required(self, auth_client):
        assert (await auth_client.get('/admin/dating/profile/work')).status_code == 422

    async def test_closed_without_login(self, client):
        answer = await client.get('/admin/dating/profile/work')

        assert answer.status_code in (401, 307), 'разметка отдалась без входа'


def _request() -> Request:
    """Запрос с той же Jinja, что у панели.

    Окружение собирается тем же вызовом и над тем же каталогом, что в
    `main_uvicorn.py`: ручка спрашивает список шаблонов у самой Jinja
    (`loader.list_templates`), и собери мы его иначе — проверяли бы не то, что
    работает в панели.
    """
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    templates = Jinja2Templates(directory=os.path.join(root, 'data/uvicorn/template'))

    class _App:
        class state:
            jinja2_template = templates

    return Request({'type': 'http', 'method': 'GET', 'path': '/admin/dating/profile/work',
                    'headers': [], 'query_string': b'', 'app': _App})


    async def test_stats_pane_is_one_slot_not_a_placeholder(self, work):
        """⚠ «Статистика» — та самая вкладка, что была «Ивентами».

        Переименована вместе с внутренним именем (`event` → `stats`): ключ,
        который называет одно, а показывает другое, врёт первому, кто пойдёт его
        искать. Проверяем, что старого имени не осталось нигде — иначе полоса
        вкладок и панели разошлись бы, и вкладка молча не открывалась.

        ⚠ **Карточки собирает страница, а не шаблон**: числа приходят ручкой и
        меняются, а «из каких блоков состоит вкладка» — один блок. Поэтому здесь
        два слота (подпись охвата и сетка), а не двадцать под каждое число.
        """
        html = await work(LADA)

        assert 'data-tab="stats"' in html, 'панели «Статистика» нет'
        assert 'data-tab="event"' not in html, 'старое имя вкладки осталось'
        assert 'data-slot="stats"' in html, 'нет слота под сетку карточек'
        assert 'data-slot="stats-note"' in html, 'нет слота под подпись охвата'
