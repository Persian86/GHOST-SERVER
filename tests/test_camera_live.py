"""Latency regressions using synthetic JPEG packets; no household feeds required."""
import asyncio
import importlib.util
import struct
from pathlib import Path

spec = importlib.util.spec_from_file_location('camera_live_test', Path(__file__).parents[1] / 'ghost_server/camera_live.py')
live = importlib.util.module_from_spec(spec)
spec.loader.exec_module(live)


def jpeg(n):
    return b'\xff\xd8' + bytes([n]) * 10 + b'\xff\xd9'


def unpack(packet):
    index, length = struct.unpack('!II', packet[:8])
    assert length == len(packet) - 8
    return index, packet[8:]


def test_jpeg_fragmentation_and_multiple_frames():
    parser = live.Jpegs()
    assert parser.feed(b'--boundary\r\nContent-Type: image/jpeg\r\n\r\n\xff') == []
    assert parser.feed(jpeg(1)[1:] + b'\r\n--boundary\r\n' + jpeg(2)) == [jpeg(1), jpeg(2)]
    assert len(parser.buffer) <= 1


def test_corrupt_frame_does_not_grow_without_bound():
    parser = live.Jpegs()
    assert parser.feed(b'\xff\xd8' + b'x' * (live.MAX_FRAME + 1)) == []
    assert len(parser.buffer) == 0
    assert parser.feed(jpeg(3)) == [jpeg(3)]


def test_slow_viewer_receives_latest_frame_and_disconnect_closes_input():
    async def check():
        release = asyncio.Event()
        drained = asyncio.Event()
        closed = []

        async def source():
            try:
                yield jpeg(1)
                await release.wait()
                for n in range(2, 30):
                    yield jpeg(n)
                    await asyncio.sleep(0)
                drained.set()
                await asyncio.Event().wait()
            finally:
                closed.append(True)

        async def opener(_):
            return 'multipart/x-mixed-replace', source()

        stream = live.wall(['front'], 10, opener)
        assert unpack(await anext(stream)) == (0, jpeg(1))
        release.set()
        await drained.wait()  # Client has not consumed any of the intervening frames.
        assert unpack(await anext(stream)) == (0, jpeg(29))
        await stream.aclose()
        assert closed == [True]
    asyncio.run(check())


def test_all_seven_cameras_share_one_output_and_expiry_cleans_every_input():
    async def check():
        opened, closed = [], []

        async def source(cam):
            try:
                yield jpeg(cam + 1)
                await asyncio.Event().wait()
            finally:
                closed.append(cam)

        async def opener(cam):
            opened.append(cam)
            return 'multipart/x-mixed-replace', source(cam)

        seen = set()
        async for packet in live.wall(list(range(7)), .05, opener):
            index, frame = unpack(packet)
            assert frame == jpeg(index + 1)
            seen.add(index)
        assert seen == set(range(7))
        assert sorted(opened) == sorted(closed) == list(range(7))
    asyncio.run(check())


def test_unavailable_camera_does_not_block_other_feeds_or_expose_its_url():
    async def check():
        async def source():
            yield jpeg(4)
            await asyncio.Event().wait()

        async def opener(cam):
            if cam == 'bad':
                raise RuntimeError('rtsp://secret:password@camera')
            return 'multipart/x-mixed-replace', source()

        stream = live.wall(['bad', 'good'], 1, opener)
        assert unpack(await anext(stream)) == (1, jpeg(4))
        await stream.aclose()
    asyncio.run(check())
