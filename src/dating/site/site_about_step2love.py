"""Step2Love: поля страницы подписаны по-своему и с двоеточием.

Сайт из семьи step2love (`mystep2love.com`, панель `agency.step2love.com`).
Вёрстка блока анкеты для GET-пути здесь не нужна — **живого пути у GET нет**:
ни одна анкета сайта не хранит сессию (`storage_state` пусто), и дверь
`companion_about_http` честно говорит «у анкеты нет живой сессии». Анкету
снимает сценарий («анкета мужчины: карточка», шаг `read_text` отдаёт **текст
всей страницы**), а значит драйверу от этого сайта нужно ровно одно — карта
подписей.

## Чем эти подписи не такие

На Ladadate поля страницы называются канонически («Eyes Color», «Hair Color»,
«About Myself»). Здесь та же информация подписана другими словами, и ниже
канона — целый блок «Questionnaire»: свободные ответы на вопросы сайта, которых
у Ladadate нет вовсе. Плюс две особенности разбора:

* **«Looking for:» стоит дважды** — в «Personal Information» это анкетный
  выбор («Female (30-45 years)»), а в вопроснике — свой рассказ о партнёре.
  Одна и та же подпись, два разных поля; порядок entries в списке задаёт,
  какое вхождение чем стало.
* **Служебные заголовки без подписей-конкурентов** («Photo Album», section
  «Questionnaire») поставлены границами с `None`: пустое поле «Graduated from:»
  иначе утащило бы в образование текст «Photo Album My details».

⚠ **Двоеточие — часть подписи, а не знак препинания разбора**: на этой
странице каждое поле подписано «Label: значение», и двоеточие в самой подписи
отсекает случайные совпадения с теми же словами в прозе ответов.
"""

from src.dating.site.site_about import DatingSiteAbout

# Подпись страницы → канон (`companion_param.py` / `companion_about`), в
# **порядке страницы**. Пары с `None` — границы без поля.
COMPANION_FIELDS_STEP2LOVE = (
    # Шапка страницы: возраст и знак зодиака — в дело, счёт фотографий — граница.
    ('Age:',           'Age'),
    ('Star sign:',     'Star sign'),
    ('Photo Album',    None),       # вхождение 1 — шапка («0 photo»)
    ('ID:',            'Id:'),
    # Personal Information.
    ('Name:',          'Name'),
    ('Date of birth:', 'Birthday'),
    ('Sex:',           'Sex'),
    ('Looking for:',   'Looking for'),
    ('For:',           'For'),
    ('Country:',       'Location'),
    ('Graduated from:', 'Education'),
    ('Photo Album',    None),       # вхождение 2 — пустое поле у границы раздела
    # My details.
    ('Weight:',        'Weight'),
    ('Height:',        'Height'),
    ('Languages:',     'Languages'),
    ('Body type:',     'Body Type'),
    ('Hair length:',   'Hair Color'),
    ('Eye color:',     'Eyes Color'),
    ('Ethnicity:',     'Ethnicity'),
    ('Marital status:', 'Marital Status'),
    ('Life priorities:', 'Life priorities'),
    ('Smoker:',        'Smoke'),
    ('Drinker:',       'Drink'),
    ('Financial position:', 'Financial position'),
    ('Place of residence:', 'Place of residence'),
    ('Children:',      'Children'),
    # Questionnaire — свободные ответы; их ищет письмо и разговор.
    ('Looking for:',   'About Partner'),   # вхождение 2 — уже рассказ, не выбор
    ('Questionnaire',  None),              # граница: иначе «Children» глотает слово
    ('About me:',      'About Myself'),
    ('Qualities that I appreciate in people the most:', 'Qualities'),
    ('My goals for the future:', 'Goals'),
    ('My travel destinations (past or future):', 'Travel'),
    ('I spend a lot of time thinking about (or my dream is):', 'Dream'),
    ('My favorite movies, books, music, shows:', 'Media'),
    ('My favorite food:', 'Food'),
    ('Pets in my life:', 'Pets'),
    ("My hobbies and what I'm good at:", 'Hobby'),
    ('Talking about sports:', 'Sports'),
    ("The five things I can't live without:", 'Five things'),
    ('You should message me if:', 'Message me if'),
    ('Job:',           'Occupation'),
    ('Start Chat Now!', None),             # кнопка: без неё — хвостом к занятию
)


class DatingSiteAboutStep2Love(DatingSiteAbout):
    """Step2Love: общий разбор, своя карта подписей страницы.

    Разбор текста — базовый (`companion_about_parse`): подписи переживают и
    перевод интерфейса, и перевёрстку, а своей копией разбора сайт бы только
    удвоил общее правило.
    """

    name = 'step2love'

    about_fields = COMPANION_FIELDS_STEP2LOVE
