"""
Redis Streams–based event bus for inter-agent communication.

Each agent publishes typed Event objects to a shared stream.
Agents subscribe to specific event types via consumer groups so each
event is processed exactly once per group (fan-out with consumer groups).
"""
from __future__ import annotations

import asyncio
import json
import logging
from typing import AsyncIterator, Callable, Dict, List, Optional

import redis.asyncio as aioredis

from core.config import settings
from core.exceptions import EventBusError
from core.models import Event

logger = logging.getLogger(__name__)

# The single Redis stream name for all events
STREAM_KEY = "nse:events"
# Separate stream for heartbeats (high-frequency, keep small)
HEARTBEAT_STREAM_KEY = "nse:heartbeats"


class EventBus:
    """
    Async Redis Streams event bus.

    Usage (publisher):
        bus = EventBus()
        await bus.connect()
        await bus.publish(event)

    Usage (subscriber):
        bus = EventBus()
        await bus.connect()
        async for event in bus.subscribe("my-agent", ["SIGNAL_GENERATED", "RISK_APPROVED"]):
            await handle(event)
    """

    def __init__(self) -> None:
        self._client: Optional[aioredis.Redis] = None
        self._lock = asyncio.Lock()

    async def connect(self) -> None:
        """Establish connection to Redis."""
        cfg = settings.redis
        self._client = await aioredis.from_url(
            f"redis://:{cfg.password}@{cfg.host}:{cfg.port}/{cfg.db}"
            if cfg.password
            else f"redis://{cfg.host}:{cfg.port}/{cfg.db}",
            encoding="utf-8",
            decode_responses=True,
        )
        logger.info("EventBus connected to Redis at %s:%d", cfg.host, cfg.port)

    async def disconnect(self) -> None:
        """Close the Redis connection."""
        if self._client:
            await self._client.aclose()
            self._client = None

    # ── Publishing ────────────────────────────────────────────────────────────

    async def publish(self, event: Event, stream: str = STREAM_KEY) -> str:
        """
        Publish an event to the stream.
        Returns the Redis stream message ID.
        """
        if not self._client:
            raise EventBusError("EventBus is not connected. Call connect() first.")

        try:
            payload = event.model_dump_json()
            msg_id = await self._client.xadd(
                stream,
                {"data": payload},
                maxlen=settings.redis.stream_max_len,
                approximate=True,
            )
            logger.debug("Published event %s [%s] → %s", event.type, event.id, msg_id)
            return msg_id
        except Exception as exc:
            raise EventBusError(f"Failed to publish event: {exc}") from exc

    async def publish_heartbeat(self, agent_id: str, agent_name: str, status: str) -> None:
        """Publish a lightweight heartbeat (separate stream)."""
        if not self._client:
            return
        try:
            await self._client.xadd(
                HEARTBEAT_STREAM_KEY,
                {"agent_id": agent_id, "agent_name": agent_name, "status": status},
                maxlen=1000,
                approximate=True,
            )
        except Exception as exc:
            logger.warning("Heartbeat publish failed: %s", exc)

    # ── Subscribing ───────────────────────────────────────────────────────────

    async def _ensure_group(self, stream: str, group: str) -> None:
        """Create consumer group if it does not already exist."""
        try:
            await self._client.xgroup_create(stream, group, id="$", mkstream=True)
        except aioredis.ResponseError as exc:
            if "BUSYGROUP" not in str(exc):
                raise

    async def subscribe(
        self,
        agent_name: str,
        event_types: Optional[List[str]] = None,
        stream: str = STREAM_KEY,
        block_ms: int = 2000,
    ) -> AsyncIterator[Event]:
        """
        Async generator that yields Event objects as they arrive.

        Args:
            agent_name:  Unique consumer group name (typically the agent's class name).
            event_types: Optional whitelist of EventType values (e.g. ["SIGNAL_GENERATED"]).
                         If None, all events are yielded.
            stream:      Redis stream key.
            block_ms:    How long to block waiting for new messages (milliseconds).
        """
        if not self._client:
            raise EventBusError("EventBus not connected.")

        group = agent_name
        consumer = f"{agent_name}:consumer"
        await self._ensure_group(stream, group)

        logger.info("Agent %s subscribed to stream %s", agent_name, stream)

        while True:
            try:
                results = await self._client.xreadgroup(
                    groupname=group,
                    consumername=consumer,
                    streams={stream: ">"},
                    count=50,
                    block=block_ms,
                )

                if not results:
                    continue

                for _stream_name, messages in results:
                    for msg_id, fields in messages:
                        try:
                            raw = fields.get("data", "{}")
                            event = Event.model_validate_json(raw)

                            # Filter by event type if whitelist is given
                            if event_types and event.type.value not in event_types:
                                await self._client.xack(stream, group, msg_id)
                                continue

                            yield event

                            # Acknowledge after successful processing
                            await self._client.xack(stream, group, msg_id)

                        except Exception as exc:
                            logger.error("Failed to deserialise event %s: %s", msg_id, exc)
                            # Ack anyway so we don't get stuck on a bad message
                            await self._client.xack(stream, group, msg_id)

            except asyncio.CancelledError:
                logger.info("EventBus subscription cancelled for %s", agent_name)
                return
            except Exception as exc:
                logger.error("EventBus subscribe error for %s: %s", agent_name, exc)
                await asyncio.sleep(2)

    # ── Utilities ─────────────────────────────────────────────────────────────

    async def get_stream_length(self, stream: str = STREAM_KEY) -> int:
        """Return the number of messages currently in the stream."""
        if not self._client:
            return 0
        return await self._client.xlen(stream)

    async def peek_latest(self, n: int = 5, stream: str = STREAM_KEY) -> List[Event]:
        """Return the N most recent events (for debugging / dashboard)."""
        if not self._client:
            return []
        raw = await self._client.xrevrange(stream, count=n)
        events = []
        for _msg_id, fields in raw:
            try:
                events.append(Event.model_validate_json(fields["data"]))
            except Exception:
                pass
        return events

    async def set_cache(self, key: str, value: str, ttl_seconds: int = 30) -> None:
        """Store a value in Redis with a TTL (used as hot cache by agents)."""
        if self._client:
            await self._client.setex(key, ttl_seconds, value)

    async def get_cache(self, key: str) -> Optional[str]:
        """Retrieve a cached value from Redis."""
        if self._client:
            return await self._client.get(key)
        return None


# ── Singleton ────────────────────────────────────────────────────────────────

_event_bus_instance: Optional[EventBus] = None


def get_event_bus_sync() -> EventBus:
    """Return the global EventBus singleton (sync, no connection guaranteed)."""
    global _event_bus_instance
    if _event_bus_instance is None:
        _event_bus_instance = EventBus()
    return _event_bus_instance


async def get_event_bus() -> EventBus:
    """
    Async FastAPI dependency — returns a connected EventBus singleton.
    Call: bus = await get_event_bus()
    """
    global _event_bus_instance
    if _event_bus_instance is None:
        _event_bus_instance = EventBus()
    if _event_bus_instance._client is None:
        await _event_bus_instance.connect()
    return _event_bus_instance
