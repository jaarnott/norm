from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass
class ConnectorResult:
    success: bool
    reference: str | None
    response_payload: dict
    error_message: str | None = None


class BaseConnector(ABC):
    name: str

    @abstractmethod
    def submit(self, payload: dict) -> ConnectorResult: ...
