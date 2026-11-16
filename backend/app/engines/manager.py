from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import time
import uuid
from pathlib import Path
from typing import Callable

from app.comsol.owned_process import OwnedProcess
from app.engines.inputs import command_for, prepare_inputs
from app.engines.capabilities import CapabilityRegistry
from app.engines.models import EngineTaskRequest, LammpsInput, MaterialsStudioInput, VaspInput, VaspWslInput, engine_for
from app.engines.results import lammps_result, materials_studio_result, poscar_structure, vasp_result
from app.engines.vasp_wsl import WslVaspTransport
from app.engines.vasp_wsl_tasks import WslVaspTaskMixin
from app.engines.native_installations import file_product_version, materials_server
from app.execution.files import bounded_read, reject_reparse
from app.execution.owned_task import OwnedTask
from app.script_workspace import child_environment
from app.source_revision import source_revision


ACTIVE = {"PENDING", "RUNNING", "CANCEL_REQUESTED"}
ARTIFACT_NAMES = {
    "LAMMPS": ("in.lammps", "log.lammps", "trajectory.dump", "final.data"),
    "VASP": ("INCAR", "POSCAR", "KPOINTS", "OUTCAR", "OSZICAR", "CONTCAR", "vasprun.xml", "vasp.log"),
    "Materials Studio": ("main.pl", "model.xsd", "energy.txt", "engine.log"),
}


def installed_executable(engine: str) -> str | None:
    from app.simulation_adapters import capabilities

    return next((str(item["executable"]) for item in capabilities()
                 if item["engine"] == engine and item["installed"]), None)


class EngineTaskManager(WslVaspTaskMixin):
    def __init__(self, root: Path, resolver: Callable[[str], str | None] = installed_executable,
                 *, execution_guard: Callable[[str, str, str], None] | None = None,
                 wsl: WslVaspTransport | None = None):
        self.root = root
        self.resolver = resolver
        self.capabilities = CapabilityRegistry(root)
        self.execution_guard = execution_guard or self.capabilities.require
        self.wsl = wsl or WslVaspTransport(root)
        self.tasks: dict[str, asyncio.Task] = {}
        self.processes: dict[str, OwnedProcess] = {}
        self.active_runs: dict[str, tuple[str, str]] = {}
        self.recovered = False

    def configuration(self) -> dict[str, str]:
        path = self.root / "executables.json"
        return json.loads(bounded_read(self.root, path.name, 16384)) if path.is_file() else {}

    def configure(self, engine: str, executable: str) -> dict[str, str]:
        if engine not in ARTIFACT_NAMES:
            raise ValueError("Unsupported engine")
        path = Path(executable)
        if executable and (not path.is_absolute() or not path.is_file() or executable.startswith("\\\\")):
            raise ValueError("Select an existing local absolute executable path")
        if executable:
            command_for(engine, executable)
        self.root.mkdir(parents=True, exist_ok=True)
        reject_reparse(self.root)
        configuration = self.configuration()
        if executable:
            configuration[engine] = str(path.resolve())
        else:
            configuration.pop(engine, None)
        temporary = self.root / "executables.tmp"
        temporary.write_text(json.dumps(configuration), encoding="utf-8")
        temporary.replace(self.root / "executables.json")
        return configuration

    async def connect(self, engine: str) -> dict:
        executable = self.configuration().get(engine) or self.resolver(engine)
        if not executable:
            raise ValueError(f"Configure an installed {engine} executable first")
        if engine not in {"LAMMPS", "Materials Studio"}:
            return {"engine": engine, "status": "UNVERIFIED",
                    "detail": "No safe non-calculating handshake is validated for this connector; input preflight remains available"}
        probe_id = str(uuid.uuid4())
        folder = self._folder(probe_id)
        folder.mkdir(parents=True)
        marker = b"SKY_MATERIALSSCRIPT_CONNECTED"
        if engine == "Materials Studio":
            (folder / "main.pl").write_bytes(b'use strict; use MaterialsScript qw(:all); '
                b'open(my $fh, ">", "sky_probe.txt") or die "probe receipt unavailable"; '
                b'print $fh "SKY_MATERIALSSCRIPT_CONNECTED\\n"; close($fh) or die "probe receipt failed"; '
                b'print "SKY_MATERIALSSCRIPT_CONNECTED\\n";')
            command = command_for(engine, executable)
        else:
            command = [executable, "-help"]
            marker = b"LAMMPS"
        lifecycle = OwnedTask(command, cwd=str(folder), env=child_environment())
        output = bytearray()
        try:
            await lifecycle.start()
            code = await lifecycle.consume(30, output.extend, byte_limit=2_000_000)
        finally:
            cleanup = await lifecycle.close()
        if engine == "Materials Studio":
            # MatServer 将 Perl stdout 写入 main.pl.out；独立回执避免把启动器退出 0 当作握手。
            try:
                receipt = bounded_read(folder, "sky_probe.txt", 128)
                vendor_output = bounded_read(folder, "main.pl.out", 65536) if (folder / "main.pl.out").exists() else bytes(output)
            except (OSError, ValueError):
                receipt, vendor_output = b"", b""
            passed = code == 0 and receipt.strip() == marker and marker in vendor_output.splitlines() and cleanup.get("reaped")
        else:
            passed = code == 0 and marker in output and cleanup.get("reaped")
        if passed:
            version = file_product_version(materials_server(executable)) if engine == "Materials Studio" else next(
                (line[:120] for line in output.decode("utf-8", errors="replace").splitlines() if "LAMMPS" in line), "UNVERIFIED")
            self.capabilities.connected(engine, executable, version)
        return {"engine": engine, "status": "HANDSHAKE_PASSED" if passed else "HANDSHAKE_FAILED",
                "scientific_validation": "UNVERIFIED"}

    def _folder(self, run_id: str) -> Path:
        # IDs, never a user filename, select persisted run directories.
        if str(uuid.UUID(run_id)) != run_id:
            raise ValueError("Invalid run ID")
        folder = self.root / run_id
        reject_reparse(folder)
        if folder.resolve().parent != self.root.resolve():
            raise ValueError("Run directory leaves the workspace")
        return folder

    def _write(self, record: dict) -> None:
        folder = self._folder(record["run_id"])
        temporary = folder / f"record-{uuid.uuid4().hex}.tmp"
        data = json.dumps(record, ensure_ascii=False, allow_nan=False).encode("utf-8")
        if len(data) > 1_000_000:
            raise ValueError("Run metadata exceeds its 1 MB limit")
        temporary.write_bytes(data)
        temporary.replace(folder / "record.json")

    def recover(self) -> None:
        if self.recovered:
            return
        self.root.mkdir(parents=True, exist_ok=True)
        for folder in self.root.iterdir():
            try:
                record = self.status(folder.name, include_result=False)
                if record["state"] in ACTIVE:
                    if record.get("transport") == "wsl2":
                        record.update(state="INTERRUPTED", linux_cleanup_pending=True,
                                      detail="Runtime restarted; verify the Linux job before a new RUN")
                    else:
                        record.update(state="INTERRUPTED", detail="Runtime restarted; outputs remain available for inspection. Re-run explicitly.")
                    self._write(record)
            except (OSError, ValueError, KeyError):
                continue
        self.recovered = True

    def status(self, run_id: str, *, include_result: bool = True) -> dict:
        path = self._folder(run_id) / "record.json"
        if not path.is_file():
            raise KeyError(run_id)
        record = json.loads(bounded_read(path.parent, path.name, 1_000_000))
        record.setdefault("record_trust", "local_corruption_checks_only")
        record.setdefault("code_revision", {"kind": "LEGACY_UNBOUND"})
        if record.get("result_file") and include_result:
            data = bounded_read(path.parent, "result.json", 1_000_000)
            if hashlib.sha256(data).hexdigest() != record["result_sha256"]:
                raise ValueError("Result changed after collection")
            record["result"] = json.loads(data)
        return record

    def list_runs(self, project_id: str) -> list[dict]:
        self.recover()
        records = []
        for folder in self.root.iterdir():
            try:
                record = self.status(folder.name, include_result=False)
                if record["project_id"] == project_id:
                    record["result"] = None
                    record["output"] = ""
                    records.append(record)
            except (OSError, ValueError, KeyError):
                continue
        return sorted(records, key=lambda item: item["created_at"], reverse=True)[:100]

    def preview(self, request: EngineTaskRequest) -> dict:
        files = prepare_inputs(request.inputs)
        structure = None
        if isinstance(request.inputs, (VaspInput, VaspWslInput)):
            from app.engines.vasp import validate_vasp_files

            structure = poscar_structure(files["POSCAR"], validate_vasp_files(
                files, local_potentials=isinstance(request.inputs, VaspWslInput))["species"])
        if isinstance(request.inputs, MaterialsStudioInput):
            structure = {"units": "angstrom", "atoms": [
                {"id": index + 1, "element": atom.element, "position": [atom.x, atom.y, atom.z]}
                for index, atom in enumerate(request.inputs.atoms)
            ]}
        if isinstance(request.inputs, LammpsInput):
            # This is the declared FCC input lattice, never a solved trajectory.
            spacing = (4 / request.inputs.density) ** (1 / 3)
            basis = ((0, 0, 0), (0.5, 0.5, 0), (0.5, 0, 0.5), (0, 0.5, 0.5))
            atoms = []
            for x in range(request.inputs.cells):
                for y in range(request.inputs.cells):
                    for z in range(request.inputs.cells):
                        for offset in basis:
                            atoms.append({"id": len(atoms) + 1, "element": "1",
                                          "position": [(index + shift) * spacing for index, shift in zip((x, y, z), offset)]})
            structure = {"units": "LJ reduced length", "atoms": atoms}
        return {"engine": engine_for(request.inputs), "files": {name: content[:16384] for name, content in files.items() if name != "POTCAR"},
                "preview_truncated": any(len(content) > 16384 for name, content in files.items() if name != "POTCAR"),
                "structure": structure, "assumptions": self._assumptions(request)}

    @staticmethod
    def _assumptions(request: EngineTaskRequest) -> list[str]:
        if isinstance(request.inputs, LammpsInput):
            return ["Exploratory monoatomic LJ fluid; FCC initial structure, periodic boundaries, NVT thermostat, reduced units.",
                    "This example does not specify or validate a real material's force field."]
        return ["User-selected engine inputs; physical suitability and convergence require task-specific review."]

    async def start(self, request: EngineTaskRequest) -> dict:
        self.recover()
        if not request.confirmed:
            raise ValueError("Review task parameters and authorize local execution")
        if isinstance(request.inputs, VaspWslInput):
            return await self.start_wsl(request)
        self.preview(request)  # Validate domain inputs before creating a process.
        engine = engine_for(request.inputs)
        self._check_run_capacity(engine, request.project_id)
        executable = self.configuration().get(engine) or self.resolver(engine)
        if not executable:
            raise ValueError(f"Configure an installed {engine} executable first")
        self.execution_guard(engine, request.inputs.kind, executable)
        command = command_for(engine, executable)
        version = self.capabilities.capability_of(engine, request.inputs.kind, executable).get("engine_version", "UNVERIFIED")
        record = self._create_run(request, engine, executable, str(uuid.uuid4()), extra={"engine_version": version})
        self._schedule_run(record, request, command)
        return record

    def _check_run_capacity(self, engine: str, project_id: str) -> None:
        if (engine, project_id) in self.active_runs.values():
            raise ValueError("This engine already has an active task in the selected project")
        if len(self.active_runs) >= 2:
            raise ValueError("The local task concurrency limit is two")

    def _create_run(self, request, engine, executable, run_id, *, extra=None, wsl_profile=None) -> dict:
        folder = self._folder(run_id)
        folder.mkdir(parents=True)
        inputs = prepare_inputs(request.inputs)
        for name, text in inputs.items():
            # Hash the exact bytes written; Windows newline conversion must not alter identity.
            (folder / name).write_bytes(text.encode("utf-8"))
        metadata = request.model_dump(mode="json", exclude={"review_ticket"})
        if isinstance(request.inputs, (VaspInput, VaspWslInput)):
            metadata["inputs"] = {"kind": request.inputs.kind, "file_names": list(inputs)}
        record = {"schema_version": 2, "run_id": run_id, "engine": engine,
                  "project_id": request.project_id, "title": request.title, "created_at": time.time(),
                  "state": "PENDING", "command_success": False, "readback_available": False,
                  "model_state_verified": False, "verification_status": "UNVERIFIED",
                  "scientific_validation": "NOT_EVALUATED", "output": "", "artifacts": [], "result": None,
                  "engine_version": "UNVERIFIED",
                  "request": metadata, "assumptions": self._assumptions(request),
                  "input_hashes": {name: hashlib.sha256((folder / name).read_bytes()).hexdigest() for name in inputs},
                  "executable": executable, "elapsed_ms": 0, "code_revision": source_revision(),
                  "record_trust": "local_corruption_checks_only", **(extra or {})}
        if wsl_profile is not None:
            with (folder / "wsl-profile.json").open("xb") as stream:
                stream.write(wsl_profile.model_dump_json().encode())
        # WSL 的授权赝势不进入 Windows 工作目录或公共记录。
        self._write(record)
        return record

    def _schedule_run(self, record, request, command, *, lifecycle_factory=None) -> None:
        run_id = record["run_id"]
        self.active_runs[run_id] = (record["engine"], request.project_id)
        self.tasks[run_id] = asyncio.create_task(
            self._execute(record, request, command, lifecycle_factory=lifecycle_factory))
        self.tasks[run_id].add_done_callback(lambda task: self._forget_task(run_id))

    def _forget_task(self, run_id: str) -> None:
        self.tasks.pop(run_id, None)
        self.active_runs.pop(run_id, None)

    async def _execute(self, record: dict, request: EngineTaskRequest, command: list[str], *, lifecycle_factory=None) -> None:
        run_id, folder = record["run_id"], self._folder(record["run_id"])
        started = time.perf_counter()
        owned = None
        lifecycle = None
        wsl = lifecycle_factory is not None
        log_size = 0
        try:
            lifecycle = await lifecycle_factory() if wsl else OwnedTask(command, cwd=str(folder), env=child_environment())
            owned = await lifecycle.start()
            self.processes[run_id] = owned
            record.update(state="RUNNING", process_identity=owned.identity)
            self._write(record)

            last_write = 0.0
            def on_output(chunk: bytes) -> None:
                nonlocal last_write, log_size
                if wsl:
                    chunk = lifecycle.safe_output(chunk)
                    if log_size < 2_000_000:
                        kept = chunk[:2_000_000 - log_size]
                        with (folder / "vasp.log").open("ab") as stream:
                            stream.write(kept)
                        log_size += len(kept)
                record["output"] = (record["output"] + chunk.decode("utf-8", errors="replace"))[-65536:]
                if wsl:
                    progress = re.findall(r"^\s*(DAV|RMM|CG|DMP|SDA)\s*:\s*(\d+)\b", record["output"], re.M)
                    if progress:
                        method, step = progress[-1]
                        record["electronic_progress"] = {"method": method, "iteration": int(step)}
                if time.monotonic() - last_write >= 0.25:
                    self._write(record)
                    last_write = time.monotonic()

            code = await lifecycle.consume(request.timeout_seconds, on_output)
            record.update(exit_code=code, command_success=code == 0)
            engine = record["engine"]
            if engine == "Materials Studio" and (folder / "main.pl.out").exists():
                from app.redaction import redact_text
                diagnostic = bounded_read(folder, "main.pl.out", 65536).decode("utf-8", errors="replace")
                diagnostic = redact_text(diagnostic).replace(str(folder), "<task>").replace(str(Path.home()), "<user>")
                server = materials_server(record["executable"])
                if server:
                    diagnostic = diagnostic.replace(str(server.parents[1]), "<MS install>")
                (folder / "engine.log").write_bytes(diagnostic.encode("utf-8"))
                on_output(diagnostic.encode("utf-8"))
            if engine == "LAMMPS":
                result = lammps_result(folder, request.inputs.steps, settings=request.inputs)
            elif engine == "VASP":
                result = vasp_result(folder, wsl=wsl)
            else:
                result = materials_studio_result(folder, engine_version=record["engine_version"])
            self._persist_result(folder, record, result)
            if result.get("engine_version") not in (None, "", "UNVERIFIED"):
                record["engine_version"] = result["engine_version"]
            record["scientific_validation"] = result["scientific_validation"]
            record["readback_available"] = bool(result.get("frames") or result.get("structure") or
                                                result["checks"].get("document_saved") or
                                                wsl and result.get("energy") is not None)
            record["verification_status"] = "EXERCISED" if record["command_success"] and record["readback_available"] else "UNVERIFIED"
            record["state"] = "COLLECTED" if record["command_success"] and all(
                value for key, value in result["checks"].items()
                if key != "ionic_accuracy_reported" or result.get("ionic_convergence_applicable", False)
            ) else "NEEDS_REVIEW"
        except asyncio.CancelledError:
            record["state"] = "CANCELED"
        except TimeoutError:
            record.update(state="TIMED_OUT", detail="Task exceeded its configured time limit")
        except OverflowError:
            record.update(state="FAILED", detail="Task output exceeded the local limit")
        except ValueError as exc:
            if wsl and lifecycle is None:
                record.update(state="FAILED", detail=f"WSL staging failed: {exc}")
            else:
                record.update(state="NEEDS_REVIEW", detail=f"Result readback requires review: {exc}")
        except (OSError, RuntimeError) as exc:
            record.update(state="FAILED", detail=f"Engine task failed: {type(exc).__name__}")
        finally:
            if lifecycle is not None and (owned or wsl):
                cleanup = await lifecycle.close()
                record["cleanup"] = cleanup
                if not cleanup.get("reaped"):
                    record.update(state="NEEDS_REVIEW", detail="Owned process cleanup could not be verified")
            if wsl:
                record["linux_cleanup_pending"] = lifecycle is not None and record.get("cleanup", {}).get("linux_reaped") is not True
            self.processes.pop(run_id, None)
            record["elapsed_ms"] = round((time.perf_counter() - started) * 1000)
            record["artifacts"] = self._collect_artifacts(folder, record["engine"])
            record["omitted_artifacts"] = [{"name": name, "size_bytes": (folder / name).stat().st_size,
                                             "reason": "Exceeds the 24 MB download limit; retained in the local run directory"}
                                            for name in ARTIFACT_NAMES[record["engine"]]
                                            if (folder / name).is_file() and (folder / name).stat().st_size > 24_000_000]
            if wsl:
                record["omitted_artifacts"].extend({"name": name, "size_bytes": None,
                    "reason": "Retained in the Linux run directory; exceeds the collection limit"}
                    for name in record.get("cleanup", {}).get("omitted", []))
            self._write(record)
            if not wsl and record["state"] == "COLLECTED" and record.get("cleanup", {}).get("reaped"):
                self.capabilities.exercised(record["engine"], request.inputs.kind, record["executable"], run_id)

    @staticmethod
    def _persist_result(folder: Path, record: dict, result: dict) -> None:
        result_data = json.dumps(result, ensure_ascii=False, allow_nan=False).encode("utf-8")
        if len(result_data) > 1_000_000:
            raise ValueError("Result preview exceeds 1 MB; inspect the saved raw artifacts")
        reject_reparse(folder / "result.json")
        (folder / "result.json").write_bytes(result_data)
        record.update(result_file="result.json", result_sha256=hashlib.sha256(result_data).hexdigest())
        record["result"] = {name: result[name] for name in ("kind", "units", "checks", "scientific_validation", "limitations")}

    @staticmethod
    def _collect_artifacts(folder: Path, engine: str) -> list[dict]:
        artifacts = []
        for name in ARTIFACT_NAMES[engine]:
            path = folder / name
            if not path.exists():
                continue
            try:
                data = bounded_read(folder, name, 24_000_000)
            except (ValueError, OSError):
                continue
            artifacts.append({"name": name, "size_bytes": len(data),
                              "sha256": hashlib.sha256(data).hexdigest()})
        return artifacts

    def artifact(self, run_id: str, name: str) -> bytes:
        record = self.status(run_id)
        expected = next((item for item in record["artifacts"] if item["name"] == name), None)
        if expected is None:
            raise KeyError(name)
        data = bounded_read(self._folder(run_id), name, 24_000_000)
        if len(data) != expected["size_bytes"] or hashlib.sha256(data).hexdigest() != expected["sha256"]:
            raise ValueError("Artifact changed after collection")
        return data

    async def cancel(self, run_id: str) -> dict:
        task = self.tasks.get(run_id)
        if task and not task.done():
            if not task.cancelling():
                task.cancel()
            # Cancellation can precede the first coroutine instruction.
            await asyncio.gather(task, return_exceptions=True)
            record = self.status(run_id)
            if record["state"] in ACTIVE:
                record["state"] = "CANCELED"
                if record.get("transport") == "wsl2":
                    record["linux_cleanup_pending"] = False
                self._write(record)
        return self.status(run_id)

    async def shutdown(self) -> None:
        for run_id in list(self.tasks):
            await self.cancel(run_id)
