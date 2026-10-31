"""The readings that go through Monid: the feeds, the X accounts, the Reddit and YouTube
readings and the AI answers. Every call is stubbed here: a test never spends money."""
import json
from datetime import date, datetime, timedelta, timezone

import pytest

from radar import ai_answers, dfs, feeds, monid, report, social
from radar.sources import base_name, novel_names

NOW = datetime(2026, 10, 3, 12, tzinfo=timezone.utc)


# --- ① X: only names the accounts have not used before ------------------------------

def _post(account, text, likes=0, hours_ago=2):
    return {"account": account, "text": text, "likes": likes, "url": "", "created": NOW - timedelta(hours=hours_ago)}


def test_x_reports_a_new_name_two_accounts_used():
    rows = novel_names([_post("a", "Strata is fast"), _post("b", "Tried Strata today")], {}, NOW)
    assert [r["term"] for r in rows] == ["strata"] and rows[0]["sources"] == ["a", "b"]
    assert rows[0]["note"] == "X: 2 accounts, 0 likes"


def test_x_skips_names_already_in_the_vocabulary():
    rows = novel_names([_post("a", "Strata is fast"), _post("b", "Tried Strata today")], {"strata": "2026-09-01"}, NOW)
    assert rows == []


def test_x_one_account_needs_likes_and_an_unmistakable_name():
    assert novel_names([_post("a", "Qwen3.9 is fast", likes=20)], {}, NOW) == []
    assert [r["term"] for r in novel_names([_post("a", "Qwen3.9 is fast", likes=500)], {}, NOW)] == ["qwen3.9"]
    # a plain capitalised word may only be the start of a sentence: one account is not enough
    assert novel_names([_post("a", "Strata is fast", likes=500)], {}, NOW) == []


def test_x_one_post_yields_at_most_three_names():
    post = _post("a", "New: Alpha1 Beta2 Gamma3 Delta4 Eps5 Zeta6 models", likes=900)
    assert len(novel_names([post], {}, NOW)) <= 3


def test_x_ignores_old_posts_and_retweets():
    old = [_post("a", "Strata is fast", hours_ago=48), _post("b", "Strata rocks", hours_ago=48)]
    rts = [_post("a", "RT @x: Strata is fast"), _post("b", "RT @x: Strata rocks")]
    assert novel_names(old, {}, NOW) == [] and novel_names(rts, {}, NOW) == []


def test_x_posts_are_read_from_monid(cfg, tmp_path, monkeypatch):
    import radar.sources as src
    asked = []

    def anyapi(endpoint, body):
        asked.append((endpoint, body))
        return ({"tweets": [
            {"text": "Strata is fast", "likes": 12, "url": "https://x.com/a/status/1",
             "createdUtc": 1790000000, "authorHandle": "a"},
            {"text": "Strata rocks", "likes": 3, "url": "https://x.com/b/status/2",
             "createdUtc": 1790000000, "authorHandle": "b"},        # a repost: not the account's own words
        ]}, 1800)

    monkeypatch.setattr(src.monid, "anyapi", anyapi)
    cfg["x_accounts"] = {"labs": ["a"], "bloggers": []}
    got = src.x_new(cfg, tmp_path)
    assert asked == [("/twitter.user_posts", {"handle": "a"})]
    assert got["accounts"] == 1 and got["answered"] == 1 and got["posts"] == 1 and got["cost_micros"] == 1800
    assert got["first_run"] and got["terms"] == []       # the first run only builds the vocabulary
    assert src._posted({"createdUtc": 1790000000}) == datetime.fromtimestamp(1790000000, tz=timezone.utc)


def test_x_keeps_the_names_of_the_day_out_of_the_vocabulary_twice(cfg, tmp_path, monkeypatch):
    import radar.sources as src
    monkeypatch.setattr(src.monid, "anyapi", lambda endpoint, body: (
        {"tweets": [{"text": "Strata is fast", "likes": 900, "url": "", "createdUtc": 1790000000,
                     "authorHandle": "a"}]}, 1800))
    cfg["x_accounts"] = {"labs": ["a"], "bloggers": []}
    src.x_new(cfg, tmp_path)                                # the vocabulary of today
    later = src.x_new(cfg, tmp_path)
    assert later["terms"] == [] and later["posts"] == 1      # the name is known: it is not new again


def test_x_names_an_account_that_failed_to_answer(cfg, tmp_path, monkeypatch):
    import radar.sources as src

    def anyapi(endpoint, body):
        raise monid.MonidError("HTTP 402: not enough wallet balance")

    monkeypatch.setattr(src.monid, "anyapi", anyapi)
    cfg["x_accounts"] = {"labs": ["a"], "bloggers": []}
    with pytest.raises(monid.MonidError):
        src.x_new(cfg, tmp_path)


# --- ① HF: copies of one model group under its base name -----------------------------

def test_hf_base_name_strips_quantisation_and_size():
    assert base_name("abenzerps/Qwen-Image-2.1-Uncensored-GGUF") == "qwen image 2.1"
    assert base_name("Qwen/Qwen3.8-Flash-Next-A6B-Instruct") == "qwen3.8 flash next"
    assert base_name("Lightricks/LTX-2.5") == "ltx 2.5"


def test_hf_reads_trending_models_and_spaces_and_merges_a_model_with_its_demo(tmp_path, monkeypatch):
    import radar.sources as src
    lists = {"models": [{"id": "old/Old-Model"}], "spaces": [{"id": "old/old-demo"}]}
    monkeypatch.setattr(src, "_trending", lambda what: lists[what])
    first = src.hf_new({}, tmp_path)
    assert first["first_run"] and first["terms"] == [] and first["models"] == 1 and first["spaces"] == 1
    lists["models"] = [{"id": "krea/Krea-2", "pipeline_tag": "text-to-image", "likes": 900}, {"id": "old/Old-Model"}]
    lists["spaces"] = [{"id": "krea/Krea-2-demo", "sdk": "gradio", "likes": 300}, {"id": "lighton/LightOnOCR-3-webgpu", "sdk": "static"}]
    later = src.hf_new({}, tmp_path)
    terms = {r["term"]: r for r in later["terms"]}
    assert set(terms) == {"krea 2", "lightonocr 3"} and not later["first_run"]
    assert terms["krea 2"]["examples"] == ["krea/Krea-2 (text-to-image, ♥900)", "krea/Krea-2-demo (gradio, ♥300)"]
    assert terms["lightonocr 3"]["note"] == "Hugging Face Spaces: new at #2"


# --- ⑨ feeds: the paid sources read through Monid ------------------------------------

PH = ('<feed><entry><id>1</id><link rel="alternate" href="https://www.producthunt.com/products/dashrendr"/>'
      '<title>Dashrendr</title><content type="html"><p>Dashboards from a sentence</p></content></entry></feed>')


def test_feeds_take_a_snapshot_first_then_report_what_is_new(cfg, tmp_path, monkeypatch):
    cfg["feeds"]["enabled"] = ["producthunt", "hn", "openrouter", "awesome", "trending"]
    cfg["feeds"]["awesome"] = ["https://raw.example/README.md"]
    cfg["feeds"]["trending"] = ["tiktok"]
    now = int(feeds.time.time())
    pages = {"https://www.producthunt.com/feed": PH,
             "https://openrouter.ai/api/v1/models": json.dumps({"data": [{"id": "x/pareto", "name": "Unbiased: Pareto 26.9", "created": now}]}),
             "https://raw.example/README.md": "- [Notchlive](https://captions-demo.app) - live captions\n"}

    def get(url):
        if "hn.algolia" in url:
            return json.dumps({"hits": [{"objectID": "9", "title": "Show HN: Quarry – SQL for spreadsheets", "points": 80}]}).encode()
        return pages[url].encode()
    monkeypatch.setattr(feeds, "get", get)
    monkeypatch.setattr(feeds.monid, "run", lambda provider, endpoint, body=None, query=None: (
        {"code": 0, "data": {"trending_search_words": [{"trendingSearchWord": "AI yearbook filter"},
                                                       {"trendingSearchWord": "Mayor Arrested"}]}}, 1000))
    first = feeds.feeds(cfg, tmp_path, today=date(2026, 10, 14))
    assert set(first["first_run"]) == {"producthunt", "hn", "openrouter", "awesome", "trending"}
    # a first run lists what the source itself dates as recent; the undated awesome list and the undated PH entry wait
    assert sorted(x["term"] for x in first["terms"]) == ["ai yearbook filter", "pareto 26.9", "quarry"]
    pages["https://raw.example/README.md"] += "- [Pixdemo](https://pixdemo.app) - prompts behind images\n"
    again = feeds.feeds(cfg, tmp_path, today=date(2026, 10, 15))
    assert [x["term"] for x in again["terms"]] == ["pixdemo"] and again["new"]["awesome"][0]["url"] == "https://pixdemo.app"
    assert again["cost_micros"] == 1000 and not again["new"].get("trending")       # the filter word was seen yesterday
    assert feeds.TOOLISH.search("AI yearbook filter") and not feeds.TOOLISH.search("Mayor Arrested")
    assert feeds.TOOLISH.search("AI换脸特效") and not feeds.TOOLISH.search("Chair")


def test_weekly_feeds_wait_for_their_day(cfg, tmp_path, monkeypatch):
    cfg["feeds"]["enabled"], cfg["feeds"]["weekly"], cfg["feeds"]["weekday"] = ["rising"], ["rising"], 0
    asked = []

    def related_queries(terms, geo="US"):
        asked.extend(terms)
        return {t: {"rising": [f"{t} free"], "top": []} for t in terms}, 6000 * len(terms), []
    monkeypatch.setattr(feeds.google, "related_queries", related_queries)
    assert feeds.feeds(cfg, tmp_path, today=date(2026, 10, 14))["read"] == {} and asked == []          # a Wednesday
    assert feeds.feeds(cfg, tmp_path, today=date(2026, 10, 14), weekly=True)["read"]["rising"] == len(cfg["feeds"]["seeds"])


def test_rising_words_must_share_the_seed(cfg, tmp_path, monkeypatch):
    cfg["feeds"]["enabled"], cfg["feeds"]["weekly"], cfg["feeds"]["seeds"] = ["rising"], [], ["ai generator"]
    monkeypatch.setattr(feeds.google, "related_queries", lambda terms, geo="US": (
        {t: {"rising": ["hotel lobby ai generator", "best pizza near me", "ai rap video generator", "how to bake a cake"],
             "top": []} for t in terms}, 6000 * len(terms), []))
    got = feeds.feeds(cfg, tmp_path, today=date(2026, 10, 15))
    assert [x["term"] for x in got["terms"]] == ["hotel lobby ai generator", "ai rap video generator"]


def test_trending_reads_tikhub_through_monid(monkeypatch):
    asked = []

    def run(provider, endpoint, body=None, query=None):
        asked.append((provider, endpoint, body, query))
        if endpoint.endswith("fetch_trending_searchwords"):
            return ({"code": 0, "data": {"trending_search_words": [{"trendingSearchWord": "AI yearbook filter"},
                                                                   {"trendingSearchWord": "Mayor Arrested"}]}}, 1000)
        return ({"code": 0, "data": {"word_list": [{"word": "AI换脸特效"}]}}, 1000)

    monkeypatch.setattr(feeds.monid, "run", run)
    rows, cost = feeds.trending({"trending": ["tiktok", "douyin"]})
    assert asked == [("tikhub", "/api/v1/tiktok/web/fetch_trending_searchwords", None, {}),
                     ("tikhub", "/api/v1/douyin/app/v3/fetch_hot_search_list", None, {})]
    assert [r["name"] for r in rows] == ["AI yearbook filter", "AI换脸特效"] and cost == 2000


def test_a_trending_source_that_fails_is_named_and_the_others_keep_reading(monkeypatch):
    def run(provider, endpoint, body=None, query=None):
        if endpoint.endswith("fetch_hot_search_list"):
            raise monid.MonidError("HTTP 402: not enough wallet balance")
        return ({"code": 0, "data": {"trending_search_words": [{"trendingSearchWord": "AI yearbook filter"}]}}, 1000)

    monkeypatch.setattr(feeds.monid, "run", run)
    with pytest.raises(feeds.PartialFailure) as exc:
        feeds.trending({"trending": ["tiktok", "douyin"]})
    assert [r["name"] for r in exc.value.rows] == ["AI yearbook filter"]
    assert "douyin" in exc.value.errors[0]


def test_the_feed_names_the_source_that_failed(cfg, tmp_path, monkeypatch):
    cfg["feeds"]["enabled"], cfg["feeds"]["trending"] = ["trending"], ["tiktok"]

    def run(provider, endpoint, body=None, query=None):
        raise monid.MonidError("HTTP 402: not enough wallet balance")

    monkeypatch.setattr(feeds.monid, "run", run)
    got = feeds.feeds(cfg, tmp_path, today=date(2026, 10, 15))
    assert got["read"] == {"trending": 0} and got["terms"] == []
    assert got["errors"] and got["errors"][0].startswith("trending: ") and "402" in got["errors"][0]


def test_google_new_reads_the_serp_through_monid(monkeypatch):
    asked = []

    def rows(endpoint, body):
        asked.append((endpoint, body))
        return ([{"type": "organic", "url": "https://figma.com/community/plugin/1",
                  "title": "Strata - Figma", "description": "A plugin."},
                 {"type": "paid", "url": "https://ads.example", "title": "Ad"}], 2000)

    monkeypatch.setattr(feeds.dfs, "rows", rows)
    got, cost = feeds.google_new({"google_new": ["figma.com/community"]})
    assert asked[0][0] == dfs.SERP and asked[0][1]["keyword"] == "site:figma.com/community"
    assert asked[0][1]["search_param"] and asked[0][1]["depth"] == 100
    assert cost == 2000
    assert len(got) == 1 and got[0]["key"] == "https://figma.com/community/plugin/1"
    assert got[0]["name"] == "Strata" and got[0]["where"] == "figma.com/community" and got[0]["fresh"]


# --- Reddit and YouTube, read through Monid -------------------------------------------

def test_reddit_reads_the_monid_search(monkeypatch):
    asked = []
    rows = [{"id": "abc", "title": "Any AI that turns slides into notes?", "selftext": "  Looking for one.  ",
             "score": 35, "numComments": 4, "subreddit": "college", "permalink": "/r/college/1",
             "createdUtc": 1790000000, "nsfw": False}]

    def anyapi_rows(endpoint, body):
        asked.append((endpoint, body))
        return rows, 350

    monkeypatch.setattr(social.monid, "anyapi_rows", anyapi_rows)
    posts, cost = social.reddit("ai notes", time="week", sort="top", limit=25)
    assert asked == [("/reddit.search", {"query": "ai notes", "sort": "top", "timeframe": "week"})]
    assert cost == 350
    assert posts == [{"id": "abc", "title": "Any AI that turns slides into notes?", "text": "Looking for one.",
                      "score": 35, "comments": 4, "subreddit": "college",
                      "url": "https://www.reddit.com/r/college/1", "created": 1790000000.0}]
    rows[0]["nsfw"] = True
    assert social.reddit("ai notes")[0] == []


def test_youtube_reads_the_monid_comments(monkeypatch):
    asked = []

    def anyapi_rows(endpoint, body):
        asked.append((endpoint, body))
        return ([{"text": "Is there a way to do this for a whole team at once?", "likes": 40}], 1200)

    monkeypatch.setattr(social.monid, "anyapi_rows", anyapi_rows)
    comments, cost = social.youtube_comments("abc123")
    assert asked == [("/youtube.video_comments", {"url": "https://www.youtube.com/watch?v=abc123"})]
    assert comments == [{"text": "Is there a way to do this for a whole team at once?", "likes": 40}] and cost == 1200


# --- ⑦ AI answers, read through DataForSEO and Monid ----------------------------------

CHATGPT = {"model_name": "gpt-4o-mini-2024-07-18", "web_search": False,
           "items": [{"type": "message", "sections": [
               {"type": "text", "text": "Here are some tools:\n\n1. **Quizlet**: flashcards.\n2. **ChatGPT**: answers.\n"}]}],
           "fan_out_queries": None,
           "search_results": [{"url": "https://www.quizlet.com/quiz", "title": "Quizlet"},
                              {"url": "https://app.mysite.com/app", "title": "My site"}]}
AI_MODE = {"items": [{"type": "ai_overview", "references": [{"source": "NoteGPT", "domain": "notegpt.io"}],
                      "markdown": "Yes, several tools do.[[1]](https://notegpt.io/a)\n\nOptions:\n\n"
                                  "- **[NoteGPT AI PPT Summarizer](https://notegpt.io/ppt-summary)** : extracts text.\n"
                                  "* **Knowt** : flashcards too.\n1. **`SlidesAI`**: also.\n\n"
                                  "Opinions on **Reddit** are mixed. Prefer a **free web tool**?",
                      "items": [{"references": [{"source": "Knowt", "domain": "knowt.com"}, {"source": "NoteGPT", "domain": "notegpt.io"}]},
                                {"references": None}]}]}


def test_ai_answers_name_tools_and_cite_sites(cfg, monkeypatch):
    rows = [{"phrase": "convert slides to notes", "posts": [], "subreddits": []}, {"phrase": "add b roll", "posts": [], "subreddits": []}]
    assert ai_answers.assess(rows, cfg) == (0, []) and "ai" not in rows[0]                      # off by default: nothing bought
    cfg["ai_answers"].update(enabled=True, engines=["chatgpt", "google", "bing"], max_rows=1)
    cfg["yours"]["domains"] = ["mysite.com"]
    asked = []

    def result(endpoint, body):
        asked.append((endpoint, body))
        return (CHATGPT if endpoint == dfs.CHATGPT else AI_MODE), 4000
    monkeypatch.setattr(dfs, "result", result)
    cost, errors = ai_answers.assess(rows, cfg)
    assert cost == 8000 and errors == ["unknown engine: bing"] and "ai" not in rows[1]
    bodies = {endpoint: body for endpoint, body in asked}
    assert set(bodies) == set(ai_answers.PATHS.values())
    assert bodies[dfs.CHATGPT] == {"model_name": "gpt-4o-mini",
                                   "user_prompt": "What is the best tool to convert slides to notes?",
                                   "max_output_tokens": 300, "web_search": True}
    assert bodies[dfs.AI_MODE] == {"keyword": "What is the best tool to convert slides to notes?",
                                   "location_code": 2840, "language_code": "en", "device": "desktop"}
    ai = rows[0]["ai"]
    assert ai["by"]["chatgpt"] == {"names": ["Quizlet", "ChatGPT"], "sites": ["quizlet.com", "app.mysite.com"]} and ai["yours"]
    assert ai["by"]["google"] == {"names": ["NoteGPT AI PPT Summarizer", "Knowt", "SlidesAI"], "sites": ["notegpt.io", "knowt.com"]}
    assert report._ai_cell(ai) == "Quizlet, ChatGPT, NoteGPT AI PPT Summarizer +2 ✅" and report._ai_cell(None) == "—"
    assert report._ai_cell({"by": {"chatgpt": {"names": [], "sites": []}}}) == "no tool named"
    lines = "\n".join(report._ai_lines(rows, cfg))
    assert "- **convert slides to notes**: ChatGPT names Quizlet, ChatGPT; cites quizlet.com, app.mysite.com" in lines
    assert "Google AI Mode names NoteGPT AI PPT Summarizer, Knowt, SlidesAI; cites notegpt.io, knowt.com · ✅ cites your site" in lines


def test_an_answer_that_failed_is_named(cfg, monkeypatch):
    cfg["ai_answers"].update(enabled=True, engines=["chatgpt"], max_rows=1)

    def result(endpoint, body):
        raise dfs.DfsError("Monid: HTTP 402; AIsa: the AIsa wallet is empty")

    monkeypatch.setattr(dfs, "result", result)
    cost, errors = ai_answers.assess([{"phrase": "convert slides to notes", "posts": [], "subreddits": []}], cfg)
    assert cost == 0 and len(errors) == 1 and "chatgpt" in errors[0]


def test_ai_answers_show_in_the_asks_table(cfg, monkeypatch):
    post = {"title": "Any AI that turns slides into notes?", "url": "https://reddit.com/r/college/1", "score": 35}
    ak = {"read": 3, "asks": 1, "unsized": 0, "quiet": [], "errors": [],
          "rows": [{"phrase": "convert slides to notes", "posts": [post], "score": 35, "subreddits": ["college"], "volume": 880,
                    "ai": {"prompt": "x", "by": {"chatgpt": {"names": [], "sites": ["quizlet.com"]}}, "yours": False}}]}
    text = "\n".join(report._asks_lines(ak, cfg))
    assert "| Top post | AI answers name |" in text and "|---|---|---|---|---|---|---|" in text
    assert text.count("| no tool named |") == 1 and "ChatGPT names no tool named; cites quizlet.com" in text
    del ak["rows"][0]["ai"]
    assert "AI answers" not in "\n".join(report._asks_lines(ak, cfg))                        # the column only when it was read
