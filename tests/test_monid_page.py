"""The page-one, market, pain and ask readings, on Monid's doors.

The same tests as tests/test_radar.py has, with the calls stubbed where they now go:
DataForSEO through Monid (radar/dfs.py) and the chat and the X search through Monid
(radar/monid.py). No test ever reaches the network: the stubs answer in their place.
"""
import json

from radar import asks, competition, dfs, markets, monid, pains, report, serpshape


def _serp_items(results, ads=False):
    """The items of a page one, the way a search's answer holds them."""
    items = [{"type": "ai_overview"}] + [{"type": "organic", "domain": d, "url": u, "title": t, "rank_group": i + 1}
                                         for i, (d, u, t) in enumerate(results)]
    return items + ([{"type": "paid", "domain": "paid.example"}] if ads else [])


def _serp_stub(pages, count=19500):
    """A search that answers the pages it is given; the intitle count comes back too."""
    def result(endpoint, body):
        keyword = body.get("keyword") or ""
        if keyword.startswith("intitle:"):        # how many pages already carry the term in the title
            return {"items": [], "se_results_count": count}, 2000
        return {"items": pages[keyword]}, 2000
    return result


def _kd_stub(difficulties):
    """The difficulty of each keyword, the way the bulk endpoint holds it."""
    def rows(endpoint, body):
        return [{"keyword": k, "keyword_difficulty": difficulties[k]} for k in body["keywords"]], 12000
    return rows


def _reddit_post(pid, title, score=12, sub="VideoEditors"):
    return {"id": pid, "title": title, "text": "", "score": score, "comments": 3, "subreddit": sub,
            "url": f"https://www.reddit.com/r/{sub}/{pid}", "created": 0}


# --- page one: can a new site get in? ------------------------------------------------------------


def test_a_crowded_rising_term_is_watched_not_prioritized(cfg, tmp_path, monkeypatch):
    cfg["competition"]["max_rows"] = 8
    monkeypatch.setenv("MONID_API_KEY", "k")
    strong = [("canva.com", "https://canva.com/ai", "Canva AI"), ("adobe.com", "https://adobe.com/x", "Adobe")]
    weak = [("tiny1.ai", "https://tiny1.ai/", "Free Tool"), ("tiny2.ai", "https://tiny2.ai/", "Free Tool"),
            ("tiny3.ai", "https://tiny3.ai/x", "A guide")]
    pages = {"crowded term": _serp_items(strong, ads=True), "open term": _serp_items(strong + weak)}
    monkeypatch.setattr(dfs, "result", _serp_stub(pages))
    monkeypatch.setattr(dfs, "rows", _kd_stub({"crowded term": 60, "open term": 25}))
    rated = {"canva.com": 94, "adobe.com": 96, "tiny1.ai": 8, "tiny2.ai": 12, "tiny3.ai": 21}
    asked = []

    def ratings_of(provider, endpoint, body=None, query=None):
        asked.append(query["target"])
        return {"domain_rating": rated[query["target"]]}, 2000
    monkeypatch.setattr(competition.monid, "data", ratings_of)
    rows = [{"phrase": "crowded term", "decision": "prioritize"}, {"phrase": "open term", "decision": "prioritize"},
            {"phrase": "faded", "decision": "skip"}]
    cost, errors = competition.assess(rows, cfg, tmp_path)
    crowded, opened, faded = rows
    assert crowded["decision"] == "watch" and crowded["demoted"] and crowded["competition"]["ads"]
    assert crowded["competition"]["verdict"] == "crowded" and crowded["competition"]["weak"] == 0
    assert opened["decision"] == "prioritize" and opened["competition"]["weak"] == 3
    assert opened["competition"]["weakest"] == {"domain": "tiny1.ai", "dr": 8, "position": 3, "url": "https://tiny1.ai/"}
    assert "competition" not in faded and errors == [] and cost == 2 * 2000 + 12000 + 5 * 2000 + 2 * 2000
    assert crowded["competition"]["built"] == 19500 and crowded["competition"]["paid"] == ["paid.example"]
    competition.assess([{"phrase": "open term", "decision": "watch"}], cfg, tmp_path)
    assert len(asked) == 5                                    # ratings are kept 30 days, not bought again
    uc = {"names": ["x"], "rows": [dict(r, kind="generic", names=["x"], brand=False, new=True, now="rising", peak_day="2026-10-10",
                                       evidence="") for r in rows[:2]],
          "proposed": 2, "relevant": 2, "kept": 2, "benchmark": "gpts", "volume_month": "2026-08", "related": True}
    text = "\n".join(report.keyword_table(cfg, uc))
    assert "crowded · 0 weak · KD 60 · ads · 19,500 pages built · 1 advertisers" in text and "watch ↓" in text
    assert "open · 3 weak · KD 25 · 19,500 pages built · AI overview |" in text and "advertising on it: paid.example" in text
    assert "tiny1.ai (rated 8) at #3" in text and "Page one: open = three or more" in text
    assert "**crowded term**: no easy slot, even the weakest, canva.com, is rated 94 (#1)" in text


def test_a_new_term_reads_one_step_easier_on_page_one(cfg, tmp_path, monkeypatch):
    assert competition.verdict(0, 55, new=True) == "mixed" and competition.verdict(1, 30, new=True) == "open"
    assert competition.verdict(3, None, new=True) == "open" and competition.verdict(None, None, new=True) == "mixed"
    cfg["competition"]["max_rows"] = 8
    strong = [("canva.com", "https://canva.com/ai", "Canva AI"), ("adobe.com", "https://adobe.com/x", "Adobe")]
    pages = {"old term": _serp_items(strong), "new term": _serp_items(strong)}
    monkeypatch.setattr(dfs, "result", _serp_stub(pages))
    monkeypatch.setattr(dfs, "rows", _kd_stub({"old term": 60, "new term": 60}))
    rows = [{"phrase": "old term", "decision": "prioritize"}, {"phrase": "new term", "decision": "prioritize", "is_new": True}]
    competition.assess(rows, cfg, tmp_path)
    old, new = rows
    assert old["decision"] == "watch" and old["demoted"] and old["competition"]["verdict"] == "crowded"
    assert new["decision"] == "prioritize" and new["competition"]["verdict"] == "mixed" and new["competition"]["eased"]
    assert new["competition"]["results"][0]["url"] == "https://canva.com/ai"
    # the SERP shape reuses the results the page-one check read: no second purchase
    cfg["serp_shape"]["enabled"] = True

    def bought_twice(*a, **k):
        raise AssertionError("bought twice")
    monkeypatch.setattr(dfs, "result", bought_twice)
    assert serpshape.assess([new], cfg) == (0, []) and new["serp"]["verdict"] == "beatable" and new["serp"]["inner"] == 2


def test_page_one_keeps_forum_threads_and_marks_what_takes_clicks(cfg, tmp_path, monkeypatch):
    cfg["competition"]["max_rows"] = 8
    items = [{"type": "ai_overview"}, {"type": "people_also_ask"},
             {"type": "organic", "domain": "www.reddit.com", "url": "https://www.reddit.com/r/x/1", "title": "Best PDF editor?", "rank_group": 1},
             {"type": "organic", "domain": "tool.com", "url": "https://tool.com/", "title": "Tool", "rank_group": 2},
             {"type": "discussions_and_forums", "items": [
                 {"url": "https://www.reddit.com/r/x/1", "title": "Best PDF editor?", "domain": "www.reddit.com"},     # the same thread
                 {"url": "https://www.quora.com/q", "title": "Which PDF editor", "domain": "quora.com"}, {"title": "no url"}]}]

    def result(endpoint, body):
        return {"items": items}, 2000
    monkeypatch.setattr(dfs, "result", result)
    monkeypatch.setattr(dfs, "rows", lambda endpoint, body: ([], 12000))
    rows = [{"phrase": "pdf editor", "decision": "watch"}, {"phrase": "best pdf editor", "decision": "watch"}]
    competition.assess(rows, cfg, tmp_path)
    comp = rows[0]["competition"]
    assert [f["url"] for f in comp["forums"]] == ["https://www.reddit.com/r/x/1", "https://www.quora.com/q"]
    assert comp["features"] == ["ai_overview", "discussions_and_forums", "people_also_ask"]
    assert report._page_one(comp).endswith("· AI overview · forums")
    lines = "\n".join(report._thread_lines(rows))
    assert "half as often" in lines and "Pew Research Center" in lines
    first, second = [x for x in lines.splitlines() if x.startswith("- **")]
    assert first.startswith("- **best pdf editor** (a best-of search") and "[Best PDF editor?](https://www.reddit.com/r/x/1)" in second
    assert report._page_one({"verdict": "open", "features": ["video", "featured_snippet"]}) == "open · answer box · videos"


# --- ⑦ asks, pains and tutorial comments ---------------------------------------------------------


def test_asks_turns_reddit_requests_into_sized_jobs(cfg, tmp_path, monkeypatch):
    cfg["asks"]["patterns"] = ['"is there an ai tool"', '"how do i automate"']
    posts = {'"is there an ai tool"': [_reddit_post("a", "Is there an AI tool to add b-roll automatically?", 45),
                                       _reddit_post("b", "Any AI that turns slides into notes?", 35, "college"),
                                       _reddit_post("c", "Rate my setup", 1)],
             '"how do i automate"': [_reddit_post("a", "Is there an AI tool to add b-roll automatically?", 45)]}
    monkeypatch.setattr(asks.social, "reddit", lambda q, **k: (posts[q], 1880))
    monkeypatch.setattr(monid, "chat", lambda model, system, user, **k: json.dumps(
        [{"i": i, "kind": "request", "job": job, "ai": True} for i, job in enumerate(
            ["add b roll automatically", "convert slides to notes"])]))
    monkeypatch.setattr(asks.usecases, "volumes", lambda phrases, loc: (
        {"add b roll automatically": {"volume": 880}, "convert slides to notes": {"volume": 10}}, 90000))
    first = asks.asks(cfg, tmp_path)
    assert first["read"] == 2 and first["asks"] == 2 and first["unsized"] == 1          # "rate my setup" scored under the floor
    assert [r["phrase"] for r in first["rows"]] == ["add b roll automatically"] and first["cost_micros"] == 2 * 1880 + 90000
    assert first["terms"][0]["note"] == "Reddit: asked 1× (r/VideoEditors)"
    assert [q["phrase"] for q in first["quiet"]] == ["convert slides to notes"]           # asked, too few searches yet
    text = "\n".join(report._asks_lines(first, cfg))
    assert "| **add b roll automatically** | 1 (45) | r/VideoEditors | 880 |" in text and "do not republish" in text
    assert "| convert slides to notes | 1 (35) | r/college |" in text and "ideas rather than keywords" in text
    again = asks.asks(cfg, tmp_path)
    assert again["rows"][0]["again"] and again["terms"] == []                            # reported once


def test_asks_also_read_x_software_recommendations_and_freelancer(monkeypatch):
    import gzip
    import io
    c = {"x_query": '"is there a tool"', "x_min_likes": 10, "stack_all": ["softwarerecs"], "stack_sites": ["superuser"],
         "stack_titles": ["automate"], "freelancer_queries": ["automate"], "min_score": 2}
    monkeypatch.setattr(monid, "anyapi_rows", lambda endpoint, body: ([
        {"id": "1", "text": "is there a tool that turns slides into notes?", "likeCount": 44, "url": "u",
         "createdUtc": 1793376859, "lang": "en", "isReply": False}], 3000))
    stack = {"softwarerecs": {"items": [{"question_id": 7, "title": "Open-source tool to auto-generate API regression tests", "score": 0, "link": "l"}]},
             "superuser": {"items": [{"question_id": 8, "title": "Automate renaming 500 PDFs &amp; folders", "score": 3, "link": "m"}]}}
    jobs = {"result": {"projects": [{"id": 40767538, "title": "Claude AI & Canva Graphic Automation Expert Needed", "seo_url": "canva/x",
                                     "bid_stats": {"bid_count": 124}, "budget": {"minimum": 20.0, "maximum": 250.0},
                                     "currency": {"exchange_rate": 1.31512}, "preview_description": "Build an AI graphic system"}]}}

    class Res(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    def urlopen(req, timeout):
        url = req.full_url
        if "freelancer.com" in url:
            return Res(json.dumps(jobs).encode())
        site = "softwarerecs" if "softwarerecs" in url else "superuser"
        return Res(gzip.compress(json.dumps(stack[site]).encode()))
    monkeypatch.setattr(asks.urllib.request, "urlopen", urlopen)
    x, _ = asks.x_posts(c)
    se, _ = asks.stack_questions(c)
    fl, _ = asks.freelancer_jobs(c)
    assert x[0]["subreddit"] == "x" and x[0]["score"] == 44
    assert [q["title"] for q in se] == ["Open-source tool to auto-generate API regression tests", "Automate renaming 500 PDFs & folders"]
    assert se[0]["score"] == 2                       # every Software Recommendations question is a request, votes or not
    assert fl[0]["score"] == 124 and fl[0]["budget_usd"] == 329 and fl[0]["url"] == "https://www.freelancer.com/projects/canva/x"


def test_pains_groups_reviews_posts_and_comments_into_gaps(cfg, monkeypatch):
    ext = "a" * 32

    def serp(keyword, location_code=2840, language="en", device="desktop", depth=10):
        url = "https://www.youtube.com/watch?v=abcdefghijk"
        return {"items": [{"type": "organic", "domain": "shop.example", "url": url, "title": "x", "rank_group": 1}],
                "results_count": 100}, 2000
    monkeypatch.setattr(pains.google, "serp", serp)
    monkeypatch.setattr(pains.cws, "search", lambda keyword, limit=10: [ext])   # the store's own search

    def reviews(handle, stars=None, limit=100):     # the store's own call, one star level at a time
        if stars != 1:
            return []
        return [{"rating": 1, "text": "Needs a photo of your face, no way to upload a group shot", "date": "2026-10-13"},
                {"rating": 5, "text": "Great!", "date": "2026-10-12"}]
    monkeypatch.setattr(pains.cws, "page", lambda ext: {"id": ext, "path": "", "sid": "", "bl": "", "rpcids": ["x"]})
    monkeypatch.setattr(pains.cws, "reviews", reviews)
    monkeypatch.setattr(pains.social, "reddit", lambda q, **k: ([_reddit_post("r1", "Headshot apps all look plastic")], 1880))
    monkeypatch.setattr(pains.social, "youtube_comments", lambda vid: ([{"text": "Is there a way to do this for a whole team at once?", "likes": 40},
                                                                        {"text": "nice", "likes": 0}], 1000))
    monkeypatch.setattr(monid, "chat", lambda model, system, user, max_tokens=2500: json.dumps([
        {"gap": "No team or group headshots", "kind": "missing", "items": [0, 2], "phrase": "ai headshots for teams"},
        {"gap": "Results look plastic", "kind": "quality", "items": [1]}]))
    result = pains.run(cfg, "ai headshot")
    assert result["by_source"] == {"chrome": 1, "reddit": 1, "youtube": 1, "appstore": 0}   # 5 stars, "nice" and repeats are left out
    assert result["gaps"][0]["count"] == 2 and result["gaps"][0]["sources"] == ["chrome", "youtube"]
    text = report.pains(result)
    assert "| No team or group headshots | missing | 2 | ai headshots for teams |" in text and "Trustpilot" in text


def test_tutorial_asks_only_for_leads_marked_one_click(cfg, monkeypatch):
    cfg["tutorials"]["max_leads"] = 3
    monkeypatch.setattr(pains.google, "serp", lambda keyword, location_code=2840, language="en", device="desktop", depth=10: (
        {"items": [{"type": "organic", "domain": "shop.example", "url": "https://www.youtube.com/watch?v=abcdefghijk",
                    "title": "t", "rank_group": 1}], "results_count": 100}, 2000))
    monkeypatch.setattr(pains.social, "youtube_comments", lambda vid: ([{"text": "Can someone make this one click? Too many steps", "likes": 12}], 1000))
    monkeypatch.setattr(monid, "chat", lambda model, system, user, max_tokens=2500: json.dumps(
        [{"gap": "Too many manual steps", "kind": "wants a tool", "items": [0], "phrase": "one click blender export"}]))
    out = pains.tutorial_asks(cfg, [{"term": "opus blender game", "one_click": "tutorial"}, {"term": "plain", "one_click": None}])
    assert [x["term"] for x in out["leads"]] == ["opus blender game"] and out["cost_micros"] == 2000 + 1000
    text = "\n".join(report._tutorial_lines(out))
    assert "Too many manual steps (wants a tool, 1) → one click blender export" in text


# --- markets: where a term is searched --------------------------------------------------------------


def test_markets_reuse_the_tables_own_volumes_and_buy_the_rest(cfg, monkeypatch):
    cfg["markets"].update(enabled=True, codes=["US", "DE"], difficulty=True, top_region=True)
    bought = []

    def rows(endpoint, body):
        if endpoint == dfs.VOLUME:
            bought.append((body["location_code"], body["keywords"]))
            vol = {2840: {"pdfcraft": 8000}, 2276: {"pdfcraft": 2000, "ai image detector": 4000}}[body["location_code"]]
            return [{"keyword": k, "search_volume": vol.get(k, 0)} for k in body["keywords"]], 90000
        kd = {"en": {"pdfcraft": 30, "ai image detector": 60}, "de": {"pdfcraft": 10, "ai image detector": 20}}[body["language_code"]]
        return [{"keyword": k, "keyword_difficulty": kd[k]} for k in body["keywords"]], 12000

    def result(endpoint, body):
        # the Trends map: the interest by country, worldwide, one map for the group and one a keyword
        names = body["keywords"]
        data = [{"geo_id": "US", "geo_name": "United States", "values": [40]},
                {"geo_id": "DE", "geo_name": "Germany", "values": [70]},
                {"geo_id": "SX", "geo_name": "Sint Maarten", "values": [100]}]
        items = [{"type": "google_trends_map", "keywords": names,
                  "data": [dict(d, values=d["values"] * len(names)) for d in data]}]
        items += [{"type": "google_trends_map", "keywords": [n], "data": data} for n in names]
        return {"items": items}, 2400
    monkeypatch.setattr(dfs, "rows", rows)
    monkeypatch.setattr(dfs, "result", result)
    rows_ = [{"phrase": "pdfcraft"}, {"phrase": "ai image detector"}]
    cost, errors = markets.assess(rows_, cfg, {2840: {"ai image detector": {"volume": 60000, "month": "2026-08"}}})
    assert errors == [] and bought == [(2840, ["pdfcraft"]), (2276, ["pdfcraft", "ai image detector"])]
    assert cost == 2 * 90000 + 2 * 12000 + 2400                  # the two rows share one Trends map call
    pdf, det = rows_
    assert pdf["markets"]["by"] == {"US": {"volume": 8000, "kd": 30}, "DE": {"volume": 2000, "kd": 10}}
    assert pdf["markets"]["best"] == "US" and det["markets"]["best"] == "US"   # 267 vs 200; 1000 vs 200
    assert det["markets"]["top_region"] == {"id": "DE", "name": "Germany", "value": 70}
    assert report._markets(pdf["markets"]) == "**best US** · US 8k/30 · DE 2k/10 · top Germany"
