"""The HUD WebSocket: origin check, token auth, validation, broadcasting (real loopback sockets)."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator

import pytest
from pydantic import BaseModel
from websockets.asyncio.client import ClientConnection, connect
from websockets.exceptions import ConnectionClosed, InvalidStatus

from jarvis.config import JarvisConfig, ServerConfig
from jarvis.events import (
    AssistantState,
    ClientCommand,
    EventBus,
    StateChanged,
    TextInput,
)
from jarvis.server import CLOSE_TOO_MANY, CLOSE_UNAUTHORIZED, HudServer

TOKEN = "t" * 40
ORIGIN = "app://jarvis"


class Harness:
    def __init__(self, config: ServerConfig) -> None:
        self.bus = EventBus()
        self.commands: list[ClientCommand] = []
        self.server = HudServer(
            config, token=TOKEN, bus=self.bus, on_command=self._on_command, snapshot=self._snapshot
        )
        self.port = 0

    async def _on_command(self, command: ClientCommand) -> None:
        self.commands.append(command)

    async def _snapshot(self) -> list[BaseModel]:
        return [StateChanged(state=AssistantState.IDLE)]

    def url(self) -> str:
        return f"ws://127.0.0.1:{self.port}"


@pytest.fixture
def server_config(config: JarvisConfig) -> ServerConfig:
    return config.server.model_copy(update={"auth_timeout_s": 0.3, "max_clients": 1})


@pytest.fixture
async def harness(server_config: ServerConfig) -> AsyncIterator[Harness]:
    h = Harness(server_config)
    h.port = await h.server.start()
    yield h
    await h.server.close()


async def _recv_json(ws: ClientConnection) -> dict[str, object]:
    return json.loads(await ws.recv())  # type: ignore[no-any-return]


async def _authed(h: Harness) -> ClientConnection:
    ws = await connect(h.url(), origin=ORIGIN)  # type: ignore[arg-type]
    await ws.send(json.dumps({"v": 2, "type": "auth", "token": TOKEN}))
    assert (await _recv_json(ws))["type"] == "state_changed"  # snapshot comes first
    return ws


async def test_binds_loopback_only(harness: Harness) -> None:
    assert harness.server._server is not None
    host = harness.server._server.sockets[0].getsockname()[0]
    assert host == "127.0.0.1"


async def test_auth_then_commands_are_dispatched(harness: Harness) -> None:
    ws = await _authed(harness)
    await ws.send(json.dumps({"v": 2, "type": "text_input", "text": "open chrome"}))
    await ws.send(json.dumps({"v": 2, "type": "interrupt"}))
    await ws.close()
    await harness.server.close()
    assert [c.type for c in harness.commands] == ["text_input", "interrupt"]
    assert isinstance(harness.commands[0], TextInput) and harness.commands[0].text == "open chrome"


async def test_bus_events_reach_authenticated_clients(harness: Harness) -> None:
    ws = await _authed(harness)
    await harness.bus.publish(StateChanged(state=AssistantState.THINKING))
    assert await _recv_json(ws) == {"v": 2, "type": "state_changed", "state": "thinking"}
    await ws.close()


async def test_wrong_token_is_rejected(harness: Harness) -> None:
    ws = await connect(harness.url(), origin=ORIGIN)  # type: ignore[arg-type]
    await ws.send(json.dumps({"v": 2, "type": "auth", "token": "x" * 40}))
    with pytest.raises(ConnectionClosed) as info:
        await ws.recv()
    assert info.value.rcvd is not None and info.value.rcvd.code == CLOSE_UNAUTHORIZED
    assert harness.commands == []


async def test_command_before_auth_is_rejected(harness: Harness) -> None:
    ws = await connect(harness.url(), origin=ORIGIN)  # type: ignore[arg-type]
    await ws.send(json.dumps({"v": 2, "type": "shutdown"}))
    with pytest.raises(ConnectionClosed):
        await ws.recv()
    assert harness.commands == []


async def test_silent_client_is_dropped_after_timeout(harness: Harness) -> None:
    ws = await connect(harness.url(), origin=ORIGIN)  # type: ignore[arg-type]
    with pytest.raises(ConnectionClosed) as info:
        await ws.recv()
    assert info.value.rcvd is not None and info.value.rcvd.code == CLOSE_UNAUTHORIZED


@pytest.mark.parametrize("origin", ["https://evil.example", "http://localhost:5173", None])
async def test_foreign_origins_cannot_even_connect(harness: Harness, origin: str | None) -> None:
    with pytest.raises(InvalidStatus) as info:
        await connect(harness.url(), origin=origin)  # type: ignore[arg-type]
    assert info.value.response.status_code == 403


async def test_invalid_command_gets_error_event(harness: Harness) -> None:
    ws = await _authed(harness)
    await ws.send(json.dumps({"v": 2, "type": "run_shell", "cmd": "dir"}))
    reply = await _recv_json(ws)
    assert reply["type"] == "error" and reply["code"] == "bad_command"
    assert harness.commands == []
    await ws.close()


async def test_client_limit(harness: Harness) -> None:
    first = await _authed(harness)
    second = await connect(harness.url(), origin=ORIGIN)  # type: ignore[arg-type]
    with pytest.raises(ConnectionClosed) as info:
        await second.recv()
    assert info.value.rcvd is not None and info.value.rcvd.code == CLOSE_TOO_MANY
    await first.close()
