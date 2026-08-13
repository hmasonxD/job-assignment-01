from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Protocol

from fastapi import WebSocket

from telemetry_gateway.models import DeviceState

RealtimeMessage = dict[str, object]


class StatePublisher(Protocol):
    async def publish(self, state: DeviceState) -> None: ...


@dataclass(slots=True)
class _ClientConnection:
    websocket: WebSocket
    queue: asyncio.Queue[RealtimeMessage]
    sender: asyncio.Task[None] | None = None


class RealtimeHub:
    def __init__(self, buffer_limit: int = 100) -> None:
        if buffer_limit <= 0:
            raise ValueError("buffer_limit must be positive")
        self._buffer_limit = buffer_limit
        self._clients: dict[WebSocket, _ClientConnection] = {}
        self._close_tasks: set[asyncio.Task[None]] = set()

    async def connect(self, client: WebSocket) -> None:
        await client.accept()

        # Each client gets a bounded queue and its own sender so network I/O
        # for one connection cannot delay publication to other clients.
        connection = _ClientConnection(
            websocket=client,
            queue=asyncio.Queue(maxsize=self._buffer_limit),
        )
        self._clients[client] = connection
        connection.sender = asyncio.create_task(self._send_messages(connection))

    async def disconnect(self, client: WebSocket) -> None:
        connection = self._clients.pop(client, None)
        if connection is None or connection.sender is None:
            return

        connection.sender.cancel()
        await asyncio.gather(connection.sender, return_exceptions=True)

    async def publish(self, state: DeviceState) -> None:
        message: RealtimeMessage = {
            "type": "device.state.changed",
            "data": state.to_api(),
        }

        for connection in tuple(self._clients.values()):
            try:
                # Never await a client network operation on the publish path.
                connection.queue.put_nowait(message)
            except asyncio.QueueFull:
                self._drop_slow_client(connection)

    async def close(self) -> None:
        for client in tuple(self._clients):
            await self.disconnect(client)

        if self._close_tasks:
            await asyncio.gather(*tuple(self._close_tasks), return_exceptions=True)

    async def _send_messages(self, connection: _ClientConnection) -> None:
        try:
            while True:
                message = await connection.queue.get()
                await connection.websocket.send_json(message)
        except asyncio.CancelledError:
            raise
        except Exception:
            # A broken client is removed without affecting other sender tasks.
            current = self._clients.get(connection.websocket)
            if current is connection:
                self._clients.pop(connection.websocket, None)

    def _drop_slow_client(self, connection: _ClientConnection) -> None:
        current = self._clients.get(connection.websocket)
        if current is not connection:
            return

        self._clients.pop(connection.websocket, None)
        if connection.sender is not None:
            connection.sender.cancel()

        # Code 1013 asks a dropped client to reconnect and recover via snapshot.
        close_task = asyncio.create_task(
            self._close_slow_client(connection.websocket)
        )
        self._close_tasks.add(close_task)
        close_task.add_done_callback(self._close_tasks.discard)

    @staticmethod
    async def _close_slow_client(client: WebSocket) -> None:
        try:
            # Bound close time as well, since a broken connection may never reply.
            await asyncio.wait_for(client.close(code=1013), timeout=1)
        except Exception:
            pass

    @property
    def size(self) -> int:
        return len(self._clients)
