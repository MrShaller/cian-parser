"""Блокировка необязательных подресурсов страницы объявления.

Парсеру нужен только HTML самого документа (инлайн-состояние
`frontend-offer-card` и `<h1>`), поэтому изображения, видео/аудио и шрифты
можно не скачивать: прерванные запросы не проходят через прокси и не попадают
в счёт трафика. Скрипты, стили и XHR/fetch не блокируются: они могут участвовать
в поведенческих проверках ЦИАН, и выигрыш от них не стоит риска.

Модуль не зависит от playwright: обработчик маршрута работает с любым объектом,
у которого есть `request.resource_type`, `abort()` и `continue_()`, поэтому
логика проверяется офлайн-тестами (см. `tests/test_resource_blocking.py`).
"""

from __future__ import annotations

import os
from collections.abc import Awaitable, Callable
from typing import Any

# Типы подресурсов Playwright, которые точно не нужны для разбора HTML:
# картинки и медиа парсер не скачивает (URL берутся из состояния страницы),
# шрифты влияют только на отрисовку.
DEFAULT_BLOCKED_RESOURCE_TYPES = ('image', 'media', 'font')

# Переопределяет набор: «CIAN_BLOCKED_RESOURCE_TYPES=image,font»,
# пустое значение отключает блокировку.
BLOCKED_RESOURCE_TYPES_ENV_VAR = 'CIAN_BLOCKED_RESOURCE_TYPES'

# Перехватываются все запросы страницы; решение принимается по resource_type.
ROUTE_PATTERN = '**/*'

# Счётчик прерванных запросов по типам ресурсов.
ResourceTally = dict[str, int]

RouteHandler = Callable[[Any], Awaitable[None]]


def blocked_resource_types() -> tuple[str, ...]:
    """Набор типов ресурсов для блокировки.

    Без переменной окружения — безопасный набор по умолчанию; пустое значение
    переменной отключает блокировку; иначе — список через запятую без повторов.
    """
    raw = os.environ.get(BLOCKED_RESOURCE_TYPES_ENV_VAR)
    if raw is None:
        return DEFAULT_BLOCKED_RESOURCE_TYPES
    return tuple(dict.fromkeys(part.strip().lower() for part in raw.split(',') if part.strip()))


def make_route_handler(blocked_types: tuple[str, ...], tally: ResourceTally) -> RouteHandler:
    """Обработчик Playwright-маршрута: прерывает заблокированные типы, остальные пропускает.

    :param blocked_types: типы ресурсов (`route.request.resource_type`), которые прерываются.
    :param tally: словарь, в который пишется количество прерванных запросов по типам;
        остальные данные о запросах (URL, заголовки, тело) нигде не сохраняются.
    """

    async def handle_route(route: Any) -> None:
        resource_type = route.request.resource_type
        if resource_type in blocked_types:
            tally[resource_type] = tally.get(resource_type, 0) + 1
            await route.abort()
        else:
            await route.continue_()

    return handle_route
