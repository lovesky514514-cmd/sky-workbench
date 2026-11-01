"""Replay 25 paired requests in an isolated workspace with optional real engines."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import sys
import time


ROOT = Path(__file__).resolve().parents[1]
CORPUS = ROOT / "backend/tests/fixtures/modeling_pairs_25.json"


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument(
        "--execute-installed",
        action="store_true",
        help="Explicitly run supported isolated fixtures",
    )
    parser.add_argument("--lammps", type=Path)
    parser.add_argument("--mathics-python", type=Path)
    args = parser.parse_args()
    output = args.out.resolve()
    output.mkdir(parents=True, exist_ok=False)
    # Import the application only after its paths and test-only principal are set.
    token = os.urandom(32).hex()
    os.environ.update(
        COMSOL_WORKBENCH_WORKSPACE_DIR=str(output / "workspace"),
        COMSOL_WORKBENCH_APP_DATA_DIR=str(output / "appdata"),
        COMSOL_WORKBENCH_KB_DIR=str(output / "empty_kb"),
        COMSOL_WORKBENCH_SESSION_TOKEN=token,
    )
    if args.lammps:
        os.environ["SKY_LAMMPS_EXECUTABLE"] = str(args.lammps.resolve())
    if args.mathics_python:
        os.environ["SKY_MATHICS_PYTHON"] = str(args.mathics_python.resolve())
    sys.path.insert(0, str(ROOT / "backend"))
    from fastapi.testclient import TestClient
    from app import main as api
    from app.agent.planner import ScientificPlanner
    from app.simulation_adapters import capabilities
    from app.source_revision import source_revision

    class Offline:
        async def complete(self, *args, **kwargs):
            return None

    # Deterministic replay is intentional: do not silently use a paid or private
    # provider, and do not describe this coverage as LLM scientific accuracy.
    api.agent_runtime.planner = ScientificPlanner(Offline())
    api.agent_runtime._retrieve = lambda *args: []
    before = source_revision()
    installed = capabilities()
    write_json(output / "environment.json", installed)
    cases = json.loads(CORPUS.read_text(encoding="utf-8"))
    records = []
    with TestClient(api.app, headers={"X-Workbench-Token": token}) as client:
        for case in cases:
            for group in ("vague", "explicit"):
                sid = client.post(
                    "/api/sessions", json={"title": f"{case['id']:02}-{group}"}
                ).json()["id"]
                url = f"/api/sessions/{sid}"
                prior = set(api.agent_runtime._tasks)
                response = client.post(
                    url + "/messages", json={"mode": "PLAN", "content": case[group]}
                )
                response.raise_for_status()
                plan = response.json()["plan"]
                checks = {
                    "structured_draft": bool(
                        plan["physics"]
                        and plan["geometry"]
                        and plan["expected_outputs"]
                    ),
                    "literal_spans": all(
                        case[group][slice(*f["span"])] == f["original"]
                        for f in plan["modeling_brief"]["facts"]
                    ),
                    "workflow_scope": plan["workflow_id"] is None
                    or plan["workflow_id"] in case["allowed_workflows"],
                    "plan_did_not_launch": prior == set(api.agent_runtime._tasks),
                    "vague_not_auto_ready": group != "vague" or not plan["runnable"],
                }
                record = dict(
                    id=case["id"],
                    title=case["title"],
                    group=group,
                    engine=case["engine"],
                    prompt=case[group],
                    session_id=sid,
                    plan=plan,
                    checks=checks,
                )
                if not plan["runnable"]:
                    refusal = client.post(
                        url + "/messages",
                        json={
                            "mode": "RUN",
                            "content": "Isolated negative test: incomplete or unsupported draft",
                        },
                    )
                    refusal.raise_for_status()
                    checks["blocked_run_did_not_launch"] = prior == set(
                        api.agent_runtime._tasks
                    )
                    checks["blocked_run_not_completed"] = (
                        refusal.json()["state"] != "COMPLETED"
                    )
                available = next(
                    (r for r in installed if r["engine"] == case["engine"]), None
                )
                record["execution"] = {
                    "status": "SKIPPED_SOFTWARE_UNAVAILABLE"
                    if available and not available["installed"]
                    else "DRAFT_ONLY",
                    "scientific_validation": "NOT_EVALUATED",
                }
                records.append(record)
                print(
                    f"{case['id']:02} {group}: {plan['workflow_id'] or 'draft'}; ready={plan['runnable']}",
                    flush=True,
                )
        write_json(output / "drafts.json", records)
        if (
            args.execute_installed
            and next(r for r in installed if r["engine"] == "COMSOL")["installed"]
        ):
            # Cooling still requires review of its proposed structural choices.
            for record in records:
                if record["group"] != "explicit" or record["id"] not in {
                    1,
                    5,
                    7,
                    11,
                    12,
                }:
                    continue
                url = f"/api/sessions/{record['session_id']}"
                plan = record["plan"]
                if plan["completion"]:
                    p = plan["completion"]
                    reply = client.post(
                        url + "/plan/completion",
                        json={
                            "proposal_id": p["proposal_id"],
                            "expected_revision": plan["revision"],
                            "reviewed_digest": p["reviewed_digest"],
                            "mode": "review",
                            "values": {},
                        },
                    )
                    reply.raise_for_status()
                    plan = reply.json()["plan"]
                    record["test_review"] = {
                        "actor": "isolated automated fixture",
                        "added_assumptions": p["parameters"],
                        "decisions": p["decisions"],
                    }
                if not plan["runnable"]:
                    record["execution"] = {
                        "status": "BLOCKED_PREFLIGHT",
                        "scientific_validation": "NOT_EVALUATED",
                    }
                    continue
                reply = client.post(
                    url + "/messages",
                    json={"mode": "RUN", "content": "Explicit isolated acceptance RUN"},
                )
                reply.raise_for_status()
                if (
                    record["session_id"] not in api.agent_runtime._tasks
                    and reply.json()["state"] != "COMPLETED"
                ):
                    write_json(output / f"run_{record['id']:02}.json", reply.json())
                    record["execution"] = {
                        "status": "BLOCKED_RUN",
                        "session_evidence": f"run_{record['id']:02}.json",
                    }
                    print(f"Real COMSOL {record['id']:02}: BLOCKED_RUN", flush=True)
                    continue
                deadline = time.monotonic() + 480
                final = reply.json()
                while time.monotonic() < deadline:
                    final = client.get(url).json()
                    write_json(output / f"run_{record['id']:02}.json", final)
                    if final["state"] in {
                        "COMPLETED",
                        "FAILED",
                        "VALIDATION_FAILED",
                        "CANCELED",
                    }:
                        break
                    time.sleep(1)
                if final["state"] not in {
                    "COMPLETED",
                    "FAILED",
                    "VALIDATION_FAILED",
                    "CANCELED",
                }:
                    raise RuntimeError(
                        "Acceptance timeout; inspect isolated run before retrying"
                    )
                record["execution"] = {
                    "status": final["state"],
                    "session_evidence": f"run_{record['id']:02}.json",
                }
                print(f"Real COMSOL {record['id']:02}: {final['state']}", flush=True)
    after = source_revision()
    summary = {
        group: {
            "count": len(rows := [r for r in records if r["group"] == group]),
            "structured_drafts": sum(r["checks"]["structured_draft"] for r in rows),
            "false_workflow_triggers": sum(
                not r["checks"]["workflow_scope"] for r in rows
            ),
            "ready_before_review": sum(r["plan"]["runnable"] for r in rows),
            "actually_completed": sum(
                r["execution"]["status"] == "COMPLETED" for r in rows
            ),
        }
        for group in ("vague", "explicit")
    }
    report = dict(
        metric="Structured draft generation and execution boundary coverage; not scientific or LLM accuracy",
        corpus_sha256=hashlib.sha256(CORPUS.read_bytes()).hexdigest(),
        source_before=before,
        source_after=after,
        source_unchanged=before == after,
        provider="offline; no real LLM",
        summary=summary,
        records=records,
        approval_scope="Isolated automated acceptance only; no production user project approvals",
    )
    write_json(output / "report.json", report)
    print(json.dumps(summary, ensure_ascii=False))
    return (
        0
        if before == after
        and all(all(r["checks"].values()) for r in records)
        and all(
            r["execution"]["status"] == "COMPLETED"
            for r in records
            if args.execute_installed and r.get("execution", {}).get("session_evidence")
        )
        else 1
    )


if __name__ == "__main__":
    raise SystemExit(main())
