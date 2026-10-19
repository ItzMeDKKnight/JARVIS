"""The local WebSocket the HUD connects to.

Security model (the HUD is the only intended client):

* bound to 127.0.0.1 only (the config type allows nothing else), on a port
  chosen per launch;
* the ``Origin`` header must be in ``server.allowed_origins`` (the HUD's
  ``app://jarvis``), so a web page open in a browser can't connect even if it
  guesses the port: browsers always send their real origin;
* the first message must be ``auth`` with the per-launch token, which
  Electron generates and hands to the backend through an environment
  variable. Without it the connection is closed after ``auth_timeout_s``;
* every command is validated against the protocol models; anything else
  gets an ``error`` event and is otherwise ignored;
* message size and client count are capped.

Events from the bus are fanned out to authenticated clients with
``broadcast``, which never blocks the assistant on a slow client.
"""

from __future__ import annotations

import asyncio
import hmac
import logging
from collections.abc import Awaitable, Callable

from pydantic import BaseModel, ValidationError
from websockets.asyncio.server import Server, ServerConnection, broadcast, serve
from websockets.exceptions import ConnectionClosed
from websockets.typing import Origin

from jarvis.config import ServerConfig
from jarvis.events import Auth, ClientCommand, ErrorEvent, EventBus, client_command_adapter

logger = logging.getLogger(__name__)

# Application close codes (4000-4999 are free for applications).
CLOSE_UNAUTHORIZED = 4401
CLOSE_TOO_MANY = 4429

CommandHandler = Callable[[ClientCommand], Awaitable[None]]
SnapshotProvider = Callable[[], Awaitable[list[BaseModel]]]


class HudServer:
    def __init__(
        self,
        config: ServerConfig,
        *,
        token: str,
        bus: EventBus,
        on_command: CommandHandler,
        snapshot: SnapshotProvider,
    ) -> None:
        self._config = config
        self._token = token.encode()
        self._on_command = on_command
        self._snapshot = snapshot
        self._clients: set[ServerConnection] = set()
        self._server: Server | None = None
        bus.subscribe(self._publish)

    @property
    def client_count(self) -> int:
        return len(self._clients)

    async def start(self) -> int:
        """Start listening; return the port actually bound."""
        self._server = await serve(
            self._handle,
            self._config.host,
            self._config.port,
            origins=[Origin(origin) for origin in self._config.allowed_origins],
            max_size=self._config.max_message_bytes,
            compression=None,  # tiny messages on loopback: compression only costs CPU
        )
        port = int(self._server.sockets[0].getsockname()[1])
        logger.info("hud server listening", extra={"host": self._config.host, "port": port})
        return port

    async def close(self) -> None:
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()
            self._server = None

    async def _publish(self, event: BaseModel) -> None:
        if self._clients:
            broadcast(self._clients, event.model_dump_json())

    async def _handle(self, connection: ServerConnection) -> None:
        if len(self._clients) >= self._config.max_clients:
            await connection.close(CLOSE_TOO_MANY, "too many clients")
            return
        if not await self._authenticate(connection):
            await connection.close(CLOSE_UNAUTHORIZED, "unauthorized")
            return
        logger.info("hud client connected", extra={"clients": len(self._clients) + 1})
        for event in await self._snapshot():
            await connection.send(event.model_dump_json())
        self._clients.add(connection)
        try:
            async for raw in connection:
                await self._dispatch(connection, raw)
        except ConnectionClosed:
            pass
        finally:
            self._clients.discard(connection)
            logger.info("hud client disconnected", extra={"clients": len(self._clients)})

    async def _authenticate(self, connection: ServerConnection) -> bool:
        try:
            raw = await asyncio.wait_for(connection.recv(), self._config.auth_timeout_s)
            command = client_command_adapter.validate_json(raw)
        except (TimeoutError, ValidationError, ConnectionClosed, ValueError):
            logger.warning("hud client failed to authenticate")
            return False
        if not isinstance(command, Auth):
            logger.warning("hud client sent a command before authenticating")
            return False
        # Constant-time comparison: no timing hints about the token.
        if not hmac.compare_digest(command.token.encode(), self._token):
            logger.warning("hud client sent a wrong token")
            return False
        return True

    async def _dispatch(self, connection: ServerConnection, raw: str | bytes) -> None:
        try:
            command = client_command_adapter.validate_json(raw)
        except ValidationError as exc:
            logger.warning("invalid hud command", extra={"errors": exc.error_count()})
            await connection.send(
                ErrorEvent(code="bad_command", message="Invalid command").model_dump_json()
            )
            return
        if isinstance(command, Auth):
            return  # already authenticated
        try:
            await self._on_command(command)
        except Exception:
            logger.exception("hud command failed", extra={"command": command.type})
            await connection.send(
                ErrorEvent(code="command_failed", message="That didn't work").model_dump_json()
            )
