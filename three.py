import os
import json
import websocket
import pyaudio
import threading
import base64
import signal
import sys
import time

# ==== CONFIG ====
AZURE_RESOURCE = "azureopenairealtimetest"
DEPLOYMENT_NAME = "gpt-4o-mini-realtime-preview"
API_VERSION = "2024-10-01-preview"
AZURE_API_KEY = "GGbHkX318jbOiTF4ZelajbdNxHZPFR8ASSfo4lZCP8lhxzHLrxIgJQQJ99BHACHYHv6XJ3w3AAABACOGEizt"

REALTIME_URL = (
    f"wss://{AZURE_RESOURCE}.openai.azure.com/openai/realtime"
    f"?deployment={DEPLOYMENT_NAME}&api-version={API_VERSION}"
)

# ==== AUDIO SETTINGS ====
# ==== AUDIO SETTINGS (Optimized) ====
RATE = 16000                # Sample rate: 16kHz is fine for voice recognition
CHUNK = 1024                # Number of frames per read (~64ms per chunk at 16kHz)
FORMAT = pyaudio.paInt16    # 16-bit PCM
CHANNELS = 1                # Mono mic input

# Voice Activity Detection (VAD) thresholds
SILENCE_THRESHOLD = 30000     # Lower threshold to detect normal speech; ~500 RMS is sensitive enough
SILENCE_CHUNKS = 1400   # Number of consecutive silent chunks to consider speech ended (~300ms)

MIN_AUDIO_MS = 300           # Minimum audio to commit (0.3s is enough for AI to process partial speech)
BYTES_PER_SAMPLE = 2         # 16-bit audio = 2 bytes per sample


# Global flags
playing_lock = threading.Lock()
is_playing = False

# Buffer for batching before commit
buffered_audio = bytearray()

# ==== HELPERS ====
def rms(data: bytes) -> float:
    count = len(data) // 2
    if count == 0:
        return 0.0
    shorts = memoryview(data).cast('h')
    sum_squares = sum(s * s for s in shorts)
    return (sum_squares / count) ** 0.5

def open_player_stream():
    pa = pyaudio.PyAudio()
    return pa.open(format=FORMAT, channels=CHANNELS, rate=RATE, output=True)

# ==== MIC LOOP (with barge-in) ====
def mic_loop(ws):
    pa = pyaudio.PyAudio()
    stream = pa.open(format=FORMAT, channels=CHANNELS, rate=RATE,
                     input=True, frames_per_buffer=CHUNK)
    print("🎙 Mic streaming with VAD & barge-in...")

    silent_chunks = 0
    speaking = False
    global buffered_audio

    while True:
        with playing_lock:
            currently_playing = is_playing

        data = stream.read(CHUNK, exception_on_overflow=False)
        volume = rms(data)
        buffered_audio.extend(data)

        # Barge-in: user speaks while AI is speaking
        if volume > SILENCE_THRESHOLD and currently_playing:
            print("⚡ User interrupted AI! Committing audio and stopping AI playback...")
            ws.send(json.dumps({
                "type": "input_audio_buffer.append",
                "audio": base64.b64encode(buffered_audio).decode("utf-8")
            }))
            ws.send(json.dumps({"type": "input_audio_buffer.commit"}))
            ws.send(json.dumps({"type": "response.create"}))
            buffered_audio.clear()
            set_playing(False)
            speaking = True
            silent_chunks = 0
            continue

        # Append audio normally
        ws.send(json.dumps({
            "type": "input_audio_buffer.append",
            "audio": base64.b64encode(data).decode("utf-8")
        }))

        if volume > SILENCE_THRESHOLD:
            if not speaking:
                print("🗣 Speaking detected")
            speaking = True
            silent_chunks = 0
        else:
            if speaking:
                silent_chunks += 1
                if silent_chunks > SILENCE_CHUNKS:
                    min_bytes = int((MIN_AUDIO_MS / 1000) * RATE * BYTES_PER_SAMPLE)
                    if len(buffered_audio) >= min_bytes:
                        print(f"🤫 Silence detected — committing {len(buffered_audio)} bytes (~{len(buffered_audio)/RATE/BYTES_PER_SAMPLE:.2f}s)")
                        ws.send(json.dumps({"type": "input_audio_buffer.commit"}))
                        ws.send(json.dumps({"type": "response.create"}))
                        buffered_audio.clear()
                    speaking = False
                    silent_chunks = 0

# ==== WS CALLBACKS ====
def on_open(ws):
    print("✅ Connected to Azure Realtime endpoint")
    ws.send(json.dumps({
        "type": "session.update",
        "session": {
            "input_audio_format": "pcm16",
            "input_audio_sample_rate": RATE,
            "output_audio_format": "pcm16",
            "instructions": (
                "You are a kind and gentle female AI assistant. "
                "Please speak in soft, clear, and simple Hindi "
                "that is easy to understand."
            ),
            "voice": {
                "language": "en-US",
                "name": "en-US-JennyNeural",
                "style": "cheerful",
                "role": "assistant"
            }
        }
    }))
    threading.Thread(target=mic_loop, args=(ws,), daemon=True).start()

player = open_player_stream()

def set_playing(value: bool):
    global is_playing
    with playing_lock:
        is_playing = value

AUDIO_EVENT_NAMES = {
    "response.audio.delta",
    "response.output_audio.delta",
}

def on_message(ws, message):
    global player
    try:
        msg = json.loads(message)
    except:
        return

    mtype = msg.get("type")

    if mtype == "error":
        print("❌ Azure Error:", json.dumps(msg.get("error", msg), indent=2))
        return

    if mtype in AUDIO_EVENT_NAMES or (mtype and mtype.endswith(".audio.delta")):
        set_playing(True)
        b64 = msg.get("delta") or msg.get("audio") or msg.get("data")
        if not b64:
            return
        try:
            audio_bytes = base64.b64decode(b64)
            player.write(audio_bytes)
        except:
            pass
        return

    if mtype in ("response.audio.done", "response.output_item.done", "response.done"):
        set_playing(False)
        return

    if "delta" in msg and isinstance(msg["delta"], str):
        print(f"🤖 {msg['delta']}")

def on_error(ws, error):
    print("❌ WebSocket error:", error)

def on_close(ws, code, reason):
    print(f"🔌 Connection closed: {code}, {reason}")

# ==== EXIT ====
def signal_handler(sig, frame):
    print("\n🛑 Exiting...")
    sys.exit(0)
signal.signal(signal.SIGINT, signal_handler)

# ==== MAIN ====
if __name__ == "__main__":
    headers = [
        f"Authorization: Bearer {AZURE_API_KEY}",
        f"api-key: {AZURE_API_KEY}"
    ]

    ws = websocket.WebSocketApp(
        REALTIME_URL,
        header=headers,
        on_open=on_open,
        on_message=on_message,
        on_error=on_error,
        on_close=on_close
    )

    ws.run_forever()
