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


class ProviderNetworkError(MarketDataError):
    pass


class MalformedProviderResponseError(MarketDataError):
    pass


class NoDataError(MarketDataError):
    pass
