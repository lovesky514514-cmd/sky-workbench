"""Run real DeepSeek planning and reviewed solver fixtures in an isolated project."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]


def save(path: Path, data):
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


async def run(args):
    import httpx
    from app import main as api
    from app.agent.planner import ScientificPlanner
    from app.config import DEFAULT_SETTINGS
    from app.providers.deepseek import DEFAULT_MODEL
    from app.providers.manager import ProviderManager
    from app.simulation_adapters import capabilities
    from app.source_revision import source_revision

    calls = []

    class ObservedDeepSeek(ProviderManager):
        async def request_started(self, request):
            if request.url.host != "api.deepseek.com":
                raise ValueError(
                    "Acceptance permits the official DeepSeek endpoint only"
                )
            if len(calls) >= 160:
                raise ValueError("Acceptance request budget reached")
            payload = json.loads(request.content)
            request.extensions["acceptance_index"] = len(calls)
            calls.append(
                {
                    "index": len(calls),
                    "model": payload.get("model"),
                    "stream": payload.get("stream", False),
                }
            )

        async def response_received(self, response):
            index = response.request.extensions["acceptance_index"]
            call = calls[index]
            call["http_status"] = response.status_code
            if response.status_code != 200 or call["stream"]:
                return
            await response.aread()
            body = response.json()
            choice = body.get("choices", [{}])[0]
            call["usage"] = body.get("usage", {})
            call["finish_reason"] = choice.get("finish_reason")
            # Keep only the public fixture answer, never headers or reasoning.
            answer = {
                "model": body.get("model"),
                "content": choice.get("message", {}).get("content"),
                "finish_reason": choice.get("finish_reason"),
            }
            save(args.out / f"response_{index:03}.json", answer)

        def _client(self, timeout):
            return httpx.AsyncClient(
                timeout=timeout,
                event_hooks={
                    "request": [self.request_started],
                    "response": [self.response_received],
                },
            )

    api.settings_store.update(
        {
            "active_provider": "deepseek",
            "providers": {
                name: {
                    "enabled": name == "deepseek",
                    **(
                        {"model": args.model or DEFAULT_MODEL}
                        if name == "deepseek"
                        else {}
                    ),
                }
                for name in DEFAULT_SETTINGS["providers"]
            },
        }
    )
    if not api.settings_store.secret("deepseek"):
        raise RuntimeError("Configure DEEPSEEK_API_KEY locally before live acceptance")
    manager = ObservedDeepSeek(api.settings_store)
    api.agent_runtime.planner = ScientificPlanner(manager)
    api.agent_runtime._retrieve = lambda *args: []
    before = source_revision()
    installed = capabilities()
    corpus = json.loads(
        (ROOT / "backend/tests/fixtures/modeling_pairs_25.json").read_text(
            encoding="utf-8"
        )
    )
    selected = [row for row in corpus if not args.case or row["id"] in args.case]
    jobs = [
        (row, group, row[group]) for row in selected for group in ("vague", "explicit")
    ]
    jobs.append(
        (
            {
                "id": "iron_original",
                "title": "用户原话",
                "allowed_workflows": ["heat_transfer_3d_cooling"],
            },
            "vague",
            "我想做一个3x3x20mm的烧红的铁块，现在想分析热变化。",
        )
    )
    semaphore = asyncio.Semaphore(args.concurrency)
    records = []
    async with api.app.router.lifespan_context(api.app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=api.app),
            base_url="http://acceptance",
            headers={"X-Workbench-Token": os.environ["COMSOL_WORKBENCH_SESSION_TOKEN"]},
            timeout=240,
        ) as client:

            async def draft(job):
                case, group, prompt = job
                async with semaphore:
                    response = await client.post(
                        "/api/sessions",
                        json={"title": f"DeepSeek {case['id']} {group}"},
                    )
                    response.raise_for_status()
                    sid = response.json()["id"]
                    url = f"/api/sessions/{sid}"
                    response = await client.post(
                        url + "/messages", json={"mode": "PLAN", "content": prompt}
                    )
                    response.raise_for_status()
                    session = response.json()
                    plan = session.get("plan")
                    checks = {
                        "structured_draft": bool(
                            plan
                            and plan["physics"]
                            and plan["geometry"]
                            and plan["expected_outputs"]
                        ),
                        "no_solve_from_plan": session.get("model") is None
                        and sid not in api.agent_runtime._tasks,
                        "live_provider": bool(
                            plan
                            and plan["ai_provider"] == "DeepSeek API"
                            and plan["ai_model"] == (args.model or DEFAULT_MODEL)
                        ),
                    }
                    if plan:
                        checks.update(
                            literal_spans=all(
                                prompt[slice(*f["span"])] == f["original"]
                                for f in plan["modeling_brief"]["facts"]
                            ),
                            workflow_scope=plan["workflow_id"] is None
                            or plan["workflow_id"] in case["allowed_workflows"],
                            generated_values_unapproved=all(
                                not value["use_authorized"]
                                and not value["user_confirmed"]
                                and not value["verified"]
                                for value in (plan.get("completion") or {})
                                .get("parameters", {})
                                .values()
                            ),
                        )
                        if not plan["runnable"]:
                            blocked = await client.post(
                                url + "/messages",
                                json={
                                    "mode": "RUN",
                                    "content": "Isolated unapproved-plan refusal test",
                                },
                            )
                            blocked.raise_for_status()
                            checks["unapproved_run_blocked"] = (
                                sid not in api.agent_runtime._tasks
                                and blocked.json()["state"] != "COMPLETED"
                            )
                    record = {
                        "id": case["id"],
                        "group": group,
                        "title": case["title"],
                        "prompt": prompt,
                        "session_id": sid,
                        "plan": plan,
                        "checks": checks,
                        "execution": {"status": "DRAFT_ONLY"},
                    }
                    save(args.out / f"draft_{case['id']}_{group}.json", record)
                    print(
                        f"DeepSeek {case['id']} {group}: live={checks['live_provider']} structured={checks['structured_draft']}",
                        flush=True,
                    )
                    return record

            records = await asyncio.gather(*(draft(job) for job in jobs))
            if (
                args.execute_installed
                and next(row for row in installed if row["engine"] == "COMSOL")[
                    "installed"
                ]
            ):
                for record in records:
                    exercise = (
                        record["group"] == "explicit"
                        and record["id"] in {1, 5, 7, 11, 12}
                        or record["id"] in {1, "iron_original"}
                        and record["group"] == "vague"
                    )
                    if (
                        not exercise
                        or not record["plan"]
                        or not all(record["checks"].values())
                    ):
                        continue
                    plan = record["plan"]
                    url = f"/api/sessions/{record['session_id']}"
                    if plan["completion"]:
                        proposal = plan["completion"]
                        record["test_review"] = {
                            "actor": "isolated automated fixture",
                            "parameters": proposal["parameters"],
                            "decisions": proposal["decisions"],
                        }
                        response = await client.post(
                            url + "/plan/completion",
                            json={
                                "proposal_id": proposal["proposal_id"],
                                "reviewed_digest": proposal["reviewed_digest"],
                                "expected_revision": plan["revision"],
                                "mode": "review",
                                "values": {},
                            },
                        )
                        response.raise_for_status()
                        plan = response.json()["plan"]
                    if not plan["runnable"]:
                        record["execution"] = {
                            "status": "BLOCKED_PREFLIGHT",
                            "plan_after_review": plan,
                        }
                        continue
                    response = await client.post(
                        url + "/messages",
                        json={
                            "mode": "RUN",
                            "content": "Explicit isolated real DeepSeek acceptance RUN",
                        },
                    )
                    response.raise_for_status()
                    session = response.json()
                    deadline = time.monotonic() + 600
                    while (
                        record["session_id"] in api.agent_runtime._tasks
                        and time.monotonic() < deadline
                    ):
                        response = await client.get(url)
                        response.raise_for_status()
                        session = response.json()
                        save(
                            args.out / f"run_{record['id']}_{record['group']}.json",
                            session,
                        )
                        if session["state"] in {
                            "COMPLETED",
                            "FAILED",
                            "VALIDATION_FAILED",
                            "CANCELED",
                        }:
                            break
                        await asyncio.sleep(0.5)
                    response = await client.get(url)
                    response.raise_for_status()
                    session = response.json()
                    if session["state"] not in {"COMPLETED", "FAILED", "VALIDATION_FAILED", "CANCELED"}:
                        # Reclaim only this isolated fixture before starting
                        # another run; timeout is never counted as completion.
                        response = await client.post(url + "/run/cancel", json={"force": True})
                        response.raise_for_status()
                        response = await client.get(url)
                        response.raise_for_status()
                        session = response.json()
                        record["test_timeout_seconds"] = 600
                    save(
                        args.out / f"run_{record['id']}_{record['group']}.json", session
                    )
                    record["execution"] = {
                        "status": session["state"],
                        "evidence": f"run_{record['id']}_{record['group']}.json",
                    }
                    print(
                        f"Actual COMSOL {record['id']} {record['group']}: {session['state']}",
                        flush=True,
                    )
    source_revision.cache_clear()
    after = source_revision()
    summary = {
        group: {
            "total": len(rows := [r for r in records if r["group"] == group]),
            "live_provider": sum(r["checks"]["live_provider"] for r in rows),
            "structured_drafts": sum(r["checks"]["structured_draft"] for r in rows),
            "workflow_scope_failures": sum(
                not r["checks"].get("workflow_scope", False) for r in rows
            ),
            "actually_completed": sum(
                r["execution"]["status"] == "COMPLETED" for r in rows
            ),
        }
        for group in ("vague", "explicit")
    }
    passed = (
        before == after
        and all(all(r["checks"].values()) for r in records)
        and all(
            r["execution"]["status"] == "COMPLETED"
            for r in records
            if "test_review" in r or "evidence" in r["execution"]
        )
    )
    report = {
        "passed": passed,
        "source_before": before,
        "source_after": after,
        "summary": summary,
        "records": records,
        "requests": calls,
        "software": installed,
        "metric": "Live LLM structured planning and reviewed execution; not general scientific accuracy",
        "approval_scope": "Isolated automated engineering fixtures only; no production project approvals",
    }
    save(args.out / "report.json", report)
    key = api.settings_store.secret("deepseek").encode()
    assert all(key not in p.read_bytes() for p in args.out.glob("*.json"))
    print(
        json.dumps(
            {"passed": passed, "summary": summary, "requests": len(calls)},
            ensure_ascii=False,
        )
    )
    return 0 if passed else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--model")
    parser.add_argument("--case", type=int, action="append")
    parser.add_argument("--concurrency", type=int, choices=range(1, 5), default=3)
    parser.add_argument("--execute-installed", action="store_true")
    args = parser.parse_args()
    args.out = args.out.resolve()
    args.out.mkdir(parents=True, exist_ok=False)
    os.environ.update(
        COMSOL_WORKBENCH_WORKSPACE_DIR=str(args.out / "workspace"),
        COMSOL_WORKBENCH_APP_DATA_DIR=str(args.out / "appdata"),
        COMSOL_WORKBENCH_KB_DIR=str(args.out / "empty_kb"),
        COMSOL_WORKBENCH_SESSION_TOKEN=os.urandom(32).hex(),
    )
    sys.path.insert(0, str(ROOT / "backend"))
    try:
        raise SystemExit(asyncio.run(run(args)))
    except Exception as exc:
        save(args.out / "failure.json", {"error_class": type(exc).__name__})
        raise SystemExit(type(exc).__name__)
