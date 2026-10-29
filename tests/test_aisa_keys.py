import json

import pytest

import radar.aisa as aisa

_REAL_POST = aisa._post   # conftest stubs _post per test (no network); capture the real one at import


@pytest.fixture(autouse=True)
def clean_state(monkeypatch):
    monkeypatch.setattr(aisa, "_ledger", None)
    monkeypatch.setattr(aisa, "_dead_run", {"data": set(), "chat": set()})
    monkeypatch.setenv("AISA_API_KEY", "")


def with_keys(monkeypatch, tmp_path, keys: str):
    monkeypatch.setenv("AISA_API_KEY", keys)
    ledger = tmp_path / "aisa_keys.json"
    aisa.set_key_ledger(ledger)
    return ledger


def test_keys_split_on_commas(monkeypatch):
    monkeypatch.setenv("AISA_API_KEY", "k1, k2 ,,k3")
    assert aisa._keys() == ["k1", "k2", "k3"]


def test_no_key_raises(monkeypatch, tmp_path):
    monkeypatch.delenv("AISA_API_KEY", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))          # no ~/.aisa either
    with pytest.raises(aisa.AisaError):
        aisa.key()


def test_keys_used_in_order(monkeypatch, tmp_path):
    with_keys(monkeypatch, tmp_path, "k1,k2,k3")
    assert aisa.key() == "k1"
    aisa._mark_dead("k1", "data")
    assert aisa.key() == "k2"
    aisa._mark_dead("k2", "data")
    assert aisa.key() == "k3"


def test_dead_keys_are_persisted_as_fingerprints(monkeypatch, tmp_path):
    ledger = with_keys(monkeypatch, tmp_path, "k1,k2")
    aisa._mark_dead("k1", "data")
    assert json.loads(ledger.read_text())["dead"]["data"] == [aisa._fingerprint("k1")]
    assert "k1" not in ledger.read_text()              # the key itself never lands on disk


def test_a_new_run_skips_the_dead_key(monkeypatch, tmp_path):
    with_keys(monkeypatch, tmp_path, "k1,k2")
    aisa._mark_dead("k1", "data")
    aisa._dead_run["data"].clear()                     # a new process starts with no in-run marks
    assert aisa.key() == "k2"


def test_all_keys_dead_raises_with_a_reset_hint(monkeypatch, tmp_path):
    with_keys(monkeypatch, tmp_path, "k1,k2")
    aisa._mark_dead("k1", "data")
    aisa._mark_dead("k2", "data")
    with pytest.raises(aisa.AisaError, match="reset"):
        aisa.key()


def test_scopes_do_not_contaminate_each_other(monkeypatch, tmp_path):
    """AIsa bills data and chat from separate wallets: a key out of model balance can still read X."""
    with_keys(monkeypatch, tmp_path, "k1,k2")
    aisa._mark_dead("k1", "chat")
    assert aisa.key("data") == "k1"                    # still fine for data calls
    assert aisa.key("chat") == "k2"


# The router answers an invalid key with HTTP 200 and the refusal inside each result,
# as seen on the wire (October 2026):
def _router_body(status: int, message: str, successful: bool) -> dict:
    result = {"call_id": "t", "successful": successful}
    if not successful:
        result["error"] = {"message": message, "status": status, "type": "authentication"}
    return {"batch_id": "b", "error_count": 0 if successful else 1, "results": [result]}


def test_body_level_refusal_is_read_from_the_results():
    body = _router_body(401, '{"code":"invalid_api_key","error":"AISA API key is invalid."}', False)
    assert aisa._refused_status(body) == 401


def test_body_level_refusal_reads_the_message_when_status_is_missing():
    body = _router_body(0, "model gpt-4.1-mini requires a paid balance. recharge_required", False)
    assert aisa._refused_status(body) == 402


def test_chat_wallet_errors_are_a_refusal():
    body = {"error": {"message": "insufficient wallet balance", "type": "invalid_request_error"}}
    assert aisa._refused_status(body) == 402


def test_a_partly_successful_batch_is_not_a_refused_key():
    body = {"results": [_router_body(401, "invalid_api_key", False),
                        _router_body(0, "", True)["results"][0]]}
    assert aisa._refused_status(body) is None


def test_other_statuses_are_not_a_refused_key():
    body = _router_body(500, "upstream exploded", False)
    assert aisa._refused_status(body) is None


def test_a_refused_key_is_skipped_and_marked(monkeypatch, tmp_path):
    ledger = with_keys(monkeypatch, tmp_path, "k1,k2")

    def fake_send(url, body, timeout, k):
        if k == "k1":
            raise aisa.AisaError("HTTP 402: recharge required", 402)
        return {"ok": True}

    monkeypatch.setattr(aisa, "_send", fake_send)
    assert aisa._attempt("https://x", {}, 1, aisa._keys(), "data") == {"ok": True}
    assert aisa._fingerprint("k1") in json.loads(ledger.read_text())["dead"]["data"]


def test_a_refused_last_key_is_marked_too(monkeypatch, tmp_path):
    with_keys(monkeypatch, tmp_path, "k1,k2")

    def fake_send(url, body, timeout, k):
        raise aisa.AisaError(f"HTTP 402: {k} out of quota", 402)

    monkeypatch.setattr(aisa, "_send", fake_send)
    with pytest.raises(aisa.AisaError):
        aisa._attempt("https://x", {}, 1, aisa._keys(), "data")
    assert aisa._fingerprint("k2") in aisa._dead("data")


def test_server_errors_do_not_switch_keys(monkeypatch, tmp_path):
    with_keys(monkeypatch, tmp_path, "k1,k2")
    calls = []

    def fake_send(url, body, timeout, k):
        calls.append(k)
        raise aisa.AisaError("HTTP 500: boom", 500)

    monkeypatch.setattr(aisa, "_send", fake_send)
    with pytest.raises(aisa.AisaError):
        aisa._attempt("https://x", {}, 1, aisa._keys(), "data")
    assert calls == ["k1"]                             # no failover, no mark


def test_a_body_refused_key_is_skipped_and_marked(monkeypatch, tmp_path):
    ledger = with_keys(monkeypatch, tmp_path, "k1,k2")
    refused = _router_body(401, '{"code":"invalid_api_key"}', False)

    def fake_send(url, body, timeout, k):
        return refused if k == "k1" else {"results": [{"call_id": "t", "successful": True}]}

    monkeypatch.setattr(aisa, "_send", fake_send)
    assert aisa._attempt("https://x", {}, 1, aisa._keys(), "data")["results"][0]["successful"] is True
    assert aisa._fingerprint("k1") in json.loads(ledger.read_text())["dead"]["data"]


def test_a_body_refused_last_key_raises_with_the_code(monkeypatch, tmp_path):
    with_keys(monkeypatch, tmp_path, "k1")
    refused = _router_body(401, '{"code":"invalid_api_key"}', False)
    monkeypatch.setattr(aisa, "_send", lambda url, body, timeout, k: refused)
    with pytest.raises(aisa.AisaError, match="HTTP 401") as exc_info:
        aisa._attempt("https://x", {}, 1, aisa._keys(), "data")
    assert exc_info.value.code == 401


def test_post_feeds_the_live_keys_to_the_attempt(monkeypatch, tmp_path):
    with_keys(monkeypatch, tmp_path, "k1,k2")
    aisa._mark_dead("k1", "data")
    seen = {}

    def fake_attempt(url, body, timeout, keys, scope):
        seen["keys"], seen["scope"] = keys, scope
        return {"ok": True}

    monkeypatch.setattr(aisa, "_attempt", fake_attempt)
    monkeypatch.setattr(aisa, "_post", _REAL_POST)
    assert aisa._post("https://x", {}, 1) == {"ok": True}
    assert seen["keys"] == ["k2"]
    assert seen["scope"] == "data"
