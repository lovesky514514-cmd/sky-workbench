import asyncio
import json

import httpx

from app.comsol.java_bridge import JavaBridgeClient

TEST_BRIDGE_TOKEN = "a" * 64


def test_bridge_health_keeps_unavailable_and_three_success_facts_separate() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/health"
        return httpx.Response(
            200,
            json={
                "status": "unavailable",
                "bridge_running": True,
                "connected": False,
                "real_comsol": False,
                "detail": "COMSOL ModelUtil is not on the Java Bridge classpath",
                "command_success": True,
                "model_state_verified": False,
                "scientific_validation_passed": False,
                "model_tags": [],
            },
        )

    client = JavaBridgeClient("http://bridge.test", httpx.MockTransport(handler),
                              token_provider=lambda: TEST_BRIDGE_TOKEN)
    status = asyncio.run(client.health())
    assert status["status"] == "Unavailable"
    assert status["bridge_running"] is True
    assert status["connected"] is False
    assert status["command_success"] is True
    assert status["model_state_verified"] is False
    assert status["scientific_validation_passed"] is False


def test_bridge_crash_is_reported_as_unavailable_not_success() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("bridge stopped")

    client = JavaBridgeClient("http://bridge.test", httpx.MockTransport(handler),
                              token_provider=lambda: TEST_BRIDGE_TOKEN)
    status = asyncio.run(client.health())
    assert status["status"] == "Unavailable"
    assert status["bridge_running"] is False
    assert status["command_success"] is False
    assert "bridge stopped" in status["detail"]


def test_bridge_event_contract_normalizes_typed_events() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params["after"] == "4"
        return httpx.Response(
            200,
            content=json.dumps(
                {
                    "latest_sequence": 5,
                    "events": [
                        {
                            "sequence": 5,
                            "event_type": "model_change",
                            "phase": "model_tree",
                            "status": "verified",
                            "detail": "readback",
                            "target": "param:wb_probe_temperature",
                            "action": "updated",
                            "old_value": "293[K]",
                            "new_value": "373[K]",
                            "detection_method": "direct_readback",
                            "command_success": True,
                            "model_state_verified": True,
                            "scientific_validation_passed": False,
                        }
                    ],
                }
            ).encode(),
        )

    client = JavaBridgeClient("http://bridge.test", httpx.MockTransport(handler),
                              token_provider=lambda: TEST_BRIDGE_TOKEN)
    payload = asyncio.run(client.events(after=4))
    assert payload["latest_sequence"] == 5
    assert payload["events"][0]["event_type"] == "model_change"
    assert payload["events"][0]["target"] == "param:wb_probe_temperature"
    assert payload["events"][0]["old_value"] == "293[K]"
    assert payload["events"][0]["new_value"] == "373[K]"
    assert payload["events"][0]["model_state_verified"] is True
    assert payload["events"][0]["scientific_validation_passed"] is False


def test_bridge_parameter_and_study_calls_preserve_real_verification_fields() -> None:
    calls: list[tuple[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append((request.method, request.url.path))
        if request.method == "GET" and request.url.path == "/model/parameters":
            return httpx.Response(200, json={"parameters": {"wb_probe_temperature": "373[K]"}, "model_state_verified": True})
        if request.method == "POST" and request.url.path == "/model/parameter":
            assert json.loads(request.content) == {"name": "wb_probe_temperature", "value": "373[K]"}
            return httpx.Response(200, json={"command_success": True, "model_state_verified": True, "readback": "373[K]"})
        if request.method == "DELETE" and request.url.path == "/model/parameter":
            assert request.url.params["name"] == "wb_probe_temperature"
            return httpx.Response(200, json={"command_success": True, "model_state_verified": True, "readback": ""})
        if request.method == "POST" and request.url.path == "/model/study/run":
            assert json.loads(request.content) == {"study_tag": "std1"}
            return httpx.Response(
                200,
                json={
                    "command_success": True,
                    "model_state_verified": True,
                    "scientific_validation_passed": False,
                    "last_computation_date": 1,
                },
            )
        raise AssertionError(f"Unexpected request: {request.method} {request.url}")

    client = JavaBridgeClient("http://bridge.test", httpx.MockTransport(handler),
                              token_provider=lambda: TEST_BRIDGE_TOKEN)
    assert asyncio.run(client.parameters())["parameters"]["wb_probe_temperature"] == "373[K]"
    assert asyncio.run(client.set_parameter("wb_probe_temperature", "373[K]"))["readback"] == "373[K]"
    assert asyncio.run(client.remove_parameter("wb_probe_temperature"))["model_state_verified"] is True
    study = asyncio.run(client.run_study("std1"))
    assert study["command_success"] is True
    assert study["model_state_verified"] is True
    assert study["scientific_validation_passed"] is False
    assert calls == [
        ("GET", "/model/parameters"),
        ("POST", "/model/parameter"),
        ("DELETE", "/model/parameter"),
        ("POST", "/model/study/run"),
    ]
