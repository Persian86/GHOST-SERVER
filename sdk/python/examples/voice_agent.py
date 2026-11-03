"""
A Ghost voice agent: its own Gemini Live session (mic in, voice out), with the
home server supplying the instruction, memory and tools. This is the template
to grow real agents from — wake word, avatar, UI etc. go on top.

    pip install httpx websockets google-genai sounddevice numpy
    python voice_agent.py http://192.168.80.244:8780

Wear headphones for this example — it has no echo cancellation (Ghost
Desktop's EchoGuard is the piece to bring over for speakers).
"""
import asyncio
import platform
import sys

import numpy as np
import sounddevice as sd

sys.path.insert(0, __file__.rsplit("examples", 1)[0])
from ghost_agent import GhostAgent  # noqa: E402

IN_RATE, OUT_RATE, BLOCK = 16000, 24000, 1024
server = sys.argv[1] if len(sys.argv) > 1 else "http://192.168.80.244:8780"
agent = GhostAgent(server, name=platform.node(), kind="desktop")
agent.pair_if_needed()


@agent.capability("set_volume", "Set this device's speaker volume (0-100) for Ghost's voice",
                  {"type": "OBJECT", "properties": {"level": {"type": "INTEGER"}}, "required": ["level"]})
def set_volume(args):
    global gain
    gain = max(0, min(100, int(args["level"]))) / 100
    return f"volume {int(gain * 100)}%"


gain = 1.0


async def converse():
    from google.genai import types
    client, model, cfg = agent.live_setup()
    mic_q: asyncio.Queue = asyncio.Queue(maxsize=100)
    loop = asyncio.get_running_loop()
    speaker = sd.RawOutputStream(samplerate=OUT_RATE, channels=1, dtype="int16")
    speaker.start()

    def on_mic(indata, frames, t, status):
        loop.call_soon_threadsafe(lambda: mic_q.full() or mic_q.put_nowait(bytes(indata)))

    async with client.aio.live.connect(model=model, config=cfg) as session:
        # The server can make this device speak (announcements, finished tasks).
        async def say(text):
            await session.send_client_content(turns={"role": "user", "parts": [{"text":
                f"[MESSAGE FROM THE HOME SERVER — say it to the user naturally] {text}"}]}, turn_complete=True)
        agent.on_say = say

        async def send_mic():
            while True:
                data = await mic_q.get()
                await session.send_realtime_input(audio=types.Blob(data=data, mime_type="audio/pcm;rate=16000"))

        async def receive():
            heard, said = [], []
            while True:
                async for msg in session.receive():
                    if msg.data:
                        pcm = np.frombuffer(msg.data, dtype=np.int16)
                        speaker.write((pcm * gain).astype(np.int16).tobytes())
                    sc = msg.server_content
                    if sc and sc.input_transcription and sc.input_transcription.text:
                        heard.append(sc.input_transcription.text)
                    if sc and sc.output_transcription and sc.output_transcription.text:
                        said.append(sc.output_transcription.text)
                    if msg.tool_call:
                        await agent.answer_tool_calls(session, msg.tool_call)
                    if sc and sc.turn_complete and (heard or said):
                        turns = ([{"role": "user", "text": "".join(heard)}] if heard else []) + \
                                ([{"role": "assistant", "text": "".join(said)}] if said else [])
                        asyncio.create_task(asyncio.to_thread(agent.log_turns, turns))
                        heard, said = [], []

        with sd.RawInputStream(samplerate=IN_RATE, channels=1, dtype="int16", blocksize=BLOCK, callback=on_mic):
            print("Listening — talk to Ghost. Ctrl+C to quit.")
            await asyncio.gather(send_mic(), receive())


async def main():
    stop = asyncio.Event()
    asyncio.create_task(agent.run_link(stop))
    while True:                         # a new Live session (and fresh memory/time) on every drop
        try:
            await converse()
        except Exception as e:
            print(f"Session ended ({e}); reconnecting…")
            await asyncio.sleep(2)


try:
    asyncio.run(main())
except KeyboardInterrupt:
    pass
