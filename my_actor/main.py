"""Точка входа Apify Actor: загрузка страницы объявления ЦИАН и извлечение данных.

Вся логика извлечения — в `my_actor.parser.parse_offer_html`; этот модуль
только получает URL из входа, загружает HTML и сохраняет результат в датасет.
"""

from __future__ import annotations

from typing import Any

from apify import Actor, Event
from httpx2 import AsyncClient, HTTPStatusError, Response

from .parser import ParseError, parse_offer_html

# Признаки CAPTCHA или ограничения доступа; ищутся в тексте ответа без учёта регистра.
_ACCESS_MARKERS = ("captcha", "access denied", "доступ ограничен", "проверка браузера")


def _error_diagnostics(response: Response) -> dict[str, Any]:
    """Собирает безопасные метаданные ответа для диагностики ошибки разбора.

    Тело ответа не сохраняется: только статус, Content-Type, размер в символах
    и в байтах, конечный URL без query-параметров и fragment и булевы маркеры
    CAPTCHA/ограничения доступа. Cookies и остальные заголовки не читаются.
    """
    text = response.text
    content = response.content
    # fragment может содержать '?', поэтому сначала срезается он, затем query.
    final_url = str(response.url).split('#', 1)[0].split('?', 1)[0] if response.url else None
    lowered = text.lower() if isinstance(text, str) else ''
    return {
        'http_status': int(response.status_code),
        'content_type': response.headers.get('content-type'),
        'response_chars': len(text) if isinstance(text, str) else None,
        'response_bytes': len(content) if isinstance(content, bytes) else None,
        'final_url': final_url,
        'markers': {marker.replace(' ', '_'): marker in lowered for marker in _ACCESS_MARKERS},
    }


async def _save_error_diagnostics(response: Response) -> None:
    """Пишет диагностику в KV-стор; краткая сводка идёт в лог, тело ответа — никуда."""
    diagnostics = _error_diagnostics(response)
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

        proxy_url = await proxy_configuration.new_url() if proxy_configuration else None
        if proxy_url:
            groups = ', '.join(proxy_input.get('apifyProxyGroups') or ['AUTO'])
            Actor.log.info(f'Using Apify Proxy (groups: {groups})')
        else:
            Actor.log.info('Proxy is disabled, connecting directly')

        async with AsyncClient(proxy=proxy_url) as client:
            Actor.log.info(f'Sending a request to {url}')
            response = await client.get(url, follow_redirects=True)

        try:
            response.raise_for_status()
            offer = parse_offer_html(response.text, source_url=str(response.url) if response.url else url)
        except (HTTPStatusError, ParseError):
            # Сначала диагностика в KV-стор, затем ошибка продолжает распространяться:
            # Actor завершается с исходным кодом (91), фиктивный результат не пишется.
            await _save_error_diagnostics(response)
            raise
        Actor.log.info(
            f'Parsed offer {offer.get("offer_id")}: '
            f'{len(offer.get("photo_urls", []))} photos, price {offer.get("price")}'
        )

        # Одно объявление — одна запись в датасете.
        await Actor.push_data([offer])
