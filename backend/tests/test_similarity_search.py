from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from shape_finder.application.market_data_service import MarketDataService
from shape_finder.application.similarity_engine import ChartSimilarityEngine
from shape_finder.application.similarity_search import (
    HistoricalSimilarityScanner,
    SimilaritySearchService,
)
from shape_finder.core.errors import MissingApiKeyError, ProviderNetworkError
from shape_finder.core.market_data import BarInterval, PriceBar, TimeSeries
from shape_finder.core.persistence import CoverageRange
from shape_finder.core.similarity_search import (
    InvalidSimilaritySearchError,
    SimilaritySearchQuery,
)
from shape_finder.infrastructure.persistence.sqlite_market_data import SQLiteMarketDataRepository
from shape_finder.main import create_app

START = datetime(2020, 1, 1, tzinfo=UTC)
REFERENCE_CLOSES = [100, 108, 116, 105, 99, 112, 121, 117, 129]


def make_series(
    symbol: str,
    closes: Sequence[int | float | Decimal],
    *,
    start: datetime = START,
    interval: BarInterval = BarInterval.ONE_DAY,
) -> TimeSeries:
    step = timedelta(days=1) if interval is BarInterval.ONE_DAY else timedelta(minutes=5)
    bars = tuple(
        PriceBar(
            timestamp=start + index * step,
            open=Decimal(str(close)),
            high=Decimal(str(close)),
            low=Decimal(str(close)),
            close=Decimal(str(close)),
            volume=Decimal("1000"),
        )
        for index, close in enumerate(closes)
    )
    return TimeSeries(symbol, interval, "UTC", bars)


def query(
    *symbols: str,
    reference_symbol: str = "NVDA",
    reference_start: datetime = START,
    reference_end: datetime = START + timedelta(days=8),
    search_start: datetime = START,
    search_end: datetime = START + timedelta(days=100),
    interval: BarInterval = BarInterval.ONE_DAY,
    top_n: int = 10,
    minimum_similarity: float | None = None,
) -> SimilaritySearchQuery:
    return SimilaritySearchQuery(
        reference_symbol=reference_symbol,
        reference_start=reference_start,
        reference_end=reference_end,
        interval=interval,
        search_start=search_start,
        search_end=search_end,
        candidate_symbols=tuple(symbols),
        top_n=top_n,
        minimum_similarity=minimum_similarity,
    )


def scanner() -> HistoricalSimilarityScanner:
    return HistoricalSimilarityScanner(ChartSimilarityEngine())


def test_obvious_final_window_ranks_first_and_statistics_are_accurate() -> None:
    reference = make_series("NVDA", REFERENCE_CLOSES)
    candidate = make_series("AMD", [70, 75, 72, *[value * 5 for value in REFERENCE_CLOSES]])

    result = scanner().scan(reference, [candidate], query("AMD", top_n=1))

    assert result.matches[0].start == candidate.bars[3].timestamp
    assert result.matches[0].end == candidate.bars[-1].timestamp
    assert result.matches[0].score.overall_score == 100
    assert result.matches[0].score == ChartSimilarityEngine().compare(
        tuple(bar.close for bar in reference.bars),
        tuple(bar.close for bar in candidate.bars[3:]),
    )
    assert result.statistics.windows_evaluated == 4
    assert result.statistics.windows_passing_threshold == 4
    assert result.statistics.matches_returned == 1


def test_no_strong_match_and_minimum_filter() -> None:
    result = scanner().scan(
        make_series("NVDA", REFERENCE_CLOSES),
        [make_series("AMD", [50] * 20)],
        query("AMD", minimum_similarity=80),
    )

    assert result.matches == ()
    assert result.statistics.windows_evaluated == 12
    assert result.statistics.windows_passing_threshold == 0


def test_top_n_overlap_suppression_and_distinct_matches() -> None:
    history = [*REFERENCE_CLOSES, 70, 65, 60, *[value * 2 for value in REFERENCE_CLOSES]]
    candidate = make_series("AMD", history)
    result = scanner().scan(
        make_series("NVDA", REFERENCE_CLOSES),
        [candidate],
        query("AMD", top_n=2, minimum_similarity=90),
    )

    assert len(result.matches) == 2
    assert result.matches[0].start == candidate.bars[0].timestamp
    assert result.matches[1].start == candidate.bars[12].timestamp


def test_global_ranking_and_tie_breaking_are_deterministic() -> None:
    reference = make_series("NVDA", REFERENCE_CLOSES)
    same_a = make_series("AAPL", REFERENCE_CLOSES)
    same_m = make_series("MSFT", REFERENCE_CLOSES)

    first = scanner().scan(reference, [same_m, same_a], query("MSFT", "AAPL"))
    second = scanner().scan(reference, [same_m, same_a], query("MSFT", "AAPL"))

    assert first == second
    assert [match.symbol for match in first.matches] == ["AAPL", "MSFT"]
    assert first.statistics.symbols_requested == first.statistics.symbols_scanned == 2


def test_search_boundaries_are_enforced() -> None:
    candidate = make_series("AMD", [*REFERENCE_CLOSES, *REFERENCE_CLOSES])
    start = candidate.bars[4].timestamp
    end = candidate.bars[15].timestamp
    result = scanner().scan(
        make_series("NVDA", REFERENCE_CLOSES),
        [candidate],
        query("AMD", search_start=start, search_end=end),
    )

    assert result.statistics.windows_evaluated == 4
    assert all(start <= match.start <= match.end <= end for match in result.matches)


def test_interval_mismatch_is_rejected() -> None:
    with pytest.raises(InvalidSimilaritySearchError, match="interval"):
        scanner().scan(
            make_series("NVDA", REFERENCE_CLOSES),
            [make_series("AMD", REFERENCE_CLOSES, interval=BarInterval.FIVE_MINUTES)],
            query("AMD"),
        )


def test_same_stock_self_match_is_excluded_but_old_match_remains() -> None:
    old = make_series("NVDA", REFERENCE_CLOSES, start=START)
    reference_start = datetime(2021, 1, 1, tzinfo=UTC)
    current = make_series("NVDA", REFERENCE_CLOSES, start=reference_start)
    candidate = TimeSeries("NVDA", BarInterval.ONE_DAY, "UTC", old.bars + current.bars)
    result = scanner().scan(
        current,
        [candidate],
        query(
            "NVDA",
            reference_symbol="NVDA",
            reference_start=reference_start,
            reference_end=reference_start + timedelta(days=8),
            search_start=START,
            search_end=reference_start + timedelta(days=8),
            minimum_similarity=99,
        ),
    )

    assert len(result.matches) == 1
    assert result.matches[0].start == START


def test_insufficient_candidate_data_returns_no_matches() -> None:
    result = scanner().scan(
        make_series("NVDA", REFERENCE_CLOSES),
        [make_series("AMD", [1, 2, 3])],
        query("AMD"),
    )
    assert result.matches == ()
    assert result.statistics.windows_evaluated == 0


@pytest.mark.parametrize(
    "bad_query",
    [
        query("AMD", top_n=0),
        query("AMD", top_n=101),
        query("AMD", minimum_similarity=-1),
        query("AMD", minimum_similarity=101),
        query(),
        query(*[f"S{index}" for index in range(11)]),
        query("AMD", search_end=START),
    ],
)
def test_invalid_search_options_are_rejected(bad_query: SimilaritySearchQuery) -> None:
    with pytest.raises(InvalidSimilaritySearchError):
        scanner().scan(make_series("NVDA", REFERENCE_CLOSES), [], bad_query)


class StubMarketDataService(MarketDataService):
    def __init__(self, series: dict[str, TimeSeries], error_symbol: str | None = None) -> None:
        self.series = series
        self.error_symbol = error_symbol
        self.calls: list[str] = []

    async def get_time_series(
        self,
        symbol: str,
        start: datetime,
        end: datetime,
        interval: BarInterval,
    ) -> TimeSeries:
        self.calls.append(symbol)
        if symbol == self.error_symbol:
            raise ProviderNetworkError()
        return self.series[symbol]


@pytest.mark.anyio
async def test_service_normalizes_deduplicates_and_fails_whole_scan() -> None:
    data = {
        "NVDA": make_series("NVDA", REFERENCE_CLOSES),
        "AMD": make_series("AMD", REFERENCE_CLOSES),
        "AAPL": make_series("AAPL", REFERENCE_CLOSES),
    }
    market_data = StubMarketDataService(data)
    service = SimilaritySearchService(market_data, scanner())
    result = await service.search(query(" amd ", "AMD", " aapl "))
    assert market_data.calls == ["NVDA", "AMD", "AAPL"]
    assert result.statistics.symbols_requested == 2

    failing = SimilaritySearchService(StubMarketDataService(data, "AAPL"), scanner())
    with pytest.raises(ProviderNetworkError):
        await failing.search(query("AMD", "AAPL"))


class StaticProvider:
    def __init__(self, data: dict[str, TimeSeries] | None = None, error: Exception | None = None):
        self.data = data or {}
        self.error = error
        self.calls = 0

    async def get_historical_bars(
        self,
        symbol: str,
        start: datetime,
        end: datetime,
        interval: BarInterval,
    ) -> TimeSeries:
        self.calls += 1
        if self.error:
            raise self.error
        series = self.data[symbol]
        return TimeSeries(
            symbol,
            interval,
            series.timezone,
            tuple(bar for bar in series.bars if start <= bar.timestamp <= end),
        )


@pytest.mark.anyio
async def test_complete_cached_scan_works_without_key(tmp_path: Path) -> None:
    repository = SQLiteMarketDataRepository(tmp_path / "cached-scan.sqlite3")
    await repository.initialize()
    reference = make_series("NVDA", REFERENCE_CLOSES)
    candidate = make_series("AMD", [*REFERENCE_CLOSES, 80, *REFERENCE_CLOSES])
    coverage = [CoverageRange(START, START + timedelta(days=100), START)] * 2
    await repository.upsert_time_series([reference, candidate], coverage, source="test")

    provider = StaticProvider(error=MissingApiKeyError())
    market_data = MarketDataService(
        provider, repository, clock=lambda: datetime(2030, 1, 1, tzinfo=UTC)
    )
    result = await SimilaritySearchService(market_data, scanner()).search(query("AMD"))

    assert result.matches[0].score.overall_score == 100
    assert provider.calls == 0


@pytest.mark.anyio
async def test_provider_required_scan_failure_does_not_persist_partial_data(tmp_path: Path) -> None:
    repository = SQLiteMarketDataRepository(tmp_path / "failed-scan.sqlite3")
    await repository.initialize()
    provider = StaticProvider(error=MissingApiKeyError())
    service = SimilaritySearchService(MarketDataService(provider, repository), scanner())

    with pytest.raises(MissingApiKeyError):
        await service.search(query("AMD"))
    stored = await repository.get_time_series(
        "NVDA", BarInterval.ONE_DAY, START, START + timedelta(days=8)
    )
    assert stored.bars == ()


def test_realistic_240_bar_embedded_pattern_ranks_first() -> None:
    reference = [100 + 8 * ((index % 10) / 10) + index * 0.2 for index in range(30)]
    history = [70 + index * 0.04 + ((index * 17) % 11) * 0.12 for index in range(240)]
    exact_start = 170
    moderate_start = 60
    inverted_start = 110
    history[exact_start : exact_start + 30] = [value * 3 for value in reference]
    history[moderate_start : moderate_start + 30] = [
        100 + (value - 100) * 0.55 for value in reference
    ]
    history[inverted_start : inverted_start + 30] = [10000 / value for value in reference]

    result = scanner().scan(
        make_series("NVDA", reference),
        [make_series("AMD", history)],
        query("AMD", search_end=START + timedelta(days=239), top_n=5),
    )

    assert result.matches[0].start == START + timedelta(days=exact_start)
    assert result.matches[0].score.overall_score == 100
    moderate_match = next(
        match for match in result.matches if match.start == START + timedelta(days=moderate_start)
    )
    assert moderate_match.score.overall_score < result.matches[0].score.overall_score
    inverted_score = (
        ChartSimilarityEngine()
        .compare(reference, history[inverted_start : inverted_start + 30])
        .overall_score
    )
    assert inverted_score < 20


def test_search_api_success_and_validation(tmp_path: Path) -> None:
    provider = StaticProvider(
        {
            "NVDA": make_series("NVDA", REFERENCE_CLOSES),
            "AMD": make_series("AMD", [*REFERENCE_CLOSES, 70, *REFERENCE_CLOSES]),
        }
    )
    repository = SQLiteMarketDataRepository(tmp_path / "search-api.sqlite3")
    body: dict[str, Any] = {
        "reference": {
            "symbol": "nvda",
            "start": "2020-01-01T00:00:00Z",
            "end": "2020-01-09T00:00:00Z",
            "interval": "1day",
        },
        "search": {
            "start": "2020-01-01T00:00:00Z",
            "end": "2020-04-10T00:00:00Z",
            "symbols": [" amd "],
        },
        "top_n": 1,
        "minimum_similarity": 90,
    }
    with TestClient(create_app(provider, repository)) as client:
        response = client.post("/api/v1/similarity/search", json=body)
        invalid_responses = [
            client.post("/api/v1/similarity/search", json={**body, "top_n": 0}),
            client.post(
                "/api/v1/similarity/search",
                json={**body, "minimum_similarity": 101},
            ),
            client.post(
                "/api/v1/similarity/search",
                json={**body, "search": {**body["search"], "symbols": []}},
            ),
            client.post(
                "/api/v1/similarity/search",
                json={
                    **body,
                    "search": {
                        **body["search"],
                        "symbols": [f"S{index}" for index in range(11)],
                    },
                },
            ),
            client.post(
                "/api/v1/similarity/search",
                json={
                    **body,
                    "reference": {**body["reference"], "symbol": "?!"},
                },
            ),
        ]

    assert response.status_code == 200
    payload = response.json()
    assert payload["reference"]["symbol"] == "NVDA"
    assert payload["matches"][0]["symbol"] == "AMD"
    assert payload["matches"][0]["overall_score"] == 100
    assert payload["statistics"]["matches_returned"] == 1
    assert all(item.status_code == 422 for item in invalid_responses)
    assert {item.json()["error"]["code"] for item in invalid_responses} <= {
        "VALIDATION_ERROR",
        "INVALID_SIMILARITY_SEARCH",
    }
