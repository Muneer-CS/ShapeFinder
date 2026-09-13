import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

import httpx
import pytest

from shape_finder.core.errors import (
    AuthenticationError,
    DailyQuotaError,
    InvalidSymbolError,
    MalformedProviderResponseError,
    MissingApiKeyError,
    NoDataError,
    ProviderNetworkError,
    ProviderRejectedError,
    RateLimitError,
)
from shape_finder.core.market_data import BarInterval
from shape_finder.infrastructure.market_data.twelve_data import TwelveDataProvider

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def success_payload() -> dict[str, Any]:
    return cast(
        dict[str, Any],
        json.loads((FIXTURES / "twelve_data_success.json").read_text(encoding="utf-8")),
    )


def provider_for(
    handler: httpx.AsyncBaseTransport,
    api_key: str | None = "test-key",
    *,
    attempts: int = 1,
) -> TwelveDataProvider:
    client = httpx.AsyncClient(base_url="https://api.twelvedata.test", transport=handler)
    return TwelveDataProvider(client, api_key, retry_attempts=attempts, retry_backoff_seconds=0)


@pytest.mark.anyio
async def test_parses_successful_ohlcv_and_orders_bars() -> None:
    transport = httpx.MockTransport(lambda _: httpx.Response(200, json=success_payload()))
    provider = provider_for(transport)

    series = await provider.get_historical_bars(
        "aapl",
        datetime(2025, 1, 1, tzinfo=UTC),
        datetime(2025, 1, 4, tzinfo=UTC),
        BarInterval.ONE_DAY,
    )

    assert series.symbol == "AAPL"
    assert series.timezone == "America/New_York"
    assert [bar.timestamp.day for bar in series.bars] == [2, 3]
    assert all(bar.timestamp.tzinfo is not None for bar in series.bars)
    assert str(series.bars[0].close) == "243.85"


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("interval", "expected"),
    [
        (BarInterval.ONE_MINUTE, "1min"),
        (BarInterval.FIVE_MINUTES, "5min"),
        (BarInterval.FIFTEEN_MINUTES, "15min"),
        (BarInterval.THIRTY_MINUTES, "30min"),
        (BarInterval.ONE_HOUR, "1h"),
        (BarInterval.ONE_DAY, "1day"),
    ],
)
async def test_translates_intervals_and_constructs_query(
    interval: BarInterval, expected: str
) -> None:
    captured: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured.update(dict(request.url.params))
        payload = success_payload()
        if interval is not BarInterval.ONE_DAY:
            payload["values"][0]["datetime"] = "2025-01-03 15:30:00"
            payload["values"] = payload["values"][:1]
        return httpx.Response(200, json=payload)

    provider = provider_for(httpx.MockTransport(handler))
    await provider.get_historical_bars(
        "aapl",
        datetime(2025, 1, 1, 10, tzinfo=UTC),
        datetime(2025, 1, 4, 10, tzinfo=UTC),
        interval,
    )

    assert captured["symbol"] == "AAPL"
    assert captured["interval"] == expected
    assert captured["timezone"] == "UTC"
    assert captured["order"] == "ASC"
    assert captured["apikey"] == "test-key"
    assert captured["start_date"] == (
        "2025-01-01" if interval is BarInterval.ONE_DAY else "2025-01-01T10:00:00"
    )


@pytest.mark.anyio
async def test_intraday_timestamp_is_explicitly_utc() -> None:
    payload = success_payload()
    payload["values"] = [{**payload["values"][0], "datetime": "2025-01-03 15:30:00"}]
    provider = provider_for(httpx.MockTransport(lambda _: httpx.Response(200, json=payload)))

    series = await provider.get_historical_bars(
        "AAPL",
        datetime(2025, 1, 3, tzinfo=UTC),
        datetime(2025, 1, 4, tzinfo=UTC),
        BarInterval.ONE_MINUTE,
    )

    assert series.timezone == "UTC"
    assert series.bars[0].timestamp.utcoffset() is not None
    assert series.bars[0].timestamp.utcoffset() == UTC.utcoffset(None)


@pytest.mark.anyio
async def test_missing_api_key_fails_before_http_request() -> None:
    called = False

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal called
        called = True
        return httpx.Response(500)

    provider = provider_for(httpx.MockTransport(handler), api_key=None)
    with pytest.raises(MissingApiKeyError):
        await provider.get_historical_bars(
            "AAPL",
            datetime(2025, 1, 1, tzinfo=UTC),
            datetime(2025, 1, 2, tzinfo=UTC),
            BarInterval.ONE_DAY,
        )
    assert called is False


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("payload", "error_type"),
    [
        (
            {"status": "error", "code": 400, "message": "Invalid symbol provided"},
            InvalidSymbolError,
        ),
        ({"status": "error", "code": 429, "message": "API credits exhausted"}, RateLimitError),
        ({"status": "error", "code": 401, "message": "Invalid API key"}, AuthenticationError),
    ],
)
async def test_maps_provider_errors(payload: dict[str, Any], error_type: type[Exception]) -> None:
    provider = provider_for(
        httpx.MockTransport(lambda _: httpx.Response(int(payload["code"]), json=payload))
    )
    with pytest.raises(error_type):
        await provider.get_historical_bars(
            "BAD",
            datetime(2025, 1, 1, tzinfo=UTC),
            datetime(2025, 1, 2, tzinfo=UTC),
            BarInterval.ONE_DAY,
        )


@pytest.mark.anyio
async def test_classifies_provider_style_date_range_no_data() -> None:
    payload = {
        "status": "error",
        "code": 400,
        "message": "No data is available on the specified dates.",
    }
    provider = provider_for(httpx.MockTransport(lambda _: httpx.Response(400, json=payload)))
    with pytest.raises(NoDataError) as captured:
        await provider.get_historical_bars(
            "NEWIPO",
            datetime(2024, 1, 2, tzinfo=UTC),
            datetime(2024, 3, 29, tzinfo=UTC),
            BarInterval.ONE_DAY,
        )
    assert captured.value.provider_code == 400  # type: ignore[attr-defined]


@pytest.mark.anyio
async def test_classifies_hard_provider_rejection_without_retry() -> None:
    calls = 0

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(
            400,
            json={"status": "error", "code": 400, "message": "Parameter not permitted"},
        )

    provider = provider_for(httpx.MockTransport(handler), attempts=3)
    with pytest.raises(ProviderRejectedError):
        await provider.get_historical_bars(
            "AAPL",
            datetime(2024, 1, 2, tzinfo=UTC),
            datetime(2024, 3, 29, tzinfo=UTC),
            BarInterval.ONE_DAY,
        )
    assert calls == 1


@pytest.mark.anyio
async def test_distinguishes_daily_quota_from_minute_rate_limit() -> None:
    provider = provider_for(
        httpx.MockTransport(
            lambda _: httpx.Response(
                429,
                json={
                    "status": "error",
                    "code": 429,
                    "message": "The daily API credits limit has been reached.",
                },
            )
        )
    )
    with pytest.raises(DailyQuotaError):
        await provider.get_historical_bars(
            "AAPL",
            datetime(2024, 1, 2, tzinfo=UTC),
            datetime(2024, 3, 29, tzinfo=UTC),
            BarInterval.ONE_DAY,
        )


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("status", "payload", "error_type"),
    [
        (401, {"status": "error", "code": 401, "message": "Invalid API key"}, AuthenticationError),
        (429, {"status": "error", "code": 429, "message": "Credits exhausted"}, RateLimitError),
    ],
)
async def test_does_not_retry_authentication_or_rate_limit_failures(
    status: int, payload: dict[str, Any], error_type: type[Exception]
) -> None:
    calls = 0

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(status, json=payload)

    provider = provider_for(httpx.MockTransport(handler), attempts=3)
    with pytest.raises(error_type):
        await provider.get_historical_bars(
            "AAPL",
            datetime(2025, 1, 1, tzinfo=UTC),
            datetime(2025, 1, 4, tzinfo=UTC),
            BarInterval.ONE_DAY,
        )
    assert calls == 1


@pytest.mark.anyio
async def test_provider_5xx_retry_is_bounded() -> None:
    calls = 0

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(503, json={"status": "error", "code": 503})

    provider = provider_for(httpx.MockTransport(handler), attempts=2)
    with pytest.raises(ProviderNetworkError):
        await provider.get_historical_bars(
            "AAPL",
            datetime(2025, 1, 1, tzinfo=UTC),
            datetime(2025, 1, 4, tzinfo=UTC),
            BarInterval.ONE_DAY,
        )
    assert calls == 2


@pytest.mark.anyio
async def test_provider_5xx_with_non_json_body_is_still_unavailable() -> None:
    provider = provider_for(
        httpx.MockTransport(lambda _: httpx.Response(503, text="upstream maintenance")),
        attempts=1,
    )
    with pytest.raises(ProviderNetworkError, match="failed"):
        await provider.get_historical_bars(
            "AAPL",
            datetime(2025, 1, 1, tzinfo=UTC),
            datetime(2025, 1, 4, tzinfo=UTC),
            BarInterval.ONE_DAY,
        )


@pytest.mark.anyio
async def test_retries_transient_network_failure_once() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise httpx.ConnectError("temporary", request=request)
        return httpx.Response(200, json=success_payload())

    provider = provider_for(httpx.MockTransport(handler), attempts=2)
    await provider.get_historical_bars(
        "AAPL",
        datetime(2025, 1, 1, tzinfo=UTC),
        datetime(2025, 1, 4, tzinfo=UTC),
        BarInterval.ONE_DAY,
    )
    assert calls == 2


@pytest.mark.anyio
async def test_network_failure_after_retry_is_safe() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("secret transport details", request=request)

    provider = provider_for(httpx.MockTransport(handler), attempts=2)
    with pytest.raises(ProviderNetworkError, match="unreachable"):
        await provider.get_historical_bars(
            "AAPL",
            datetime(2025, 1, 1, tzinfo=UTC),
            datetime(2025, 1, 4, tzinfo=UTC),
            BarInterval.ONE_DAY,
        )


@pytest.mark.anyio
async def test_provider_timeout_is_bounded_and_safe() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        raise httpx.ReadTimeout("request URL contains apikey=secret", request=request)

    provider = provider_for(httpx.MockTransport(handler), attempts=2)
    with pytest.raises(ProviderNetworkError, match="unreachable") as captured:
        await provider.get_historical_bars(
            "AAPL",
            datetime(2025, 1, 1, tzinfo=UTC),
            datetime(2025, 1, 4, tzinfo=UTC),
            BarInterval.ONE_DAY,
        )
    assert calls == 2
    assert "secret" not in str(captured.value)


@pytest.mark.anyio
@pytest.mark.parametrize(
    "payload",
    [
        {"meta": {"symbol": "AAPL"}},
        {"meta": {"symbol": "AAPL", "exchange_timezone": "Mars/Olympus"}, "values": [{}]},
        {"meta": {"symbol": "AAPL", "exchange_timezone": "UTC"}, "values": [{"datetime": "bad"}]},
    ],
)
async def test_rejects_malformed_payload(payload: dict[str, Any]) -> None:
    provider = provider_for(httpx.MockTransport(lambda _: httpx.Response(200, json=payload)))
    with pytest.raises(MalformedProviderResponseError):
        await provider.get_historical_bars(
            "AAPL",
            datetime(2025, 1, 1, tzinfo=UTC),
            datetime(2025, 1, 4, tzinfo=UTC),
            BarInterval.ONE_DAY,
        )


@pytest.mark.anyio
async def test_rejects_non_json_payload() -> None:
    provider = provider_for(httpx.MockTransport(lambda _: httpx.Response(200, text="not-json")))
    with pytest.raises(MalformedProviderResponseError):
        await provider.get_historical_bars(
            "AAPL",
            datetime(2025, 1, 1, tzinfo=UTC),
            datetime(2025, 1, 4, tzinfo=UTC),
            BarInterval.ONE_DAY,
        )


@pytest.mark.anyio
async def test_empty_values_raise_no_data() -> None:
    payload = success_payload()
    payload["values"] = []
    provider = provider_for(httpx.MockTransport(lambda _: httpx.Response(200, json=payload)))
    with pytest.raises(NoDataError):
        await provider.get_historical_bars(
            "AAPL",
            datetime(2025, 1, 1, tzinfo=UTC),
            datetime(2025, 1, 4, tzinfo=UTC),
            BarInterval.ONE_DAY,
        )
