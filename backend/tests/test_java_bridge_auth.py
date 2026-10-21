"""The Java HTTP listener must reject callers outside its owning Runtime."""

import asyncio
import os
import unittest
from unittest.mock import patch
from pathlib import Path

import httpx

from app.comsol.java_bridge import JavaBridgeClient
from app.comsol.bridge_auth import BRIDGE_TOKEN_HEADER, new_bridge_token
from app.comsol.bridge_auth import BRIDGE_TOKEN_ENV
from app.comsol.bridge_process import _bridge_child_environment


class JavaBridgeAuthTests(unittest.TestCase):
    def test_packaged_and_tauri_bridge_source_match(self) -> None:
        root = Path(__file__).resolve().parents[2]
        packaged = root / "java_bridge/src/main/java/workbench/bridge/ComsolBridgeServer.java"
        tauri = root / "frontend/src-tauri/resources/java_bridge/src/main/java/workbench/bridge/ComsolBridgeServer.java"
        self.assertEqual(packaged.read_bytes(), tauri.read_bytes())

    def test_each_launch_uses_an_independent_256_bit_token(self) -> None:
        first, second = new_bridge_token(), new_bridge_token()
        self.assertRegex(first, r"^[0-9a-f]{64}$")
        self.assertNotEqual(first, second)

    def test_child_environment_does_not_inherit_unrelated_credentials(self) -> None:
        token = new_bridge_token()
        with patch.dict(os.environ, {"SYNTHETIC_DIAGNOSTIC_API_KEY": "not-a-real-key"}):
            child = _bridge_child_environment(token)
            self.assertNotIn("SYNTHETIC_DIAGNOSTIC_API_KEY", child)
            self.assertNotIn(BRIDGE_TOKEN_ENV, os.environ)
        self.assertEqual(child[BRIDGE_TOKEN_ENV], token)


    def test_client_fails_closed_without_an_owned_bridge_token(self) -> None:
        observed = []

        def handler(request: httpx.Request) -> httpx.Response:
            observed.append(request)
            return httpx.Response(200, json={"command_success": True})

        client = JavaBridgeClient("http://bridge.test", httpx.MockTransport(handler), token_provider=lambda: None)
        self.assertEqual(asyncio.run(client.health())["status"], "Unavailable")
        with self.assertRaisesRegex(ValueError, "authentication unavailable"):
            asyncio.run(client.set_parameter("wb_probe_test", "1"))
        self.assertEqual(observed, [])


    def test_client_authenticates_every_direct_bridge_request(self) -> None:
        token = new_bridge_token()
        observed = []

        def handler(request: httpx.Request) -> httpx.Response:
            self.assertEqual(request.headers.get(BRIDGE_TOKEN_HEADER), token)
            observed.append((request.method, request.url.path))
            if request.url.path == "/events":
                return httpx.Response(200, json={"latest_sequence": 0, "events": []})
            if request.url.path == "/health":
                return httpx.Response(200, json={"connected": True})
            return httpx.Response(200, json={"command_success": True})

        client = JavaBridgeClient("http://bridge.test", httpx.MockTransport(handler), token_provider=lambda: token)

        async def exercise() -> None:
            await client.health()
            await client.model_tree()
            await client.parameters()
            await client.set_parameter("wb_probe_test", "1")
            await client.remove_parameter("wb_probe_test")
            await client.boundary_feature(component_tag="comp1", physics_tag="ht", feature_tag="temp1", property_key="T0")
            await client.heat_core()
            await client.set_boundary_feature(component_tag="comp1", physics_tag="ht", feature_tag="temp1",
                                              property_key="T0", expected_selection=1,
                                              expected_expression="293[K]", proposed_expression="294[K]")
            await client.run_study()
            await client.heat_field()
            await client.export_corrected_heat("run", "attempt")
            await client.test_feature({"tag": "wb_probe_test"})
            await client.events()

        asyncio.run(exercise())
        self.assertEqual(len(observed), 13)
        self.assertIn(("POST", "/model/study/run"), observed)
        self.assertIn(("POST", "/model/boundary-feature"), observed)


    def test_token_never_enters_client_payload_or_error_text(self) -> None:
        token = new_bridge_token()

        def handler(request: httpx.Request) -> httpx.Response:
            self.assertNotIn(token, request.url.path)
            self.assertNotIn(token, request.content.decode("utf-8", errors="ignore"))
            return httpx.Response(401, json={"detail": "Unauthorized"})

        client = JavaBridgeClient("http://bridge.test", httpx.MockTransport(handler), token_provider=lambda: token)
        with self.assertRaises(httpx.HTTPStatusError) as caught:
            asyncio.run(client.model_tree())
        self.assertNotIn(token, str(caught.exception))
