from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from time import perf_counter
from typing import Any, Protocol
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from shape_finder.application.similarity_search import HistoricalSimilarityScanner
from shape_finder.core.market_data import BarInterval, TimeSeries
from shape_finder.core.similarity_search import SimilaritySearchQuery, SimilaritySearchResult

_INTERVAL_STEPS = {
    BarInterval.ONE_MINUTE: timedelta(minutes=1),
    BarInterval.FIVE_MINUTES: timedelta(minutes=5),
    BarInterval.FIFTEEN_MINUTES: timedelta(minutes=15),
    BarInterval.THIRTY_MINUTES: timedelta(minutes=30),
    BarInterval.ONE_HOUR: timedelta(hours=1),
    BarInterval.ONE_DAY: timedelta(days=1),
    BarInterval.ONE_WEEK: timedelta(weeks=1),
}


class MarketDataLoader(Protocol):
    async def get_time_series(
        self,
        symbol: str,
        start: datetime,
        end: datetime,
        interval: BarInterval,
    ) -> TimeSeries: ...


@dataclass(frozen=True, slots=True)
class DataQualityIssue:
    code: str
    severity: str
    count: int
    samples: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class DataQualityReport:
    symbol: str
    interval: BarInterval
    timezone: str
    bar_count: int
    issues: tuple[DataQualityIssue, ...]

    @property
    def error_count(self) -> int:
        return sum(issue.count for issue in self.issues if issue.severity == "error")


@dataclass(frozen=True, slots=True)
class RealMarketValidationConfig:
    reference_symbol: str
    reference_start: datetime
    reference_end: datetime
    interval: BarInterval
    search_start: datetime
    search_end: datetime
    candidate_symbols: tuple[str, ...]
    top_n: int = 10
    minimum_similarity: float | None = None


@dataclass(frozen=True, slots=True)
class RealMarketValidationReport:
    config: RealMarketValidationConfig
    result: SimilaritySearchResult
    quality: tuple[DataQualityReport, ...]
    data_load_seconds: float
    scan_seconds: float
    total_seconds: float


def inspect_time_series(series: TimeSeries) -> DataQualityReport:
    """Report corrupt or suspicious input without inventing an exchange calendar."""
    problems: dict[tuple[str, str], list[str]] = {}

    def record(code: str, severity: str, sample: object) -> None:
        problems.setdefault((code, severity), []).append(str(sample))

    expected_zone: ZoneInfo | None
    try:
        expected_zone = ZoneInfo(series.timezone)
    except ZoneInfoNotFoundError:
        expected_zone = None
        record("unknown_series_timezone", "error", series.timezone)

    seen: set[datetime] = set()
    previous: datetime | None = None
    step = _INTERVAL_STEPS[series.interval]
    gap_limit = max(step * 4, timedelta(days=4))
    for bar in series.bars:
        stamp = bar.timestamp
        if stamp.tzinfo is None or stamp.utcoffset() is None:
            record("timezone_naive_timestamp", "error", stamp)
        elif expected_zone is not None:
            expected_offset = stamp.astimezone(expected_zone).utcoffset()
            if stamp.utcoffset() != expected_offset:
                record("timezone_inconsistency", "error", stamp)
        if stamp in seen:
            record("duplicate_timestamp", "error", stamp)
        seen.add(stamp)
        comparable = stamp.tzinfo is not None and stamp.utcoffset() is not None
        if previous is not None and comparable:
            if stamp < previous:
                record("out_of_order_timestamp", "error", stamp)
            elif stamp - previous > gap_limit:
                record("large_time_gap", "info", f"{previous.isoformat()} -> {stamp.isoformat()}")
        previous = stamp if comparable else None

        prices = (bar.open, bar.high, bar.low, bar.close)
        if any(not value.is_finite() or value <= Decimal(0) for value in prices):
            record("non_positive_or_non_finite_price", "error", stamp)
        elif bar.high < max(bar.open, bar.low, bar.close) or bar.low > min(
            bar.open, bar.high, bar.close
        ):
            record("malformed_ohlc", "error", stamp)
        if not bar.volume.is_finite() or bar.volume < Decimal(0):
            record("invalid_volume", "error", stamp)
        elif bar.volume == Decimal(0):
            record("zero_volume", "warning", stamp)

    issues = tuple(
        DataQualityIssue(code, severity, len(samples), tuple(samples[:3]))
        for (code, severity), samples in sorted(problems.items())
    )
    return DataQualityReport(
        symbol=series.symbol,
        interval=series.interval,
        timezone=series.timezone,
        bar_count=len(series.bars),
        issues=issues,
    )


async def run_real_market_validation(
    market_data: MarketDataLoader,
    scanner: HistoricalSimilarityScanner,
    config: RealMarketValidationConfig,
) -> RealMarketValidationReport:
    total_started = perf_counter()
    load_started = perf_counter()
    reference = await market_data.get_time_series(
        config.reference_symbol,
        config.reference_start,
        config.reference_end,
        config.interval,
    )
    candidates = [
        await market_data.get_time_series(
            symbol,
            config.search_start,
            config.search_end,
            config.interval,
        )
        for symbol in config.candidate_symbols
    ]
    data_load_seconds = perf_counter() - load_started
    quality = tuple(inspect_time_series(series) for series in (reference, *candidates))
    query = SimilaritySearchQuery(
        reference_symbol=config.reference_symbol,
        reference_start=config.reference_start,
        reference_end=config.reference_end,
        interval=config.interval,
        search_start=config.search_start,
        search_end=config.search_end,
        candidate_symbols=config.candidate_symbols,
        top_n=config.top_n,
        minimum_similarity=config.minimum_similarity,
    )
    scan_started = perf_counter()
    result = scanner.scan(reference, candidates, query)
    scan_seconds = perf_counter() - scan_started
    return RealMarketValidationReport(
        config=config,
        result=result,
        quality=quality,
        data_load_seconds=data_load_seconds,
        scan_seconds=scan_seconds,
        total_seconds=perf_counter() - total_started,
    )


def validation_report_to_dict(report: RealMarketValidationReport) -> dict[str, Any]:
    def timestamp(value: datetime) -> str:
        return value.isoformat()

    return {
        "configuration": {
            "reference_symbol": report.config.reference_symbol,
            "reference_start": timestamp(report.config.reference_start),
            "reference_end": timestamp(report.config.reference_end),
            "interval": report.config.interval.value,
            "search_start": timestamp(report.config.search_start),
            "search_end": timestamp(report.config.search_end),
            "candidate_symbols": list(report.config.candidate_symbols),
            "top_n": report.config.top_n,
            "minimum_similarity": report.config.minimum_similarity,
        },
        "reference": {
            "symbol": report.result.reference.symbol,
            "start": timestamp(report.result.reference.start),
            "end": timestamp(report.result.reference.end),
            "interval": report.result.reference.interval.value,
            "bar_count": report.result.reference.bar_count,
        },
        "matches": [
            {
                "symbol": match.symbol,
                "start": timestamp(match.start),
                "end": timestamp(match.end),
                "interval": match.interval.value,
                "bar_count": match.bar_count,
                "scores": asdict(match.score),
            }
            for match in report.result.matches
        ],
        "statistics": asdict(report.result.statistics),
        "quality": [
            {
                "symbol": item.symbol,
                "interval": item.interval.value,
                "timezone": item.timezone,
                "bar_count": item.bar_count,
                "error_count": item.error_count,
                "issues": [asdict(issue) for issue in item.issues],
            }
            for item in report.quality
        ],
        "timing_seconds": {
            "data_load": round(report.data_load_seconds, 6),
            "scan": round(report.scan_seconds, 6),
            "total": round(report.total_seconds, 6),
        },
    }
