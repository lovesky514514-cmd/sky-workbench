"""Windows 侧固定参数传输与 WSL 本机能力探测。"""

from __future__ import annotations

from functools import lru_cache
import json
import os
from pathlib import Path, PurePosixPath, PureWindowsPath
import re
import shutil
import subprocess
from typing import Callable, Literal

from pydantic import Field, field_validator

from app.engines.models import EngineInput
from app.execution.owned_task import OwnedTask
from app.script_workspace import child_environment


class WslVaspProfile(EngineInput):
    distribution: str = Field(min_length=1, max_length=128)
    bin_dir: str = Field(min_length=2, max_length=1024)
    mpi_path: str = Field(default="/usr/bin/mpirun", max_length=1024)
    potential_root: str = Field(min_length=2, max_length=1024)
    variant: Literal["vasp_std", "vasp_gam", "vasp_ncl"] = "vasp_std"
    ranks: int = Field(default=2, strict=True, ge=1, le=32)

    @field_validator("distribution")
    @classmethod
    def validate_distro(cls, value):
        if value != value.strip() or value.startswith("-") or any(ord(char) < 32 for char in value):
            raise ValueError("Select a discovered WSL distribution")
        return value

    @field_validator("bin_dir", "mpi_path", "potential_root")
    @classmethod
    def validate_linux_path(cls, value):
        path = PurePosixPath(value)
        if (not path.is_absolute() or value.startswith("//") or ".." in path.parts
                or len(path.parts) < 2 or any(ord(char) < 32 for char in value)):
            raise ValueError("Use a bounded absolute Linux path")
        return str(path)


class WslIdentity(EngineInput):
    vasp_version: str = Field(pattern=r"^\d+\.\d+\.\d+$")
    mpi_version: str = Field(pattern=r"^\d+\.\d+\.\d+$")
    ubuntu_version: str = Field(pattern=r"^\d+\.\d+$")
    binary_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    mpi_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    variant: Literal["vasp_std", "vasp_gam", "vasp_ncl"]


@lru_cache(maxsize=1)
def worker_source() -> str:
    return Path(__file__).with_name("vasp_wsl_worker.py").read_text(encoding="utf-8")


def decode_wsl_output(output: bytes) -> str:
    if output.startswith((b"\xff\xfe", b"\xfe\xff")):
        return output.decode("utf-16", errors="replace")
    if b"\x00" in output:
        return output.decode("utf-16-le", errors="replace")
    return output.decode("utf-8-sig", errors="replace")


def wsl_lines(output: bytes) -> list[str]:
    # wsl.exe 的代理提示和正常 stdout 共用 OwnedTask 的输出流。
    return [line for line in decode_wsl_output(output).splitlines()
            if line.strip() and not line.lstrip().lower().startswith("wsl:")]


def wsl_executable() -> str:
    windows = os.environ.get("SystemRoot")
    native = Path(windows) / "System32" / "wsl.exe" if windows else None
    path = str(native) if native and native.is_file() else shutil.which("wsl.exe")
    if not path:
        raise ValueError("wsl_unavailable")
    return path


def json_argument(value: dict) -> str:
    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"))


class WslVaspTransport:
    def __init__(self, root: Path, launcher: Callable[[], str] = wsl_executable):
        self.root, self.launcher = root, launcher

    def command(self, profile: WslVaspProfile, *arguments: str) -> list[str]:
        return [self.launcher(), "--distribution", profile.distribution, "--exec", *arguments]

    async def capture(self, argv: list[str], *, timeout: float = 25) -> bytes:
        output = bytearray()
        self.root.mkdir(parents=True, exist_ok=True)
        owned = OwnedTask(argv, cwd=str(self.root), env={**child_environment(), "WSL_UTF8": "1"})
        try:
            await owned.start()
            code = await owned.consume(timeout, output.extend, byte_limit=512_000)
        finally:
            cleanup = await owned.close()
        if code or not cleanup.get("reaped"):
            raise ValueError("wsl_command_failed")
        return bytes(output)

    async def distributions(self) -> list[str]:
        raw = await self.capture([self.launcher(), "--list", "--quiet"])
        return list(dict.fromkeys(line.strip().lstrip("*").strip() for line in wsl_lines(raw)))

    async def convert_path(self, profile: WslVaspProfile, path: str, *, linux: bool) -> str:
        if not path or path.startswith("-") or any(ord(char) < 32 for char in path):
            raise ValueError("invalid_wsl_path")
        output = await self.capture(self.command(profile, "/usr/bin/wslpath", "-a", "-u" if linux else "-w", path))
        lines = wsl_lines(output)
        path_type = PurePosixPath if linux else PureWindowsPath
        if len(lines) != 1 or not path_type(lines[0]).is_absolute() or ".." in path_type(lines[0]).parts:
            raise ValueError("wsl_path_conversion_failed")
        return lines[0]

    def worker_command(self, profile: WslVaspProfile, operation: str, payload: dict) -> list[str]:
        command = self.command(profile, "/usr/bin/python3", "-I", "-c", worker_source(), operation,
                               json_argument(profile.model_dump()), json_argument(payload))
        if len(subprocess.list2cmdline(command)) >= 30_000:
            raise ValueError("wsl_command_too_large")
        return command

    async def control(self, profile: WslVaspProfile, operation: str, payload: dict, *, timeout=45) -> dict:
        output = await self.capture(self.worker_command(profile, operation, payload), timeout=timeout)
        replies = [line.removeprefix("SKY_VASP_JSON ") for line in decode_wsl_output(output).splitlines()
                   if line.startswith("SKY_VASP_JSON ")]
        if len(replies) != 1:
            raise ValueError("wsl_worker_no_receipt")
        result = json.loads(replies[0])
        if not isinstance(result, dict):
            raise ValueError("wsl_worker_invalid_receipt")
        if "error" in result:
            error = result["error"]
            raise ValueError(error if isinstance(error, str) and re.fullmatch(r"[a-z0-9_]{1,80}", error)
                             else "wsl_worker_failed")
        return result

    async def probe(self, profile: WslVaspProfile) -> WslIdentity:
        if profile.distribution not in await self.distributions():
            raise ValueError("wsl_distribution_not_registered")
        result = await self.control(profile, "probe", {})
        identity = WslIdentity.model_validate(result)
        if identity.variant != profile.variant:
            raise ValueError("wsl_wrong_variant_receipt")
        return identity

    async def potential_hash(self, profile: WslVaspProfile, selections: list[list[str]]) -> str:
        result = await self.control(profile, "potentials", {"selections": selections})
        digest = result.get("potential_sha256")
        if not isinstance(digest, str) or not re.fullmatch(r"[a-f0-9]{64}", digest):
            raise ValueError("wsl_invalid_potential_receipt")
        return digest

    async def cleanup(self, profile: WslVaspProfile, run_id: str, exchange: str, *, cancel: bool) -> dict:
        return await self.control(profile, "cleanup", {"run_id": run_id, "exchange": exchange, "cancel": cancel})


class WslVaspProcess:
    """沿用 OwnedTask；Windows launcher 和 Linux MPI 分别确认退出。"""

    def __init__(self, transport: WslVaspTransport, profile: WslVaspProfile, folder: Path, payload: dict):
        self.transport, self.profile, self.payload = transport, profile, payload
        self.owned = OwnedTask(transport.worker_command(profile, "run", payload), cwd=str(folder),
                               env={**child_environment(), "WSL_UTF8": "1"})
        self.linux_cleanup: dict | None = None

    async def start(self):
        return await self.owned.start()

    async def consume(self, timeout, on_output):
        code = await self.owned.consume(timeout, on_output, byte_limit=8_000_000)
        self.linux_cleanup = await self.transport.cleanup(
            self.profile, self.payload["run_id"], self.payload["exchange"], cancel=code != 0)
        if self.linux_cleanup.get("reaped") is not True:
            raise RuntimeError("linux_cleanup_unverified")
        return code

    def safe_output(self, chunk: bytes) -> bytes:
        for path in (self.profile.bin_dir, self.profile.potential_root, self.payload["exchange"]):
            chunk = chunk.replace(path.encode(), b"<local path>")
        return chunk

    async def close(self) -> dict:
        try:
            if not self.linux_cleanup or self.linux_cleanup.get("reaped") is not True:
                self.linux_cleanup = await self.transport.cleanup(
                    self.profile, self.payload["run_id"], self.payload["exchange"], cancel=True)
        except (ValueError, OSError, RuntimeError, TimeoutError, OverflowError):
            self.linux_cleanup = {"reaped": False, "omitted": []}
        finally:
            windows = await self.owned.close()
        linux = self.linux_cleanup or {}
        return {"reaped": windows.get("reaped") is True and linux.get("reaped") is True,
                "linux_reaped": linux.get("reaped") is True, "windows_reaped": windows.get("reaped") is True,
                "omitted": [name for name in linux.get("omitted", []) if name in {"OUTCAR", "OSZICAR", "CONTCAR", "vasprun.xml"}]}
