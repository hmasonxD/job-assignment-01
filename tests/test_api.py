from datetime import datetime, timezone

from fastapi.testclient import TestClient

from telemetry_gateway.api import create_app


def test_health_and_dashboard_are_local(tmp_path) -> None:
    app = create_app(str(tmp_path / "gateway.db"))
    with TestClient(app) as client:
        assert client.get("/health/live").json() == {"status": "live"}
        assert client.get("/health/ready").json() == {"status": "ready"}
        dashboard = client.get("/")
        assert dashboard.status_code == 200
        assert "Local Telemetry Dashboard" in dashboard.text


def test_rejects_telemetry_for_an_unknown_boot(tmp_path) -> None:
    app = create_app(str(tmp_path / "gateway.db"))
    with TestClient(app) as client:
        response = client.post(
            "/api/telemetry",
            json={
                "deviceId": "device-01",
                "bootId": "unknown-boot",
                "sequence": 1,
                "deviceTime": "2026-08-12T09:00:00Z",
                "metric": "temperature",
                "value": 22,
            },
        )

        assert response.status_code == 409
        assert response.json() == {"error": "unknown_boot"}


def test_registers_boot_ingests_and_lists_state(tmp_path) -> None:
    app = create_app(
        str(tmp_path / "gateway.db"),
        now=lambda: datetime(2026, 8, 12, 9, 0, 1, tzinfo=timezone.utc),
    )
    with TestClient(app) as client:
        boot = client.post(
            "/api/boots", json={"deviceId": "device-01", "bootId": "boot-a"}
        )
        assert boot.status_code == 201
        assert boot.json()["generation"] == 1

        ingestion = client.post(
            "/api/telemetry",
            json={
                "deviceId": "device-01",
                "bootId": "boot-a",
                "sequence": 1,
                "deviceTime": "2026-08-12T09:00:00Z",
                "metric": "temperature",
                "value": 21.4,
            },
        )
        assert ingestion.status_code == 200
        assert ingestion.json()["currentChanged"] is True

        devices = client.get("/api/devices")
        assert devices.status_code == 200
        assert len(devices.json()["devices"]) == 1
        assert devices.json()["devices"][0]["value"] == 21.4


def test_snapshot_recovers_state_missed_while_disconnected(tmp_path) -> None:
    app = create_app(str(tmp_path / "gateway.db"))
    with TestClient(app) as client:
        client.post(
            "/api/boots",
            json={"deviceId": "device-01", "bootId": "boot-a"},
        )

        with client.websocket_connect("/ws") as websocket:
            client.post(
                "/api/telemetry",
                json={
                    "deviceId": "device-01",
                    "bootId": "boot-a",
                    "sequence": 1,
                    "deviceTime": "2026-08-12T09:00:00Z",
                    "metric": "temperature",
                    "value": 21.4,
                },
            )
            assert websocket.receive_json()["data"]["sequence"] == 1

        # This update occurs while the dashboard is disconnected, so no
        # notification can be replayed after it reconnects.
        client.post(
            "/api/telemetry",
            json={
                "deviceId": "device-01",
                "bootId": "boot-a",
                "sequence": 2,
                "deviceTime": "2026-08-12T09:00:01Z",
                "metric": "temperature",
                "value": 22.5,
            },
        )

        # A reconnecting dashboard recovers the missed state from the
        # authoritative snapshot endpoint.
        with client.websocket_connect("/ws"):
            snapshot = client.get("/api/devices").json()["devices"]

        assert snapshot[0]["sequence"] == 2
        assert snapshot[0]["value"] == 22.5
