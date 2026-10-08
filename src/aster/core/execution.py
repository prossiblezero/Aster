"""Bounded detached read execution; caller owns authorization and ordered publication."""
import asyncio
import time

from ..schema import Outcome


async def execute_reads(preparations, parallelism):
    semaphore = asyncio.Semaphore(parallelism)

    async def execute(prepared):
        async with semaphore:
            call, registry = prepared

            def read():
                started = time.monotonic()
                try:
                    outcome = asyncio.run(registry.execute(call))
                except Exception as exc:
                    outcome = Outcome(
                        status="error", reason=type(exc).__name__, content=str(exc)
                    )
                return outcome, (time.monotonic() - started) * 1000

            work = asyncio.create_task(asyncio.to_thread(read))
            try:
                return await asyncio.shield(work)
            except asyncio.CancelledError:
                # Python cannot cancel a running thread. Keep the operation gate
                # until it exits; cancelled results never enter Runtime state.
                await work
                raise

    jobs = [
        asyncio.create_task(execute(prepared))
        for prepared in preparations
    ]
    group = asyncio.gather(*jobs, return_exceptions=True)
    try:
        return await asyncio.shield(group)
    except asyncio.CancelledError:
        for job in jobs:
            job.cancel()
        while not group.done():
            try:
                await asyncio.shield(group)
            except asyncio.CancelledError:
                continue
        raise
