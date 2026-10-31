from datetime import date

from radar import aisa, google

TODAY = date(2026, 10, 15)


def _serp(items, count=1234):
    return {"serp": {"tasks": [{"result": [{"items": items, "se_results_count": count}]}]}}, 2000


def test_serp_reads_the_items_and_googles_own_count(monkeypatch):
    monkeypatch.setattr(aisa, "call", lambda calls: _serp([
        {"type": "organic", "domain": "www.a.com", "url": "https://a.com/x", "title": "A", "rank_group": 1},
        {"type": "paid", "domain": "www.b.com"},
        {"type": "ai_overview"}], 45600))
    out, cost = google.serp("ai detector")
    assert out["results_count"] == 45600 and cost == 2000
    assert google.organic(out["items"]) == [{"url": "https://a.com/x", "title": "A", "rank": 1}]
    assert google.advertisers(out["items"]) == ["b.com"]


def test_a_serp_without_a_count_is_not_a_number(monkeypatch):
    monkeypatch.setattr(aisa, "call", lambda calls: ({"serp": {"tasks": [{"result": [{"items": []}]}]}}, 2000))
    assert google.serp("ai detector")[0]["results_count"] is None


def test_intitle_count_asks_for_the_title_and_reads_the_count(monkeypatch):
    asked = []

    def call(calls):
        asked.append(calls[0]["arguments"]["body"][0]["keyword"])
        return _serp([], 19500)
    monkeypatch.setattr(aisa, "call", call)
    count, cost = google.intitle_count("synthid detector")
    assert count == 19500 and cost == 2000 and asked == ['intitle:"synthid detector"']


def test_related_queries_read_the_two_lists_and_normalize_the_names(monkeypatch):
    def rest(path, task):
        assert path == google.TRENDS_PATH
        assert task["location_code"] == 2840
        assert task["item_types"] == ["google_trends_queries_list"]
        return {"items": [{"type": "google_trends_queries_list", "data": {
            "top": [{"query": "AI  Photo Detector", "value": "100"}],
            "rising": [{"query": "ai photo detector online", "value": "50"}]}}]}, 1200
    monkeypatch.setattr(aisa, "rest", rest)
    out, cost, errors = google.related_queries(["synthid detector"], "US")
    assert out == {"synthid detector": {"rising": ["ai photo detector online"], "top": ["ai photo detector"]}}
    assert cost == 1200 and not errors


def test_daily_series_reads_the_days_of_the_one_country(monkeypatch):
    def rest(path, task):
        assert task["location_code"] == 2840 and task["item_types"] == ["google_trends_graph"]
        assert task["date_to"] == "2026-10-15" and task["date_from"] == "2026-07-17"
        return {"items": [{"type": "google_trends_graph", "data": [
            {"date_from": "2026-10-13", "date_to": "2026-10-13", "values": [5]},
            {"date_from": "2026-10-14", "date_to": "2026-10-14", "values": [7]}]}]}, 1200
    monkeypatch.setattr(aisa, "rest", rest)
    out, cost, errors = google.daily_series(["synthid detector"], "US", today=TODAY)
    assert out == {"synthid detector": [(date(2026, 10, 13), 5), (date(2026, 10, 14), 7)]}
    assert cost == 1200 and not errors


def test_a_failed_call_is_named_and_the_others_still_come(monkeypatch):
    def rest(path, task):
        if task["keywords"][0] == "bad":
            raise aisa.AisaError("HTTP 502")
        return {"items": []}, 1200
    monkeypatch.setattr(aisa, "rest", rest)
    out, cost, errors = google.related_queries(["good", "bad"], "US")
    assert list(out) == ["good"] and len(errors) == 1
    assert errors[0].startswith("bad: ") and "HTTP 502" in errors[0]


def test_the_country_code_becomes_the_location_dataforseo_lists():
    assert google.location("US") == 2840 and google.location("jp") == 2392
    assert google.location("Atlantis") == "Atlantis"
