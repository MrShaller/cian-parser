# Cian.ru offer parser

Apify Actor, который загружает страницу одного объявления ЦИАН по URL и сохраняет
структурированные данные в Dataset: цена, адрес, координаты, характеристики
квартиры и дома, фотографии, метро, описание.

Загрузку страницы выполняет [HTTPX2](https://github.com/pydantic/httpx2),
извлечение данных — собственный модуль `my_actor/parser.py` на
[Beautiful Soup](https://www.crummy.com/software/BeautifulSoup/bs4/doc/).

## Что делает Actor

1. Читает вход: URL страницы объявления (обязательное поле `url`).
2. Выполняет один GET-запрос по этому URL (`follow_redirects`, ошибки HTTP
   приводят к падению запуска).
3. Извлекает данные функцией `parse_offer_html()`. Основной источник —
   встроенное состояние микросервиса `frontend-offer-card`
   (`window._cianConfig['frontend-offer-card']` → `defaultState` →
   `offerData.offer`, `offerData.features`, `offerData.bti`); из DOM берётся
   только заголовок `<h1>`. Значения, которых нет на странице, возвращаются
   как `null` и никогда не додумываются.
4. Сохраняет одну запись в Dataset (`Actor.push_data([offer])`).

Сейчас поддержан сценарий «одно объявление за запуск» (проверено на страницах
`cian.ru/<deal_type>/<offer_type>/<id>/`): обход поисковой выдачи, пагинация
и обработка капчи не реализованы — страница капчи или другой раздел ЦИАН
приводит к ошибке `ParseError` с понятным сообщением.

## Требования и установка

- **Python ≥ 3.11** — минимальная версия пакета `apify` 4.x (локально
  проверено на 3.14; на платформе Actor работает в образе
  `apify/actor-python:3.14`).
- **Node.js LTS и Apify CLI** — для локальных запусков и валидации схем
  (`npm install -g apify-cli`).

Зависимости проекта описаны в `requirements.txt`: `apify` ^4.0,
`beautifulsoup4[lxml]` ^4.0, `httpx2` ^2.5, `types-beautifulsoup4` ^4.0.

```bash
python -m venv .venv
source .venv/bin/activate        # Windows Git Bash; в PowerShell: .venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

## Входные данные

Форма входа строится из `.actor/input_schema.json`.

| Поле | Тип | Описание |
| --- | --- | --- |
| `url` | string, обязателен | URL страницы объявления, например `https://spb.cian.ru/sale/flat/308497101/` |
| `proxyConfiguration` | object, по умолчанию выключен | штатная настройка Apify Proxy (см. ниже) |

Минимальный ввод:

```json
{
    "url": "https://spb.cian.ru/sale/flat/308497101/"
}
```

## Запуск локально через Apify CLI

```bash
apify run
```

Локальный ввод кладётся в `storage/key_value_stores/default/INPUT.json`
(создаётся по input schema), результаты появляются в
`storage/datasets/default/` по одному JSON на объявление. Локальное
хранилище `storage/` не синхронизируется с Apify Console — вывод платформенного
запуска проверяется в Console.

Для отладки парсера без сети и без Actor можно разобрать сохранённый HTML
напрямую:

```bash
python -m my_actor.parse_local test_data/cian_308497101.html
```

## Apify Proxy: включение и выключение

Прокси управляется полем `proxyConfiguration` во входе, код менять не нужно.

- **Выключено (по умолчанию).** Чекбокс «Use Apify Proxy» снят — Actor ходит
  по URL напрямую с IP машины, где запущен.
- **Включено.** Отметьте «Use Apify Proxy» и при необходимости выберите группу
  (`RESIDENTIAL`, `UNBLOCKER`), страну и/или список собственных прокси
  (`proxyUrls`). Группа выбирается на каждый запуск в интерфейсе, в коде она
  не зашита. Пароль прокси на платформе подставляется автоматически.

В логе запуска видно, какой режим выбран: `Proxy is disabled, connecting
directly` или `Using Apify Proxy (groups: …)`.

> **Предупреждение для локальных запусков.** Apify Proxy требует авторизации:
> выполните `apify login` или задайте `APIFY_TOKEN` / `APIFY_PROXY_PASSWORD`.
> Если включить прокси локально без авторизации, запуск завершится ошибкой —
> Actor намеренно не «тихо» переключается на прямые запросы. На платформе
> (Apify Console, scheduled/API-запуски) авторизация не требуется.

Расход и актуальные цены прокси (RESIDENTIAL — за гигабайт трафика, UNBLOCKER —
за успешные запросы) видны в Apify Console на странице Proxy и на
[странице тарифов](https://apify.com/pricing).

## Какие данные извлекаются

Каждый запуск добавляет в Dataset одну запись со следующими полями
(полное описание — в `.actor/dataset_schema.json`):

- **Объявление:** `url`, `offer_id`, `title`, `price`, `price_currency`,
  `price_per_meter`, `residential_complex`, `description`, `photo_urls`,
  `floor_plan_url`, `metro` (список `{name, travel_time_min, travel_type}`),
  `created_at`, `edited_at`, `parsed_at` (ISO-8601 UTC).
- **`address`:** `full`, `city`, `district`, `okrug`, `street`, `house_number`.
- **`coordinates`:** `{lat, lng}` или `null`.
- **`flat`:** тип жилья, общая/жилая/кухонная площадь, этаж, потолки, санузел,
  балкон/лоджия, вид из окон, ремонт, комнаты, признак апартаментов.
- **`building`:** год постройки, этажность, потолки, парковка, отопление,
  перекрытия, аварийность, лифты, краны.
- **`about_flat_raw`, `about_building_raw`:** разделы «О квартире» и «О доме»
  в том виде, как их показывает страница.

В Console результаты видны на вкладке Output (таблица `overview`: url,
offer_id, title, price, residential_complex, parsed_at); полный датасет
доступен по ссылке из Output schema и через API.

Усечённый пример записи (значения выверены тестами на сохранённой странице):

```json
{
    "url": "https://spb.cian.ru/sale/flat/308497101/",
    "offer_id": 308497101,
    "title": "Продается 3-комн. квартира, 79,6 м²",
    "price": 33000000,
    "price_currency": "rur",
    "residential_complex": "Promenade (Променад)",
    "parsed_at": "…время запуска в ISO-8601 UTC…"
}
```

## Ограничения парсера

Ниже — только подтверждённые кодом и тестами ограничения
(`tests/test_parser.py`):

- **Планировки.** `floor_plan_url` заполняется только если ЦИАН отдал
  планировки в `offer.cianLayouts` с явным `fullUrl`. Среди обычных фотографий
  планировки не маркируются, а в сохранённом примере `cianLayouts` был `null`,
  поэтому поле остаётся `null` — планировка не выдумывается
  (`test_floor_plan_not_invented`).
- **Жилая площадь.** Берётся из раздела «О квартире» либо из `areaParts`;
  если данных нет — `null`, а не 0 и не общая площадь
  (`test_living_area_absent_not_invented`).
- **Метро.** Способ передвижения (`travel_type`) указывается только для
  значений `walk`/`transport`, иначе `null`.
- **Адрес.** Адресные элементы неизвестных типов игнорируются, чтобы значения
  разных уровней не смешивались (`test_address_parts_not_mixed`).
- **Аварийность дома** — из данных БТИ (`isEmergency`): «Да»/«Нет», при
  отсутствии данных — `null`.
- **Чужой HTML.** Если в странице нет состояния объявления (капча, другой
  раздел сайта), парсер raises `ParseError`
  (`test_parse_error_on_foreign_html`).

## Проверки

```bash
python -m unittest discover          # 14 тестов
python -m compileall my_actor tests  # синтаксис и компилируемость
apify validate-schema                # input и dataset схемы Apify
```

Одиннадцать тестов проверяют точные значения на сохранённой копии страницы
`test_data/cian_308497101.html`; три — вспомогательные функции. На свежем
клоне без этой папки 11 тестов пропускаются (`skipUnless`), 3 выполняются.

Папка `test_data/` содержит персональные данные продавца и исключена из Git
и Docker-образа через `.gitignore`/`.dockerignore` — не добавляйте её в
коммиты и не включайте в сборку.

## Документация

- [Apify SDK для Python](https://docs.apify.com/sdk/python)
- [Apify Proxy](https://docs.apify.com/platform/proxy)
- [Input schema](https://docs.apify.com/platform/actors/development/actor-definition/input-schema/specification/v1)
- [Dataset schema](https://docs.apify.com/platform/actors/development/actor-definition/dataset-schema)
