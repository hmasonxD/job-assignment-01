import asyncio
from datetime import datetime, timezone

import pytest

from telemetry_gateway.models import (
    BootRegistrationResult,
    DeviceState,
    IngestResult,
    TelemetryInput,
)
from telemetry_gateway.service import TelemetryService


def telemetry_event() -> TelemetryInput:
    return TelemetryInput.model_validate(
        {
            "deviceId": "device-01",
            "bootId": "boot-a",
            "sequence": 1,
            "deviceTime": "2026-08-12T09:00:00Z",
            "metric": "temperature",
            "value": 21.4,
        }
    )


def device_state() -> DeviceState:
    return DeviceState(
        device_id="device-01",
        boot_id="boot-a",
        generation=1,
        sequence=1,
        device_time="2026-08-12T09:00:00+00:00",
        received_at="2026-08-12T09:00:01+00:00",
        metric="temperature",
        value=21.4,
    )


class FakeRepository:
    def __init__(
        self,
        result: IngestResult,
        operations: list[str],
        ingest_error: Exception | None = None,
    ) -> None:
        self.result = result
        self.operations = operations
        self.ingest_error = ingest_error
        self.ingest_calls = 0

    def register_boot(self, _event):
        return BootRegistrationResult("device-01", "boot-a", 1, True)

    def ingest(self, _event, _received_at):
        self.ingest_calls += 1
        self.operations.append("ingest")
        if self.ingest_error is not None:
            raise self.ingest_error
        return self.result

    def list_current_states(self):
        return []

    def list_events(self, _limit):
        return []

    def ping(self):
        return True


class RecordingPublisher:
    def __init__(self, operations: list[str]) -> None:
        self.operations = operations
        self.states: list[DeviceState] = []

    async def publish(self, state: DeviceState) -> None:
        self.operations.append("publish")
        self.states.append(state)


def make_service(
    result: IngestResult,
    ingest_error: Exception | None = None,
) -> tuple[TelemetryService, FakeRepository, RecordingPublisher]:
    operations: list[str] = []
    repository = FakeRepository(
        result=result,
        operations=operations,
        ingest_error=ingest_error,
    )
    publisher = RecordingPublisher(operations)
    service = TelemetryService(
        repository,
        publisher,
        now=lambda: datetime(2026, 8, 12, 9, 0, 1, tzinfo=timezone.utc),
    )
    return service, repository, publisher


def test_service_publishes_changed_state_after_ingestion() -> None:
    state = device_state()
    service, repository, publisher = make_service(
        IngestResult(duplicate=False, current_changed=True, state=state)
    )

    result = asyncio.run(service.ingest(telemetry_event()))

    assert result.current_changed is True
    assert repository.ingest_calls == 1
    assert publisher.states == [state]
    assert publisher.operations == ["ingest", "publish"]


def test_service_does_not_publish_duplicate_event() -> None:
    service, repository, publisher = make_service(
        IngestResult(duplicate=True, current_changed=False)
    )

    result = asyncio.run(service.ingest(telemetry_event()))

    assert result.duplicate is True
    assert repository.ingest_calls == 1
    assert publisher.states == []


def test_service_does_not_publish_stale_event() -> None:
    service, repository, publisher = make_service(
        IngestResult(duplicate=False, current_changed=False)
    )

    result = asyncio.run(service.ingest(telemetry_event()))

    assert result.duplicate is False
    assert result.current_changed is False
    assert repository.ingest_calls == 1
    assert publisher.states == []


def test_service_does_not_publish_when_ingestion_fails() -> None:
    service, repository, publisher = make_service(
        IngestResult(duplicate=False, current_changed=False),
        ingest_error=RuntimeError("database write failed"),
    )

    with pytest.raises(RuntimeError, match="database write failed"):
        asyncio.run(service.ingest(telemetry_event()))

    assert repository.ingest_calls == 1
    assert publisher.states == []
