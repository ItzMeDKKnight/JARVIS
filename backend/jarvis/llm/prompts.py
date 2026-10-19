"""Builds the system prompt from config/persona.md.

The prompt must stay byte-identical across turns: prompt caching is a prefix
match, so anything that varies (the time, memory facts from Phase 4) must be
added later in the conversation, never here.
"""

from __future__ import annotations

from jarvis.config import JarvisConfig


def build_system_prompt(config: JarvisConfig) -> str:
    """Fill the persona placeholders.

    Plain ``str.replace`` is used instead of ``str.format`` so a stray brace
    typed into persona.md can never crash startup.
    """
    return (
        config.persona_template.replace("{assistant_name}", config.assistant.name)
        .replace("{user_name}", config.assistant.user_name)
        .strip()
    )
