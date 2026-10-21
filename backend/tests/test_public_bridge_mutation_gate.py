"""Public HTTP must not bypass the scientific plan/run approval path."""

from __future__ import annotations

import asyncio
import os
from unittest.mock import AsyncMock, patch

import httpx

from app.main import app, agent_runtime, java_bridge_client, java_bridge_process_manager, session_store


BASE = "http://localhost"
TOKEN = "synthetic-test-session-token"
HEADERS = {"X-Workbench-Token": TOKEN}


async def send_requests(requests: list[tuple[str, str, dict | None]], headers: dict[str, str]):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=BASE) as client:
        results = []
        for method, path, payload in requests:
            results.append(await client.request(method, path, json=payload, headers=headers))
        return results


DIRECT_MUTATIONS = [
    ("POST", "/api/comsol/bridge/parameter", {"name": "temperature", "value": "373[K]"}),
    ("DELETE", "/api/comsol/bridge/parameter/wb_probe_test", None),
    ("DELETE", "/api/comsol/bridge/parameter/temperature", None),
    ("POST", "/api/comsol/bridge/study/run", {"study_tag": "std1"}),
]


def test_valid_desktop_token_cannot_mutate_without_scientific_plan():
    with patch.dict(os.environ, {"COMSOL_WORKBENCH_SESSION_TOKEN": TOKEN}):
        with (
            patch.object(java_bridge_client, "set_parameter", new_callable=AsyncMock) as write,
            patch.object(java_bridge_client, "remove_parameter", new_callable=AsyncMock) as delete,
            patch.object(java_bridge_client, "run_study", new_callable=AsyncMock) as solve,
            patch.object(java_bridge_process_manager, "status", side_effect=AssertionError("unexpected Bridge probe")),
            patch.object(session_store, "acquire_bridge_owner", side_effect=AssertionError("unexpected owner mutation")),
        ):
            results = asyncio.run(send_requests(DIRECT_MUTATIONS, HEADERS))
            assert all(r.status_code == 403 for r in results)
            assert all(r.json()["detail"].startswith("scientific_plan_required:") for r in results)
            write.assert_not_called()
            delete.assert_not_called()
            solve.assert_not_called()


def test_no_desktop_token_cannot_mutate_either():
    with patch.dict(os.environ, {"COMSOL_WORKBENCH_SESSION_TOKEN": TOKEN}):
        results = asyncio.run(send_requests(DIRECT_MUTATIONS, {}))
    assert all(r.status_code == 401 for r in results)


def test_read_only_bridge_endpoint_still_works():
    with patch.dict(os.environ, {"COMSOL_WORKBENCH_SESSION_TOKEN": TOKEN}):
        with patch.object(java_bridge_client, "parameters", new_callable=AsyncMock) as read:
            read.return_value = {"parameters": {}, "model_state_verified": False}
            results = asyncio.run(send_requests([("GET", "/api/comsol/bridge/parameters", None)], HEADERS))
            assert results[0].status_code == 200
            assert results[0].json()["model_state_verified"] is False
            read.assert_awaited_once()


def test_registered_agent_session_entry_remains_routed_to_runtime():
    # Routing availability only, NOT proof that an actual scientific plan passed.
    with patch.dict(os.environ, {"COMSOL_WORKBENCH_SESSION_TOKEN": TOKEN}):
        with patch.object(agent_runtime, "handle_message", new_callable=AsyncMock) as invoke:
            invoke.return_value = {"accepted": True, "scientific_verdict": "NOT_RUN"}
            results = asyncio.run(send_requests([(
                "POST", "/api/sessions/synthetic-session/messages",
                {"mode": "RUN", "content": "synthetic test request"},
            )], HEADERS))
            assert results[0].status_code == 200
            assert results[0].json()["scientific_verdict"] == "NOT_RUN"
            invoke.assert_awaited_once_with("synthetic-session", "RUN", "synthetic test request")
