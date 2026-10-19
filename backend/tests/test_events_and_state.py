from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from pydantic import BaseModel, ValidationError

from jarvis.events import (
    AssistantState,
    AudioLevel,
    EventBus,
    StateChanged,
    TextInput,
    Transcript,
    backend_event_adapter,
    client_command_adapter,
)
from jarvis.state import InvalidTransitionError, StateMachine
from tests.fakes import EventRecorder

REPO_ROOT = Path(__file__).resolve().parents[2]
S = AssistantState


def test_events_carry_version_and_type() -> None:
    event = Transcript(turn_id="a", role="user", text="hi", language="en", final=True)
    payload = json.loads(event.model_dump_json())
    assert payload["v"] == 2 and payload["type"] == "transcript"


def test_round_trip_through_discriminated_unions() -> None:
    event = backend_event_adapter.validate_json(
        '{"v":2,"type":"audio_level","source":"mic","level":0.5}'
    )
    assert isinstance(event, AudioLevel)
    command = client_command_adapter.validate_json(
        '{"v":2,"type":"text_input","text":"open chrome"}'
    )
    assert isinstance(command, TextInput)


def test_invalid_messages_are_rejected() -> None:
    with pytest.raises(ValidationError):
        AudioLevel(source="mic", level=1.5)
    with pytest.raises(ValidationError):
        client_command_adapter.validate_json('{"v":2,"type":"run_shell","cmd":"dir"}')
    with pytest.raises(ValidationError):
        client_command_adapter.validate_json('{"v":1,"type":"interrupt"}')


def test_committed_schema_matches_models() -> None:
    sys.path.insert(0, str(REPO_ROOT / "scripts"))
    try:
        from export_protocol_schema import SCHEMA_PATH, render_schema
    finally:
        sys.path.pop(0)
    assert SCHEMA_PATH.read_text(encoding="utf-8") == render_schema(), (
        "protocol/events.schema.json is stale: run scripts/export_protocol_schema.py"
    )


async def test_failing_subscriber_does_not_break_the_bus() -> None:
    bus = EventBus()
    recorder = EventRecorder()

    async def broken(event: BaseModel) -> None:
        raise RuntimeError("display crashed")

    bus.subscribe(broken)
    bus.subscribe(recorder)
    await bus.publish(StateChanged(state=S.IDLE))
    assert len(recorder.events) == 1


async def test_state_machine_publishes_changes_and_ignores_repeats() -> None:
    bus = EventBus()
    recorder = EventRecorder()
    bus.subscribe(recorder)
    machine = StateMachine(bus)

    await machine.transition(S.THINKING)
    await machine.transition(S.THINKING)
    await machine.transition(S.SPEAKING)

    assert [e.state for e in recorder.events] == [S.THINKING, S.SPEAKING]


@pytest.mark.parametrize(
    ("start", "target"),
    [(S.SPEAKING, S.MUTED), (S.MUTED, S.SPEAKING), (S.IDLE, S.SPEAKING), (S.ERROR, S.THINKING)],
)
async def test_illegal_transitions_raise(start: AssistantState, target: AssistantState) -> None:
    machine = StateMachine(EventBus(), initial=start)
    with pytest.raises(InvalidTransitionError):
        await machine.transition(target)
