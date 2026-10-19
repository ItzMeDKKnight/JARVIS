from __future__ import annotations

import pytest

from jarvis.config import JarvisConfig
from jarvis.llm.router import ModelRouter


@pytest.fixture(scope="module")
def router(config: JarvisConfig) -> ModelRouter:
    return ModelRouter(config.llm)


@pytest.mark.parametrize(
    "text",
    [
        "write a python function for binary search",
        "Why does my JavaScript throw this error?",
        "explain the time complexity of merge sort",
        "how do I use templates in C++?",
        "in vs code, why does this python code fail?",  # app name ignored, rest still coding
        "मुझे बाइनरी सर्च का कोड लिखकर दो",
    ],
)
def test_coding_questions_use_coding_route(router: ModelRouter, text: str) -> None:
    name, route = router.route(text)
    assert name == "coding"
    assert route.model == "claude-sonnet-5-5"


@pytest.mark.parametrize(
    "text",
    [
        "open vs code",
        "Jarvis, open Visual Studio Code please",
        "वीएस कोड खोलो",
        "what is the weather in Mumbai",
        "calculator kholo",
        "आज का मौसम कैसा है",
        "tell me a joke about codecs",  # "codecs" must not match the keyword "code"
    ],
)
def test_everything_else_uses_default_route(router: ModelRouter, text: str) -> None:
    name, route = router.route(text)
    assert name == "routine"
    assert route.model == "claude-haiku-5-5"
