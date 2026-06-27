"""Generate natural filler audio files for conversation system.

This script creates all the natural filler sounds needed for
human-like conversation flow using Edge-TTS.
"""

import asyncio
import edge_tts
import os


# Output directory
OUTPUT_DIR = "static/audio"

# Filler definitions: (filename, text, voice, rate, pitch, volume)
FILLERS = [
    # Basic fillers
    ("umm.mp3", "umm", "en-US-AnaNeural", "+0%", "+2Hz", "-5%"),
    ("uh.mp3", "uh", "en-US-AnaNeural", "-5%", "+1Hz", "-8%"),
    ("well.mp3", "well", "en-US-AnaNeural", "-10%", "+0Hz", "-3%"),
    ("let_me_see.mp3", "let me see", "en-US-AnaNeural", "-8%", "+1Hz", "+0%"),
    
    # Thinking sounds (hmm, let_me_think already exist, we'll keep them)
    # ("hmm.mp3", "hmm", "en-US-AnaNeural", "-5%", "-2Hz", "-5%"),
    # ("let_me_think.mp3", "let me think", "en-US-AnaNeural", "-10%", "-1Hz", "+0%"),
    
    # Breathing sounds (using soft non-verbal sounds)
    ("breath_soft.mp3", "hh", "en-US-AnaNeural", "-20%", "-8Hz", "-15%"),
    ("breath_thinking.mp3", "hh", "en-US-AnaNeural", "-15%", "-6Hz", "-10%"),
    
    # Emotional sounds
    ("sigh_soft.mp3", "ahh", "en-US-AnaNeural", "-20%", "-10Hz", "-12%"),
    ("hesitation.mp3", "um... uh", "en-US-AnaNeural", "-15%", "+1Hz", "-10%"),
    ("emotional_breath.mp3", "hh", "en-US-AnaNeural", "-25%", "-10Hz", "-18%"),
]


async def generate_filler(filename, text, voice, rate, pitch, volume):
    """Generate a single filler audio file."""
    output_path = os.path.join(OUTPUT_DIR, filename)
    
    # Skip if file already exists
    if os.path.exists(output_path):
        print(f"[OK] {filename} already exists, skipping...")
        return
    
    print(f"Generating {filename}...")
    
    try:
        communicate = edge_tts.Communicate(
            text,
            voice,
            rate=rate,
            pitch=pitch,
            volume=volume
        )
        
        audio_data = b""
        async for chunk in communicate.stream():
            if chunk["type"] == "audio":
                audio_data += chunk["data"]
        
        # Write to file
        with open(output_path, "wb") as f:
            f.write(audio_data)
        
        print(f"[OK] Generated {filename} ({len(audio_data)} bytes)")
        
    except Exception as e:
        print(f"[FAIL] Failed to generate {filename}: {e}")


async def main():
    """Generate all filler audio files."""
    print("=" * 60)
    print("Natural Conversation Filler Generator")
    print("=" * 60)
    
    # Ensure output directory exists
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    
    # Generate all fillers
    tasks = []
    for filler_def in FILLERS:
        tasks.append(generate_filler(*filler_def))
    
    await asyncio.gather(*tasks)
    
    print("=" * 60)
    print("[OK] Filler generation complete!")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
