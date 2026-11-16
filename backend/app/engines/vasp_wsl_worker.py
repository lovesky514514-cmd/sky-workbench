"""WSL 端固定程序。仅用 Ubuntu 自带 Python，不导入 Windows Runtime。"""

from contextlib import contextmanager
import fcntl
from hashlib import file_digest, sha256
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import time
from uuid import UUID

INPUTS = ("INCAR", "POSCAR", "KPOINTS")
OUTPUTS = ("OUTCAR", "OSZICAR", "CONTCAR", "vasprun.xml")
MARKER = "SKY_VASP_JSON "
TAG = "SKY_VASP_JOB"
BOOT = Path("/proc/sys/kernel/random/boot_id")


class WorkerError(ValueError):
    pass


def bounded(path, limit):
    if path.is_symlink() or not path.is_file() or path.stat().st_size > limit:
        raise WorkerError("file_unavailable_or_oversized")
    with path.open("rb") as stream:
        data = stream.read(limit + 1)
    if len(data) > limit:
        raise WorkerError("file_oversized")
    return data


def file_hash(path):
    with path.open("rb") as stream:
        return file_digest(stream, "sha256").hexdigest()


def version_command(command, error):
    try:
        result = subprocess.run(command, cwd="/", stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                timeout=12, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise WorkerError(error) from exc
    if result.returncode != 0 or len(result.stdout) > 256_000:
        raise WorkerError(error)
    return result.stdout.decode(errors="replace")


def tool_paths(config):
    name = config["variant"]
    if name not in {"vasp_std", "vasp_gam", "vasp_ncl"}:
        raise WorkerError("invalid_vasp_variant")
    binary = (Path(config["bin_dir"]) / name).resolve()
    # Open MPI 按 argv[0] 区分 mpirun 和 orterun；保留用户配置的启动器名称。
    mpi = Path(config["mpi_path"])
    for path, kind in ((binary, "vasp"), (mpi, "mpi")):
        if not path.is_file() or not os.access(path, os.X_OK):
            raise WorkerError(kind + "_not_executable")
    return binary, mpi


def probe(config):
    if os.geteuid() == 0:
        raise WorkerError("wsl_non_root_user_required")
    os_release = Path("/etc/os-release").read_text(encoding="utf-8")
    if not re.search(r'^ID="?ubuntu"?$', os_release, re.M):
        raise WorkerError("ubuntu_required")
    version_id = re.search(r'^VERSION_ID="?([\d.]+)"?$', os_release, re.M)
    kernel = Path("/proc/sys/kernel/osrelease").read_text().lower()
    if "wsl2" not in kernel:
        raise WorkerError("wsl2_required")
    if not 1 <= config["ranks"] <= len(os.sched_getaffinity(0)):
        raise WorkerError("mpi_ranks_exceed_cpu_affinity")
    binary, mpi = tool_paths(config)
    for path in (binary, mpi):
        dependencies = version_command(["/usr/bin/ldd", str(path)], "dynamic_link_check_failed")
        if re.search(r"=>\s*not found", dependencies):
            raise WorkerError("dynamic_library_missing")
    vasp_output = version_command([str(binary), "--version"], "vasp_version_probe_failed")
    vasp = re.search(r"\bvasp[.\s]+(\d+\.\d+\.\d+)", vasp_output, re.I)
    mpi_output = version_command([str(mpi), "--version"], "mpi_version_probe_failed")
    mpi_version = re.search(r"\bOpen MPI\)?\s+(\d+\.\d+\.\d+)", mpi_output, re.I)
    if not vasp or not mpi_version:
        raise WorkerError("unrecognized_vasp_or_openmpi_version")
    potential = Path(config["potential_root"])
    if not potential.is_dir() or not os.access(potential, os.R_OK | os.X_OK):
        raise WorkerError("potential_library_unavailable")
    return {"vasp_version": vasp[1], "mpi_version": mpi_version[1], "ubuntu_version": version_id[1] if version_id else "",
            "binary_sha256": file_hash(binary), "mpi_sha256": file_hash(mpi), "variant": config["variant"]}


def potential_bytes(config, selections):
    root = Path(config["potential_root"]).resolve(strict=True)
    parts = []
    for element, name in selections:
        if not re.fullmatch(r"[A-Z][a-z]?(?:_[A-Za-z0-9]+)*", name):
            raise WorkerError("invalid_potential_selection")
        path = (root / name / "POTCAR").resolve(strict=True)
        if not path.is_relative_to(root):
            raise WorkerError("potential_outside_library")
        raw = bounded(path, 8_000_000)
        titles = re.findall(rb"^\s*TITEL\s*=\s*\S+\s+([A-Z][a-z]?)(?:_\w+)?\s", raw, re.M)
        if titles != [element.encode("ascii")]:
            raise WorkerError("potential_element_mismatch")
        if parts and not parts[-1].endswith(b"\n"):
            raise WorkerError("potential_dataset_separator_missing")
        parts.append(raw)
        if sum(map(len, parts)) > 32_000_000:
            raise WorkerError("potential_total_size_exceeded")
    return b"".join(parts)


def job_directory(run_id):
    if str(UUID(run_id)) != run_id:
        raise WorkerError("invalid_run_id")
    root = Path.home() / ".sky-workbench-vasp"
    root.mkdir(mode=0o700, exist_ok=True)
    folder = root / run_id
    folder.mkdir(mode=0o700, exist_ok=True)
    for directory in (root, folder):
        if directory.is_symlink() or not directory.is_dir() or directory.stat().st_uid != os.geteuid() \
                or directory.stat().st_mode & 0o077:
            raise WorkerError("unsafe_linux_job_directory")
    return folder


@contextmanager
def launch_lock(folder):
    fd = os.open(folder / "launch.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        until = time.monotonic() + 8
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= until:
                    raise WorkerError("linux_launch_lock_busy")
                time.sleep(0.05)
        yield
    finally:
        os.close(fd)


def proc_stat(pid):
    try:
        fields = (Path("/proc") / str(pid) / "stat").read_text().rsplit(")", 1)[1].split()
        return fields[0], int(fields[2]), int(fields[19])
    except (OSError, ValueError, IndexError):
        return None


def tagged_pids(run_id):
    marker = f"{TAG}={run_id}".encode()
    result = {}
    for entry in Path("/proc").iterdir():
        if not entry.name.isascii() or not entry.name.isdigit():
            continue
        pid = int(entry.name)
        stat = proc_stat(pid)
        if not stat or stat[0] == "Z":
            continue
        try:
            if marker in (entry / "environ").read_bytes().split(b"\0"):
                result[pid] = stat
        except OSError:
            continue
    return result


def group_alive(group):
    if not group:
        return False
    for entry in Path("/proc").iterdir():
        if entry.name.isascii() and entry.name.isdigit():
            stat = proc_stat(int(entry.name))
            if stat and stat[0] != "Z" and stat[1] == group:
                return True
    return False


def send_to_job(run_id, identity, sig):
    tagged = tagged_pids(run_id)
    leader = identity.get("pid")
    if leader in tagged and tagged[leader][1:] == (leader, identity.get("start")):
        try:
            os.killpg(leader, sig)
        except ProcessLookupError:
            pass
    for pid, stat in tagged.items():
        try:
            fd = os.pidfd_open(pid)
            try:
                current = proc_stat(pid)
                if current and current[2] == stat[2]:
                    signal.pidfd_send_signal(fd, sig)
            finally:
                os.close(fd)
        except ProcessLookupError:
            continue


def exchange_path(value, run_id):
    path = Path(value)
    if not path.is_absolute() or path.name != run_id or path.is_symlink() or not path.is_dir():
        raise WorkerError("invalid_exchange_directory")
    return path


def collect(folder, exchange):
    omitted = []
    for name in OUTPUTS:
        path = folder / name
        if not path.is_file() or path.is_symlink():
            continue
        try:
            data = bounded(path, 24_000_000)
        except WorkerError:
            omitted.append(name)
            continue
        dest = exchange / name
        if not dest.exists():
            with dest.open("xb") as stream:
                stream.write(data)
    return omitted


def mpi_arguments(mpi, binary, ranks):
    # Open MPI 的 --host localhost 默认只分配一个 slot。
    return [str(mpi), "--host", f"localhost:{ranks}", "-np", str(ranks), "-x", TAG, str(binary)]


def cleanup(payload):
    run_id = payload["run_id"]
    folder = job_directory(run_id)
    exchange = exchange_path(payload["exchange"], run_id)
    if payload["cancel"]:
        (folder / "cancel.request").touch(mode=0o600, exist_ok=True)
    with launch_lock(folder):
        record = folder / "process.json"
        identity = json.loads(bounded(record, 4096)) if record.is_file() else {}
    group = identity.get("pid") if identity.get("boot") == BOOT.read_text().strip() else None
    if payload["cancel"]:
        for sig in (signal.SIGTERM, signal.SIGKILL):
            send_to_job(run_id, identity if group else {}, sig)
            until = time.monotonic() + 2
            while time.monotonic() < until and (tagged_pids(run_id) or group_alive(group)):
                time.sleep(0.05)
    reaped = not tagged_pids(run_id) and not group_alive(group)
    if reaped:
        return {"reaped": True, "omitted": collect(folder, exchange)}
    return {"reaped": False, "omitted": []}


def run(config, payload):
    run_id = payload["run_id"]
    folder = job_directory(run_id)
    exchange = exchange_path(payload["exchange"], run_id)
    with launch_lock(folder):
        if (folder / "cancel.request").exists() or (folder / "claimed").exists():
            raise WorkerError("linux_job_already_claimed_or_cancelled")
        (folder / "claimed").touch(mode=0o600, exist_ok=False)
        identity = probe(config)
        if identity != payload["engine_identity"]:
            raise WorkerError("wsl_engine_changed_after_review")
        raw_potential = potential_bytes(config, payload["selections"])
        if sha256(raw_potential).hexdigest() != payload["potential_sha256"]:
            raise WorkerError("potential_changed_after_review")
        for name in INPUTS:
            raw = bounded(exchange / name, 8_000_000)
            if sha256(raw).hexdigest() != payload["input_hashes"][name]:
                raise WorkerError("input_changed_after_review")
            with (folder / name).open("xb") as stream:
                stream.write(raw)
        with (folder / "POTCAR").open("xb") as stream:
            stream.write(raw_potential)
        binary, mpi = tool_paths(config)
        if (folder / "cancel.request").exists():
            raise WorkerError("linux_job_cancelled_before_submission")
        # 固定本机 MPI，传播任务标记到各 rank，便于跨 Windows/WSL 回收。
        args = mpi_arguments(mpi, binary, config["ranks"])
        process = subprocess.Popen(args, cwd=folder, start_new_session=True, env={**os.environ, TAG: run_id})
        stat = proc_stat(process.pid)
        temporary = folder / "process.tmp"
        with temporary.open("x", encoding="utf-8") as record:
            json.dump({"pid": process.pid, "start": stat[2] if stat else None,
                       "boot": BOOT.read_text().strip()}, record)
        temporary.replace(folder / "process.json")
    return process.wait()


def main():
    operation, raw_config, raw_payload = sys.argv[1:]
    config, payload = json.loads(raw_config), json.loads(raw_payload)
    try:
        if operation == "run":
            return run(config, payload)
        if operation == "probe":
            result = probe(config)
        elif operation == "potentials":
            result = {"potential_sha256": sha256(potential_bytes(config, payload["selections"])).hexdigest()}
        elif operation == "cleanup":
            result = cleanup(payload)
        else:
            raise WorkerError("unsupported_worker_operation")
    except (WorkerError, OSError, ValueError, KeyError) as exc:
        result = {"error": str(exc) if isinstance(exc, WorkerError) else "linux_access_or_process_error"}
        if operation == "run":
            print(MARKER + json.dumps(result), flush=True)
            return 65
    print(MARKER + json.dumps(result, ensure_ascii=True), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
