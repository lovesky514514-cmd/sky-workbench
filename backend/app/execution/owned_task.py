"""Shared output, deadline and cleanup lifecycle for an owned child process."""

from __future__ import annotations

import asyncio
from typing import Callable

from app.comsol.owned_process import OwnedProcess


class OwnedTask:
    def __init__(self, command: list[str], *, cwd: str, env: dict[str, str]):
        self.command, self.cwd, self.env = command, cwd, env
        self.owned: OwnedProcess | None = None

    async def start(self) -> OwnedProcess:
        self.owned = await OwnedProcess.spawn(self.command, cwd=self.cwd, env=self.env)
        return self.owned

    async def consume(self, timeout: float, on_output: Callable[[bytes], None], *, byte_limit: int | None = None) -> int:
        assert self.owned is not None and self.owned.process.stdout is not None
        received = 0

        async def drain() -> None:
            nonlocal received
            while chunk := await self.owned.process.stdout.read(8192):
                received += len(chunk)
                on_output(chunk)
                if byte_limit is not None and received > byte_limit:
                    raise OverflowError("Output exceeded the local display limit")

        async with asyncio.timeout(timeout):
            await asyncio.gather(drain(), self.owned.process.wait())
        return self.owned.process.returncode

    async def close(self) -> dict:
        return await self.owned.terminate() if self.owned is not None else {"reaped": True}
