"""Uvicorn loop factory: Windows asyncpg requires a Proactor loop here."""

import asyncio
import sys


def create_event_loop() -> asyncio.AbstractEventLoop:
    # This Windows/Python runtime stalls asyncpg writes on Selector. The main
    # entry point also disables Windows reload/workers: inherited listening
    # sockets can fail IOCP registration after reload on Proactor.
    if sys.platform == "win32":
        return asyncio.ProactorEventLoop()
    return asyncio.new_event_loop()
