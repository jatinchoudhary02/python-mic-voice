#!/usr/bin/env python3
"""
Azure OpenAI Real-time Voice Chat
A Python script for real-time voice conversation with Azure OpenAI
"""

import asyncio
import websockets
import json
import pyaudio
import base64
import threading
import queue
import signal
import sys
import time
from typing import Optional

# Azure OpenAI Configuration
AZURE_RESOURCE = "azureopenairealtimetest"
DEPLOYMENT_NAME = "gpt-4o-mini-realtime-preview"
API_VERSION = "2024-10-01-preview"
AZURE_API_KEY = "GGbHkX318jbOiTF4ZelajbdNxHZPFR8ASSfo4lZCP8lhxzHLrxIgJQQJ99BHACHYHv6XJ3w3AAABACOGEizt"

REALTIME_URL = f"wss://{AZURE_RESOURCE}.openai.azure.com/openai/realtime?deployment={DEPLOYMENT_NAME}&api-version={API_VERSION}"

# Audio Configuration
SAMPLE_RATE = 16000
CHANNELS = 1
CHUNK_SIZE = 512  # reduced for lower latency
FORMAT = pyaudio.paInt16

class VoiceChatClient:
    def __init__(self):
        self.websocket: Optional[websockets.WebSocketServerProtocol] = None
        self.audio = pyaudio.PyAudio()
        self.input_stream: Optional[pyaudio.Stream] = None
        self.output_stream: Optional[pyaudio.Stream] = None
        
        # Queues for audio data
        self.audio_input_queue = queue.Queue()
        self.audio_output_queue = queue.Queue()
        
        # Control flags
        self.is_recording = False
        self.is_playing = False
        self.is_connected = False
        self.is_assistant_speaking = False
        self.session_started = False
        
        # Threads
        self.audio_input_thread: Optional[threading.Thread] = None
        self.audio_output_thread: Optional[threading.Thread] = None
        
        # Current conversation state
        self.current_user_transcript = ""
        self.current_assistant_response = ""
        
        # Event loop (set in run)
        self.loop: Optional[asyncio.AbstractEventLoop] = None

        print("🎤 Azure OpenAI Voice Chat Client Initialized")
        print("Press 'q' to quit, 's' to start/stop recording, 'i' to interrupt")

    async def connect(self):
        """Connect to Azure OpenAI Realtime API"""
        try:
            print("🔗 Connecting to Azure OpenAI...")
            
            headers = {
                "api-key": AZURE_API_KEY,
                "OpenAI-Beta": "realtime=v1"
            }
            
            self.websocket = await websockets.connect(
                REALTIME_URL,
                additional_headers=headers,
                subprotocols=["realtime"]
            )
            
            self.is_connected = True
            print("✅ Connected to Azure OpenAI Realtime API")
            
            # Initialize session
            await self.initialize_session()
            
            # Start audio streams
            self.start_audio_streams()
            
            return True
            
        except Exception as e:
            print(f"❌ Connection failed: {e}")
            return False

    async def initialize_session(self):
        """Initialize the conversation session"""
        session_config = {
            "type": "session.update",
            "session": {
                "modalities": ["text", "audio"],
                "instructions": "You are a helpful AI assistant. Respond naturally and conversationally. Keep responses concise but informative.",
                "voice": "alloy",
                "input_audio_format": "pcm16",
                "output_audio_format": "pcm16",
                "input_audio_transcription": {
                    "model": "whisper-1"
                },
                "turn_detection": {
                    "type": "server_vad",
                    "threshold": 0.5,
                    "prefix_padding_ms": 300,
                    "silence_duration_ms": 500
                },
                "tools": [],
                "tool_choice": "auto",
                "temperature": 0.8,
                "max_response_output_tokens": 4096
            }
        }
        
        await self.websocket.send(json.dumps(session_config))
        print("🔧 Session configuration sent")

    def start_audio_streams(self):
        """Start audio input and output streams"""
        try:
            # Input stream for recording
            self.input_stream = self.audio.open(
                format=FORMAT,
                channels=CHANNELS,
                rate=SAMPLE_RATE,
                input=True,
                frames_per_buffer=CHUNK_SIZE,
                stream_callback=self.audio_input_callback
            )
            
            # Output stream for playback
            self.output_stream = self.audio.open(
                format=FORMAT,
                channels=CHANNELS,
                rate=SAMPLE_RATE,
                output=True,
                frames_per_buffer=CHUNK_SIZE,
                stream_callback=self.audio_output_callback
            )
            
            # Start audio threads
            self.audio_input_thread = threading.Thread(target=self.process_audio_input, daemon=True)
            self.audio_output_thread = threading.Thread(target=self.process_audio_output, daemon=True)
            
            self.audio_input_thread.start()
            self.audio_output_thread.start()
            
            print("🔊 Audio streams started")
            
        except Exception as e:
            print(f"❌ Failed to start audio streams: {e}")

    def audio_input_callback(self, in_data, frame_count, time_info, status):
        """Callback for audio input"""
        if self.is_recording:
            self.audio_input_queue.put(in_data)
        return (None, pyaudio.paContinue)

    def audio_output_callback(self, in_data, frame_count, time_info, status):
        """Callback for audio output"""
        try:
            data = self.audio_output_queue.get_nowait()
            return (data, pyaudio.paContinue)
        except queue.Empty:
            return (b'\x00' * frame_count * CHANNELS * 2, pyaudio.paContinue)

    def process_audio_input(self):
        """Process audio input in a separate thread"""
        while True:
            try:
                if not self.audio_input_queue.empty() and self.is_recording and self.websocket:
                    audio_data = self.audio_input_queue.get()
                    if self.loop:
                        asyncio.run_coroutine_threadsafe(
                            self.send_audio(audio_data),
                            self.loop
                        )
                time.sleep(0.005)  # very short delay to reduce lag
            except Exception as e:
                print(f"❌ Audio input processing error: {e}")

    def process_audio_output(self):
        """Process audio output in a separate thread"""
        while True:
            time.sleep(0.005)

    async def send_audio(self, audio_data: bytes):
        """Send audio data to Azure OpenAI"""
        try:
            audio_base64 = base64.b64encode(audio_data).decode('utf-8')
            event = {"type": "input_audio_buffer.append", "audio": audio_base64}
            await self.websocket.send(json.dumps(event))
        except Exception as e:
            print(f"❌ Failed to send audio: {e}")

    async def commit_audio(self):
        """Commit the audio buffer and request a response"""
        try:
            await self.websocket.send(json.dumps({"type": "input_audio_buffer.commit"}))
            await self.websocket.send(json.dumps({
                "type": "response.create",
                "response": {
                    "modalities": ["text", "audio"],
                    "instructions": "Please respond to the user's input naturally and conversationally."
                }
            }))
        except Exception as e:
            print(f"❌ Failed to commit audio: {e}")

    async def interrupt_assistant(self):
        """Interrupt the assistant's response"""
        try:
            await self.websocket.send(json.dumps({"type": "response.cancel"}))
            self.is_assistant_speaking = False
            print("🛑 Interrupted assistant")
        except Exception as e:
            print(f"❌ Failed to interrupt: {e}")

    async def handle_message(self, message: str):
        """Handle incoming WebSocket messages"""
        try:
            data = json.loads(message)
            event_type = data.get("type")
            
            if event_type == "session.created":
                print("✅ Session created successfully")
                self.session_started = True
                
            elif event_type == "input_audio_buffer.speech_started":
                print("🎤 User started speaking")
                if self.is_assistant_speaking:
                    await self.interrupt_assistant()
                    
            elif event_type == "input_audio_buffer.speech_stopped":
                print("🤐 User stopped speaking")
                
            elif event_type == "conversation.item.input_audio_transcription.completed":
                transcript = data.get("transcript", "")
                if transcript:
                    print(f"👤 User: {transcript}")
                    
            elif event_type == "response.created":
                print("🤖 Assistant response created")
                self.is_assistant_speaking = True
                self.current_assistant_response = ""
                
            elif event_type == "response.audio.delta":
                audio_delta = data.get("delta", "")
                if audio_delta:
                    try:
                        audio_bytes = base64.b64decode(audio_delta)
                        self.audio_output_queue.put(audio_bytes)
                    except Exception as e:
                        print(f"❌ Failed to decode audio: {e}")
                        
            elif event_type == "response.audio_transcript.delta":
                delta = data.get("delta", "")
                if delta:
                    self.current_assistant_response += delta
                    
            elif event_type == "response.done":
                print(f"🤖 Assistant: {self.current_assistant_response}")
                self.is_assistant_speaking = False
                self.current_assistant_response = ""
                
            elif event_type == "error":
                print(f"❌ Azure OpenAI Error: {data.get('error', {}).get('message', 'Unknown error')}")
                
        except Exception as e:
            print(f"❌ Failed to handle message: {e}")

    def start_recording(self):
        if not self.is_connected:
            print("❌ Not connected to Azure OpenAI")
            return
        self.is_recording = True
        print("🔴 Recording started - speak now...")

    def stop_recording(self):
        if not self.is_recording:
            return
        self.is_recording = False
        print("⏹️ Recording stopped")
        if self.websocket:
            asyncio.run_coroutine_threadsafe(self.commit_audio(), self.loop)

    async def listen(self):
        try:
            async for message in self.websocket:
                await self.handle_message(message)
        except websockets.exceptions.ConnectionClosed:
            print("🔌 Connection closed")
            self.is_connected = False
        except Exception as e:
            print(f"❌ Listen error: {e}")

    def cleanup(self):
        print("\n🧹 Cleaning up...")
        self.is_recording = False
        self.is_connected = False
        if self.input_stream:
            self.input_stream.stop_stream()
            self.input_stream.close()
        if self.output_stream:
            self.output_stream.stop_stream()
            self.output_stream.close()
        self.audio.terminate()
        print("✅ Cleanup completed")

    async def run(self):
        self.loop = asyncio.get_running_loop()  # store loop for threads
        if not await self.connect():
            return
        listen_task = asyncio.create_task(self.listen())
        input_task = asyncio.create_task(self.handle_keyboard_input())
        try:
            await asyncio.wait([listen_task, input_task], return_when=asyncio.FIRST_COMPLETED)
        except KeyboardInterrupt:
            print("\n🛑 Interrupted by user")
        finally:
            if self.websocket:
                await self.websocket.close()
            self.cleanup()

    async def handle_keyboard_input(self):
        import aioconsole
        print("\n📝 Controls:\n  's' - Start/Stop recording\n  'i' - Interrupt assistant\n  'q' - Quit\n  Enter - Toggle recording\n")
        while True:
            key = await aioconsole.ainput()
            if key.lower() == 'q':
                print("👋 Goodbye!")
                break
            elif key.lower() == 's' or key == '':
                if self.is_recording:
                    self.stop_recording()
                else:
                    self.start_recording()
            elif key.lower() == 'i':
                if self.is_assistant_speaking:
                    await self.interrupt_assistant()
                else:
                    print("ℹ️ Assistant is not currently speaking")

def signal_handler(sig, frame):
    print("\n🛑 Shutting down...")
    sys.exit(0)

async def main():
    signal.signal(signal.SIGINT, signal_handler)
    print("🚀 Starting Azure OpenAI Voice Chat\n" + "=" * 50)
    client = VoiceChatClient()
    await client.run()

if __name__ == "__main__":
    try:
        import websockets
        import pyaudio
        import aioconsole
    except ImportError as e:
        print(f"❌ Missing dependency: {e}")
        print("pip install websockets pyaudio aioconsole")
        sys.exit(1)
    asyncio.run(main())
