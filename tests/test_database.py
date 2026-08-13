import sqlite3

from telemetry_gateway.database import TelemetryStore
from telemetry_gateway.migrations import migration_001
from telemetry_gateway.models import BootRegistrationInput, TelemetryInput


def telemetry(**overrides) -> TelemetryInput:
    values = {
        "deviceId": "device-01",
        "bootId": "boot-a",
        "sequence": 1,
        "deviceTime": "2026-08-12T09:00:00+00:00",
        "metric": "temperature",
        "value": 21.4,
    }
    values.update(overrides)
    return TelemetryInput.model_validate(values)


def test_registers_a_boot_idempotently() -> None:
    store = TelemetryStore(":memory:")
    try:
        event = BootRegistrationInput(deviceId="device-01", bootId="boot-a")

        first = store.register_boot(event)
        second = store.register_boot(event)

        assert first.to_api() == {
            "deviceId": "device-01",
            "bootId": "boot-a",
            "generation": 1,
            "created": True,
        }
        assert second.to_api() == {
            "deviceId": "device-01",
            "bootId": "boot-a",
            "generation": 1,
            "created": False,
        }
    finally:
        store.close()


def test_stores_a_basic_event_and_calculates_current_state() -> None:
    store = TelemetryStore(":memory:")
    try:
        store.register_boot(BootRegistrationInput(deviceId="device-01", bootId="boot-a"))

        result = store.ingest(telemetry(), "2026-08-12T09:00:01+00:00")

        assert result.duplicate is False
        assert result.current_changed is True
        assert store.list_current_states()[0].to_api() == {
            "deviceId": "device-01",
            "bootId": "boot-a",
            "generation": 1,
            "sequence": 1,
            "deviceTime": "2026-08-12T09:00:00+00:00",
            "receivedAt": "2026-08-12T09:00:01+00:00",
            "metric": "temperature",
            "value": 21.4,
        }
        assert len(store.list_events(10)) == 1
    finally:
        store.close()


def test_repeated_event_from_same_boot_is_a_duplicate() -> None:
    store = TelemetryStore(":memory:")
    try:
        store.register_boot(BootRegistrationInput(deviceId="device-01", bootId="boot-a"))
        store.ingest(telemetry(), "2026-08-12T09:00:01+00:00")

        duplicate = store.ingest(telemetry(), "2026-08-12T09:00:02+00:00")

        assert duplicate.to_api() == {
            "accepted": True,
            "duplicate": True,
            "currentChanged": False,
        }
        assert len(store.list_events(10)) == 1
    finally:
        store.close()


def test_same_sequence_from_different_boots_is_not_a_duplicate() -> None:
    store = TelemetryStore(":memory:")
    try:
        store.register_boot(
            BootRegistrationInput(deviceId="device-01", bootId="boot-a")
        )
        store.register_boot(
            BootRegistrationInput(deviceId="device-01", bootId="boot-b")
        )

        first = store.ingest(
            telemetry(bootId="boot-a", sequence=1),
            "2026-08-12T09:00:01+00:00",
        )
        restarted = store.ingest(
            telemetry(bootId="boot-b", sequence=1),
            "2026-08-12T09:00:02+00:00",
        )

        assert first.duplicate is False
        assert restarted.duplicate is False
        assert restarted.current_changed is True
        assert restarted.state is not None
        assert restarted.state.generation == 2
        assert len(store.list_events(10)) == 2
    finally:
        store.close()


def test_higher_sequence_wins_when_device_clock_moves_backward() -> None:
    store = TelemetryStore(":memory:")
    try:
        store.register_boot(
            BootRegistrationInput(deviceId="device-01", bootId="boot-a")
        )
        store.ingest(
            telemetry(
                sequence=1,
                deviceTime="2099-01-01T00:00:00+00:00",
                value=20,
            ),
            "2026-08-12T09:00:01+00:00",
        )

        result = store.ingest(
            telemetry(
                sequence=2,
                deviceTime="2020-01-01T00:00:00+00:00",
                value=22,
            ),
            "2026-08-12T09:00:02+00:00",
        )

        current = store.list_current_states()[0]
        assert result.current_changed is True
        assert current.sequence == 2
        assert current.value == 22
    finally:
        store.close()


def test_newer_boot_wins_and_delayed_older_boot_stays_in_history() -> None:
    store = TelemetryStore(":memory:")
    try:
        store.register_boot(
            BootRegistrationInput(deviceId="device-01", bootId="boot-a")
        )
        store.register_boot(
            BootRegistrationInput(deviceId="device-01", bootId="boot-b")
        )
        store.ingest(
            telemetry(
                bootId="boot-a",
                sequence=10,
                deviceTime="2099-01-01T00:00:00+00:00",
                value=20,
            ),
            "2026-08-12T09:00:01+00:00",
        )

        restarted = store.ingest(
            telemetry(
                bootId="boot-b",
                sequence=1,
                deviceTime="2020-01-01T00:00:00+00:00",
                value=22,
            ),
            "2026-08-12T09:00:02+00:00",
        )
        delayed = store.ingest(
            telemetry(
                bootId="boot-a",
                sequence=11,
                deviceTime="2100-01-01T00:00:00+00:00",
                value=19,
            ),
            "2026-08-12T09:00:03+00:00",
        )

        current = store.list_current_states()[0]
        assert restarted.current_changed is True
        assert delayed.duplicate is False
        assert delayed.current_changed is False
        assert current.boot_id == "boot-b"
        assert current.generation == 2
        assert current.sequence == 1
        assert current.value == 22
        assert len(store.list_events(10)) == 3
    finally:
        store.close()


def test_migration_preserves_existing_audit_history(tmp_path) -> None:
    database_path = tmp_path / "gateway.db"
    connection = sqlite3.connect(database_path)
    try:
        connection.execute(
            """
            CREATE TABLE schema_migrations (
                version INTEGER PRIMARY KEY,
                applied_at TEXT NOT NULL
            )
            """
        )
        migration_001(connection)
        connection.execute(
            """
            INSERT INTO schema_migrations (version, applied_at)
            VALUES (1, datetime('now'))
            """
        )
        connection.execute(
            """
            INSERT INTO device_boots
                (device_id, boot_id, generation, registered_at)
            VALUES (?, ?, ?, datetime('now'))
            """,
            ("device-01", "boot-a", 1),
        )
        connection.execute(
            """
            INSERT INTO telemetry_events
                (device_id, boot_id, generation, sequence, device_time,
                 received_at, metric, value)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "device-01",
                "boot-a",
                1,
                1,
                "2026-08-12T09:00:00+00:00",
                "2026-08-12T09:00:01+00:00",
                "temperature",
                21.4,
            ),
        )
        connection.commit()
    finally:
        connection.close()

    store = TelemetryStore(str(database_path))
    try:
        existing_events = store.list_events(10)
        assert len(existing_events) == 1
        assert existing_events[0].state.boot_id == "boot-a"
        assert existing_events[0].state.value == 21.4

        registration = store.register_boot(
            BootRegistrationInput(deviceId="device-01", bootId="boot-b")
        )
        restarted = store.ingest(
            telemetry(bootId="boot-b", sequence=1, value=22),
            "2026-08-12T09:00:02+00:00",
        )

        assert registration.generation == 2
        assert restarted.duplicate is False
        assert len(store.list_events(10)) == 2
    finally:
        store.close()

def test_lower_sequence_does_not_move_current_state_backward() -> None:
    store = TelemetryStore(":memory:")
    try:
        store.register_boot(
            BootRegistrationInput(deviceId="device-01", bootId="boot-a")
        )
        store.ingest(
            telemetry(sequence=2, value=22),
            "2026-08-12T09:00:01+00:00",
        )

        delayed = store.ingest(
            telemetry(sequence=1, value=20),
            "2026-08-12T09:00:02+00:00",
        )

        current = store.list_current_states()[0]
        assert delayed.duplicate is False
        assert delayed.current_changed is False
        assert current.sequence == 2
        assert current.value == 22
        assert len(store.list_events(10)) == 2
    finally:
        store.close()
