"""Извлечение структурированных данных из страницы объявления ЦИАН.

Парсер не зависит от сети: он принимает HTML одной страницы объявления и
возвращает JSON-сериализуемый словарь. Одна и та же логика используется
локально (см. `parse_local.py`) и внутри Apify Actor (см. `main.py`).

Основной источник данных — встроенное состояние микросервиса
`frontend-offer-card`: `window._cianConfig['frontend-offer-card']` содержит
запись с ключом `defaultState`, в которой лежат `offerData.offer` (данные
объявления), `offerData.features` (разделы «О квартире» и «О доме» в том виде,
в каком их показывает страница) и `offerData.bti` (данные БТИ о доме).
Отдельные поля (заголовок, исходный URL) дополняются из DOM.

Отсутствующие значения возвращаются как `None` — они никогда не выдумываются.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from typing import Any

from bs4 import BeautifulSoup

# Служебные значения БТИ, означающие «данных нет»: их нельзя выдавать за факт.
_BTI_UNKNOWN_VALUES = {"none", "unknown", ""}

# Отображаемые значения, которые страница строит из перечислений.
# Используются только как fallback, если раздела features в состоянии нет.
_WINDOWS_VIEW_TYPES = {
    "yardAndStreet": "На улицу и двор",
    "yard": "Во двор",
    "street": "На улицу",
}
_RENOVATION_TYPES = {
    "design": "Дизайнерский",
    "euro": "Евроремонт",
    "cosmetic": "Косметический",
    "none": "Без ремонта",
}
_PARKING_TYPES = {
    "underground": "Подземная",
    "ground": "Наземная",
    "multilevel": "Многоуровневая",
    "open": "Открытая",
    "closed": "Закрытая",
}
_TRAVEL_TYPES = {
    "walk": "пешком",
    "transport": "на транспорте",
}

_SAVED_FROM_RE = re.compile(r"saved\s+from\s+url=\(\d+\)(\S+)")


class ParseError(ValueError):
    """HTML не содержит распознаваемого состояния объявления ЦИАН."""


def parse_offer_html(html: str, source_url: str | None = None) -> dict[str, Any]:
    """Разбирает HTML страницы объявления ЦИАН и возвращает структурированные данные.

    :param html: HTML страницы объявления (сохранённый файл или ответ сервера).
    :param source_url: исходный URL страницы; если не задан, берётся из
        комментария «saved from url» браузера, иначе восстанавливается по ID.
    :raises ParseError: если в HTML нет состояния объявления (например,
        сохранена страница капчи или другая страница ЦИАН).
    """
    state = _extract_default_state(html)
    if state is None:
        raise ParseError(
            "В HTML не найдено состояние объявления (defaultState "
            "микросервисного конфига frontend-offer-card). "
            "Страница может быть капчей, редиректом или не страницей объявления."
        )

    offer_data: dict[str, Any] = state.get("offerData") or {}
    offer: dict[str, Any] = offer_data.get("offer") or {}
    if not offer:
        raise ParseError("Состояние объявления найдено, но offerData.offer пуст.")

    soup = BeautifulSoup(html, "lxml")
    geo: dict[str, Any] = offer.get("geo") or {}
    building: dict[str, Any] = offer.get("building") or {}
    about_flat, about_building = _feature_sections(offer_data)
    bargain_terms: dict[str, Any] = offer.get("bargainTerms") or {}

    return {
        "url": source_url or _saved_from_url(html) or _offer_url_from_offer(offer),
        "offer_id": offer.get("id") or offer.get("cianId"),
        "title": _extract_title(soup),
        "price": _as_int(offer.get("priceTotalRur") or offer.get("priceTotal")),
        "price_currency": bargain_terms.get("currency"),
        "price_per_meter": _price_per_meter(offer_data),
        "address": _address(geo),
        "coordinates": _coordinates(geo),
        "residential_complex": _residential_complex(offer, geo),
        "photo_urls": _photo_urls(offer),
        "floor_plan_url": _floor_plan_url(offer),
        "flat": {
            "housing_type": about_flat.get("Тип жилья"),
            "total_area": _as_number(offer.get("totalArea")),
            "living_area": _living_area(offer, about_flat),
            "kitchen_area": _as_number(offer.get("kitchenArea")),
            "floor": _as_int(offer.get("floorNumber")),
            "floors_total": _as_int(building.get("floorsCount")),
            "ceiling_height": _as_number(building.get("ceilingHeight")),
            "bathroom": about_flat.get("Санузел") or _bathroom_from_counts(offer),
            "balcony": about_flat.get("Балкон/лоджия") or _balcony_from_counts(offer),
            "windows_view": about_flat.get("Вид из окон")
            or _WINDOWS_VIEW_TYPES.get(offer.get("windowsViewType")),
            "renovation": about_flat.get("Ремонт") or _RENOVATION_TYPES.get(offer.get("repairType")),
            "rooms_count": _as_int(offer.get("roomsCount")),
            "is_apartments": offer.get("isApartments"),
        },
        "building": {
            "build_year": _as_int(building.get("buildYear")),
            "floors_count": _as_int(building.get("floorsCount")),
            "ceiling_height": _as_number(building.get("ceilingHeight")),
            "parking": about_building.get("Парковка")
            or _PARKING_TYPES.get((building.get("parking") or {}).get("type")),
            "heating": about_building.get("Отопление"),
            "overlap_type": about_building.get("Тип перекрытий"),
            "emergency": about_building.get("Аварийность") or _emergency_from_bti(offer_data),
            "lift_types": building.get("liftTypes") or [],
            "cranage_types": building.get("cranageTypes") or [],
        },
        "about_flat_raw": about_flat,
        "about_building_raw": about_building,
        "description": _description(offer),
        "metro": _metro(geo),
        "created_at": offer.get("creationDate"),
        "edited_at": offer.get("editDate"),
        "parsed_at": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
    }


# --- Встроенное состояние --------------------------------------------------


def _extract_default_state(html: str) -> dict[str, Any] | None:
    """Достаёт `defaultState` из конфига `frontend-offer-card`.

    Скрипт имеет вид
    ``window._cianConfig['frontend-offer-card'] = (... || []).concat([{...}]);``
    где массив содержит запись ``{"key": "defaultState", "value": {...}}``.
    """
    for match in re.finditer(r"<script\b[^>]*>(.*?)</script>", html, re.S):
        text = match.group(1)
        if "'frontend-offer-card'" not in text:
            continue
        anchor = text.index("'frontend-offer-card'")
        concat = text.find(".concat([", anchor)
        if concat == -1:
            continue
        try:
            entries, _ = json.JSONDecoder().raw_decode(text[concat + len(".concat("):])
        except ValueError:
            continue
        if not isinstance(entries, list):
            continue
        for entry in entries:
            if isinstance(entry, dict) and entry.get("key") == "defaultState":
                value = entry.get("value")
                if isinstance(value, dict):
                    return value
    return None


# --- Простые поля ----------------------------------------------------------


def _extract_title(soup: BeautifulSoup) -> str | None:
    """Заголовок объявления — текст `<h1>` карточки."""
    h1 = soup.find("h1")
    if h1 is None:
        return None
    title = h1.get_text(" ", strip=True)
    return title or None


def _saved_from_url(html: str) -> str | None:
    """URL из комментария, который браузер добавляет при сохранении страницы."""
    match = _SAVED_FROM_RE.search(html)
    if match:
        url = match.group(1).rstrip('-–—.,;')
        return url or None
    return None


def _offer_url_from_offer(offer: dict[str, Any]) -> str | None:
    """Восстанавливает URL объявления по его типу и ID (канонический host www)."""
    offer_id = offer.get("id")
    deal_type = offer.get("dealType")
    offer_type = offer.get("offerType")
    if offer_id and deal_type and offer_type:
        return f"https://www.cian.ru/{deal_type}/{offer_type}/{offer_id}/"
    return None


def _price_per_meter(offer_data: dict[str, Any]) -> int | None:
    price_info = offer_data.get("priceInfo") or {}
    return _as_int(price_info.get("pricePerSquareValue"))


def _residential_complex(offer: dict[str, Any], geo: dict[str, Any]) -> str | None:
    newbuilding = offer.get("newbuilding") or {}
    name = newbuilding.get("name") or (geo.get("jk") or {}).get("name")
    return name or None


def _description(offer: dict[str, Any]) -> str | None:
    """Полное описание объявления без изменений (страница хранит его как текст)."""
    description = offer.get("description")
    if isinstance(description, str) and description:
        return description
    return None


# --- Адрес и метро ---------------------------------------------------------

_ADDRESS_TYPES = {
    "location": "city",
    "city": "city",
    "raion": "district",
    "okrug": "okrug",
    "street": "street",
    "house": "house_number",
}


def _address(geo: dict[str, Any]) -> dict[str, Any]:
    """Адресная цепочка ЦИАН: город → район → округ → улица → дом.

    Неизвестные типы адресных элементов игнорируются, чтобы значения разных
    уровней не смешивались.
    """
    result: dict[str, str | None] = {
        "full": None,
        "city": None,
        "district": None,
        "okrug": None,
        "street": None,
        "house_number": None,
    }
    parts: list[str] = []
    for item in geo.get("address") or []:
        if not isinstance(item, dict):
            continue
        key = _ADDRESS_TYPES.get(item.get("type"))
        name = item.get("name") or item.get("fullName")
        if key and name and result.get(key) is None:
            result[key] = name
        full_name = item.get("fullName") or name
        if full_name:
            parts.append(full_name)
    result["full"] = ", ".join(parts) or None
    return result


def _coordinates(geo: dict[str, Any]) -> dict[str, float] | None:
    coordinates = geo.get("coordinates") or {}
    lat, lng = coordinates.get("lat"), coordinates.get("lng")
    if isinstance(lat, (int, float)) and isinstance(lng, (int, float)):
        return {"lat": float(lat), "lng": float(lng)}
    return None


def _metro(geo: dict[str, Any]) -> list[dict[str, Any]]:
    """Станции метро: название, время в пути и способ передвижения.

    Способ передвижения указывается только если ЦИАН его сообщает
    (`travelType: walk | transport`); иначе — `None`.
    """
    stations = []
    for underground in geo.get("undergrounds") or []:
        if not isinstance(underground, dict):
            continue
        stations.append(
            {
                "name": underground.get("name"),
                "travel_time_min": _as_int(underground.get("travelTime")),
                "travel_type": _TRAVEL_TYPES.get(underground.get("travelType")),
            }
        )
    return stations


# --- Разделы «О квартире» и «О доме» ---------------------------------------


def _feature_sections(offer_data: dict[str, Any]) -> tuple[dict[str, str], dict[str, str]]:
    """Значения разделов «О квартире» (aboutFlat) и «О доме» (aboutBuilding).

    Возвращает два словаря «название характеристики → значение как на странице».
    Разделы с неизвестными идентификаторами не попадают ни в один из них.
    """
    about_flat: dict[str, str] = {}
    about_building: dict[str, str] = {}
    for section in offer_data.get("features") or []:
        if not isinstance(section, dict):
            continue
        target = {"aboutFlat": about_flat, "aboutBuilding": about_building}.get(section.get("id"))
        if target is None:
            continue
        for feature in section.get("features") or []:
            if isinstance(feature, dict) and feature.get("label"):
                # В HTML значения содержат &nbsp;; заменяем их обычными пробелами.
                value = feature.get("value")
                target[str(feature["label"]).replace("\xa0", " ")] = (
                    value.replace("\xa0", " ") if isinstance(value, str) else value
                )
    return about_flat, about_building


def _living_area(offer: dict[str, Any], about_flat: dict[str, str]) -> float | None:
    """Жилая площадь: из раздела «О квартире» либо из `areaParts` объявления."""
    displayed = about_flat.get("Площадь жилая") or about_flat.get("Жилая площадь")
    if displayed:
        return _as_number(displayed)
    for part in offer.get("areaParts") or []:
        if not isinstance(part, dict):
            continue
        kind = str(part.get("type") or part.get("name") or "")
        if "living" in kind.lower():
            return _as_number(part.get("value") if "value" in part else part.get("area"))
    return None


def _bathroom_from_counts(offer: dict[str, Any]) -> str | None:
    separate = _as_int(offer.get("separateWcsCount")) or 0
    combined = _as_int(offer.get("combinedWcsCount")) or 0
    if not separate and not combined:
        return None
    parts = []
    if separate:
        parts.append(f"{separate} раздельных")
    if combined:
        parts.append(f"{combined} совмещённых")
    return ", ".join(parts)


def _balcony_from_counts(offer: dict[str, Any]) -> str | None:
    balconies = _as_int(offer.get("balconiesCount")) or 0
    loggias = _as_int(offer.get("loggiasCount")) or 0
    parts = []
    if balconies:
        parts.append(f"{balconies} " + _plural(balconies, "балкон", "балкона", "балконов"))
    if loggias:
        parts.append(f"{loggias} " + _plural(loggias, "лоджия", "лоджии", "лоджий"))
    return ", ".join(parts) or None


def _emergency_from_bti(offer_data: dict[str, Any]) -> str | None:
    house_data = (offer_data.get("bti") or {}).get("houseData") or {}
    is_emergency = house_data.get("isEmergency")
    if is_emergency is None:
        return None
    return "Да" if is_emergency else "Нет"


def _plural(n: int, one: str, few: str, many: str) -> str:
    if n % 10 == 1 and n % 100 != 11:
        return one
    if n % 10 in (2, 3, 4) and n % 100 not in (12, 13, 14):
        return few
    return many


# --- Фотографии ------------------------------------------------------------


def _photo_urls(offer: dict[str, Any]) -> list[str]:
    """Прямые URL всех изображений объявления, без повторов, в порядке галереи."""
    urls: list[str] = []
    for photo in offer.get("photos") or []:
        if not isinstance(photo, dict):
            continue
        url = photo.get("fullUrl")
        if isinstance(url, str) and url and url not in urls:
            urls.append(url)
    return urls


def _floor_plan_url(offer: dict[str, Any]) -> str | None:
    """URL планировки, если ЦИАН отдал её отдельно от фотографий.

    Планировки на карточке живут в отдельной вкладке галереи и приходят в
    `offer.cianLayouts`; в галерее обычных фотографий они не маркируются,
    поэтому среди `photos` планировку определить нельзя — там возвращается
    `None`. Структура заполненного `cianLayouts` на сохранённой странице не
    встретилась (в примере поле `null`), поэтому берём только записи с
    явным `fullUrl`.
    """
    layouts = offer.get("cianLayouts")
    if isinstance(layouts, list):
        for layout in layouts:
            if isinstance(layout, dict) and isinstance(layout.get("fullUrl"), str):
                return layout["fullUrl"]
    return None


# --- Приведение типов ------------------------------------------------------


def _as_int(value: Any) -> int | None:
    number = _as_number(value)
    if number is None:
        return None
    return int(number) if float(number).is_integer() else round(number)


def _as_number(value: Any) -> float | None:
    """Число из значения вида ``79.6``, ``"79,6 м²"`` или ``2,75``; иначе `None`."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).replace("\xa0", "").replace(" ", "").replace(",", ".")
    match = re.search(r"-?\d+(?:\.\d+)?", text)
    return float(match.group()) if match else None
