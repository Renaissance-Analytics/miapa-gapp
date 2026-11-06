"""Actual finite-peer startup, separate from the RPC behavior under test."""
import asyncio
import time


async def wait_for_peer(case, marker):
    started = time.monotonic()
    while not marker.is_file() or marker.read_bytes() != b'ready':
        case.assertLess(time.monotonic() - started, 5, 'Finite peer startup unavailable')
        await asyncio.sleep(.01)
    return time.monotonic() - started
