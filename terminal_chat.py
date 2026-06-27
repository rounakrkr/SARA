import httpx

def chat_loop():
    print("="*60)
    print("                 SARA - TERMINAL CHAT")
    print("  (Type 'exit' to stop. Make sure run.bat is running first!)")
    print("="*60)
    
    session_id = "terminal_user_1"
    
    try:
        # Check if server is running
        with httpx.Client(timeout=3.0) as client:
            try:
                client.get("http://127.0.0.1:8000/docs")
            except httpx.ConnectError:
                print("\n[!] Error: SARA's server is not running!")
                print("Please double-click 'run.bat' FIRST to wake up her backend.")
                input("\nPress Enter to exit...")
                return

        # Start chat loop
        with httpx.Client(timeout=30.0) as client:
            while True:
                user_msg = input("\n[You]: ")
                if user_msg.strip().lower() in ['exit', 'quit']:
                    print("[System]: Disconnecting SARA...")
                    break
                    
                if not user_msg.strip():
                    continue

                payload = {"message": user_msg, "session_id": session_id}
                
                try:
                    resp = client.post("http://127.0.0.1:8000/api/chat?llm_provider=groq&tts_provider=mock", json=payload)
                    if resp.status_code == 200:
                        data = resp.json()
                        text = data.get("text", "")
                        emotion = data.get("emotion", "neutral")
                        
                        # Print response with the dynamically generated emotion
                        print(f"\n[SARA (Emotion: {emotion.upper()})]: {text}")
                    else:
                        print(f"\n[System Error]: Code {resp.status_code} - {resp.text}")
                except Exception as e:
                    print(f"\n[System Error]: Connection lost. Is run.bat still running? ({e})")
                    
    except KeyboardInterrupt:
        print("\n[System]: Chat closed.")

if __name__ == "__main__":
    chat_loop()
