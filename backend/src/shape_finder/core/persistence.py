from dataclasses import dataclass
from datetime import datetime
from typing import Protocol


@dataclass(frozen=True, slots=True)
class SimilarityResult:
    reference_symbol: str
    candidate_symbol: str
    candidate_start: datetime
    candidate_end: datetime
    score: float


class SimilarityResultRepository(Protocol):
    """Storage boundary, independent of SQLite or a future PostgreSQL adapter."""

    async def save(self, result: SimilarityResult) -> None: ...
