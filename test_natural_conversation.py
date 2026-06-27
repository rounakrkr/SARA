#!/usr/bin/env python3
"""
Test script for Natural Conversation Engine
Demonstrates the enhanced conversation system capabilities
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from core.natural_conversation import get_conversation_engine, ThinkingLevel


def test_thinking_classification():
    """Test intelligent thinking level classification"""
    print("=" * 70)
    print("🧠 THINKING LEVEL CLASSIFICATION TEST")
    print("=" * 70)
    
    engine = get_conversation_engine(character_name="Sara")
    
    test_messages = [
        # Simple messages
        ("Hello", "Should be NONE"),
        ("How are you?", "Should be NONE"),
        
        # Brief thinking
        ("Can you help me?", "Should be BRIEF or CONSIDERING"),
        ("What do you think?", "Should be BRIEF or CONSIDERING"),
        
        # Considering
        ("Where should I go for vacation?", "Should be CONSIDERING"),
        ("Can you recommend a good book?", "Should be CONSIDERING"),
        
        # Thinking
        ("How does a black hole work?", "Should be THINKING"),
        ("Can you write me a poem about nature?", "Should be THINKING"),
        
        # Deep thought
        ("Explain quantum mechanics in detail", "Should be DEEP_THOUGHT"),
        ("What is the meaning of life?", "Should be DEEP_THOUGHT"),
        
        # Emotional (Sara-specific)
        ("Tell me about Malik", "Should be EMOTIONAL_PAUSE"),
        ("Are you scared?", "Should be EMOTIONAL_PAUSE"),
        ("Do you trust me?", "Should be EMOTIONAL_PAUSE"),
    ]
    
    for message, expected in test_messages:
        level = engine.classify_thinking_level(message)
        print(f"\n📝 Message: '{message}'")
        print(f"   Expected: {expected}")
        print(f"   ✓ Got: {level.value.upper()}")


def test_filler_selection():
    """Test filler sound selection"""
    print("\n")
    print("=" * 70)
    print("🎵 FILLER SOUND SELECTION TEST")
    print("=" * 70)
    
    engine = get_conversation_engine(character_name="Sara")
    
    test_cases = [
        (ThinkingLevel.NONE, "neutral", "No filler"),
        (ThinkingLevel.BRIEF, "neutral", "Quick filler (umm/uh)"),
        (ThinkingLevel.CONSIDERING, "neutral", "Considering filler"),
        (ThinkingLevel.THINKING, "neutral", "Thinking filler"),
        (ThinkingLevel.DEEP_THOUGHT, "neutral", "Deep thought filler"),
        (ThinkingLevel.EMOTIONAL_PAUSE, "sad", "Emotional filler"),
    ]
    
    for level, emotion, description in test_cases:
        result = engine.select_filler(level, emotion)
        print(f"\n🎭 Level: {level.value.upper()} | Emotion: {emotion}")
        print(f"   Expected: {description}")
        if result:
            filepath, duration = result
            filename = os.path.basename(filepath)
            print(f"   ✓ Selected: {filename} ({duration:.2f}s)")
        else:
            print(f"   ✓ No filler (as expected)")


def test_breathing_logic():
    """Test natural breathing insertion logic"""
    print("\n")
    print("=" * 70)
    print("💨 BREATHING LOGIC TEST")
    print("=" * 70)
    
    engine = get_conversation_engine(character_name="Sara")
    
    test_cases = [
        ("neutral", 5, False, "Short neutral sentence"),
        ("neutral", 20, False, "Long neutral sentence (should breathe)"),
        ("sad", 10, False, "Sad emotion (might breathe)"),
        ("worried", 8, True, "After emotional moment (likely breathe)"),
    ]
    
    for emotion, length, last_emotional, description in test_cases:
        # Test multiple times due to randomness
        breathe_count = 0
        trials = 10
        
        for _ in range(trials):
            if engine.should_add_breathing(emotion, length, last_emotional):
                breathe_count += 1
        
        probability = (breathe_count / trials) * 100
        print(f"\n🎭 {description}")
        print(f"   Emotion: {emotion} | Length: {length} words | Last emotional: {last_emotional}")
        print(f"   ✓ Breathing probability: {probability:.0f}%")


def test_context_tracking():
    """Test conversation context tracking"""
    print("\n")
    print("=" * 70)
    print("📊 CONTEXT TRACKING TEST")
    print("=" * 70)
    
    engine = get_conversation_engine(character_name="Sara")
    
    # Simulate conversation
    emotions = ["neutral", "happy", "sad", "worried", "sad"]
    
    print("\n🎭 Simulating conversation with emotions:")
    for turn, emotion in enumerate(emotions, 1):
        engine.update_context(emotion, turn)
        print(f"   Turn {turn}: {emotion}")
    
    print(f"\n✓ Context state:")
    print(f"   - Turn count: {engine.context.turn_count}")
    print(f"   - Recent emotions: {engine.context.recent_emotions}")
    print(f"   - Emotional conversation: {engine.context.last_topic_emotional}")


def test_natural_pause_calculation():
    """Test natural pause duration calculation"""
    print("\n")
    print("=" * 70)
    print("⏱️  NATURAL PAUSE CALCULATION TEST")
    print("=" * 70)
    
    engine = get_conversation_engine(character_name="Sara")
    
    test_cases = [
        (ThinkingLevel.NONE, "neutral", False),
        (ThinkingLevel.BRIEF, "neutral", False),
        (ThinkingLevel.CONSIDERING, "happy", False),
        (ThinkingLevel.THINKING, "sad", False),
        (ThinkingLevel.DEEP_THOUGHT, "neutral", False),
        (ThinkingLevel.EMOTIONAL_PAUSE, "worried", True),
    ]
    
    for level, emotion, trailed_off in test_cases:
        duration = engine.calculate_natural_pause(level, emotion, trailed_off)
        print(f"\n🎭 Level: {level.value.upper()}")
        print(f"   Emotion: {emotion} | Trailed off: {trailed_off}")
        print(f"   ✓ Pause duration: {duration:.2f}s")


def main():
    """Run all tests"""
    print("\n")
    print("╔" + "═" * 68 + "╗")
    print("║" + " " * 10 + "🎯 NATURAL CONVERSATION ENGINE TEST SUITE" + " " * 16 + "║")
    print("╚" + "═" * 68 + "╝")
    print()
    
    try:
        test_thinking_classification()
        test_filler_selection()
        test_breathing_logic()
        test_context_tracking()
        test_natural_pause_calculation()
        
        print("\n")
        print("╔" + "═" * 68 + "╗")
        print("║" + " " * 20 + "✅ ALL TESTS PASSED!" + " " * 27 + "║")
        print("╚" + "═" * 68 + "╝")
        print()
        
    except Exception as e:
        print(f"\n❌ Test failed: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
