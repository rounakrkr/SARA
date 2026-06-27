"""Natural Conversation Engine - Ultimate Human-like Interaction System.

This module enhances SARA's conversational naturalness with:
- Intelligent filler sound selection
- Context-aware pause management
- Character-specific speech patterns
- Dynamic conversation flow
"""

import os
import random
from typing import Dict, List, Tuple, Optional
from dataclasses import dataclass
from enum import Enum


class ThinkingLevel(Enum):
    """Granular thinking intensity levels."""
    NONE = "none"                    # Simple response, no pause
    BRIEF = "brief"                  # Quick "umm" (0.3-0.5s)
    CONSIDERING = "considering"       # "let me see" (0.8-1.2s)
    THINKING = "thinking"             # "hmm, let me think" (1.5-2.5s)
    DEEP_THOUGHT = "deep_thought"     # "let me think about that" (3-5s)
    EMOTIONAL_PAUSE = "emotional"     # Sara-specific: emotional hesitation (2-4s)


class FillerType(Enum):
    """Types of natural filler sounds."""
    UMM = "umm"
    UH = "uh"
    WELL = "well"
    LET_ME_SEE = "let_me_see"
    HMM = "hmm"
    LET_ME_THINK = "let_me_think"
    BREATH_SOFT = "breath_soft"
    BREATH_THINKING = "breath_thinking"
    SIGH_SOFT = "sigh_soft"
    HESITATION = "hesitation"         # Sara's nervous pause
    EMOTIONAL_BREATH = "emotional_breath"  # Before emotional responses


@dataclass
class ConversationContext:
    """Tracks conversation state for dynamic behavior."""
    turn_count: int = 0
    recent_emotions: List[str] = None
    last_topic_emotional: bool = False
    user_speaking_pace: str = "normal"  # slow, normal, fast
    character_mood: str = "neutral"
    
    def __post_init__(self):
        if self.recent_emotions is None:
            self.recent_emotions = []


class NaturalConversationEngine:
    """Enhanced conversation naturalness system."""
    
    # Keyword patterns for different thinking levels
    THINKING_PATTERNS = {
        ThinkingLevel.DEEP_THOUGHT: [
            "why", "how does", "explain", "tell me about", "what is",
            "describe", "philosophy", "meaning of", "universe", "life",
            "complex", "difficult", "understand", "nuclear", "quantum"
        ],
        ThinkingLevel.THINKING: [
            "can you", "would you", "could you", "what do you think",
            "opinion", "believe", "remember", "story", "poem", "create"
        ],
        ThinkingLevel.CONSIDERING: [
            "where", "when", "which", "should i", "recommendation",
            "suggest", "advice", "help me", "show me"
        ],
        ThinkingLevel.BRIEF: [
            "um", "uh", "well", "so", "like", "maybe", "perhaps"
        ]
    }
    
    # Sara's emotional triggers (require emotional pause)
    SARA_EMOTIONAL_TRIGGERS = [
        "malik", "owner", "hurt", "pain", "scared", "afraid", "fear",
        "family", "parents", "mother", "father", "home", "safe",
        "trust", "believe", "care", "love", "alone", "lonely"
    ]
    
    def __init__(self, character_name: str = "Sara", audio_dir: str = "static/audio"):
        self.character_name = character_name
        self.audio_dir = audio_dir
        self.context = ConversationContext()
        
        # Map fillers to audio files (will be generated)
        self.filler_files = {
            FillerType.UMM: "umm.mp3",
            FillerType.UH: "uh.mp3",
            FillerType.WELL: "well.mp3",
            FillerType.LET_ME_SEE: "let_me_see.mp3",
            FillerType.HMM: "hmm.mp3",
            FillerType.LET_ME_THINK: "let_me_think.mp3",
            FillerType.BREATH_SOFT: "breath_soft.mp3",
            FillerType.BREATH_THINKING: "breath_thinking.mp3",
            FillerType.SIGH_SOFT: "sigh_soft.mp3",
            FillerType.HESITATION: "hesitation.mp3",
            FillerType.EMOTIONAL_BREATH: "emotional_breath.mp3",
        }
    
    def classify_thinking_level(self, user_message: str, context: Optional[ConversationContext] = None) -> ThinkingLevel:
        """Intelligently classify how much 'thinking' this message requires.
        
        Returns appropriate ThinkingLevel with granular categorization.
        """
        msg_lower = user_message.lower().strip()
        
        # Check for Sara's emotional triggers first
        if any(trigger in msg_lower for trigger in self.SARA_EMOTIONAL_TRIGGERS):
            return ThinkingLevel.EMOTIONAL_PAUSE
        
        # Simple greetings and personal questions - no pause
        simple_patterns = [
            "hello", "hi", "hey", "good morning", "good night",
            "how are you", "what's your name", "who are you",
            "are you okay", "you there", "can you hear me"
        ]
        if any(pattern in msg_lower for pattern in simple_patterns):
            if len(msg_lower.split()) <= 4:
                return ThinkingLevel.NONE
        
        # Check patterns from most complex to least
        for level, patterns in self.THINKING_PATTERNS.items():
            if any(pattern in msg_lower for pattern in patterns):
                return level
        
        # Default based on message length and complexity
        word_count = len(msg_lower.split())
        if word_count <= 5:
            return ThinkingLevel.NONE
        elif word_count <= 10:
            return ThinkingLevel.BRIEF
        else:
            return ThinkingLevel.CONSIDERING
    
    def get_fillers_for_level(self, level: ThinkingLevel) -> List[FillerType]:
        """Get appropriate filler sounds for a thinking level."""
        filler_map = {
            ThinkingLevel.NONE: [],
            ThinkingLevel.BRIEF: [
                FillerType.UMM, FillerType.UH
            ],
            ThinkingLevel.CONSIDERING: [
                FillerType.HMM, FillerType.WELL, FillerType.LET_ME_SEE,
                FillerType.BREATH_SOFT
            ],
            ThinkingLevel.THINKING: [
                FillerType.HMM, FillerType.LET_ME_THINK,
                FillerType.BREATH_THINKING
            ],
            ThinkingLevel.DEEP_THOUGHT: [
                FillerType.LET_ME_THINK, FillerType.BREATH_THINKING,
                FillerType.HMM
            ],
            ThinkingLevel.EMOTIONAL_PAUSE: [
                FillerType.EMOTIONAL_BREATH, FillerType.SIGH_SOFT,
                FillerType.HESITATION
            ]
        }
        return filler_map.get(level, [])
    
    def select_filler(self, level: ThinkingLevel, emotion: str = "neutral") -> Optional[Tuple[str, float]]:
        """Select appropriate filler sound and duration.
        
        Returns: (filepath, duration_seconds) or None
        """
        if level == ThinkingLevel.NONE:
            return None
        
        # Get candidate fillers
        candidates = self.get_fillers_for_level(level)
        if not candidates:
            return None
        
        # Character-specific adjustments for Sara
        if self.character_name.lower() == "sara":
            # Sara is more hesitant and nervous
            if level == ThinkingLevel.BRIEF:
                # Chance to add hesitation even for brief pauses
                if random.random() < 0.3:  # 30% chance
                    candidates.append(FillerType.HESITATION)
        
        # Select random filler from candidates
        selected = random.choice(candidates)
        filepath = os.path.join(self.audio_dir, self.filler_files[selected])
        
        # Estimate duration based on type
        duration_map = {
            FillerType.UMM: 0.4,
            FillerType.UH: 0.3,
            FillerType.WELL: 0.6,
            FillerType.LET_ME_SEE: 1.0,
            FillerType.HMM: 0.5,
            FillerType.LET_ME_THINK: 1.5,
            FillerType.BREATH_SOFT: 0.4,
            FillerType.BREATH_THINKING: 0.8,
            FillerType.SIGH_SOFT: 0.7,
            FillerType.HESITATION: 0.6,
            FillerType.EMOTIONAL_BREATH: 1.2,
        }
        
        duration = duration_map.get(selected, 0.5)
        
        # Add slight randomness to make it more natural
        duration *= random.uniform(0.9, 1.1)
        
        return (filepath, duration)
    
    def should_add_breathing(self, 
                            emotion: str = "neutral",
                            sentence_length: int = 0,
                            last_was_emotional: bool = False) -> bool:
        """Determine if a breathing sound should be added between sentences."""
        
        # Always breathe before long sentences
        if sentence_length > 15:
            return True
        
        # Breathe after emotional moments
        if last_was_emotional:
            return random.random() < 0.6  # 60% chance
        
        # Emotion-based breathing
        breathing_emotions = ["sad", "worried", "scared", "tired"]
        if emotion in breathing_emotions:
            return random.random() < 0.4  # 40% chance
        
        # Random natural breathing
        return random.random() < 0.15  # 15% chance for natural flow
    
    def get_breathing_sound(self, emotion: str = "neutral") -> Optional[str]:
        """Get appropriate breathing sound based on emotion."""
        
        emotional_breaths = ["sad", "worried", "scared"]
        
        if emotion in emotional_breaths:
            # Use emotional breath
            return os.path.join(self.audio_dir, self.filler_files[FillerType.EMOTIONAL_BREATH])
        else:
            # Use soft breath
            return os.path.join(self.audio_dir, self.filler_files[FillerType.BREATH_SOFT])
    
    def calculate_natural_pause(self,
                               thinking_level: ThinkingLevel,
                               emotion: str = "neutral",
                               trailed_off: bool = False) -> float:
        """Calculate natural pause duration in seconds."""
        
        base_durations = {
            ThinkingLevel.NONE: 0.0,
            ThinkingLevel.BRIEF: 0.4,
            ThinkingLevel.CONSIDERING: 1.0,
            ThinkingLevel.THINKING: 2.0,
            ThinkingLevel.DEEP_THOUGHT: 3.5,
            ThinkingLevel.EMOTIONAL_PAUSE: 2.5,
        }
        
        duration = base_durations.get(thinking_level, 0.5)
        
        # Adjust for emotion
        if emotion in ["sad", "worried"]:
            duration *= 1.2  # Slower response
        elif emotion == "happy":
            duration *= 0.9  # Quicker response
        
        # Adjust for trail-off
        if trailed_off:
            duration *= 0.7  # Respond quicker if user trailed off
        
        # Add natural variation
        duration *= random.uniform(0.85, 1.15)
        
        return max(0.0, duration)
    
    def update_context(self, emotion: str, turn_count: int):
        """Update conversation context for better flow tracking."""
        self.context.turn_count = turn_count
        self.context.recent_emotions.append(emotion)
        
        # Keep only last 5 emotions
        if len(self.context.recent_emotions) > 5:
            self.context.recent_emotions.pop(0)
        
        # Check if recent conversation has been emotional
        emotional_states = ["sad", "worried", "scared", "angry"]
        recent_emotional_count = sum(
            1 for e in self.context.recent_emotions[-3:] if e in emotional_states
        )
        self.context.last_topic_emotional = recent_emotional_count >= 2


# Global instance
_engine = None

def get_conversation_engine(character_name: str = "Sara") -> NaturalConversationEngine:
    """Get or create the global conversation engine."""
    global _engine
    if _engine is None:
        _engine = NaturalConversationEngine(character_name=character_name)
    return _engine
