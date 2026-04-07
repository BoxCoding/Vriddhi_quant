"""
Abstract base class for all trading agents.

Every agent in the system inherits from BaseAgent and gains:
  - Lifecycle management (start / stop / health check)
  - Event bus integration (publish / subscribe)
  - Structured logging
  - Heartbeat publishing
  - Graceful shutdown via asyncio.Event
"""
from __future__ import annotations

import asyncio
import logging
import uuid
from abc import ABC, abstractmethod
from datetime import datetime
from typing import List, Optional

from core.config import settings
from core.enums import AgentStatus, EventType
from core.event_bus import EventBus, get_event_bus_sync
from core.models import AgentHeartbeat, Event

logger = logging.getLogger(__name__)


class BaseAgent(ABC):
    """Abstract base for every agent in the NSE Options Trading system."""

    name: str = "base_agent"     # Override in subclass
    version: str = "1.0.0"

    def __init__(self) -> None:
        self.id: str = str(uuid.uuid4())
        self.status: AgentStatus = AgentStatus.STOPPED
        self._stop_event: asyncio.Event = asyncio.Event()
        self._event_bus: EventBus = get_event_bus_sync()
        self._tasks: List[asyncio.Task] = []
        self.logger = logging.getLogger(f"agent.{self.name}")

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    async def start(self) -> None:
        """Start the agent. Sets up event bus and launches background tasks."""
        self.logger.info("Starting agent %s (id=%s)", self.name, self.id)
        self.status = AgentStatus.STARTING
        self._stop_event.clear()

        # Ensure event bus is connected
        await self._event_bus.connect()

        # Run agent-specific initialisation
        await self.on_start()

        self.status = AgentStatus.RUNNING

        # Publish started event
        await self._publish_system_event(EventType.AGENT_STARTED, {"agent": self.name})

        # Launch heartbeat background task
        heartbeat_task = asyncio.create_task(self._heartbeat_loop(), name=f"{self.name}:heartbeat")
        self._tasks.append(heartbeat_task)

        # Launch agent's main work loop
        main_task = asyncio.create_task(self.run(), name=f"{self.name}:main")
        self._tasks.append(main_task)

        self.logger.info("Agent %s is RUNNING", self.name)

    async def stop(self) -> None:
        """Gracefully stop the agent."""
        self.logger.info("Stopping agent %s", self.name)
        self.status = AgentStatus.STOPPED
        self._stop_event.set()

        # Cancel all background tasks
        for task in self._tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks.clear()

        await self.on_stop()
        await self._publish_system_event(EventType.AGENT_STOPPED, {"agent": self.name})
        self.logger.info("Agent %s stopped", self.name)

    @property
    def is_running(self) -> bool:
        return self.status == AgentStatus.RUNNING and not self._stop_event.is_set()

    # ── Abstract interface ────────────────────────────────────────────────────

    @abstractmethod
    async def run(self) -> None:
        """
        Main agent loop. Should run until self._stop_event is set.
        Example pattern:
            while not self._stop_event.is_set():
                await self.do_work()
                await asyncio.sleep(interval)
        """
        ...

    async def on_start(self) -> None:
        """Hook called during start(). Override for agent-specific setup."""
        pass

    async def on_stop(self) -> None:
        """Hook called during stop(). Override for agent-specific teardown."""
        pass

    # ── Event bus helpers ─────────────────────────────────────────────────────

    async def publish(self, event: Event) -> None:
        """Publish an event to the event bus."""
        try:
            await self._event_bus.publish(event)
        except Exception as exc:
            self.logger.error("Failed to publish event %s: %s", event.type, exc)

    async def _publish_system_event(self, event_type: EventType, payload: dict) -> None:
        event = Event(
            type=event_type,
            source_agent=self.name,
            payload=payload,
        )
        await self.publish(event)

    # ── Heartbeat ─────────────────────────────────────────────────────────────

    async def _heartbeat_loop(self) -> None:
        """Publish a heartbeat on a fixed interval."""
        interval = settings.app.agent_heartbeat_interval
        while not self._stop_event.is_set():
            try:
                await self._event_bus.publish_heartbeat(
                    agent_id=self.id,
                    agent_name=self.name,
                    status=self.status.value,
                )
            except Exception as exc:
                self.logger.warning("Heartbeat failed: %s", exc)
            await asyncio.sleep(interval)

    # ── Utilities ─────────────────────────────────────────────────────────────

    async def sleep(self, seconds: float) -> None:
        """
        Interruptible sleep — respects the stop event so agents shut down quickly.
        """
        try:
            await asyncio.wait_for(self._stop_event.wait(), timeout=seconds)
        except asyncio.TimeoutError:
            pass

    def build_event(self, event_type: EventType, payload: dict, correlation_id: Optional[str] = None) -> Event:
        """Convenience method to build a typed Event."""
        return Event(
            type=event_type,
            source_agent=self.name,
            payload=payload,
            correlation_id=correlation_id,
        )
