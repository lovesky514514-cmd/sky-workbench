"""将 WSL 预检、审批及恢复接到现有 EngineTaskManager。"""

from __future__ import annotations

import base64
import binascii
from hashlib import sha256
import hmac
import json
import math
import os
import time
from uuid import UUID, uuid4

from app.engines.models import EngineTaskRequest, VaspWslInput
from app.engines.results import poscar_structure, vasp_result
from app.engines.vasp import numeric_tag, validate_vasp_files
from app.engines.vasp_wsl import WslVaspProcess, WslVaspProfile, WslVaspTransport, json_argument
from app.execution.files import bounded_read, reject_reparse


def _hash(value: dict) -> str:
    return sha256(json_argument(value).encode()).hexdigest()


def _sign(payload: str) -> str:
    secret = os.getenv("COMSOL_WORKBENCH_SESSION_TOKEN")
    if not secret:
        raise ValueError("Desktop session authentication is required")
    return hmac.new(secret.encode(), ("vasp-wsl-review:" + payload).encode(), sha256).hexdigest()


def issue_ticket(project: str, digest: str) -> tuple[str, str]:
    run_id = str(uuid4())
    body = json_argument({"project": project, "digest": digest, "run_id": run_id, "expires": int(time.time()) + 600})
    encoded = base64.urlsafe_b64encode(body.encode()).decode()
    return run_id, encoded + "." + _sign(encoded)


def verify_ticket(ticket: str | None, project: str) -> dict:
    try:
        encoded, signature = (ticket or "").split(".", 1)
        if not hmac.compare_digest(_sign(encoded), signature):
            raise ValueError
        body = json.loads(base64.urlsafe_b64decode(encoded))
        if (body["project"] != project or body["expires"] < time.time()
                or str(UUID(body["run_id"])) != body["run_id"]):
            raise ValueError
        return body
    except (ValueError, TypeError, KeyError, binascii.Error, UnicodeError) as exc:
        raise ValueError("Review the WSL VASP inputs again before authorizing RUN") from exc


def _request_digest(request: EngineTaskRequest) -> str:
    return _hash({"inputs": request.inputs.model_dump(mode="json"), "timeout_seconds": request.timeout_seconds})


def _variant_inputs(profile, info, inputs):
    # 首轮只放行静态 SCF；支持选择三种二进制，其他物理模式须单独验收。
    tags = info["tags"]
    truthy = {".TRUE.", ".T.", "TRUE", "T", "1"}
    advanced = ("LNONCOLLINEAR", "LSORBIT", "LEPSILON", "LCALCEPS", "LOPTICS", "ML_LMLFF")
    if (info["task_type"] != "single_point" or numeric_tag(tags, "NSW", 0) != 0
            or numeric_tag(tags, "IBRION", -1) != -1 or numeric_tag(tags, "ICHARG", 2) >= 10
            or any(tags.get(key, "").upper() in truthy for key in advanced)
            or tags.get("ALGO", "").lower() in {"none", "nothing"}):
        raise ValueError("The WSL connector currently accepts static SCF without SOC/noncollinear mode")
    lines = [line.split() for line in inputs.files["KPOINTS"].splitlines() if line.strip()]
    if lines[1] != ["0"] or not lines[2][0].lower().startswith(("g", "m")) or len(lines) not in (4, 5):
        raise ValueError("WSL static SCF requires an automatic Gamma or Monkhorst-Pack mesh")
    shift = [float(value) for value in lines[4]] if len(lines) == 5 else [0, 0, 0]
    if len(shift) != 3 or not all(math.isfinite(value) for value in shift):
        raise ValueError("KPOINTS shift must contain three finite values")
    if profile.variant == "vasp_gam":
        if not lines[2][0].lower().startswith("g") or lines[3] != ["1", "1", "1"] or any(shift):
            raise ValueError("vasp_gam requires an unshifted Gamma-only mesh")


class WslVaspTaskMixin:
    wsl: WslVaspTransport

    def _wsl_profile_path(self, project_id: str):
        key = sha256(project_id.encode()).hexdigest()
        return self.root / f"vasp-wsl-{key}.json"

    def wsl_profile(self, project_id: str) -> WslVaspProfile | None:
        path = self._wsl_profile_path(project_id)
        return WslVaspProfile.model_validate_json(bounded_read(self.root, path.name, 12_000)) if path.is_file() else None

    def configure_wsl(self, project_id: str, profile: WslVaspProfile) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        reject_reparse(self.root)
        path = self._wsl_profile_path(project_id)
        reject_reparse(path)
        temporary = self.root / f"vasp-wsl-{uuid4().hex}.tmp"
        with temporary.open("xb") as stream:
            stream.write(profile.model_dump_json().encode())
        temporary.replace(path)

    def _require_wsl_profile(self, project_id: str) -> WslVaspProfile:
        profile = self.wsl_profile(project_id)
        if profile is None:
            raise ValueError("Configure a WSL VASP installation in this project first")
        return profile

    async def _wsl_manifest(self, request: EngineTaskRequest) -> tuple[WslVaspProfile, dict]:
        inputs = request.inputs
        if not isinstance(inputs, VaspWslInput):
            raise ValueError("WSL VASP requires local potential references")
        profile = self._require_wsl_profile(request.project_id)
        info = validate_vasp_files(inputs.files, local_potentials=True)
        _variant_inputs(profile, info, inputs)
        structure = poscar_structure(inputs.files["POSCAR"], info["species"])
        a, b, c = structure["lattice"]
        volume = sum(a[i] * (b[(i + 1) % 3] * c[(i + 2) % 3] - b[(i + 2) % 3] * c[(i + 1) % 3])
                     for i in range(3))
        if not math.isfinite(volume) or abs(volume) <= 1e-12:
            raise ValueError("POSCAR must have a finite nonzero cell volume")
        selections = [[symbol, inputs.potentials.get(symbol, symbol)] for symbol in info["species"]]
        identity = (await self.wsl.probe(profile)).model_dump()
        manifest = {
            "engine_identity": identity,
            "profile_sha256": _hash(profile.model_dump()),
            "input_hashes": {name: sha256(text.encode()).hexdigest() for name, text in inputs.files.items()},
            "selections": selections,
            "potential_sha256": await self.wsl.potential_hash(profile, selections),
            "timeout_seconds": request.timeout_seconds,
        }
        return profile, manifest

    async def review_wsl(self, request: EngineTaskRequest) -> dict:
        profile, manifest = await self._wsl_manifest(request)
        _, ticket = issue_ticket(request.project_id, _hash(manifest))
        assert isinstance(request.inputs, VaspWslInput)
        return {**self.preview(request), "review_ticket": ticket, "wsl_review": {
            "variant": profile.variant, "ranks": profile.ranks, "vasp_version": manifest["engine_identity"]["vasp_version"],
            "input_hashes": manifest["input_hashes"], "potential_sha256": manifest["potential_sha256"],
        }}

    def _wsl_pending_cleanup(self, project_id: str) -> bool:
        # 检查全部历史，而不依赖 UI 最多显示的 100 项。
        for folder in self.root.iterdir():
            try:
                run = self.status(folder.name, include_result=False)
            except (KeyError, OSError, ValueError):
                continue
            if (run["project_id"] == project_id and run.get("transport") == "wsl2"
                    and run.get("linux_cleanup_pending") is True):
                return True
        return False

    async def start_wsl(self, request: EngineTaskRequest) -> dict:
        if not request.confirmed:
            raise ValueError("Explicit RUN authorization is required")
        ticket = verify_ticket(request.review_ticket, request.project_id)
        run_id = ticket["run_id"]
        request_digest = _request_digest(request)

        def claimed():
            try:
                existing = self.status(run_id)
            except KeyError:
                return None
            if (existing.get("review_digest") != ticket["digest"] or existing["project_id"] != request.project_id
                    or existing.get("review_request_digest") != request_digest):
                raise ValueError("WSL review belongs to different run inputs")
            return existing

        existing = claimed()
        if existing:
            return existing
        profile, manifest = await self._wsl_manifest(request)
        if not hmac.compare_digest(_hash(manifest), ticket["digest"]):
            raise ValueError("WSL configuration, inputs or potentials changed after review")
        existing = claimed()
        if existing:
            return existing
        self._check_run_capacity("VASP", request.project_id)
        if self._wsl_pending_cleanup(request.project_id):
            raise ValueError("An interrupted Linux VASP task still requires cleanup verification")
        try:
            record = self._create_run(request, "VASP", profile.variant, run_id, wsl_profile=profile, extra={
                "transport": "wsl2", "review_digest": ticket["digest"], "linux_cleanup_pending": True,
                "review_request_digest": request_digest,
                "engine_version": manifest["engine_identity"]["vasp_version"], "mpi_ranks": profile.ranks,
                "vasp_variant": profile.variant, "potential_sha256": manifest["potential_sha256"],
            })
        except FileExistsError as exc:
            # 目录创建是跨进程的一次性认领，缺记录时不能覆盖或补提交。
            existing = claimed()
            if existing:
                return existing
            raise ValueError("WSL RUN was already claimed; inspect the existing attempt") from exc
        folder = self._folder(run_id)

        async def make_process():
            exchange = await self.wsl.convert_path(profile, str(folder), linux=True)
            payload = {**manifest, "run_id": run_id, "exchange": exchange}
            return WslVaspProcess(self.wsl, profile, folder, payload)

        self._schedule_run(record, request, [], lifecycle_factory=make_process)
        return record

    async def reconcile_wsl(self, run_id: str) -> dict:
        record = self.status(run_id, include_result=False)
        if record.get("transport") != "wsl2" or run_id in self.active_runs:
            raise ValueError("Select a stopped WSL VASP task")
        if record.get("linux_cleanup_pending") is not True:
            return self.status(run_id)
        folder = self._folder(run_id)
        profile = WslVaspProfile.model_validate_json(bounded_read(folder, "wsl-profile.json", 12_000))
        exchange = await self.wsl.convert_path(profile, str(folder), linux=True)
        cleanup = await self.wsl.cleanup(profile, run_id, exchange, cancel=True)
        verified = cleanup.get("reaped") is True
        record.update(linux_cleanup_pending=not verified, cleanup={"linux_reaped": verified},
                      detail="Linux process exit verified; review preserved outputs" if verified else
                      "Linux process exit is still unverified")
        record["artifacts"] = self._collect_artifacts(folder, "VASP")
        if verified and not record.get("result_file") and (folder / "OUTCAR").is_file():
            try:
                self._persist_result(folder, record, vasp_result(folder, wsl=True))
                record["scientific_validation"] = "NEEDS_REVIEW"
            except (OSError, ValueError):
                record["detail"] = "Linux process exit verified; preserved output could not be parsed"
        self._write(record)
        return self.status(run_id)
