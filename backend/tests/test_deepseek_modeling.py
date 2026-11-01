"""Cloud JSON compatibility and reviewed suggestions do not grant execution."""

import asyncio
import json

import httpx
import pytest

from app.agent.planner import ScientificPlanner, enforce_deterministic_contract
from app.config import SettingsStore
from app.providers.deepseek import DEFAULT_MODEL, planning_schema
from app.providers.manager import ProviderManager
from app.scientific.cooling import cooling_plan
from app.scientific.plan_completion import accept_completion
from app.scientific.schemas import ScientificParameter, ScientificPlan
from app.scientific.solver_gate import solver_input_issues


def store(tmp_path):
    config = SettingsStore(tmp_path / "settings.json")
    config.update(
        {
            "active_provider": "deepseek",
            "providers": {"deepseek": {"enabled": True, "api_key": "test-only-key"}},
        }
    )
    return config


def test_deepseek_json_uses_supported_mode_with_schema_in_prompt(tmp_path):
    schema = {
        "type": "object",
        "properties": {"value": {"type": "integer"}},
        "required": ["value"],
    }

    async def handler(request):
        payload = json.loads(request.content)
        assert payload["model"] == DEFAULT_MODEL and payload["response_format"] == {
            "type": "json_object"
        }
        assert any(
            json.dumps(schema, separators=(",", ":")) in m["content"]
            for m in payload["messages"]
        )
        return httpx.Response(
            200,
            json={
                "choices": [
                    {"finish_reason": "stop", "message": {"content": '{"value":7}'}}
                ]
            },
        )

    manager = ProviderManager(store(tmp_path), httpx.MockTransport(handler))
    result = asyncio.run(
        manager.complete(
            [{"role": "user", "content": "return a value"}],
            json_mode=True,
            json_schema=schema,
        )
    )
    assert (
        result
        and result.provider == "deepseek"
        and json.loads(result.text) == {"value": 7}
    )
    assert "test-only-key" not in manager.store.path.read_text(encoding="utf-8")


@pytest.mark.parametrize(
    "content,finish",
    [("   ", "stop"), ('{"value":7}', "length"), ('{"value":7}', "content_filter")],
)
def test_blank_or_incomplete_generation_is_never_accepted(tmp_path, content, finish):
    manager = ProviderManager(
        store(tmp_path),
        httpx.MockTransport(
            lambda req: httpx.Response(
                200,
                json={
                    "choices": [
                        {"finish_reason": finish, "message": {"content": content}}
                    ]
                },
            )
        ),
    )
    assert (
        asyncio.run(
            manager.complete([{"role": "user", "content": "json"}], json_mode=True)
        )
        is None
    )


def test_saved_explicit_model_is_preserved(tmp_path):
    config = store(tmp_path)
    config.update({"providers": {"deepseek": {"model": "explicit-user-model"}}})
    assert config.load()["providers"]["deepseek"]["model"] == "explicit-user-model"


@pytest.mark.parametrize(
    "first_finish,first_text", [("length", '{"value":'), ("stop", "   ")]
)
def test_deepseek_json_retries_one_incomplete_answer_with_same_endpoint_and_model(
    tmp_path, first_finish, first_text
):
    calls = []

    def handler(request):
        payload = json.loads(request.content)
        calls.append(payload)
        assert str(request.url) == "https://api.deepseek.com/v1/chat/completions"
        assert request.headers["Authorization"] == "Bearer test-only-key"
        assert payload["model"] == DEFAULT_MODEL
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "finish_reason": first_finish if len(calls) == 1 else "stop",
                        "message": {
                            "content": first_text if len(calls) == 1 else '{"value":7}'
                        },
                    }
                ]
            },
        )

    manager = ProviderManager(store(tmp_path), httpx.MockTransport(handler))
    result = asyncio.run(
        manager.complete([{"role": "user", "content": "Return JSON"}], json_mode=True)
    )
    assert result and result.text == '{"value":7}'
    assert len(calls) == 2 and calls[1]["reasoning_effort"] == "none"
    assert calls[0]["messages"] == calls[1]["messages"]


@pytest.mark.parametrize(
    "finish,expected_calls", [("length", 2), ("content_filter", 1)]
)
def test_deepseek_json_retry_is_bounded_and_never_retries_refusals(
    tmp_path, finish, expected_calls
):
    calls = []

    def handler(request):
        if request.method != "POST":
            return httpx.Response(404)
        calls.append(request)
        return httpx.Response(
            200,
            json={
                "choices": [
                    {"finish_reason": finish, "message": {"content": '{"value":7}'}}
                ]
            },
        )

    manager = ProviderManager(store(tmp_path), httpx.MockTransport(handler))
    result = asyncio.run(
        manager.complete([{"role": "user", "content": "Return JSON"}], json_mode=True)
    )
    assert result is None and len(calls) == expected_calls


def test_prompt_schema_omits_authority_and_leaves_full_local_schema_unchanged():
    original = ScientificPlan.model_json_schema()
    before = json.dumps(original)
    compact = planning_schema(original)
    assert json.dumps(original) == before
    assert len(json.dumps(compact)) < len(before) / 2
    assert {"physics", "parameters", "modeling_brief"} <= set(compact["properties"])
    assert not {"runnable", "plan_id", "completion", "resource_policy"} & set(
        compact["properties"]
    )
    assert not {"verified", "user_confirmed", "use_authorized"} & set(
        compact["$defs"]["ScientificParameter"]["properties"]
    )


def test_cooling_ai_candidates_survive_both_contract_passes_without_overwriting_facts():
    baseline = ScientificPlan.model_validate(
        cooling_plan("3×3×20 mm 的烧红铁块在空气中冷却")
    )
    candidate = baseline.model_copy(deep=True)
    candidate.completion.parameters["initial_temperature"] = ScientificParameter(
        name="initial_temperature",
        value=700,
        unit="°C",
        source="model",
        verified=True,
        user_confirmed=True,
        use_authorized=True,
    )
    candidate.parameters["width"].value = 99
    once = enforce_deterministic_contract(candidate, baseline)
    twice = enforce_deterministic_contract(once, baseline)
    item = twice.completion.parameters["initial_temperature"]
    assert (
        item.value == 973.15
        and item.unit == "K"
        and item.assumption
        and not item.verified
    )
    assert (
        not item.user_confirmed
        and not item.use_authorized
        and item.authorization == "none"
    )
    assert twice.parameters["width"].value == 0.003 and not twice.runnable


def test_invalid_ai_units_keep_the_explicitly_labeled_exploratory_fallback():
    baseline = ScientificPlan.model_validate(
        cooling_plan("3×3×20 mm 的烧红铁块在空气中冷却")
    )
    candidate = baseline.model_copy(deep=True)
    candidate.completion.parameters["heat_capacity"].unit = "eV"
    result = enforce_deterministic_contract(candidate, baseline)
    assert result.completion.parameters["heat_capacity"].unit == "J/(kg*K)"


def test_generated_proposal_values_survive_but_generated_approval_does_not():
    prompt = "3×3×20 mm 的烧红铁块在空气中冷却"
    baseline = ScientificPlan.model_validate(cooling_plan(prompt))
    payload = baseline.model_dump(mode="json")
    payload["completion"]["proposal_id"] = "model-forged-id"
    payload["completion"]["parameters"]["heat_capacity"].update(
        value=450, verified=True, user_confirmed=True, use_authorized=True
    )
    candidate = ScientificPlanner._parse_provider_plan(
        json.dumps(payload), [], "DeepSeek API", DEFAULT_MODEL, prompt
    )
    assert (
        candidate.completion is None
        and not candidate.parameters["heat_capacity"].use_authorized
    )
    reviewed = enforce_deterministic_contract(candidate, baseline)
    assert reviewed.completion.parameters["heat_capacity"].value == 450
    assert (
        reviewed.completion.proposal_id != "model-forged-id" and not reviewed.runnable
    )


def test_missing_block_dimensions_are_reviewable_assumptions_not_user_facts():
    model = ScientificPlan.model_validate(
        cooling_plan("烧红的铁块放在空气中冷却，看看温度怎么变")
    )
    assert {"width", "height", "length"} <= set(model.completion.parameters)
    assert not {"width", "height", "length"} & set(model.parameters)
    assert not any(
        f.key in {"width", "height", "length"} for f in model.modeling_brief.facts
    )
    accepted = accept_completion(model, model.completion.proposal_id, {})
    assert not solver_input_issues(accepted)
    assert all(
        accepted.parameters[name].assumption and not accepted.parameters[name].verified
        for name in ("width", "height", "length")
    )


@pytest.mark.parametrize(
    "prompt", ["铁块 3×3×0 mm 烧红后冷却", "铁块长 50 mm 烧红后冷却"]
)
def test_ambiguous_or_invalid_explicit_geometry_is_not_replaced_by_defaults(prompt):
    assert cooling_plan(prompt) is None
