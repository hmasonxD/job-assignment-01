from __future__ import annotations

import sqlite3
from collections.abc import Callable

Migration = tuple[int, Callable[[sqlite3.Connection], None]]


def migration_001(connection: sqlite3.Connection) -> None:
    connection.execute(
        """
        CREATE TABLE device_boots (
            device_id TEXT NOT NULL,
            boot_id TEXT NOT NULL,
            generation INTEGER NOT NULL,
            registered_at TEXT NOT NULL,
            PRIMARY KEY (device_id, boot_id),
            UNIQUE (device_id, generation)
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE telemetry_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            device_id TEXT NOT NULL,
            boot_id TEXT NOT NULL,
            generation INTEGER NOT NULL,
            sequence INTEGER NOT NULL,
            device_time TEXT NOT NULL,
            received_at TEXT NOT NULL,
            metric TEXT NOT NULL,
            value REAL NOT NULL,
            UNIQUE (device_id, sequence),
            FOREIGN KEY (device_id, boot_id)
                REFERENCES device_boots (device_id, boot_id)
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE current_state (
            device_id TEXT NOT NULL,
            metric TEXT NOT NULL,
            boot_id TEXT NOT NULL,
            generation INTEGER NOT NULL,
            sequence INTEGER NOT NULL,
            device_time TEXT NOT NULL,
            received_at TEXT NOT NULL,
            value REAL NOT NULL,
            PRIMARY KEY (device_id, metric),
            FOREIGN KEY (device_id, boot_id)
                REFERENCES device_boots (device_id, boot_id)
        )
        """
    )
    connection.execute(
        """
        CREATE INDEX telemetry_events_received_at_idx
        ON telemetry_events (received_at DESC)
        """
    )


def migration_002(connection: sqlite3.Connection) -> None:
    # SQLite cannot alter a UNIQUE constraint in place, so rebuild the
    # table while preserving the complete audit history and original IDs.
    connection.execute("DROP INDEX telemetry_events_received_at_idx")
    connection.execute(
        "ALTER TABLE telemetry_events RENAME TO telemetry_events_v1"
    )
    connection.execute(
        """
        CREATE TABLE telemetry_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            device_id TEXT NOT NULL,
            boot_id TEXT NOT NULL,
            generation INTEGER NOT NULL,
            sequence INTEGER NOT NULL,
            device_time TEXT NOT NULL,
            received_at TEXT NOT NULL,
            metric TEXT NOT NULL,
            value REAL NOT NULL,
            UNIQUE (device_id, boot_id, sequence),
            FOREIGN KEY (device_id, boot_id)
                REFERENCES device_boots (device_id, boot_id)
        )
        """
    )
    connection.execute(
        """
        INSERT INTO telemetry_events
            (id, device_id, boot_id, generation, sequence, device_time,
             received_at, metric, value)
        SELECT id, device_id, boot_id, generation, sequence, device_time,
               received_at, metric, value
        FROM telemetry_events_v1
        """
    )
    connection.execute("DROP TABLE telemetry_events_v1")
    connection.execute(
        """
        CREATE INDEX telemetry_events_received_at_idx
        ON telemetry_events (received_at DESC)
        """
    )


MIGRATIONS: list[Migration] = [
    (1, migration_001),
    (2, migration_002),
]


def apply_migrations(connection: sqlite3.Connection) -> None:
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS schema_migrations (
            version INTEGER PRIMARY KEY,
            applied_at TEXT NOT NULL
        )
        """
    )
    applied = {
        row[0]
        for row in connection.execute(
            "SELECT version FROM schema_migrations ORDER BY version"
        ).fetchall()
    }

    for version, migration in MIGRATIONS:
        if version in applied:
            continue

        connection.execute("BEGIN IMMEDIATE")
        try:
            migration(connection)
            connection.execute(
                "INSERT INTO schema_migrations (version, applied_at) VALUES (?, datetime('now'))",
                (version,),
            )
            connection.commit()
        except Exception:
            connection.rollback()
            raise