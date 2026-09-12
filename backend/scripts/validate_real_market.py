"""Run a small, cache-aware real-market similarity validation experiment."""

import argparse
import asyncio
import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx

from shape_finder.application.market_data_service import MarketDataService
from shape_finder.application.real_market_validation import (
    RealMarketValidationConfig,
    run_real_market_validation,
    validation_report_to_dict,
)
from shape_finder.application.similarity_engine import ChartSimilarityEngine
from shape_finder.application.similarity_search import HistoricalSimilarityScanner
from shape_finder.config import get_settings
from shape_finder.core.market_data import BarInterval
from shape_finder.infrastructure.market_data.twelve_data import INTERVAL_MAP, TwelveDataProvider
from shape_finder.infrastructure.persistence.sqlite_market_data import SQLiteMarketDataRepository

_SYMBOL_PATTERN = re.compile(r"^[A-Z][A-Z0-9.\-]{0,14}$")


def _aware_datetime(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise argparse.ArgumentTypeError("timestamps must include a UTC offset, such as +00:00")
    return parsed


def _symbols(value: str) -> tuple[str, ...]:
    symbols = tuple(
        dict.fromkeys(item.strip().upper() for item in value.split(",") if item.strip())
    )
    if not symbols:
        raise argparse.ArgumentTypeError("at least one candidate symbol is required")
    if len(symbols) > 10:
        raise argparse.ArgumentTypeError("live validation is limited to 10 candidate symbols")
    if any(not _SYMBOL_PATTERN.fullmatch(symbol) for symbol in symbols):
        raise argparse.ArgumentTypeError("candidate symbols use an unsupported format")
    return symbols


def _symbol(value: str) -> str:
    symbol = value.strip().upper()
    if not _SYMBOL_PATTERN.fullmatch(symbol):
        raise argparse.ArgumentTypeError("symbol uses an unsupported format")
    return symbol


def _top_n(value: str) -> int:
    count = int(value)
    if not 1 <= count <= 100:
        raise argparse.ArgumentTypeError("top-n must be between 1 and 100")
    return count


def _score(value: str) -> float:
    score = float(value)
    if not 0 <= score <= 100:
        raise argparse.ArgumentTypeError("minimum similarity must be between 0 and 100")
    return score


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run a bounded real-market validation using the normal provider/cache path."
    )
    parser.add_argument("--reference", required=True, type=_symbol)
    parser.add_argument("--reference-start", required=True, type=_aware_datetime)
    parser.add_argument("--reference-end", required=True, type=_aware_datetime)
    parser.add_argument("--interval", choices=[item.value for item in INTERVAL_MAP], required=True)
    parser.add_argument("--search-start", required=True, type=_aware_datetime)
    parser.add_argument("--search-end", required=True, type=_aware_datetime)
    parser.add_argument("--candidates", required=True, type=_symbols)
    parser.add_argument("--top-n", type=_top_n, default=10)
    parser.add_argument("--minimum-similarity", type=_score)
    parser.add_argument("--json", type=Path, help="Optional path for a compact JSON report.")
    return parser


async def _run(args: argparse.Namespace) -> dict[str, Any]:
    settings = get_settings()
    repository = SQLiteMarketDataRepository(settings.database_path)
    await repository.initialize()
    async with httpx.AsyncClient(
        base_url=settings.twelve_data_base_url,
        timeout=settings.market_data_timeout_seconds,
    ) as client:
        provider = TwelveDataProvider(
            client,
            settings.twelve_data_api_key.get_secret_value()
            if settings.twelve_data_api_key
            else None,
        )
        report = await run_real_market_validation(
            MarketDataService(provider, repository),
            HistoricalSimilarityScanner(ChartSimilarityEngine()),
            RealMarketValidationConfig(
                reference_symbol=args.reference.upper(),
                reference_start=args.reference_start,
                reference_end=args.reference_end,
                interval=BarInterval(args.interval),
                search_start=args.search_start,
                search_end=args.search_end,
                candidate_symbols=args.candidates,
                top_n=args.top_n,
                minimum_similarity=args.minimum_similarity,
            ),
        )
    return validation_report_to_dict(report)


def main() -> None:
    parser = _parser()
    args = parser.parse_args()
    if args.reference_start >= args.reference_end:
        parser.error("reference start must be earlier than reference end")
    if args.search_start >= args.search_end:
        parser.error("search start must be earlier than search end")
    payload = asyncio.run(_run(args))
    print(json.dumps(payload, indent=2))
    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
