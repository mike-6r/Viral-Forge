from typing import Protocol


class MediaProcessor(Protocol):
    def process(self, asset_id: str) -> None: ...


class ObjectStorage(Protocol):
    def put(self, key: str, content: bytes) -> str: ...


class Publisher(Protocol):
    def publish(self, content_id: str) -> str: ...


class AnalysisProvider(Protocol):
    def assess(self, content_id: str) -> dict[str, object]: ...


class UnimplementedIntegration:
    def __init__(self, name: str) -> None:
        self.name = name

    def unavailable(self) -> None:
        raise NotImplementedError(
            f"{self.name} integration is intentionally not implemented in Milestone 1"
        )
