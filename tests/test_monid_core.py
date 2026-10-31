"""The core steps, read through Monid instead of AIsa.

The judge, the review, the word age and the use cases now ask Monid: the chat proxy for
the model, the DataForSEO door for Trends and Google Ads, and the anyapi tools for X's
search. These are the tests of those steps with the paid calls stubbed, so nothing here
spends money (tests/conftest.py blocks every paid call that is not stubbed).

The same tests are still in tests/test_radar.py, where they stub the AIsa calls; the ones
that had to change are the ones named here.
"""
from __future__ import annotations

import json
from datetime import date, timedelta

from radar import aisa, dfs, judge, llm, monid, review, store, usecases, wordage

TODAY = date(2026, 10, 12)
SEEN = date(2026, 9, 25)


def _lead(term, kinds, **more):
    return {"term": term, "kinds": kinds, "signals": [f"note {term}"], **more}


def _daily(values, end=TODAY - timedelta(days=1)):
    """A daily series ending yesterday, as Trends returns it; its last two days are unsettled."""
    start = end - timedelta(days=len(values) - 1)
    return [(start + timedelta(days=i), v) for i, v in enumerate(values)]


def _weeks(values, end=TODAY):
    """A weekly Trends result ending with the week still running (its period ends today)."""
    starts = [end - timedelta(days=7 * (len(values) - 1 - i)) for i in range(len(values))]
    data = [{"date_from": s.isoformat(), "date_to": min(s + timedelta(days=6), end).isoformat(), "values": [v]}
            for s, v in zip(starts, values)]
    return {"items": [{"type": "google_trends_graph", "data": data}]}


# --- the name judge ------------------------------------------------------------------------

def test_judge_scores_rows_in_chunks(monkeypatch):
    rows = [{"term": f"t{i}", "examples": []} for i in range(judge.CHUNK + 2)]
    sizes = []

    def chat(model, system, user):
        n = len(json.loads(user))
        sizes.append(n)
        return json.dumps([{"i": i, "score": 0.8} for i in range(n)])
    monkeypatch.setattr(llm, "chat", chat)
    assert judge.score(rows, "m") == {"judged": judge.CHUNK + 2} and sizes == [judge.CHUNK, 2]
    assert all(r["named"] == 0.8 for r in rows)


def test_judge_failure_leaves_rows_unscored(monkeypatch):
    def chat(*a):
        raise llm.LlmError("HTTP 402: no credit")
    monkeypatch.setattr(llm, "chat", chat)
    rows = [{"term": "strata", "examples": []}]
    assert "error" in judge.score(rows, "m") and "named" not in rows[0]


# --- the review: did it rise, judged against its own baseline --------------------------------

def test_review_sends_one_trends_task_per_call():
    # a Monid run takes one task, so each variant is asked on its own
    due = [("strata", date(2026, 9, 25), ["strata", "niko1221 strata", "niko strata", "a fourth"]),
           ("veo 3 free", date(2026, 10, 3), [])]
    bodies, plan = review.plan_calls(due, date(2026, 10, 17))
    assert len(bodies) == 4 and all(len(b["keywords"]) == 1 for b in bodies)
    assert [b["keywords"] for b in bodies[:3]] == [["strata"], ["niko1221 strata"], ["niko strata"]]
    assert bodies[3]["keywords"] == ["veo 3 free"]
    assert bodies[0]["date_to"] == "2026-10-09" and bodies[3]["date_to"] == "2026-10-17"
    assert plan[0][1] == "strata" and plan[0][3] == ["strata", "niko1221 strata", "niko strata"]


def test_review_stores_the_verdict(cfg, tmp_path, monkeypatch):
    conn = store.connect(tmp_path)
    store.record(conn, [_lead("niko strata", ["breakout"])], SEEN)
    points = [{"date_from": (date(2026, 9, 19) + timedelta(days=i)).isoformat(), "values": [v]}
              for i, v in enumerate([0, 0, 0, 0, 0, 0, 0, 0, 0, 30, 30, 100, 0, 0])]
    answer = {"items": [{"type": "google_trends_graph", "data": points}]}
    monkeypatch.setattr(dfs, "result", lambda endpoint, body: (answer, 1200))
    out = review.review(cfg, conn, date(2026, 10, 10))
    assert out["checked"] == [{"term": "niko strata", "first_seen": "2026-09-25", "verdict": "hit", "lead_days": 3}]
    assert conn.execute("SELECT verdict, lead_days FROM leads").fetchone() == ("hit", 3)


def test_review_ignores_the_unsettled_days(cfg, tmp_path, monkeypatch):
    conn = store.connect(tmp_path)
    store.record(conn, [_lead("ai image detector", ["usecase"])], date(2026, 9, 28))
    points = [{"date_from": d.isoformat(), "values": [v]} for d, v in _daily([5] * 43 + [0, 100])]
    answer = {"items": [{"type": "google_trends_graph", "data": points}]}
    monkeypatch.setattr(dfs, "result", lambda endpoint, body: (answer, 1200))
    assert review.review(cfg, conn, TODAY)["checked"][0]["verdict"] == "miss"


def test_review_a_failed_variant_leaves_its_lead_due(cfg, tmp_path, monkeypatch):
    conn = store.connect(tmp_path)
    store.record(conn, [_lead("strata", ["breakout"]), _lead("niko strata", ["breakout"])], SEEN)
    points = [{"date_from": (date(2026, 9, 19) + timedelta(days=i)).isoformat(), "values": [v]}
              for i, v in enumerate([0, 0, 0, 0, 0, 0, 0, 0, 0, 30, 30, 100, 0, 0])]

    def result(endpoint, body):
        if body["keywords"] == ["strata"]:
            raise dfs.DfsError("Monid: HTTP 502; AIsa: HTTP 502")
        return ({"items": [{"type": "google_trends_graph", "data": points}]}, 1200)
    monkeypatch.setattr(dfs, "result", result)
    out = review.review(cfg, conn, date(2026, 10, 10))
    assert [c["term"] for c in out["checked"]] == ["niko strata"]
    assert [d[0] for d in store.due(conn, date(2026, 10, 10), 14, 5)] == ["strata"]   # stays due


# --- word age: when did a term first draw searches ---------------------------------------------

def test_first_seen_is_the_first_week_with_interest():
    age = wordage.first_seen(wordage.series(_weeks([0] * 250 + [3, 20, 60, 100, 80, 70, 90])), TODAY)
    assert age["first_seen"] == (TODAY - timedelta(days=42)).isoformat() and age["is_new"] and not age["since_start"]
    old = wordage.first_seen(wordage.series(_weeks([0] * 200 + [5] + [0] * 56)), TODAY)
    assert old["first_seen"] == (TODAY - timedelta(days=56 * 7)).isoformat() and not old["is_new"]
    always = wordage.first_seen(wordage.series(_weeks([12] * 257)), TODAY)
    assert always["since_start"] and not always["is_new"]


def test_first_seen_ignores_the_unsettled_tail():
    # the week still running reads 100 for a term nobody searched: not a new word, no data at all
    assert wordage.first_seen(wordage.series(_weeks([0] * 256 + [100])), TODAY)["no_data"]
    # daily data: Google's "0, 100" on the last two days
    task = {"items": [{"type": "google_trends_graph", "data": [
        {"date_from": d.isoformat(), "date_to": d.isoformat(), "values": [v]} for d, v in _daily([0] * 88 + [0, 100], end=TODAY - timedelta(days=1))]}]}
    assert wordage.first_seen(wordage.series(task), TODAY)["no_data"]
    # a period flagged missing_data is not read either
    task = _weeks([0] * 255 + [40, 0])
    task["items"][0]["data"][-2]["missing_data"] = True
    assert wordage.first_seen(wordage.series(task), TODAY)["no_data"]


def test_word_age_reads_the_acted_on_rows(cfg, monkeypatch):
    cfg["word_age"]["enabled"] = True
    asked = []

    def result(endpoint, body):
        asked.append(body)
        return (_weeks([0] * 250 + [9] * 7), 1200)
    monkeypatch.setattr(dfs, "result", result)
    rows = [{"phrase": "pdfcraft"}, {"phrase": "ai image detector"}]
    assert wordage.assess(rows, cfg, TODAY) == (2400, [])
    assert all(r["is_new"] for r in rows) and asked[0] == {"keywords": ["pdfcraft"], "time_range": "past_5_years"}


def test_word_age_names_the_row_whose_call_failed(cfg, monkeypatch):
    cfg["word_age"]["enabled"] = True

    def result(endpoint, body):
        if body["keywords"] == ["pdfcraft"]:
            raise dfs.DfsError("Monid: HTTP 502; AIsa: HTTP 502")
        return (_weeks([0] * 250 + [9] * 7), 1200)
    monkeypatch.setattr(dfs, "result", result)
    rows = [{"phrase": "pdfcraft"}, {"phrase": "ai image detector"}]
    cost, errors = wordage.assess(rows, cfg, TODAY)
    assert errors == ["pdfcraft (word age)"] and cost == 1200
    assert "age" not in rows[0] and rows[1]["is_new"]


# --- the use cases: the names and the generic terms behind them --------------------------------

def test_a_failed_post_search_costs_one_name_its_posts_not_the_step(monkeypatch):
    def rows(endpoint, body):
        if "photocraft" in body["query"]:
            raise monid.MonidError("HTTP 504: <html><h1>504 Gateway Time-out</h1></html>")
        return ([{"text": "check any image", "likeCount": 900, "authorUsername": "a"}], 3000)
    monkeypatch.setattr(monid, "anyapi_rows", rows)
    monkeypatch.setattr(usecases, "_readme", lambda repo: "PhotoCraft opens real PSD files")
    ev, cost = usecases.evidence(["photocraft", "synthid detector"], {"photocraft": "storytold/photocraft"}, TODAY)
    assert cost == 3000 and ev["photocraft"]["readme"] and ev["photocraft"]["posts"] == []
    assert ev["photocraft"]["error"] == "HTTP 504: 504 Gateway Time-out" and len(ev["synthid detector"]["posts"]) == 1


def test_the_posts_are_read_from_monid_s_x_search(monkeypatch):
    asked = []

    def rows(endpoint, body):
        asked.append((endpoint, body))
        return ([{"text": "check any image", "likeCount": 900, "authorUsername": "a", "url": "https://x.com/a/1"}], 800)
    monkeypatch.setattr(monid, "anyapi_rows", rows)
    ev, cost = usecases.evidence(["synthid detector"], {}, TODAY)
    assert asked[0][0] == "/twitter.search" and '"synthid detector"' in asked[0][1]["query"]
    assert ev["synthid detector"]["posts"] == [{"text": "check any image", "likes": 900, "by": "a",
                                               "url": "https://x.com/a/1"}]
    assert cost == 800


def test_volumes_carry_twelve_months_oldest_first(monkeypatch):
    newest_first = [{"year": 2026, "month": m, "search_volume": 100 * m} for m in range(8, 0, -1)] + \
                   [{"year": 2025, "month": m, "search_volume": None} for m in range(12, 8, -1)]

    def rows(endpoint, body):
        return ([{"keyword": "pdf editor", "search_volume": 400, "monthly_searches": newest_first}], 90000)
    monkeypatch.setattr(dfs, "rows", rows)
    vol, cost = usecases.volumes(["pdf editor"], 2840)
    got = vol["pdf editor"]
    assert cost == 90000 and got["volume"] == 400 and got["month"] == "2026-08"
    assert got["months"][0] == ["2025-09", 0] and got["months"][-1] == ["2026-08", 800] and len(got["months"]) == 12


def test_the_pair_reads_both_keywords_off_one_trends_call(monkeypatch):
    asked = []
    points = [{"date_from": (TODAY - timedelta(days=2)).isoformat(), "values": [30, 70]}]

    def result(endpoint, body):
        asked.append(body)
        return ({"items": [{"type": "google_trends_graph", "data": points}]}, 1200)
    monkeypatch.setattr(dfs, "result", result)
    series, cost = usecases.fetch_series(["pdfcraft"], "gpts", TODAY)
    assert asked[0]["keywords"] == ["pdfcraft"] and asked[1]["keywords"] == ["pdfcraft", "gpts"]
    assert series["pdfcraft"]["solo"] == [(TODAY - timedelta(days=2), 30)]
    assert series["pdfcraft"]["pair"] == [(TODAY - timedelta(days=2), 30, 70)]
    assert cost == 2400


def test_the_trends_read_falls_back_to_aisa_when_monid_does_not_answer(monkeypatch):
    # the second door: AIsa's own passthrough, at the path the adapter uses
    asked = []
    points = [{"date_from": (TODAY - timedelta(days=2)).isoformat(), "values": [30]}]

    def rest(path, body):
        asked.append(path)
        return ({"items": [{"type": "dataforseo_trends_graph", "data": points}]}, 1200)
    monkeypatch.setattr(aisa, "rest", rest)
    result, cost = dfs.result(dfs.TRENDS, {"keywords": ["pdfcraft"], "time_range": "past_5_years"})
    assert asked == [dfs.AISA_REST[dfs.TRENDS]] and cost == 1200
    assert review._series(result) == [(TODAY - timedelta(days=2), 30)]     # AIsa's graph type reads the same


def test_screen_drops_loosely_related_keywords_and_keeps_all_when_the_call_fails(monkeypatch):
    cands = [{"phrase": p, "brand": False} for p in ("ai image detector", "chatgpt alternative", "ai audio detector")]
    ev = {"posts": [{"text": "check if any image is AI generated", "likes": 900, "by": "a"}], "readme": ""}
    monkeypatch.setattr(llm, "chat", lambda *a, **k: '[{"i": 0, "score": 0.95}, {"i": 1, "score": 0.2}, {"i": 2, "score": 0.7}]')
    kept = usecases.relevant(usecases.screen("m", "synthid detector", ev, cands))
    assert [(c["phrase"], c["relevance"]) for c in kept] == [("ai image detector", 0.95), ("ai audio detector", 0.7)]

    def down(*a, **k):
        raise llm.LlmError("HTTP 500")
    monkeypatch.setattr(llm, "chat", down)
    assert usecases.screen("m", "synthid detector", ev, cands) == cands


def test_screen_marks_brands_and_other_names(monkeypatch):
    cands = [{"phrase": "free up mac disk space", "brand": False, "source": "model"},
             {"phrase": "gpt image 2.5", "brand": False, "source": "searched"}]
    monkeypatch.setattr(llm, "chat", lambda *a, **k:
                        '[{"i": 0, "score": 0.8, "brand": true, "name": false}, {"i": 1, "score": 0.1, "brand": true, "name": true}]')
    viewed = usecases.screen("m", "removemacai", {"posts": [], "readme": "frees 12 GB"}, cands)
    assert viewed[0]["brand"] and viewed[1]["is_name"]
    assert [c["phrase"] for c in usecases.relevant(viewed)] == ["free up mac disk space"]
