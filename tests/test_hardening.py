"""One bad reading must not take the run down with it.

A paid reading fails in two ways: the call is refused (a DfsError), or the answer comes
back in a shape nobody expected (a row without a keyword, a month without a year, a Trends
item named the other way round). The first is named and skipped; the second used to take
the whole table with it. These are the tests of that.
"""
from __future__ import annotations

from datetime import date

from radar import asks, competition, dfs, google, llm, markets, monid, usecases

TODAY = date(2026, 10, 12)
NAME = "photocraft"


def test_a_model_that_refuses_does_not_take_the_table_with_it(monkeypatch, cfg, tmp_path):
    monkeypatch.setattr(usecases.monid, "anyapi_rows", lambda endpoint, body: (
        [{"text": "ai image detector", "likeCount": 10, "authorUsername": "x", "url": "u"}], 100))

    def chat(*a, **k):
        raise llm.LlmError("HTTP 402: no credit")
    monkeypatch.setattr(usecases.llm, "chat", chat)

    uc = usecases.run(cfg, tmp_path, [NAME], {}, TODAY)
    assert uc["names"] == [NAME]                       # the name is read, and the table still comes out
    assert any("model" in e for e in uc["read_errors"])
    assert usecases.degraded(uc)


def test_the_week_s_posts_survive_a_refused_volume_call(monkeypatch, cfg, tmp_path):
    post = {"id": "r1", "title": "is there an ai tool", "text": "", "subreddit": "x",
            "score": 5, "url": "", "created": 0}
    monkeypatch.setattr(asks, "collect", lambda c: ([post], 100, []))
    monkeypatch.setattr(asks, "classify", lambda model, posts: ([{**posts[0], "kind": "request",
                                                                   "job": "convert slides to notes"}], []))

    def volumes(*a, **k):
        raise dfs.DfsError("Monid: HTTP 402")
    monkeypatch.setattr(usecases, "volumes", volumes)

    out = asks.asks(cfg, tmp_path, TODAY)
    assert out["asks"] == 1                              # the posts were paid for: they are kept
    assert any("search volumes" in e for e in out["errors"])


def test_a_row_without_a_keyword_is_left_out_rather_than_raised(monkeypatch):
    monkeypatch.setattr(dfs, "rows", lambda endpoint, body: ([{"keyword": "a", "search_volume": 10}, {}], 100))
    vol, cost = usecases.volumes(["a"], 2840)
    assert vol == {"a": {"volume": 10, "month": "", "months": []}} and cost == 100


def test_a_month_without_a_year_is_left_out(monkeypatch):
    rows = [{"keyword": "a", "search_volume": 10, "monthly_searches": [{"month": 3, "search_volume": 5},
                                                                     {"year": 2026, "month": 4, "search_volume": 7}]}]
    monkeypatch.setattr(dfs, "rows", lambda endpoint, body: (rows, 100))
    vol, _ = usecases.volumes(["a"], 2840)
    assert vol["a"]["month"] == "2026-04" and len(vol["a"]["months"]) == 1


def test_the_door_that_answers_nothing_gives_no_rows(monkeypatch):
    monkeypatch.setattr(monid, "run", lambda *a, **k: ([{}], 10))
    assert dfs.rows(dfs.VOLUME, {}) == ([], 10)


def test_the_other_door_s_trends_type_is_read_too(monkeypatch):
    # Monid answers "google_trends_graph", AIsa "dataforseo_trends_graph": both are read
    monkeypatch.setattr(dfs, "result", lambda endpoint, body: ({"items": [
        {"type": "dataforseo_trends_graph", "data": [{"date_from": "2026-10-01", "values": [5]}]}]}, 100))
    series, cost, errors = google.daily_series(["ai image detector"], "US", TODAY)
    assert series["ai image detector"] == [(date(2026, 10, 1), 5)] and not errors


def test_the_other_door_s_related_queries_are_read_too(monkeypatch):
    monkeypatch.setattr(dfs, "result", lambda endpoint, body: ({"items": [
        {"type": "dataforseo_trends_queries_list", "data": {"rising": [{"query": "ai photo detector", "value": "10"}],
                                                          "top": []}}]}, 100))
    out, cost, errors = google.related_queries([NAME], "US")
    assert out[NAME]["rising"] == ["ai photo detector"] and not errors


def test_the_difficulty_already_read_is_not_bought_again(monkeypatch, cfg, tmp_path):
    cfg["competition"]["max_rows"] = 8
    called = []
    monkeypatch.setattr(competition, "difficulty", lambda keywords, location, language="en": (
        called.append(keywords) or ({}, 0)))
    monkeypatch.setattr(competition, "page_one", lambda phrase, loc: ({"results": [], "ads": [], "features": {}}, 100))
    monkeypatch.setattr(competition, "ratings", lambda domains, state: ({}, 0, []))
    rows = [{"phrase": "ai image detector", "decision": "watch"}]
    cost, errors = competition.assess(rows, cfg, tmp_path, {"ai image detector": 37})
    assert not called                                   # the markets reading bought it already
    assert rows[0]["competition"]["kd"] == 37


def test_the_difficulty_of_a_row_nobody_read_is_bought(monkeypatch, cfg, tmp_path):
    cfg["competition"]["max_rows"] = 8
    called = []
    monkeypatch.setattr(competition, "difficulty", lambda keywords, location, language="en": (
        called.append(keywords) or ({"ai image detector": 21}, 1200)))
    monkeypatch.setattr(competition, "page_one", lambda phrase, loc: ({"results": [], "ads": [], "features": {}}, 100))
    monkeypatch.setattr(competition, "ratings", lambda domains, state: ({}, 0, []))
    rows = [{"phrase": "ai image detector", "decision": "watch"}]
    competition.assess(rows, cfg, tmp_path, {})
    assert called == [["ai image detector"]] and rows[0]["competition"]["kd"] == 21


def test_the_market_the_table_reads_is_the_one_the_page_one_check_wants(monkeypatch):
    # the code the difficulty is bought for, so the two readings share it
    assert markets.LOCATIONS["US"][0] == 2840
