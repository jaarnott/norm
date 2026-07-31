from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass
class ConnectorResult:
    success: bool
    reference: str | None
    response_payload: dict
    error_message: str | None = None
    # True when the failure is an authorization problem the user must fix by
    # reconnecting the connector (a dead/rejected OAuth token), as opposed to a
    # transient or logical error. Lets the agent loop surface a reconnect card
    # instead of a dead-end error string.
    auth_failed: bool = False


class BaseConnector(ABC):
    name: str

    @abstractmethod
    def submit(self, payload: dict) -> ConnectorResult: ...
