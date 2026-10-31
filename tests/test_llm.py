"""The model door: the OpenAI-compatible gateway first, Monid's chat second.

AIsa is not a model door: the radar's model calls go to the gateway, and these tests say
so. The gateway names its models with the maker in front, so the name the config asks
for is not always the name the gateway has: that lookup, and the fallback when the
gateway does not carry the model at all, are tested here.
"""
from __future__ import annotations

import json
import socket

import pytest

from radar import aisa, llm, monid

GATEWAY_MODELS = ["anthropic/claude-haiku-4.5", "openai/gpt-5.4", "openai/gpt-5.4-mini"]


@pytest.fixture(autouse=True)
def gateway(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "k")
    monkeypatch.setattr(llm, "_models", GATEWAY_MODELS)
    monkeypatch.setattr(llm, "_answered", "")


# --- the name the gateway knows -------------------------------------------------------

def test_a_plain_name_is_resolved_to_the_gateways_own_name():
    assert llm.candidates("gpt-5.4-mini")[0] == "openai/gpt-5.4-mini"


def test_a_name_with_a_maker_in_front_is_tried_as_it_is():
    assert llm.candidates("openai/gpt-5.4")[0] == "openai/gpt-5.4"


def test_a_model_the_gateway_does_not_carry_falls_back_to_the_chain():
    names = llm.candidates("gpt-4.1-mini")
    assert names[0] == "gpt-4.1-mini"                     # the name asked for is tried first
    assert "openai/gpt-5.4-mini" in names                # and the chain behind it


def test_the_chain_holds_only_models_the_gateway_carries(monkeypatch):
    monkeypatch.setattr(llm, "_models", ["z-ai/glm-5.3-flash"])
    assert llm.candidates("gpt-5.4-mini") == ["gpt-5.4-mini", "z-ai/glm-5.3-flash"]


def test_an_unreadable_model_list_leaves_the_name_as_it_is(monkeypatch):
    monkeypatch.setattr(llm, "_models", [])
    assert llm.candidates("gpt-5.4-mini")[0] == "gpt-5.4-mini"


# --- the two doors --------------------------------------------------------------------

def test_the_gateway_answers_first_and_the_model_is_remembered(monkeypatch):
    asked = []
    monkeypatch.setattr(llm, "_ask", lambda model, system, user, max_tokens: asked.append(model) or "answer")
    assert llm.chat("gpt-5.4-mini", "s", "u") == "answer"
    assert asked == ["openai/gpt-5.4-mini"]
    assert llm.answered_model() == "openai/gpt-5.4-mini"


def test_a_name_the_gateway_does_not_carry_moves_to_the_next(monkeypatch):
    asked = []

    def ask(model, system, user, max_tokens):
        asked.append(model)
        if model == "gpt-4.1-mini":
            raise llm.LlmError("HTTP 400", 400, '{"error":{"code":"model_not_found"}}')
        return "answer"
    monkeypatch.setattr(llm, "_ask", ask)
    assert llm.chat("gpt-4.1-mini", "s", "u") == "answer"
    assert asked == ["gpt-4.1-mini", "openai/gpt-5.4-mini"]
    assert llm.answered_model() == "openai/gpt-5.4-mini"


def test_monid_answers_when_the_gateway_does_not(monkeypatch):
    def boom(*a, **k):
        raise llm.LlmError("HTTP 500")
    monkeypatch.setattr(llm, "_ask", boom)
    monkeypatch.setattr(monid, "chat", lambda model, system, user, max_tokens=1500: f"monid:{model}")
    assert llm.chat("openai/gpt-5.4", "s", "u") == "monid:gpt-4o-mini"      # a model Monid carries


def test_without_a_key_monid_answers(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setattr(monid, "chat", lambda *a, **k: "monid")
    assert llm.chat("openai/gpt-5.4", "s", "u") == "monid"


def test_both_doors_failing_names_both(monkeypatch):
    def boom(*a, **k):
        raise llm.LlmError("HTTP 500")
    monkeypatch.setattr(llm, "_ask", boom)

    def down(*a, **k):
        raise monid.MonidError("HTTP 402")
    monkeypatch.setattr(monid, "chat", down)
    with pytest.raises(llm.LlmError) as exc:
        llm.chat("openai/gpt-5.4", "s", "u")
    assert "the gateway" in str(exc.value) and "Monid" in str(exc.value)


def test_aisa_is_not_a_model_door():
    assert not hasattr(aisa, "chat")
    assert not hasattr(aisa, "CHAT")


# --- the two clients ------------------------------------------------------------------

def test_the_sdk_is_used_when_it_is_installed(monkeypatch):
    asked = []
    monkeypatch.setattr(llm, "_sdk", lambda: object())
    monkeypatch.setattr(llm, "_ask_sdk", lambda sdk, *a: asked.append(a[0]) or "sdk")
    monkeypatch.setattr(llm, "_ask_http", lambda *a: "http")
    assert llm._ask("m", "s", "u", 10) == "sdk"
    assert asked == ["m"]


def test_the_standard_library_is_used_when_the_sdk_is_missing(monkeypatch):
    monkeypatch.setattr(llm, "_sdk", lambda: None)
    monkeypatch.setattr(llm, "_ask_http", lambda *a: "http")
    assert llm._ask("m", "s", "u", 10) == "http"


def test_the_gateway_is_asked_over_the_openai_protocol(monkeypatch):
    seen = {}

    class _Res:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return b'{"choices":[{"message":{"content":"hi"}}]}'

    def fake_fetch(req, timeout):
        seen["url"] = req.full_url
        seen["body"] = json.loads(req.data)
        return _Res()
    monkeypatch.setattr(llm.net, "fetch", fake_fetch)
    assert llm._ask_http("openai/gpt-5.4-mini", "s", "u", 100) == "hi"
    assert seen["url"].endswith("/chat/completions")
    assert seen["body"]["model"] == "openai/gpt-5.4-mini"
    assert seen["body"]["messages"] == [{"role": "system", "content": "s"}, {"role": "user", "content": "u"}]


def test_the_sdk_call_asks_the_gateway(monkeypatch):
    sdk = pytest.importorskip("openai")
    seen = {}

    class _Client:
        def __init__(self, **kw):
            seen["init"] = kw

        class chat:
            class completions:
                @staticmethod
                def create(**kw):
                    seen["call"] = kw
                    message = type("M", (), {"content": "hi"})()
                    choice = type("C", (), {"message": message})()
                    return type("R", (), {"choices": [choice]})()
    monkeypatch.setattr(sdk, "OpenAI", _Client)
    assert llm._ask_sdk(sdk, "openai/gpt-5.4-mini", "s", "u", 100) == "hi"
    assert seen["call"]["model"] == "openai/gpt-5.4-mini"
    assert seen["init"]["base_url"] == llm.BASE


# --- what is not asked again -----------------------------------------------------------

def test_only_a_connection_that_never_left_is_asked_again():
    def with_cause(cause):
        err = llm.LlmError("x")
        err.__cause__ = cause
        return err

    class ConnectError(Exception):
        pass
    assert llm._undelivered(with_cause(ConnectionRefusedError()))
    assert llm._undelivered(with_cause(socket.gaierror()))
    assert llm._undelivered(with_cause(ConnectError()))
    assert not llm._undelivered(with_cause(TimeoutError()))
    assert not llm._undelivered(llm.LlmError("x"))


def test_a_refusal_of_the_models_name_is_told_from_the_other_refusals():
    assert llm._no_such_model(llm.LlmError("HTTP 400", 400, '{"error":{"code":"model_not_found"}}'))
    assert llm._no_such_model(llm.LlmError("No available channel for model x"))
    assert not llm._no_such_model(llm.LlmError("HTTP 500"))
    assert not llm._no_such_model(llm.LlmError("HTTP 401"))


def test_the_reason_is_read_out_of_the_body():
    assert llm._why('{"error":{"message":"no credit"}}') == "no credit"
    assert llm._why("plain text") == "plain text"
