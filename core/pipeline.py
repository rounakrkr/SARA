"""Interaction pipeline — connects session memory to the core brain."""

from dataclasses import dataclass, field
from typing import Any

from core.brain import CoreBrain
from core.personality import DEFAULT_MOOD
from core.types import BrainResponse
from memory.session import SessionMemory


@dataclass(slots=True)
class InteractionPipeline:
    brain: CoreBrain
    memory: SessionMemory
    # Per-lore LongTermMemory override. None → brain uses its own global LTM.
    # Set by app.py when the user selects a lore pack so every session gets
    # fully isolated memory (different ChromaDB collection per lore).
    long_term_memory: Any = None

    def handle(
        self,
        message: str,
        tts_prov: str = "elevenlabs",
        llm_provider: str = None,
        llm_model: str = None,
        mood_preset: str = DEFAULT_MOOD,
        trailed_off: bool = False,
        message_id: str = None,
        internal: bool = False,
        regen_direction: str = None,
        lore_enabled: bool = True,   # Passed from app.py state.lore_enabled[session_id]
        enabled_packs: set | None = None,  # Active lore packs; None = all packs
    ) -> BrainResponse:
        self.memory.record_user_message(message, internal=internal, explicit_id=message_id)
        recent_history = self.memory.get_llm_history(10)

        response = self.brain.respond(
            message=message,
            turn_count=self.memory.turn_count,
            chat_history=recent_history,
            tts_prov=tts_prov,
            llm_provider=llm_provider,
            llm_model=llm_model,
            mood_preset=mood_preset,
            trailed_off=trailed_off,
            regen_direction=regen_direction,
            lore_enabled=lore_enabled,
            enabled_packs=enabled_packs,
            ltm_override=self.long_term_memory,  # Per-lore isolation
        )

        # An empty response.text is the "I'm done continuing, nothing more
        # to add" signal — not a real turn, so don't pollute history with
        # a blank assistant message, and don't hand out an id for it.
        if response.text.strip():
            response.message_id = self.memory.record_assistant_message(response.text)

        return response