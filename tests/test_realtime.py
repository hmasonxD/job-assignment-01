import asyncio

import pytest

from telemetry_gateway.models import DeviceState
from telemetry_gateway.realtime import RealtimeHub


def state(sequence: int) -> DeviceState:
    return DeviceState(
        device_id="device-01",
        boot_id="boot-a",
        generation=1,
        sequence=sequence,
        device_time="2026-08-12T09:00:00+00:00",
        received_at="2026-08-12T09:00:01+00:00",
        metric="temperature",
        value=20 + sequence,
    )


class FakeWebSocket:
    def __init__(
        self,
        send_gate: asyncio.Event | None = None,
        send_error: Exception | None = None,
    ) -> None:
        self.send_gate = send_gate
        self.send_error = send_error
        self.send_started = asyncio.Event()
        self.message_sent = asyncio.Event()
        self.closed = asyncio.Event()
        self.accepted = False
        self.messages: list[dict[str, object]] = []
        self.close_codes: list[int] = []

    async def accept(self) -> None:
        self.accepted = True

    async def send_json(self, message: dict[str, object]) -> None:
        self.send_started.set()
        if self.send_error is not None:
            raise self.send_error
        if self.send_gate is not None:
            await self.send_gate.wait()
        self.messages.append(message)
        self.message_sent.set()

    async def close(self, code: int) -> None:
        self.close_codes.append(code)
        self.closed.set()

    async def wait_for_messages(self, expected: int) -> None:
        while len(self.messages) < expected:
            self.message_sent.clear()
            if len(self.messages) < expected:
                await self.message_sent.wait()


def test_slow_client_is_dropped_without_blocking_healthy_client() -> None:
    async def exercise() -> None:
        send_gate = asyncio.Event()
        slow = FakeWebSocket(send_gate=send_gate)
        healthy = FakeWebSocket()
        hub = RealtimeHub(buffer_limit=1)

        await hub.connect(slow)
        await hub.connect(healthy)

        await hub.publish(state(1))
        await slow.send_started.wait()
        await healthy.wait_for_messages(1)

        # The slow sender is blocked, so its one-slot queue fills and the
        # following publication exceeds the configured client buffer.
        await hub.publish(state(2))
        await healthy.wait_for_messages(2)
        await hub.publish(state(3))
        await healthy.wait_for_messages(3)
        await slow.closed.wait()

        assert [message["data"]["sequence"] for message in healthy.messages] == [
            1,
            2,
            3,
        ]
        assert slow.close_codes == [1013]
        assert hub.size == 1

        await hub.disconnect(healthy)

    asyncio.run(exercise())


def test_broken_client_does_not_block_healthy_client() -> None:
    async def exercise() -> None:
        broken = FakeWebSocket(send_error=RuntimeError("connection lost"))
        healthy = FakeWebSocket()
        hub = RealtimeHub(buffer_limit=2)

        await hub.connect(broken)
        await hub.connect(healthy)
        await hub.publish(state(1))

        await broken.send_started.wait()
        await healthy.wait_for_messages(1)
        await asyncio.sleep(0)

        assert [message["data"]["sequence"] for message in healthy.messages] == [1]
        assert hub.size == 1

        await hub.disconnect(healthy)

    asyncio.run(exercise())


def test_buffer_limit_must_be_positive() -> None:
    with pytest.raises(ValueError, match="buffer_limit must be positive"):
        RealtimeHub(buffer_limit=0)
