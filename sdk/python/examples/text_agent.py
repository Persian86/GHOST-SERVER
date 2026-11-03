"""
The smallest useful Ghost agent: type to Ghost, and let the house use this
machine. Run:  python text_agent.py http://192.168.80.244:8780
"""
import asyncio
import platform
import subprocess
import sys
import webbrowser

sys.path.insert(0, __file__.rsplit("examples", 1)[0])
from ghost_agent import GhostAgent  # noqa: E402

server = sys.argv[1] if len(sys.argv) > 1 else "http://192.168.80.244:8780"
agent = GhostAgent(server, name=f"{platform.node()} (text)", kind="desktop")
agent.pair_if_needed()


@agent.capability("open_url", "Open a web page on this computer",
                  {"type": "OBJECT", "properties": {"url": {"type": "STRING"}}, "required": ["url"]})
def open_url(args):
    webbrowser.open(args["url"])
    return f"opened {args['url']}"


@agent.capability("show_message", "Show a message on this computer's screen",
                  {"type": "OBJECT", "properties": {"text": {"type": "STRING"}}, "required": ["text"]})
def show_message(args):
    print(f"\n📣  {args['text']}\n> ", end="", flush=True)
    return "shown"


@agent.capability("lock_screen", "Lock this computer")
def lock_screen(args):
    if platform.system() == "Windows":
        subprocess.run(["rundll32.exe", "user32.dll,LockWorkStation"])
        return "locked"
    return "locking is only wired up on Windows in this example"


agent.on_say = lambda text: print(f"\n👻  {text}\n> ", end="", flush=True)


async def main():
    stop = asyncio.Event()
    link = asyncio.create_task(agent.run_link(stop))
    print(f"Connected to {server} as '{agent.name}'. Type to talk, Ctrl+C to quit.")
    while True:
        text = await asyncio.to_thread(input, "> ")
        if not text.strip():
            continue
        reply = await asyncio.to_thread(agent.chat, text)
        print(f"👻  {reply['text']}" + (f"   [{', '.join(reply['tools'])}]" if reply["tools"] else ""))


try:
    asyncio.run(main())
except KeyboardInterrupt:
    pass
