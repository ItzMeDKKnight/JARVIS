"""Builds the system prompt from config/persona.md.

Two parts. The *core* must stay byte-identical across turns and personality modes: prompt
caching is a prefix match, so anything that varies (the time, memory facts) is added later
in the conversation, never here. The *personality block* (active mode and intensity band)
is a separate, small second system block after the core; see jarvis.personality.
"""

from __future__ import annotations

from jarvis.config import JarvisConfig
from jarvis.personality import PersonaSections, split_persona


def _filled(config: JarvisConfig) -> str:
    """Fill the placeholders. Plain ``str.replace``, so a stray brace can't crash startup."""
    return config.persona_template.replace("{assistant_name}", config.assistant.name).replace(
        "{user_name}", config.assistant.user_name
    )


def persona_sections(config: JarvisConfig) -> PersonaSections:
    return split_persona(_filled(config))


def build_system_prompt(config: JarvisConfig) -> str:
    """The core system prompt (identical in every personality mode)."""
    return persona_sections(config).core
