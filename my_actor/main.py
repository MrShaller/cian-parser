"""Точка входа Apify Actor: загрузка страницы объявления ЦИАН в Chromium и извлечение данных.

Вся логика извлечения — в `my_actor.parser.parse_offer_html`; этот модуль
открывает URL в headless-Chromium через PlaywrightCrawler (настоящий браузерный
отпечаток вместо HTTP-клиента — против TLS/HTTP-фингерпринтинга ЦИАН),
сохраняет безопасные метаданные неудачных ответов и кладёт результат в датасет.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from apify import Actor, Event
from crawlee.crawlers import PlaywrightCrawler, PlaywrightCrawlingContext

from .parser import ParseError, parse_offer_html

# Признаки CAPTCHA или ограничения доступа; ищутся в тексте ответа без учёта регистра.
_ACCESS_MARKERS = ("captcha", "access denied", "доступ ограничен", "проверка браузера")


def _strip_query_and_fragment(url: str) -> str:
    """Срезает query-параметры и fragment; fragment может содержать '?', поэтому срезается первым."""
    return url.split('#', 1)[0].split('?', 1)[0]


def _error_diagnostics(
    *,
    http_status: int,
    content_type: str | None,
    text: str | None,
    body_bytes: int | None,
    final_url: str | None,
) -> dict[str, Any]:
    """Собирает безопасные метаданные ответа для диагностики ошибки разбора.

    Тело ответа не сохраняется: только статус, Content-Type, размер в символах
    и в байтах, конечный URL без query-параметров и fragment и булевы маркеры
    CAPTCHA/ограничения доступа. Cookies и остальные заголовки не читаются.
    """
    lowered = text.lower() if isinstance(text, str) else ''
    return {
        'http_status': int(http_status),
        'content_type': content_type,
        'response_chars': len(text) if isinstance(text, str) else None,
        'response_bytes': body_bytes,
        'final_url': _strip_query_and_fragment(final_url) if final_url else None,
        'markers': {marker.replace(' ', '_'): marker in lowered for marker in _ACCESS_MARKERS},
    }


async def _save_error_diagnostics(diagnostics: dict[str, Any]) -> None:
    """Пишет диагностику в KV-стор; краткая сводка идёт в лог, тело ответа — никуда."""
    summary = {key: value for key, value in diagnostics.items() if key != 'markers'}
    Actor.log.info(f'Saving parse-error diagnostics: {summary}, markers={diagnostics["markers"]}')
    await Actor.set_value('parse-error-diagnostics', diagnostics)


async def main() -> None:
    async with Actor:
        async def on_aborting() -> None:
            Actor.log.info('Получен сигнал прерывания: завершаемся.')
            await Actor.exit()

        Actor.on(Event.ABORTING, on_aborting)

        actor_input = await Actor.get_input() or {}
        url = actor_input.get('url')
        if not url:
            raise ValueError('Missing "url" attribute in input!')

        # Прокси включается только явным чекбоксом «Use Apify Proxy» или списком proxyUrls:
        # вызов create_proxy_configuration без этого условия при пустом входе включил бы
        # автоматический Apify Proxy.
        proxy_input = actor_input.get('proxyConfiguration') or {}
        proxy_configuration = None
        if proxy_input.get('useApifyProxy', False) or proxy_input.get('proxyUrls'):
            proxy_configuration = await Actor.create_proxy_configuration(actor_proxy_input=proxy_input)
        if proxy_configuration:
            groups = ', '.join(proxy_input.get('apifyProxyGroups') or ['AUTO'])
            Actor.log.info(f'Using Apify Proxy (groups: {groups})')
        else:
            Actor.log.info('Proxy is disabled, connecting directly')

        # Один запрос без повторов: каждый прогон — ровно одно обращение к ЦИАН.
        crawler = PlaywrightCrawler(
            proxy_configuration=proxy_configuration,
            headless=True,
            max_requests_per_crawl=1,
            max_request_retries=0,
            navigation_timeout=timedelta(minutes=2),
        )

        # Результат передаётся через словарь: исключение должно выйти из main() (и уронить
        # Actor с прежним кодом 91), а не погаситься внутри crawler'а как ошибка запроса.
        outcome: dict[str, Any] = {}

        @crawler.router.default_handler
        async def request_handler(context: PlaywrightCrawlingContext) -> None:
            response = context.response
            Actor.log.info(f'Got response: HTTP {response.status}, final URL {context.page.url}')
            html = await response.text()
            try:
                body_bytes: int | None = len(await response.body())
            except Exception:  # размер вторичен — не роняем диагностику
                body_bytes = None

            try:
                outcome['offer'] = parse_offer_html(
                    html,
                    source_url=_strip_query_and_fragment(context.page.url) or url,
                )
            except ParseError as err:
                outcome['error'] = err
                await _save_error_diagnostics(_error_diagnostics(
                    http_status=response.status,
                    content_type=response.headers.get('content-type'),
                    text=html,
                    body_bytes=body_bytes,
                    final_url=context.page.url,
                ))

        await crawler.run([url])

        if 'error' in outcome:
            raise outcome['error']
        if 'offer' not in outcome:
            raise RuntimeError('Страница не загружена: навигация не дошла до обработчика.')

        offer = outcome['offer']
        Actor.log.info(
            f'Parsed offer {offer.get("offer_id")}: '
            f'{len(offer.get("photo_urls", []))} photos, price {offer.get("price")}'
        )

        # Одно объявление — одна запись в датасете.
        await Actor.push_data([offer])
