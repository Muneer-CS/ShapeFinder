class MarketDataError(Exception):
    """Base exception for provider-neutral market-data failures."""


class MissingApiKeyError(MarketDataError):
    pass


class AuthenticationError(MarketDataError):
    pass


class InvalidSymbolError(MarketDataError):
    pass


class InvalidDateRangeError(MarketDataError):
    pass


class UnsupportedIntervalError(MarketDataError):
    pass


class RateLimitError(MarketDataError):
    pass


class DailyQuotaError(RateLimitError):
    """Provider daily credit allowance is exhausted."""


class ProviderRejectedError(MarketDataError):
    """Provider rejected an otherwise well-formed request."""

    def __init__(self, message: str, *, provider_code: int | str | None = None) -> None:
        super().__init__(message)
        self.provider_code = provider_code


class ProviderNetworkError(MarketDataError):
    pass


class MalformedProviderResponseError(MarketDataError):
    pass


class NoDataError(MarketDataError):
    pass


class CacheError(MarketDataError):
    """Persistent market-data storage failed."""


class CachedChartHistoryError(Exception):
    """A cached reference chart could not be loaded."""

    def __init__(self, code: str, message: str, *, status: int = 422) -> None:
        super().__init__(message)
        self.code = code
        self.status = status


class ScanTimeoutError(Exception):
    """Raised when a similarity request exceeds its configured execution budget."""


class ScanCapacityError(Exception):
    """Raised when the process-local expensive-search limit is full."""


class ReadinessError(Exception):
    """Raised when a required local application component is unavailable."""
