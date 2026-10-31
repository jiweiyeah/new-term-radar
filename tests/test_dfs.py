import pytest

from radar import aisa, dfs, monid


def test_monid_is_asked_first(monkeypatch):
    asked = {}

    def run(provider, endpoint, body=None, query=None):
        asked["provider"], asked["endpoint"], asked["body"] = provider, endpoint, body
        return [{"items": [{"keyword": "a"}]}], 2000
    monkeypatch.setattr(monid, "run", run)
    rows, cost = dfs.rows(dfs.VOLUME, {"keywords": ["a"], "location_code": 2840})
    assert asked == {"provider": "dataforseo", "endpoint": dfs.VOLUME,
                     "body": {"keywords": ["a"], "location_code": 2840}}
    assert rows == [{"keyword": "a"}] and cost == 2000


def test_aisa_answers_when_monid_does_not(monkeypatch):
    monkeypatch.setattr(monid, "run", lambda *a, **k: (_ for _ in ()).throw(monid.MonidError("no Monid key")))
    monkeypatch.setattr(aisa, "call", lambda calls: ({"d": {"tasks": [
        {"status_code": 20000, "result": [{"items": [{"keyword": "a"}]}]}]}}, 1200))
    rows, cost = dfs.rows(dfs.VOLUME, {"keywords": ["a"]})
    assert rows == [{"keyword": "a"}] and cost == 1200


def test_aisa_answers_with_a_row_a_keyword_the_way_dataforseo_does(monkeypatch):
    # the searches of a keyword: DataForSEO's result is one object a keyword, not one object
    # holding them, so the second door must hand them all over
    monkeypatch.setattr(monid, "run", lambda *a, **k: (_ for _ in ()).throw(monid.MonidError("no Monid key")))
    monkeypatch.setattr(aisa, "call", lambda calls: ({"d": {"tasks": [
        {"status_code": 20000, "result": [{"keyword": "a", "search_volume": 10},
                                          {"keyword": "b", "search_volume": 20}]}]}}, 90000))
    rows, cost = dfs.rows(dfs.VOLUME, {"keywords": ["a", "b"]})
    assert [r["keyword"] for r in rows] == ["a", "b"] and cost == 90000
    monkeypatch.setattr(monid, "run", lambda *a, **k: ([{"items": [{"n": 1}]}], 2000))
    assert dfs.result(dfs.SERP, {})[0] == {"items": [{"n": 1}]}       # one result object: the first one


def test_a_failure_on_both_doors_is_named(monkeypatch):
    monkeypatch.setattr(monid, "run", lambda *a, **k: (_ for _ in ()).throw(monid.MonidError("Monid is down")))
    monkeypatch.setattr(aisa, "call", lambda calls: (_ for _ in ()).throw(aisa.AisaError("HTTP 402")))
    with pytest.raises(dfs.DfsError) as exc_info:
        dfs.rows(dfs.VOLUME, {"keywords": ["a"]})
    assert "Monid is down" in str(exc_info.value) and "402" in str(exc_info.value)


def test_the_rows_are_under_the_items_or_under_the_keywords(monkeypatch):
    monkeypatch.setattr(monid, "run", lambda *a, **k: ([{"items": [{"n": 1}]}], 10))
    assert dfs.rows(dfs.SERP, {}) == ([{"n": 1}], 10)
    monkeypatch.setattr(monid, "run", lambda *a, **k: ([{"keywords": [{"n": 2}]}], 20))
    assert dfs.rows(dfs.VOLUME, {}) == ([{"n": 2}], 20)


def test_the_ads_volume_answers_with_the_keywords_themselves(monkeypatch):
    monkeypatch.setattr(monid, "run", lambda *a, **k: ([{"keyword": "a", "search_volume": 10},
                                                       {"keyword": "b", "search_volume": 20}], 90))
    rows, cost = dfs.rows(dfs.VOLUME, {})
    assert [r["keyword"] for r in rows] == ["a", "b"] and cost == 90


def test_many_keeps_the_order_and_names_the_failures(monkeypatch):
    def run(provider, endpoint, body=None, query=None):
        if body["keywords"] == ["bad"]:
            raise monid.MonidError("no answer")
        return [{"items": [{"keyword": body["keywords"][0]}]}], 100
    monkeypatch.setattr(monid, "run", run)
    monkeypatch.setattr(aisa, "call", lambda calls: (_ for _ in ()).throw(aisa.AisaError("no answer")))
    results, cost, errors = dfs.many(dfs.VOLUME, [{"keywords": ["good"]}, {"keywords": ["bad"]}])
    assert results[0] == {"items": [{"keyword": "good"}]} and results[1] is None
    assert cost == 100 and len(errors) == 1 and "bad" not in errors[0]      # the failure names the call, not the keyword
