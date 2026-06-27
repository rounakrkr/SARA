"""Shared core types."""

from dataclasses import dataclass


@dataclass(slots=True)
class BrainResponse:
    text: str
    emotion: str = "neutral"
    affection_delta: int = 0          # Raw delta from this turn's LLM response.
                                      # Applied to self.affection_level inside
                                      # CoreBrain.respond() AND propagated here
                                      # so the caller (app.py) can broadcast it
                                      # to the frontend for expression intensity.
    should_exit: bool = False
    continue_in_seconds: int = 0
    llm_provider: str = None
    llm_model: str = None
    message_id: str = None
    # Set by InteractionPipeline after recording the assistant turn in
    # session memory. The frontend uses THIS id — never array position —
    # to reference this exact message for delete/regenerate. Position-based
    # indexing broke the moment any internal/system turn got recorded
    # (continuations, proactive nudges), since the frontend never sees
    # those and the two arrays silently drifted out of sync.