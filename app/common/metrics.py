from typing import Protocol


class Metrics(Protocol):
    def increment(self, name: str, value: int = 1) -> None: ...
    def observe(self, name: str, value: float) -> None: ...


class NoopMetrics:
    def increment(self, name: str, value: int = 1) -> None:
        return None

    def observe(self, name: str, value: float) -> None:
        return None
