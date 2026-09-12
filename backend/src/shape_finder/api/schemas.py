from datetime import datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from shape_finder.application.similarity_search import MAX_CANDIDATE_SYMBOLS, MAX_TOP_N
from shape_finder.core.market_data import BarInterval
from shape_finder.core.universe import UniverseKind


class HealthResponse(BaseModel):
    status: Literal["ok"]
    service: str
    version: str


class PriceBarResponse(BaseModel):
    timestamp: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal


class TimeSeriesResponse(BaseModel):
    symbol: str
    interval: str
    timezone: str
    bars: list[PriceBarResponse]


class ReferenceSearchRequest(BaseModel):
    symbol: str
    start: datetime
    end: datetime
    interval: BarInterval

    @field_validator("symbol")
    @classmethod
    def normalize_symbol(cls, value: str) -> str:
        return value.strip().upper()

    @model_validator(mode="after")
    def validate_range(self) -> "ReferenceSearchRequest":
        if self.start.tzinfo is None or self.end.tzinfo is None or self.start >= self.end:
            raise ValueError("Reference timestamps must be timezone-aware and ordered.")
        return self


class SearchScopeRequest(BaseModel):
    start: datetime
    end: datetime
    symbols: list[str] | None = Field(default=None, max_length=MAX_CANDIDATE_SYMBOLS)
    universe: "UniverseSelectionRequest | None" = None

    @field_validator("symbols")
    @classmethod
    def normalize_symbols(cls, values: list[str] | None) -> list[str] | None:
        if values is None:
            return None
        return list(dict.fromkeys(value.strip().upper() for value in values))

    @model_validator(mode="after")
    def validate_range(self) -> "SearchScopeRequest":
        if self.start.tzinfo is None or self.end.tzinfo is None or self.start >= self.end:
            raise ValueError("Search timestamps must be timezone-aware and ordered.")
        if self.symbols and self.universe:
            raise ValueError("Choose either symbols or a universe, not both.")
        if not self.symbols and self.universe is None:
            raise ValueError("Candidate symbols or a universe are required.")
        return self


class UniverseSelectionRequest(BaseModel):
    kind: UniverseKind
    symbols: list[str] = Field(default_factory=list, max_length=MAX_CANDIDATE_SYMBOLS)

    @field_validator("symbols")
    @classmethod
    def normalize_symbols(cls, values: list[str]) -> list[str]:
        return list(dict.fromkeys(value.strip().upper() for value in values))

    @model_validator(mode="after")
    def validate_custom(self) -> "UniverseSelectionRequest":
        if self.kind is UniverseKind.CUSTOM and not self.symbols:
            raise ValueError("A custom universe requires symbols.")
        if self.kind is not UniverseKind.CUSTOM and self.symbols:
            raise ValueError("Named universes do not accept explicit symbols.")
        return self


class SimilaritySearchRequest(BaseModel):
    reference: ReferenceSearchRequest
    search: SearchScopeRequest
    top_n: int = Field(default=10, ge=1, le=MAX_TOP_N)
    minimum_similarity: float | None = Field(default=None, ge=0, le=100)


class SimilarityComponentsResponse(BaseModel):
    shape: float
    direction: float
    error: float
    amplitude: float


class SimilarityMatchResponse(BaseModel):
    symbol: str
    start: datetime
    end: datetime
    interval: str
    bar_count: int
    overall_score: float
    components: SimilarityComponentsResponse


class ReferenceSummaryResponse(BaseModel):
    symbol: str
    start: datetime
    end: datetime
    interval: str
    bar_count: int


class ScanStatisticsResponse(BaseModel):
    symbols_requested: int
    symbols_scanned: int
    windows_evaluated: int
    windows_passing_threshold: int
    matches_returned: int
    universe_id: str
    universe_symbols_total: int
    symbols_eligible: int
    symbols_skipped: int
    symbols_failed: int
    universe_stale: bool


class SimilaritySearchResponse(BaseModel):
    reference: ReferenceSummaryResponse
    search_start: datetime
    search_end: datetime
    matches: list[SimilarityMatchResponse]
    statistics: ScanStatisticsResponse


class UniverseResponse(BaseModel):
    id: str
    name: str
    total_symbols: int
    refreshed_at: datetime | None
    stale: bool


class UniverseListResponse(BaseModel):
    universes: list[UniverseResponse]


class ErrorDetail(BaseModel):
    code: str
    message: str


class ErrorResponse(BaseModel):
    error: ErrorDetail
