"""Explicit read health for disclosure collectors; no credentials or global state."""
from dataclasses import asdict, dataclass, field
from datetime import date, datetime
from zoneinfo import ZoneInfo


def beijing_date(now: datetime | None = None) -> date:
    return (now or datetime.now(ZoneInfo("Asia/Shanghai"))).astimezone(ZoneInfo("Asia/Shanghai")).date()


class PaginationError(RuntimeError):
    pass


class SourceFetchError(RuntimeError):
    pass


@dataclass
class SourceHealth:
    source: str
    ticker: str
    requests: list[dict] = field(default_factory=list)
    errors: list[dict] = field(default_factory=list)
    limitations: list[str] = field(default_factory=list)
    item_count: int = 0
    successful_requests: int = 0
    finished: bool = False

    @property
    def status(self) -> str:
        if self.errors:
            return "partial" if self.item_count or self.successful_requests else "error"
        if self.limitations:
            return "limited"
        return "complete" if self.finished else "not_started"

    def fail(self, operation: str, error: Exception) -> None:
        # Exception bodies may contain URLs, request data or credentials.
        self.errors.append({"operation": operation, "error_type": type(error).__name__})

    def finish(self, count: int, strict: bool = False) -> None:
        self.item_count = count
        self.finished = True
        if strict and self.status != "complete":
            raise SourceFetchError(f"{self.source} [{self.ticker}]: {self.status}; errors={len(self.errors)}")

    def to_dict(self) -> dict:
        return {**asdict(self), "status": self.status}
