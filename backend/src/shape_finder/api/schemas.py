from datetime import datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from shape_finder.application.similarity_search import MAX_CANDIDATE_SYMBOLS, MAX_TOP_N
from shape_finder.core.market_data import BarInterval


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
    symbols: list[str] = Field(min_length=1, max_length=MAX_CANDIDATE_SYMBOLS)

    @field_validator("symbols")
    @classmethod
    def normalize_symbols(cls, values: list[str]) -> list[str]:
        return list(dict.fromkeys(value.strip().upper() for value in values))

    @model_validator(mode="after")
    def validate_range(self) -> "SearchScopeRequest":
        if self.start.tzinfo is None or self.end.tzinfo is None or self.start >= self.end:
            raise ValueError("Search timestamps must be timezone-aware and ordered.")
        if not self.symbols:
            raise ValueError("At least one candidate symbol is required.")
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


class SimilaritySearchResponse(BaseModel):
    reference: ReferenceSummaryResponse
    search_start: datetime
    search_end: datetime
    matches: list[SimilarityMatchResponse]
    statistics: ScanStatisticsResponse


class ErrorDetail(BaseModel):
    code: str
    message: str


class ErrorResponse(BaseModel):
    error: ErrorDetail
