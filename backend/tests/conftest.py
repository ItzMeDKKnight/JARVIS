from __future__ import annotations

import pytest

from jarvis.config import JarvisConfig, load_config
from jarvis.events import EventBus
from tests.fakes import EventRecorder


@pytest.fixture(scope="session")
def config() -> JarvisConfig:
    """The real repository config, so tests also guard the shipped YAML files.

    Local overrides are skipped so results don't depend on the developer's config.local.yaml.
    """
    return load_config(local_overrides=False)


@pytest.fixture
def bus_and_events() -> tuple[EventBus, EventRecorder]:
    bus = EventBus()
    recorder = EventRecorder()
    bus.subscribe(recorder)
    return bus, recorder
