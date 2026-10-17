"""Офлайн-тесты логики блокировки необязательных подресурсов.

Playwright локально не установлен, поэтому обработчик маршрута проверяется
на подставных объектах с тем же интерфейсом (`request.resource_type`,
`abort()`, `continue_()`), а чтение переменной окружения — через patch.dict.
"""

from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from my_actor.resource_blocking import (
    BLOCKED_RESOURCE_TYPES_ENV_VAR,
    DEFAULT_BLOCKED_RESOURCE_TYPES,
    blocked_resource_types,
    make_route_handler,
)

ENV_VAR = BLOCKED_RESOURCE_TYPES_ENV_VAR


class _StubRequest:
    def __init__(self, resource_type: str) -> None:
        self.resource_type = resource_type


class _StubRoute:
    """Мини-копия playwright.sync_api.Route: считает вызовы abort/continue_."""

    def __init__(self, resource_type: str) -> None:
        self.request = _StubRequest(resource_type)
        self.abort_calls = 0
        self.continue_calls = 0

    async def abort(self) -> None:
        self.abort_calls += 1

    async def continue_(self) -> None:
        self.continue_calls += 1


class BlockedResourceTypesTest(unittest.TestCase):
    def test_default_without_env_var(self) -> None:
        with patch.dict(os.environ, clear=True):
            self.assertEqual(blocked_resource_types(), ('image', 'media', 'font'))

    def test_custom_list_from_env_var(self) -> None:
        env = {ENV_VAR: ' Font, image , font, MEDIA'}
        with patch.dict(os.environ, env, clear=True):
            self.assertEqual(blocked_resource_types(), ('font', 'image', 'media'))

    def test_empty_env_var_disables_blocking(self) -> None:
        for raw in ('', '  ', ',,,'):
            with self.subTest(raw=raw), patch.dict(os.environ, {ENV_VAR: raw}, clear=True):
                self.assertEqual(blocked_resource_types(), ())

    def test_default_set_is_the_safe_minimum(self) -> None:
        # Парсеру не нужны ни картинки, ни медиа, ни шрифты; расширять набор
        # (стили, скрипты, xhr) без отдельной проверки нельзя.
        self.assertEqual(DEFAULT_BLOCKED_RESOURCE_TYPES, ('image', 'media', 'font'))


class RouteHandlerTest(unittest.IsolatedAsyncioTestCase):
    async def test_blocked_type_is_aborted_and_counted(self) -> None:
        tally: dict[str, int] = {}
        handler = make_route_handler(('image', 'media', 'font'), tally)

        route = _StubRoute('image')
        await handler(route)

        self.assertEqual(route.abort_calls, 1)
        self.assertEqual(route.continue_calls, 0)
        self.assertEqual(tally, {'image': 1})

    async def test_allowed_types_pass_through_untouched(self) -> None:
        tally: dict[str, int] = {}
        handler = make_route_handler(('image',), tally)

        for resource_type in ('document', 'stylesheet', 'script', 'xhr', 'fetch', 'other'):
            route = _StubRoute(resource_type)
            await handler(route)
            with self.subTest(resource_type=resource_type):
                self.assertEqual(route.continue_calls, 1)
                self.assertEqual(route.abort_calls, 0)
        self.assertEqual(tally, {})

    async def test_tally_accumulates_per_type(self) -> None:
        tally: dict[str, int] = {}
        handler = make_route_handler(('image', 'font'), tally)

        for resource_type in ('image', 'image', 'font', 'image'):
            await handler(_StubRoute(resource_type))

        self.assertEqual(tally, {'image': 3, 'font': 1})


if __name__ == '__main__':
    unittest.main()
