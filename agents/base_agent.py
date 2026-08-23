"""Abstract BaseAgent — shared interface for all EcoGuardAI agents."""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Dict

from loguru import logger


class BaseAgent(ABC):
    """Abstract base class for all EcoGuardAI agents.

    Each agent has:
    - A unique ``name`` and human-readable ``description``.
    - An async ``run`` method for batch/scheduled processing.
    - An async ``on_event`` method invoked by the orchestrator event bus.
    - A ``log`` helper that prefixes messages with the agent name.
    """

    name: str = "BaseAgent"
    description: str = "Abstract base agent"

    def __init__(self, orchestrator: Any) -> None:
        """
        Args:
            orchestrator: The AgentOrchestrator instance used to emit events.
        """
        self.orchestrator = orchestrator
        self.log(f"Initialised agent: {self.name}")

    @abstractmethod
    async def run(self, context: Dict[str, Any]) -> Dict[str, Any]:
        """Execute the agent's primary processing logic.

        Args:
            context: Arbitrary input dictionary relevant to the agent's task.

        Returns:
            Result dictionary with at minimum a ``status`` key.
        """

    @abstractmethod
    async def on_event(self, event: Dict[str, Any]) -> None:
        """Handle an event dispatched by the orchestrator.

        Args:
            event: The event payload dict (structure depends on event type).
        """

    def log(self, message: str, level: str = "info") -> None:
        """Log a message prefixed with the agent name.

        Args:
            message: Log message text.
            level: Loguru level name (debug, info, warning, error, critical).
        """
        log_fn = getattr(logger, level.lower(), logger.info)
        log_fn("[{}] {}", self.name, message)

    async def shutdown(self) -> None:
        """Optional cleanup called by the orchestrator on graceful stop."""
        self.log("Shutting down.", level="info")
