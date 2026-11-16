import asyncio
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
import pytest_asyncio
from pydantic import ValidationError

from app.engines.manager import EngineTaskManager
from app.engines.models import VaspSiliconDraft, VaspWslInput
from app.engines.results import vasp_result
from app.engines.vasp_wsl import WslIdentity, WslVaspProfile, WslVaspTransport, decode_wsl_output, worker_source

PROFILE = {
    "distribution": "Ubuntu Fixture", "bin_dir": "/home/fixture/程序 文件/bin",
    "potential_root": "/home/fixture/potentials", "mpi_path": "/usr/bin/mpirun", "variant": "vasp_std", "ranks": 2,
}
TOKEN = {"X-Workbench-Token": "fixture-desktop-token"}
BASE = "/api/engine-tasks"


def draft():
    return VaspSiliconDraft(lattice_angstrom=5.43).files()


def task(**extra):
    return {"project_id": "untrusted-client-project", "inputs": {"kind": "vasp_wsl", "files": draft()},
            "timeout_seconds": 600, **extra}


def write_outputs(folder):
    (folder / "OUTCAR").write_text(
        " vasp.6.4.3 fixture\n aborting loop because EDIFF is reached\n"
        " free energy TOTEN = -10.65638172 eV\n General timing and accounting\n Elapsed time (sec): 2.40\n",
        encoding="utf-8")
    (folder / "OSZICAR").write_text(
        "".join(f"DAV: {number} -10.65638172 0.1\n" for number in range(1, 12)) + " 1 F= -10.656382 E0= -10.656382\n",
        encoding="utf-8")
    (folder / "vasprun.xml").write_text(
        '<modeling><calculation><energy><i name="e_fr_energy">-10.65638172</i></energy></calculation></modeling>',
        encoding="utf-8")


class MockTransport:
    def __init__(self):
        self.potential = "a" * 64
        self.start_count = 0
        self.cleanup_count = 0
        self.wait_on_run = False
        self.reaped = True
        self.started = asyncio.Event()

    async def distributions(self):
        return ["Ubuntu Fixture", "Other"]

    async def probe(self, profile):
        return WslIdentity(vasp_version="6.4.3", mpi_version="4.1.6", ubuntu_version="24.04",
                           binary_sha256="b" * 64, mpi_sha256="c" * 64, variant=profile.variant)

    async def potential_hash(self, profile, selections):
        assert selections == [["Si", "Si"]]
        return self.potential

    async def convert_path(self, profile, path, *, linux):
        return "/mnt/e/space 中文/" + Path(path).name

    async def cleanup(self, profile, run_id, exchange, *, cancel):
        self.cleanup_count += 1
        return {"reaped": self.reaped, "omitted": []}


class MockWslProcess:
    def __init__(self, transport, profile, folder, payload):
        self.transport, self.folder, self.payload = transport, folder, payload

    async def start(self):
        self.transport.start_count += 1
        assert self.payload["engine_identity"]["variant"] == "vasp_std"
        assert self.payload["potential_sha256"] == self.transport.potential
        assert not (self.folder / "POTCAR").exists()
        self.transport.started.set()
        return SimpleNamespace(identity={"fixture": True})

    async def consume(self, timeout, output):
        if self.transport.wait_on_run:
            await asyncio.Future()
        output(b"fixture: DAV: 11\n")
        write_outputs(self.folder)
        return 0

    def safe_output(self, chunk):
        return chunk

    async def close(self):
        return {"reaped": self.transport.reaped, "linux_reaped": self.transport.reaped, "windows_reaped": True}


@pytest_asyncio.fixture
async def desktop(tmp_path, monkeypatch):
    import app.main as main
    import app.engines.vasp_wsl_tasks as tasks

    transport = MockTransport()
    manager = EngineTaskManager(tmp_path / "engines", wsl=transport)
    monkeypatch.setenv("COMSOL_WORKBENCH_SESSION_TOKEN", TOKEN["X-Workbench-Token"])
    monkeypatch.setattr(tasks, "WslVaspProcess", MockWslProcess)
    monkeypatch.setattr(main, "engine_tasks", manager)
    monkeypatch.setattr(main.project_service, "projects", lambda: [{"id": "project", "active": True}])
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app),
                                 base_url="http://desktop", headers=TOKEN) as client:
        yield client, manager, transport
    await main.engine_tasks.shutdown()


async def configure(client, profile=PROFILE):
    assert (await client.put(BASE + "/vasp-wsl/configuration", json=profile)).status_code == 200


async def review(client):
    reply = await client.post(BASE + "/preview", json=task())
    assert reply.status_code == 200, reply.text
    return reply.json()


async def status_until(client, run_id, state):
    async with asyncio.timeout(3):
        while True:
            reply = await client.get(f"{BASE}/{run_id}")
            assert reply.status_code == 200
            result = reply.json()
            if result["state"] == state:
                return result
            assert result["state"] in {"PENDING", "RUNNING"}, result
            await asyncio.sleep(0.02)


@pytest.mark.asyncio
async def test_product_review_run_and_idempotent_request(desktop):
    client, manager, transport = desktop
    await configure(client)
    assert (await client.get(BASE + "/vasp-wsl/distributions")).json()["distributions"] == ["Ubuntu Fixture", "Other"]
    probe = (await client.post(BASE + "/vasp-wsl/probe")).json()
    assert probe["status"] == "HANDSHAKE_PASSED" and probe["scientific_validation"] == "UNVERIFIED"
    assert transport.start_count == 0
    proposed = await review(client)
    assert proposed["structure"]["atoms"][0]["element"] == "Si"
    assert set(proposed["files"]) == {"INCAR", "POSCAR", "KPOINTS"}
    assert proposed["wsl_review"]["ranks"] == 2
    ticket = proposed["review_ticket"]
    assert (await client.post(BASE, json=task(confirmed=True))).status_code == 409
    assert (await client.post(BASE, json=task(confirmed=False, review_ticket=ticket))).status_code == 409
    payload = task(confirmed=True, review_ticket=ticket)
    one, two = await asyncio.gather(client.post(BASE, json=payload), client.post(BASE, json=payload))
    assert one.status_code == two.status_code == 200, (one.text, two.text)
    run_id = one.json()["run_id"]
    assert run_id == two.json()["run_id"]
    outcome = await status_until(client, run_id, "COLLECTED")
    result = outcome["result"]
    assert transport.start_count == 1 and outcome["transport"] == "wsl2" and outcome["project_id"] == "project"
    assert result["energy"] == -10.65638172 and result["electronic_iterations"] == 11
    assert result["scf_converged"] and result["normal_termination"]
    assert result["xml_status"] == "PARSED" and result["solver_elapsed_seconds"] == 2.4
    assert outcome["scientific_validation"] == "NEEDS_REVIEW"
    assert not outcome["linux_cleanup_pending"]
    assert "review_ticket" not in outcome["request"] and PROFILE["bin_dir"] not in str(outcome)
    assert not (manager._folder(run_id) / "POTCAR").exists()
    assert (await client.get(f"{BASE}/{run_id}/artifacts/wsl-profile.json")).status_code == 404
    assert (await client.get(f"{BASE}/{run_id}/artifacts/POTCAR")).status_code == 404
    assert (await client.get(f"{BASE}/{run_id}/artifacts/vasp.log")).status_code == 200
    different = task(confirmed=True, review_ticket=ticket)
    different["inputs"]["files"]["INCAR"] += "NELM=90\n"
    assert (await client.post(BASE, json=different)).status_code == 409


@pytest.mark.parametrize("change", ["profile", "potential", "inputs", "ticket"])
@pytest.mark.asyncio
async def test_modified_approved_input_is_rejected_without_submission(desktop, change):
    client, _, transport = desktop
    await configure(client)
    ticket = (await review(client))["review_ticket"]
    payload = task(confirmed=True, review_ticket=ticket)
    if change == "profile":
        await configure(client, {**PROFILE, "ranks": 3})
    elif change == "potential":
        transport.potential = "d" * 64
    elif change == "inputs":
        payload["inputs"]["files"]["INCAR"] += "NELM = 90\n"
    else:
        payload["review_ticket"] = ticket[:-2] + "zz"
    assert (await client.post(BASE, json=payload)).status_code == 409
    assert transport.start_count == 0


@pytest.mark.parametrize("case", ["gamma_mesh", "noncollinear", "response"])
@pytest.mark.asyncio
async def test_unverified_physics_and_incompatible_variants_are_blocked(desktop, case):
    client, _, transport = desktop
    await configure(client, {**PROFILE, "variant": "vasp_gam"} if case == "gamma_mesh" else PROFILE)
    request = task()
    if case == "noncollinear":
        request["inputs"]["files"]["INCAR"] += "LNONCOLLINEAR=.TRUE.\n"
    elif case == "response":
        request["inputs"]["files"]["INCAR"] = request["inputs"]["files"]["INCAR"].replace("IBRION = -1", "IBRION = 8")
    assert (await client.post(BASE + "/preview", json=request)).status_code == 422
    assert transport.start_count == 0


@pytest.mark.asyncio
async def test_cancel_requires_linux_cleanup_and_reconcile_never_resubmits(desktop):
    client, manager, transport = desktop
    await configure(client)
    transport.wait_on_run = True
    transport.reaped = False
    first = await review(client)
    run = (await client.post(BASE, json=task(confirmed=True, review_ticket=first["review_ticket"]))).json()
    await asyncio.wait_for(transport.started.wait(), 3)
    stopped = (await client.post(f'{BASE}/{run["run_id"]}/cancel')).json()
    assert stopped["state"] == "NEEDS_REVIEW" and stopped["linux_cleanup_pending"]
    second = await review(client)
    assert (await client.post(BASE, json=task(confirmed=True, review_ticket=second["review_ticket"]))).status_code == 409
    transport.reaped = True
    reconciled = (await client.post(f'{BASE}/{run["run_id"]}/vasp-wsl/reconcile')).json()
    assert not reconciled["linux_cleanup_pending"] and reconciled["state"] == "NEEDS_REVIEW"
    assert transport.cleanup_count == 1 and transport.start_count == 1


@pytest.mark.asyncio
async def test_restart_does_not_replay_an_existing_claim(desktop, monkeypatch):
    client, manager, transport = desktop
    await configure(client)
    proposal = await review(client)
    payload = task(confirmed=True, review_ticket=proposal["review_ticket"])
    run = (await client.post(BASE, json=payload)).json()
    await status_until(client, run["run_id"], "COLLECTED")
    # 注入进程退出前的持久状态，新的 Manager 只能核对，不能重放。
    persisted = manager.status(run["run_id"])
    manager._write({**persisted, "state": "RUNNING", "linux_cleanup_pending": True})
    restarted = EngineTaskManager(manager.root, wsl=transport)
    import app.main as main
    monkeypatch.setattr(main, "engine_tasks", restarted)
    repeated = (await client.post(BASE, json=payload)).json()
    assert repeated["run_id"] == run["run_id"] and repeated["state"] == "INTERRUPTED"
    assert transport.start_count == 1


@pytest.mark.asyncio
async def test_transport_handles_unicode_and_wsl_banners_without_a_shell(tmp_path, monkeypatch):
    transport = WslVaspTransport(tmp_path, launcher=lambda: "wsl.exe")
    profile = WslVaspProfile.model_validate(PROFILE)
    recorded = []

    async def capture(argv, **kwargs):
        recorded.append(argv)
        if "--list" in argv:
            return "wsl: banner\r\nUbuntu Fixture\r\n".encode("utf-16-le")
        if "-u" in argv:
            return "wsl: banner\r\n/mnt/e/中文 路径\r\n".encode()
        return "wsl: banner\r\nE:\\中文 路径\r\n".encode()

    monkeypatch.setattr(transport, "capture", capture)
    assert await transport.distributions() == ["Ubuntu Fixture"]
    assert await transport.convert_path(profile, "E:\\中文 路径", linux=True) == "/mnt/e/中文 路径"
    assert await transport.convert_path(profile, "/mnt/e/中文 路径", linux=False) == "E:\\中文 路径"
    assert recorded[1] == ["wsl.exe", "--distribution", "Ubuntu Fixture", "--exec", "/usr/bin/wslpath", "-a", "-u", "E:\\中文 路径"]
    assert decode_wsl_output("Ubuntu".encode("utf-16")) == "Ubuntu"
    compile(worker_source(), "vasp_wsl_worker.py", "exec")
    with pytest.raises(ValueError):
        await transport.convert_path(profile, "-unsafe", linux=True)


def test_restricted_potential_inputs_and_incomplete_xml(tmp_path):
    with pytest.raises(ValidationError):
        VaspWslInput(files={**draft(), "POTCAR": "licensed fixture"})
    with pytest.raises(ValidationError):
        VaspWslInput(files=draft(), potentials={"Si": "../Si"})
    with pytest.raises(ValidationError):
        WslVaspProfile.model_validate({**PROFILE, "bin_dir": "/root/../unsafe"})
    write_outputs(tmp_path)
    (tmp_path / "vasprun.xml").write_text("<modeling><unfinished>", encoding="utf-8")
    result = vasp_result(tmp_path, wsl=True)
    assert result["energy"] == -10.65638172 and result["xml_status"] == "INVALID"
    assert not result["checks"]["xml_energy_agrees"]
    (tmp_path / "OUTCAR").write_text("vasp.6.4.3\n", encoding="utf-8")
    result = vasp_result(tmp_path, wsl=True)
    assert not result["scf_converged"] and not result["normal_termination"]
