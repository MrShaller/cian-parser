"""CLI для разбора локально сохранённого HTML страницы объявления ЦИАН.

Сетевых запросов не выполняет: читает файл и печатает JSON результата.

Пример:
    python -m my_actor.parse_local test_data/cian_308497101.html
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .parser import parse_offer_html


def main(argv: list[str] | None = None) -> int:
    argument_parser = argparse.ArgumentParser(
        description="Извлечь структурированные данные из сохранённого HTML объявления ЦИАН."
    )
    argument_parser.add_argument("html_path", help="путь к сохранённому HTML страницы объявления")
    argument_parser.add_argument(
        "--url",
        default=None,
        help="исходный URL (иначе берётся из комментария 'saved from url')",
    )
    options = argument_parser.parse_args(argv)

    html = Path(options.html_path).read_text(encoding="utf-8", errors="replace")
    try:
        result = parse_offer_html(html, source_url=options.url)
    except ValueError as error:
        argument_parser.error(f"не удалось разобрать HTML: {error}")

    json.dump(result, sys.stdout, ensure_ascii=False, indent=2)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
