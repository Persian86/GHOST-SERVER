"""One browser connection for the camera wall, with a bounded latest-frame slot.

Each packet is a big-endian uint32 camera index, uint32 JPEG length, then JPEG.
No camera URLs, credentials or upstream errors are sent to the browser.
"""
from __future__ import annotations

import asyncio
import struct

MAX_FRAME = 4 * 1024 * 1024


class Jpegs:
    """Extract multipart JPEGs without forwarding upstream backlog or headers."""
    def __init__(self):
        self.buffer = bytearray()

    def feed(self, chunk):
        self.buffer.extend(chunk)
        frames = []
        while True:
            start = self.buffer.find(b'\xff\xd8')
            if start < 0:
                self.buffer[:] = self.buffer[-1:]
                break
            if start:
                del self.buffer[:start]
            end = self.buffer.find(b'\xff\xd9', 2)
            if end < 0:
                if len(self.buffer) > MAX_FRAME:
                    self.buffer.clear()
                break
            end += 2
            if end <= MAX_FRAME:
                frames.append(bytes(self.buffer[:end]))
            del self.buffer[:end]
        return frames


async def wall(cams, lifetime, opener=None, indices=None):
    """Drain inputs independently; a slow client gets latest frames, never a queue."""
    if opener is None:
        from .cameras import mjpeg
        opener = mjpeg
    pending = {}
    changed = asyncio.Event()

    async def read_camera(index, cam):
        while True:
            body = None
            try:
                _, body = await opener(cam)
                parser = Jpegs()
                async for chunk in body:
                    frames = parser.feed(chunk)
                    if frames:
                        pending[index] = frames[-1]
                        changed.set()
            except asyncio.CancelledError:
                raise
            except Exception:
                pass  # The viewer reports unavailable feeds without exposing URLs.
            finally:
                if body is not None:
                    await body.aclose()
            await asyncio.sleep(1)

    tasks = [asyncio.create_task(read_camera(i, cam)) for i, cam in enumerate(cams) if indices is None or i in indices]
    try:
        async with asyncio.timeout(max(0, lifetime)):
            while True:
                await changed.wait()
                changed.clear()
                # Remove each slot immediately before yielding, so slow sends
                # cannot keep a stale batch of frames from the other cameras.
                for i in list(pending):
                    jpeg = pending.pop(i, None)
                    if jpeg:
                        yield struct.pack('!II', i, len(jpeg)) + jpeg
    except TimeoutError:
        pass
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
