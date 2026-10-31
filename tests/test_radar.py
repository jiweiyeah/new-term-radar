"""The radar's pure logic: name extraction, signal scoring, the store, the judge's parsing,
the review's verdict, the reports. Network calls are not exercised here."""
import json
import re
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from radar import __main__ as cli
from radar import adsense, breakout, config, cws, dfs, domains, gsc, i18n, judge, new_sites, notify, report, review, sitemaps, store
from radar import page_age as _page_age
from radar.sources import base_name, novel_names
from radar.terms import names

REAL_ASK = _page_age.ask       # conftest stubs RDAP for every test; the RDAP test needs the real one

NOW = datetime(2026, 10, 3, 12, tzinfo=timezone.utc)


# --- names(): what a post can name -------------------------------------------------

def test_names_keeps_versioned_model_names_whole():
    found = names("Introducing Claude Opus 5.5, our best model.")
    assert "claude opus 5.5" in found and "opus 5.5" in found


def test_names_drops_phrases_made_only_of_stop_words():
    assert "claude code" not in names("Try it in Claude Code today")


def test_names_takes_a_name_that_starts_the_sentence():
    assert "strata" in names("Strata now runs Qwen3.8-Flash-Next on a 12GB RTX 5070. Big day.")


def test_names_stops_at_the_end_of_a_sentence():
    assert not any("big" in n and "5070" in n for n in names("on a RTX 5070. Big day."))


def test_names_ignores_urls_and_handles():
    assert names("see https://Example.com/Foo by @SomeOne") == set()


def test_names_breaks_on_chinese_text_between_words():
    found = names("用 Opus 5.5 配音（Gemini 3.8 Flash TTS，我提供了 API Key）")
    assert "gemini 3.8 flash" in found and not any("tts" in n and "api" in n for n in found)


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


# --- ② breakout: one repo taking off --------------------------------------------------

def _repo(name, stars, days_old):
    created = (NOW - timedelta(days=days_old)).isoformat().replace("+00:00", "Z")
    return {"full_name": name, "stargazers_count": stars, "created_at": created, "description": ""}


def test_breakout_flags_a_fast_repo_and_skips_a_slow_one():
    rows = breakout.score([_repo("Niko1221/Strata", 7734, 9), _repo("a/slow", 900, 12)], None, NOW)
    assert [r["repo"] for r in rows] == ["Niko1221/Strata"]


def test_breakout_flags_a_jump_since_yesterday_even_when_the_average_is_low():
    rows = breakout.score([_repo("a/late", 1500, 13)], {"a/late": 900}, NOW)
    assert rows and rows[0]["delta"] == 600


def test_breakout_variants_include_handle_and_display_name():
    row = {"owner": "Niko1221", "name": "Strata"}
    assert breakout.variants(row, "Niko") == ["strata", "niko1221 strata", "niko strata"]
    assert breakout.variants(row, "") == ["strata", "niko1221 strata"]


# --- ③ sitemaps: one entry in 17 languages counts once ---------------------------------

def test_locale_prefix_is_stripped():
    urls = ["https://lobehub.com/skills/x", "https://lobehub.com/zh/skills/x", "https://lobehub.com/zh-TW/skills/x"]
    assert {re.sub(sitemaps._LOCALE, r"\1", u) for u in urls} == {"https://lobehub.com/skills/x"}


def test_paths_are_not_mistaken_for_locales():
    assert re.sub(sitemaps._LOCALE, r"\1", "https://lobehub.com/mcp/foo") == "https://lobehub.com/mcp/foo"


def test_sitemap_diff_reports_a_word_new_urls_share(cfg, tmp_path, monkeypatch):
    cfg["sitemaps"] = {"site": {"index": "https://example.com/sitemap.xml", "pause": 0}}
    old = {f"https://example.com/tools/thing-{i}" for i in range(40)}
    new = old | {f"https://example.com/tools/hyperframes-{w}" for w in ("starter", "captions", "studio")}
    answers = iter([old, new])
    monkeypatch.setattr(sitemaps, "site_urls", lambda *a: next(answers))
    first = sitemaps.diff(cfg, tmp_path)
    assert first["sites"]["site"]["first_run"] and first["terms"] == []
    second = sitemaps.diff(cfg, tmp_path)
    assert second["sites"]["site"]["new"] == 3 and second["terms"][0]["term"] == "hyperframes"


def test_a_site_that_fails_keeps_its_snapshot(cfg, tmp_path, monkeypatch):
    cfg["sitemaps"] = {"site": {"index": "https://example.com/sitemap.xml", "pause": 0}}
    monkeypatch.setattr(sitemaps, "site_urls", lambda *a: {"https://example.com/a"})
    sitemaps.diff(cfg, tmp_path)

    def down(*a):
        raise OSError("503")
    monkeypatch.setattr(sitemaps, "site_urls", down)
    assert "error" in sitemaps.diff(cfg, tmp_path)["sites"]["site"]
    monkeypatch.setattr(sitemaps, "site_urls", lambda *a: {"https://example.com/a"})
    assert sitemaps.diff(cfg, tmp_path)["sites"]["site"]["new"] == 0


# --- ③ new sites (whois overview, weekly) -----------------------------------------------

FIXTURE = Path(__file__).parent / "fixtures/whois_new_ai_sites.json"   # a real answer, 2026-10-06


def test_new_site_name_terms():
    assert new_sites.name_terms("ai-hailuo.com") == ["hailuo"]
    assert new_sites.name_terms("livephoto.video") == ["livephoto"]
    assert new_sites.name_terms("ai-video-tool.com") == []   # only stop words left




def test_new_sites_query_needs_both_dates(cfg):
    body = new_sites._task(cfg["new_sites"], date(2026, 10, 6), "%ai%")
    fields = [f[0] for f in body["filters"] if isinstance(f, list)]
    assert fields[:2] == ["created_datetime", "first_seen"] and body["filters"][0][2].startswith("2026-07-08")


def ranked(word, pos, loc=2840, vol=30, kd=0, new=True):
    """One item of DataForSEO Labs' ranked keywords."""
    return {"keyword_data": {"keyword": word, "location_code": loc, "keyword_info": {"search_volume": vol},
                             "keyword_properties": {"keyword_difficulty": kd}},
            "ranked_serp_element": {"serp_item": {"rank_absolute": pos, "relative_url": "/" + word.replace(" ", "-"),
                                                  "rank_changes": {"is_new": new}},
                                    "previous_updated_time": "2026-08-26 03:00:00 +00:00", "last_updated_time": "2026-10-06 03:00:00 +00:00"}}






def test_rest_reads_a_dataforseo_answer_and_its_cost(monkeypatch):
    from radar import aisa
    monkeypatch.setattr(aisa, "_post", lambda url, body, timeout: {"status_code": 20000, "cost": 0.1212,
                                                                    "tasks": [{"status_code": 20000, "result": [{"total_count": 38912}]}]})
    assert aisa.rest("domain_analytics/whois/overview/live", {"limit": 1}) == ({"total_count": 38912}, 121200)
    monkeypatch.setattr(aisa, "_post", lambda url, body, timeout: {"status_code": 20000, "tasks": [{"status_code": 40501, "status_message": "Invalid Field"}]})
    try:
        aisa.rest("x", {})
        raise AssertionError("a refused task must raise")
    except aisa.AisaError as exc:
        assert "Invalid Field" in str(exc)


# --- ④ Search Console ---------------------------------------------------------------------

def test_gsc_keeps_queries_the_earlier_window_never_saw():
    recent = [{"query": "hyperframes skill", "page": "/a", "impressions": 4, "clicks": 1},
              {"query": "hyperframes skill", "page": "/b", "impressions": 3, "clicks": 0},
              {"query": "ppt skill", "page": "/c", "impressions": 90, "clicks": 9}]
    fresh = gsc.fresh_queries(recent, [{"query": "ppt skill"}])
    assert fresh == [{"query": "hyperframes skill", "impressions": 7, "clicks": 1, "page": "/a"}]


def test_gsc_is_off_without_a_site(cfg):
    assert gsc.gsc_new(cfg) == {"off": True}


# --- the name judge ------------------------------------------------------------------------

def test_judge_parses_scores_and_drops_what_it_cannot_use():
    answer = 'Here you go:\n[{"i": 0, "score": 0.9}, {"i": 1, "score": "0.1"}, {"i": 7, "score": 1}, {"i": 2, "score": 3}, {"x": 1}]'
    assert judge.parse(answer, 3) == {0: 0.9, 1: 0.1}
    assert judge.parse("sorry", 3) == {}






# --- merging the signals ---------------------------------------------------------------------

def _no_judge(monkeypatch):
    monkeypatch.setattr(report.judge, "score", lambda rows, model: {"judged": 0})


def test_leads_merge_a_term_seen_by_two_signals(cfg, monkeypatch):
    _no_judge(monkeypatch)
    blocks = {"x": {"terms": [{"term": "strata", "recent": 2, "note": "X: 2 accounts"}]},
              "breakout": {"terms": [{"term": "strata", "recent": 1, "note": "GitHub breakout",
                                      "variants": ["strata", "niko1221 strata"]}]},
              "sitemap": {"terms": [{"term": "other", "recent": 3, "lift": 4.0}]}}
    rows, _ = report.leads(cfg, blocks)
    assert rows[0]["term"] == "strata" and rows[0]["kinds"] == ["x", "breakout"]
    assert rows[0]["variants"] == ["strata", "niko1221 strata"]
    assert rows[1]["signals"] == ["③ sitemaps: 3 places (×4.0)"]


def test_names_lead_and_known_terms_are_not_recorded(cfg, monkeypatch):
    cfg["known_terms"] = ["Remotion"]

    def score(rows, model):
        for r in rows:
            r["named"] = {"dashboard": 0.1, "remotion skill": 0.9, "hyperframes": 0.95}[r["term"]]
        return {"judged": len(rows)}
    monkeypatch.setattr(report.judge, "score", score)
    blocks = {"sitemap": {"terms": [{"term": t, "recent": 3, "lift": 2.0} for t in ("dashboard", "remotion skill", "hyperframes")]}}
    rows, _ = report.leads(cfg, blocks)
    assert [r["term"] for r in report.named(cfg, rows)] == ["hyperframes", "remotion skill"]
    assert [r["term"] for r in report.to_record(cfg, rows)] == ["hyperframes"]


def test_known_terms_match_either_way(cfg):
    cfg["known_terms"] = ["ppt skills"]
    assert config.known(cfg, "ppt") == "ppt skills" and config.known(cfg, "best ppt skills") == "ppt skills"
    assert config.known(cfg, "hyperframes") == ""


# --- the store ---------------------------------------------------------------------------------

def _lead(term, kinds, **more):
    return {"term": term, "kinds": kinds, "signals": [f"note {term}"], **more}


def test_store_upserts_and_merges(tmp_path):
    conn = store.connect(tmp_path)
    store.record(conn, [_lead("strata", ["x"], named=0.9, variants=["strata"])], date(2026, 9, 25))
    store.record(conn, [_lead("strata", ["breakout"], variants=["strata", "niko strata"])], date(2026, 9, 26))
    row = conn.execute("SELECT first_seen, last_seen, signals, named, variants FROM leads").fetchone()
    assert row[:2] == ("2026-09-25", "2026-09-26") and json.loads(row[2]) == ["breakout", "x"]
    assert row[3] == 0.9 and json.loads(row[4]) == ["strata", "niko strata"]


def test_store_due_verdict_and_summary(tmp_path):
    conn = store.connect(tmp_path)
    store.record(conn, [_lead("strata", ["x", "breakout"]), _lead("flop", ["sitemap"])], date(2026, 9, 25))
    store.record(conn, [_lead("young", ["hf"])], date(2026, 10, 8))
    today = date(2026, 10, 10)
    assert [d[0] for d in store.due(conn, today, 14, 5)] == ["flop", "strata"]
    store.save_verdict(conn, "strata", "hit", 2, {}, today)
    store.save_verdict(conn, "flop", "miss", None, {}, today)
    assert store.due(conn, today, 14, 5) == []
    summ = store.summary(conn, today)
    assert summ["pending"] == 1 and summ["by_signal"]["x"] == {"checked": 1, "hit": 1, "median_lead": 2}
    assert summ["by_signal"]["sitemap"] == {"checked": 1, "hit": 0, "median_lead": None}


def test_store_recent_sources_are_only_x_and_hf(tmp_path):
    conn = store.connect(tmp_path)
    store.record(conn, [_lead("qwen3.9", ["x"]), _lead("strata", ["breakout"])], date(2026, 10, 10))
    assert [r["term"] for r in store.recent_sources(conn, date(2026, 10, 10))] == ["qwen3.9"]


# --- ⑤ review: did it rise, judged against its own baseline ----------------------------------

SEEN = date(2026, 9, 25)


def _series(values_by_day):
    start = date(2026, 9, 19)
    return [(start + timedelta(days=i), v) for i, v in enumerate(values_by_day)]


def test_review_a_word_with_an_older_meaning_does_not_count_as_rising():
    # 'strata' alone, 2026-09-19..10-02, as DataForSEO returned it: high baseline throughout
    assert not review.rose(_series([40, 67, 70, 67, 76, 80, 68, 62, 70, 100, 89, 98, 36, 33]), SEEN)["rose"]


def test_review_a_new_name_rising_from_zero_counts_and_dates_the_rise():
    out = review.rose(_series([0, 0, 0, 0, 0, 0, 0, 0, 0, 30, 30, 100, 0, 0]), SEEN)
    assert out["rose"] and out["first_rise"] == "2026-09-28"


def test_review_all_zero_is_no_data():
    assert review.rose(_series([0] * 14), SEEN)["no_data"]


def test_review_verdict_takes_the_earliest_variant_that_rose():
    per = {"strata": {"rose": False, "no_data": False},
           "niko1221 strata": {"rose": True, "first_rise": "2026-09-27"},
           "niko strata": {"rose": True, "first_rise": "2026-09-30"}}
    assert review.verdict(per, SEEN) == ("hit", 2)


def test_review_verdict_miss_and_no_data():
    assert review.verdict({"a": {"rose": False, "no_data": False}}, SEEN) == ("miss", None)
    assert review.verdict({"a": {"rose": False, "no_data": True}}, SEEN) == ("no_data", None)






# --- the two commands ---------------------------------------------------------------------------

def _write_config(tmp_path, **more):
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"x_accounts": {"labs": ["a"]}, **more}))
    return path


def test_sources_run_writes_no_report_when_nothing_is_new(tmp_path, monkeypatch):
    monkeypatch.setattr(cli.sources, "x_new", lambda cfg, state: {"posts": 400, "terms": []})
    monkeypatch.setattr(cli.sources, "hf_new", lambda cfg, state: {"models": 100, "terms": []})
    out = tmp_path / "sources.md"
    assert cli.main(["sources", "--config", str(_write_config(tmp_path)), "--state", str(tmp_path / "s"), "--out", str(out)]) == 0
    assert not out.exists()


def test_sources_run_reports_and_keeps_a_new_name(tmp_path, monkeypatch):
    _no_judge(monkeypatch)
    monkeypatch.setattr(cli.sources, "x_new", lambda cfg, state: {"posts": 400, "cost_micros": 72000, "terms": [
        {"term": "qwen3.9", "recent": 2, "note": "X: 2 accounts, 900 likes", "examples": ["@Alibaba_Qwen: Qwen3.9 is out"],
         "posts": [{"account": "Alibaba_Qwen", "text": "Qwen3.9 is out", "url": "https://x.com/p/1", "likes": 900}]}]})
    monkeypatch.setattr(cli.sources, "hf_new", lambda cfg, state: {"models": 100, "terms": []})
    out, state = tmp_path / "sources.md", tmp_path / "s"
    cli.main(["sources", "--config", str(_write_config(tmp_path)), "--state", str(state), "--out", str(out)])
    text = out.read_text()
    assert "**qwen3.9**" in text and "$0.072" in text and "[post](https://x.com/p/1)" in text
    assert store.connect(state).execute("SELECT term FROM leads").fetchall() == [("qwen3.9",)]


def test_daily_run_writes_a_report_when_every_signal_is_down(tmp_path, monkeypatch):
    def down(*a, **k):
        raise OSError("network is down")
    for mod, fn in ((cli.sources, "x_new"), (cli.sources, "hf_new"), (cli.breakout, "breakouts"), (cli.sitemaps, "diff")):
        monkeypatch.setattr(mod, fn, down)
    out = tmp_path / "report.md"
    assert cli.main(["daily", "--config", str(_write_config(tmp_path)), "--state", str(tmp_path / "s"), "--out", str(out),
                     "--skip", "newsites,review"]) == 0
    text = out.read_text()
    assert text.count("network is down") >= 4 and out.with_suffix(".json").exists()


def test_daily_report_in_chinese(tmp_path, monkeypatch):
    _no_judge(monkeypatch)
    monkeypatch.setattr(cli.breakout, "breakouts", lambda cfg, state: {"searched": 120, "first_run": False, "terms": [], "repos": [
        {"repo": "Niko1221/Strata", "stars": 7734, "velocity": 859, "delta": 600, "age_days": 9.0, "description": "Qwen3.8", "new": True}]})
    out = tmp_path / "report.md"
    cli.main(["daily", "--config", str(_write_config(tmp_path, lang="zh")), "--state", str(tmp_path / "s"), "--out", str(out),
              "--skip", "x,hf,sitemaps,newsites,review"])
    text = out.read_text()
    assert "## 新词雷达" in text and "🆕 [Niko1221/Strata]" in text and "| +600 |" in text


# --- topics and mail -----------------------------------------------------------------------------

def test_topic_section_lists_matching_leads_repos_and_queries(cfg):
    cfg["topics"] = {"AI video": r"\bvideo"}
    lead = [{"term": "reelmimic", "examples": ["edenfunf/reelmimic: Show it a video you love"], "signals": ["② breakout"]},
            {"term": "strata", "examples": ["Niko1221/Strata: Qwen3.8 on a gaming PC"], "signals": ["② breakout"]}]
    blocks = {"breakout": {"repos": [{"repo": "a/motion-video-kit", "description": "video kit", "stars": 900, "velocity": 300},
                                     {"repo": "Niko1221/Strata", "description": "Qwen3.8", "stars": 7700, "velocity": 870}]}}
    found = {"queries": [{"query": "claude video skills", "impressions": 6, "page": "/best/x/"},
                         {"query": "best ai scraping tools", "impressions": 3, "page": "/best/web-scraping/"}]}
    text = "\n".join(report.topic_lines(cfg, lead, blocks, found))
    assert "reelmimic" in text and "motion-video-kit" in text and "claude video skills" in text
    assert "strata" not in text.lower() and "scraping" not in text


def test_every_text_has_both_languages():
    assert all(len(pair) == 2 and all(pair) for pair in i18n.T.values())


def test_mail_html_renders_tables():
    out = notify.html("| a | b |\n|---|---|\n| 1 | 2 |")
    assert "<table style=" in out and ">1</td>" in out
    # Gmail drops a <style> block outside <head>: every tag carries its own style.
    assert "<style>" not in out and '<td style="border:1px solid' in out


def test_mail_without_recipient_is_skipped(monkeypatch):
    monkeypatch.delenv("RADAR_EMAIL_TO", raising=False)
    assert notify.send("s", "x").startswith("mail skipped")


def test_mail_recipients_tolerate_how_people_type_them():
    assert notify.recipients('"a@b.com"') == ["a@b.com"]
    assert notify.recipients("a@b.com，c@d.cn") == ["a@b.com", "c@d.cn"]
    assert notify.recipients("<a@b.com>; c@d.cn\n") == ["a@b.com", "c@d.cn"]


# --- the generic terms behind the names ---------------------------------------------------------

from radar import usecases  # noqa: E402

TODAY = date(2026, 10, 12)


def _daily(values, end=TODAY - timedelta(days=1)):
    """A daily series ending yesterday, as Trends returns it; its last two days are unsettled."""
    start = end - timedelta(days=len(values) - 1)
    return [(start + timedelta(days=i), v) for i, v in enumerate(values)]


def test_usecases_reads_new_names_first_then_those_due_again():
    seen = {"old": {"first": "2026-10-08", "runs": 1}, "done": {"first": "2026-10-01", "runs": 2},
            "fresh": {"first": "2026-10-11", "runs": 1}}
    assert usecases.due(["a", "old", "b"], seen, TODAY, 5, 3) == ["a", "b", "old"]
    assert usecases.due(["a", "b", "c"], seen, TODAY, 2, 3) == ["a", "b"]


def test_usecases_parse_drops_the_name_itself_sentences_and_repeats():
    answer = json.dumps({"use_cases": [
        {"phrase": "AI Image Detector", "evidence": "check if any image is AI generated", "brand": False},
        {"phrase": "synthid detector online", "evidence": "x", "brand": True},
        {"phrase": "detector", "evidence": "x"},
        {"phrase": "check whether a picture was made by a model today", "evidence": "x"},
        {"phrase": "ai image detector", "evidence": "again"},
        {"phrase": "photoshop alternative", "evidence": "free photoshop clone", "brand": True}]})
    out = usecases.parse("Sure:\n" + answer, "synthid detector")
    assert [(u["phrase"], u["brand"]) for u in out] == [("ai image detector", False), ("photoshop alternative", True)]
    assert usecases.parse("no json here", "x") == []


def test_trends_last_two_days_are_not_a_rise():
    # "ai image detector" on 2026-10-12: flat for 88 days, then 0 and 100 on the two unsettled days
    s = usecases.shape(_daily([5] * 88 + [0, 100]), TODAY)
    assert s["now"] == "flat" and not s["rose"]


def test_a_single_day_spike_on_a_small_term_is_not_a_rise():
    s = usecases.shape(_daily([0] * 80 + [100] + [0] * 9), TODAY)   # "debloat macos"
    assert not s["rose"]


def test_a_real_rise_is_dated_and_still_rising():
    # "synthid detector": nothing, then 9, 53, 40 on the last settled days, then the unsettled two
    s = usecases.shape(_daily([1] * 85 + [9, 53, 40, 59, 100]), TODAY)
    assert s["rose"] and s["new"] and s["now"] == "rising" and s["started"] == "2026-10-07" and s["peak_day"] == "2026-10-08"


def test_a_rise_that_came_back_down_is_fading():
    s = usecases.shape(_daily([1] * 70 + [60, 100, 70, 30, 10] + [5] * 15), TODAY)
    assert s["rose"] and s["now"] == "fading"


def test_searches_a_day_follow_the_terms_own_volume_along_its_curve():
    # 30,000 searches in August at level 10; level 20 in the last settled week: twice as many a day
    points = _daily([10] * 81 + [20] * 9)
    assert usecases.own_per_day(points, 30000, "2026-08", TODAY) == 2000
    assert usecases.own_per_day(points, 0, "2026-08", TODAY) is None


def test_the_benchmark_is_sized_from_terms_whose_volume_is_known():
    def row(volume, term_level, bench_level):
        return {"volume": volume, "month": "2026-08", "_pair": [(d, term_level, bench_level) for d, _ in _daily([0] * 90)]}
    # a term with 3,000 searches a day at a third of the benchmark's level puts the benchmark at 9,000
    assert usecases.bench_daily([row(90000, 10, 30), row(900, 1, 50)]) == 9000   # the 900/month term is too small to count
    assert usecases.bench_daily([row(900, 1, 50)]) is None


def test_decisions():
    up, flat, down = {"now": "rising"}, {"now": "flat"}, {"now": "fading"}
    assert usecases.decide(up, 5000, 300) == "prioritize" and usecases.decide(up, 40, 300) == "watch"
    assert usecases.decide(flat, 5000, 300) == "watch" and usecases.decide(flat, 40, 300) == "skip"
    assert usecases.decide(down, 5000, 300) == "skip" and usecases.decide({"no_data": True}, None, 300) == "watch"


def _fake_usecase_network(monkeypatch):
    rise = _daily([1] * 85 + [9, 53, 40, 59, 100])
    flat = _daily([20] * 90)
    monkeypatch.setattr(usecases, "evidence", lambda names, repos, today, texts=None: (
        {n: {"posts": [{"text": "check if any image is AI generated", "likes": 900, "by": "a", "url": ""}], "readme": "", "repo": ""} for n in names}, 3000))
    monkeypatch.setattr(usecases, "propose", lambda model, name, ev: [
        {"phrase": "ai image detector", "evidence": "check if any image is AI generated", "brand": False},
        {"phrase": "photoshop alternative", "evidence": "free clone", "brand": True},
        {"phrase": "nobody searches this", "evidence": "x", "brand": False}])
    monkeypatch.setattr(usecases, "screen", lambda model, name, ev, cands: cands)
    monkeypatch.setattr(usecases, "volumes", lambda phrases, loc: (
        {"ai image detector": {"volume": 60000, "month": "2026-08"}, "photoshop alternative": {"volume": 6000, "month": "2026-08"},
         "nobody searches this": {"volume": 0, "month": ""}}, 90000))
    monkeypatch.setattr(usecases, "fetch_series", lambda keywords, bench, today: (
        {k: {"solo": rise if k == "synthid detector" else flat,
             "pair": [(d, v, 40) for d, v in (rise if k == "synthid detector" else flat)]} for k in keywords}, 7200))


def _fake_optional_readings(monkeypatch):
    """The related queries and the one-country series are readings of their own; here they bring nothing."""
    monkeypatch.setattr(usecases.google, "related_queries", lambda terms, geo: ({}, 0, []))
    monkeypatch.setattr(usecases, "country_series", lambda rows, c: ({}, 0, []))


def test_usecases_run_builds_one_table_and_reads_a_name_twice(cfg, tmp_path, monkeypatch):
    _fake_usecase_network(monkeypatch)
    uc = usecases.run(cfg, tmp_path, ["synthid detector"], {}, TODAY)
    rows = {r["phrase"]: r for r in uc["rows"]}
    assert uc["proposed"] == 3 and uc["relevant"] == 3 and uc["kept"] == 2 and "nobody searches this" not in rows
    assert rows["synthid detector"]["kind"] == "name" and rows["synthid detector"]["decision"] == "prioritize"
    assert rows["ai image detector"]["per_day"] == 2000 and rows["ai image detector"]["decision"] == "watch"
    assert rows["photoshop alternative"]["brand"] and rows["photoshop alternative"]["decision"] == "skip"
    assert uc["bench_daily"] == 2200 and uc["cost_micros"] == 100200   # the median of 4,000 and 400
    assert usecases.run(cfg, tmp_path, ["synthid detector"], {}, TODAY + timedelta(days=1))["names"] == []
    assert usecases.run(cfg, tmp_path, ["synthid detector"], {}, TODAY + timedelta(days=3))["names"] == ["synthid detector"]
    assert usecases.run(cfg, tmp_path, ["synthid detector"], {}, TODAY + timedelta(days=9))["names"] == []


def test_keyword_table_marks_brands_and_generic_terms_become_leads(cfg, tmp_path, monkeypatch):
    _fake_usecase_network(monkeypatch)
    uc = usecases.run(cfg, tmp_path, ["synthid detector"], {}, TODAY)
    text = "\n".join(report.keyword_table(cfg, uc))
    assert "| **synthid detector** | name |" in text and "**prioritize**" in text
    assert "| **photoshop alternative** ⚠ | generic | synthid detector |" in text and "someone's brand" in text
    assert "“check if any image is AI generated”" in text
    leads = report.usecase_leads(uc)
    assert [x["term"] for x in leads] == ["ai image detector", "photoshop alternative"] and leads[0]["kinds"] == ["usecase"]






def test_momentum_is_the_last_settled_week_against_the_week_before():
    assert usecases.shape(_daily([10] * 76 + [20] * 7 + [40] * 7 + [0, 100]), TODAY)["momentum"] == 2.0
    assert usecases.shape(_daily([0] * 83 + [40] * 7 + [0, 100]), TODAY)["momentum"] is None   # nothing the week before


# --- the optional second source: what people searched together with a name ----------------------

def test_related_queries_split_into_long_tails_and_candidates():
    related = {"rising": ["ai photo detector", "google synthid detector", "openai", "gpt image 2.5"],
               "top": ["ai detector", "synthid detector", "synthid detector online", "ai photo detector"]}
    tails, cands = usecases.split_related("synthid detector", related)
    assert tails == ["google synthid detector", "synthid detector online"]
    assert [c["phrase"] for c in cands] == ["ai photo detector", "gpt image 2.5", "ai detector"]   # "openai" is one word
    assert cands[0]["source"] == "searched" and "rising" in cands[0]["evidence"]




def test_related_queries_join_the_table_and_other_names_become_leads(cfg, tmp_path, monkeypatch):
    _fake_usecase_network(monkeypatch)
    monkeypatch.setattr(dfs, "keyed", lambda: True)      # a door is open: the related queries are bought
    monkeypatch.setattr(usecases.google, "related_queries", lambda terms, geo: (
        {t: {"rising": ["ai photo detector", "gpt image 2.5", "synthid detector online"], "top": []} for t in terms}, 6000, []))
    monkeypatch.setattr(usecases, "screen", lambda model, name, ev, cands: [
        dict(c, relevance=0.9, is_name=c["phrase"] == "gpt image 2.5") for c in cands])
    monkeypatch.setattr(usecases, "volumes", lambda phrases, loc: (
        {p: {"volume": 9000, "month": "2026-08"} for p in phrases if p != "nobody searches this"}, 90000))
    cfg["usecases"]["country_trends"] = False
    uc = usecases.run(cfg, tmp_path, ["synthid detector"], {}, TODAY)
    rows = {r["phrase"]: r for r in uc["rows"]}
    assert rows["ai photo detector"]["source"] == "searched" and "gpt image 2.5" not in rows
    assert uc["cosearch"] == [{"term": "gpt image 2.5", "with": "synthid detector"}]
    assert uc["tails"] == {"synthid detector": ["synthid detector online"]} and uc["cost_micros"] == 106200
    text = "\n".join(report.keyword_table(cfg, uc))
    assert "| **ai photo detector** 🖼 🔎 |" in text and "gpt image 2.5 (with synthid detector)" in text and "synthid detector online" in text
    assert "incomplete today" not in text
    assert {"term": "gpt image 2.5", "kinds": ["cosearch"]}.items() <= report.usecase_leads(uc)[-1].items()


def test_with_the_related_queries_off_the_table_says_so_and_a_failure_is_reported(cfg, tmp_path, monkeypatch):
    _fake_usecase_network(monkeypatch)
    monkeypatch.setattr(dfs, "keyed", lambda: True)
    cfg["usecases"]["related"] = False
    cfg["usecases"]["country_trends"] = False
    uc = usecases.run(cfg, tmp_path, ["synthid detector"], {}, TODAY)
    assert not uc["related"] and "off in the config" in "\n".join(report.keyword_table(cfg, uc))
    cfg["usecases"]["related"] = True

    def down(terms, geo):
        return {}, 0, [f"{t}: HTTP 502" for t in terms]
    monkeypatch.setattr(usecases.google, "related_queries", down)
    uc = usecases.run(cfg, tmp_path / "b", ["synthid detector"], {}, TODAY)
    assert uc["related_errors"] == ["synthid detector: HTTP 502"] and uc["kept"] == 2
    assert "HTTP 502" in "\n".join(report.keyword_table(cfg, uc))


def test_without_a_key_the_related_queries_are_not_bought(cfg, tmp_path, monkeypatch):
    _fake_usecase_network(monkeypatch)
    asked = []

    def related(terms, geo):
        asked.extend(terms)
        return {t: {"rising": [], "top": []} for t in terms}, 0, []
    monkeypatch.setattr(usecases.google, "related_queries", related)
    monkeypatch.setattr(usecases.google, "daily_series", lambda terms, geo: ({}, 0, []))
    uc = usecases.run(cfg, tmp_path, ["synthid detector"], {}, TODAY)
    assert asked == [] and not uc["related"] and "no key to buy them" in "\n".join(report.keyword_table(cfg, uc))


def test_a_reading_that_cannot_be_bought_is_named_and_the_table_still_comes_out(cfg, tmp_path, monkeypatch):
    _fake_usecase_network(monkeypatch)

    def down(phrases, loc):
        raise dfs.DfsError("Monid: BLOCKED: insufficient_wallet_balance; AIsa: HTTP 402")
    monkeypatch.setattr(usecases, "volumes", down)
    uc = usecases.run(cfg, tmp_path, ["synthid detector"], {}, TODAY)
    assert uc["read_errors"] and "insufficient_wallet_balance" in uc["read_errors"][0]
    assert usecases.degraded(uc) and uc["rows"] and uc["rows"][0]["decision"] == "watch"   # no data: nothing is prioritized
    text = "\n".join(report.keyword_table(cfg, uc))
    assert "Some readings failed" in text and "insufficient_wallet_balance" in text


def test_other_names_leave_out_old_products_siblings_and_spelling_variants():
    # what the first real run kept beside filmcraft and pdfcraft (2026-10-13)
    viewed = [{"phrase": p, "is_name": True, "rising": True, "established": old} for p, old in (
        ("premiere pro", True), ("davinci resolve", True), ("photocraft github", False), ("gpt image 2.5", False),
        ("adobe artcraft", False), ("artcraft adobe", False))]
    assert [x["term"] for x in usecases.other_names("filmcraft", viewed, ["photocraft", "filmcraft"])] == ["gpt image 2.5", "adobe artcraft"]
    tails, cands = usecases.split_related("pdfcraft", {"rising": ["pdf craft", "pdf-craft review", "merge pdf files"], "top": []})
    assert tails == ["pdf craft", "pdf-craft review"] and [c["phrase"] for c in cands] == ["merge pdf files"]


def test_every_name_gets_the_country_series_and_the_cap_is_on_generic_terms(cfg, monkeypatch):
    rows = [{"phrase": f"name{i}", "kind": "name"} for i in range(5)] + \
           [{"phrase": f"g{v}", "kind": "generic", "volume": v} for v in (27100, 22200, 8100, 6600, 900)]
    cfg["usecases"]["country_max"] = 3
    asked = []

    def daily_series(terms, geo):
        asked.extend(terms)
        return {t: [(TODAY, 1)] for t in terms}, 6000 * len(terms), []
    monkeypatch.setattr(usecases.google, "daily_series", daily_series)
    out, cost, errors = usecases.country_series(rows, cfg["usecases"])
    assert sorted(asked) == sorted([f"name{i}" for i in range(5)] + ["g27100", "g22200", "g8100"]) and cost == 48000 and not errors


def test_other_names_must_be_rising_and_not_the_same_family():
    viewed = [{"phrase": p, "is_name": True, "rising": r} for p, r in (
        ("gpt image 2.5", True), ("google nano banana", True), ("nano banana pro", True), ("google gemini", False), ("mistral large 4", True))]
    assert [x["term"] for x in usecases.other_names("nano banana 2.1", viewed)] == ["gpt image 2.5", "mistral large 4"]


# --- one country's settled series for the rows that matter -------------------------------------

def _month(values):
    """A 30-day series ending today, as google.daily_series returns it."""
    return [(TODAY - timedelta(days=len(values) - 1 - i), v) for i, v in enumerate(values)]


def test_an_established_term_80_percent_up_for_five_days_is_a_rise_in_the_country_series():
    # "photoshop alternative" in the US around PhotoCraft: about 50, then 77, 86, 83, 100, 94, and 79 on the partial day
    points = _month([52, 48, 50, 47, 55, 51, 49, 53, 50, 46, 52, 50, 48, 54, 49, 51, 50, 47, 53, 46, 47, 53, 57, 55, 77, 86, 83, 100, 94, 79])
    s = usecases.shape(points, TODAY, usecases.COUNTRY)
    assert s["rose"] and not s["new"] and s["now"] == "rising" and s["started"] == "2026-10-07" and s["momentum"] >= 1.4


def test_a_flat_country_series_stays_flat():
    assert usecases.shape(_month([30, 34, 28, 31] * 7 + [33, 29]), TODAY, usecases.COUNTRY)["now"] == "flat"


def test_names_and_big_generic_terms_take_their_shape_from_the_country(cfg, tmp_path, monkeypatch):
    _fake_usecase_network(monkeypatch)
    monkeypatch.setattr(dfs, "keyed", lambda: True)
    cfg["usecases"]["related"] = False
    up = _month([50] * 24 + [77, 86, 83, 100, 94, 79])
    asked = []

    def daily_series(terms, geo):
        asked.extend(terms)
        return ({t: up for t in terms if t != "synthid detector"}, 6000 * len(terms),
                ["synthid detector: HTTP 502"] if "synthid detector" in terms else [])
    monkeypatch.setattr(usecases.google, "daily_series", daily_series)
    uc = usecases.run(cfg, tmp_path, ["synthid detector"], {}, TODAY)
    rows = {r["phrase"]: r for r in uc["rows"]}
    # The lookups run side by side, so the order they are recorded in is the threads' own.
    assert sorted(asked) == ["ai image detector", "photoshop alternative", "synthid detector"]
    assert rows["ai image detector"]["trend_geo"] and rows["ai image detector"]["now"] == "rising"
    assert rows["ai image detector"]["decision"] == "prioritize"      # flat worldwide, up in the US
    assert "trend_geo" not in rows["synthid detector"] and uc["country_errors"] == ["synthid detector: HTTP 502"]
    text = "\n".join(report.keyword_table(cfg, uc))
    assert "still rising (US)" in text and "worldwide data used instead: synthid detector" in text
    assert "This table is incomplete today" in text


def test_small_generic_terms_are_not_worth_a_country_lookup(cfg, monkeypatch):
    rows = [{"phrase": "a", "kind": "generic", "volume": 300}, {"phrase": "b", "kind": "generic", "volume": 5000}, {"phrase": "n", "kind": "name"}]
    asked = []

    def daily_series(terms, geo):
        asked.extend(terms)
        return {t: [] for t in terms}, 0, []
    monkeypatch.setattr(usecases.google, "daily_series", daily_series)
    usecases.country_series(rows, cfg["usecases"])
    assert asked == ["n", "b"]


def test_started_rising_is_the_run_that_holds_the_peak_not_an_earlier_blip():
    # a one-day blip on 10-01, then the real rise from 10-07
    points = _month([20] * 19 + [45] + [20] * 5 + [70, 100, 68, 51, 42])
    s = usecases.shape(points, TODAY, usecases.COUNTRY)
    assert s["rose"] and s["started"] == "2026-10-08" and s["peak_day"] == "2026-10-09"


def test_only_things_are_read_for_generic_terms_not_words_that_surged():
    today = [{"term": "mcd", "kinds": ["github"], "named": 0.75}, {"term": "kr.co.klisten.api", "kinds": ["sitemap"], "named": 0.78},
             {"term": "photocraft", "kinds": ["breakout"], "named": 0.95}, {"term": "dots", "kinds": ["breakout"], "named": 0.65},
             {"term": "hailuo", "kinds": ["newsite", "sitemap"]}]
    recent = [{"term": "qwen image 2.1", "kinds": ["hf"], "named": 0.96}, {"term": "use rea", "kinds": ["x"], "named": 0.72},
              {"term": "photocraft", "kinds": ["x"], "named": 0.9}]
    assert usecases.names_to_read(today, recent) == ["photocraft", "hailuo", "qwen image 2.1"]




# --- names someone asks about: the `keywords` command and the MCP tool ---------------------------

def test_keywords_on_request_reads_every_name_now_and_leaves_the_record_alone(cfg, tmp_path, monkeypatch):
    _fake_usecase_network(monkeypatch)
    uc, text = cli.keyword_table(cfg, tmp_path, ["SynthID Detector ", "synthid detector", ""], {})
    assert uc["names"] == ["synthid detector"] and "| **ai image detector** 🖼 |" in text
    again, _ = cli.keyword_table(cfg, tmp_path, ["synthid detector"], {})
    assert again["names"] == ["synthid detector"]                 # asked again, read again
    assert not (tmp_path / "usecases.json.gz").exists()           # the daily run's read-twice record is untouched
    many, _ = cli.keyword_table(cfg, tmp_path, [f"name {i}" for i in range(20)], {})
    assert len(many["names"]) == usecases.ASKED_MAX


def test_leads_command_lists_what_was_kept(tmp_path, capsys):
    conn = store.connect(tmp_path)
    store.record(conn, [_lead("strata", ["breakout"], named=0.9)], date.today() - timedelta(days=2))
    store.record(conn, [_lead("old one", ["x"])], date.today() - timedelta(days=30))
    data = cli.kept_leads(tmp_path, 7)
    assert [r["term"] for r in data["leads"]] == ["strata"] and data["hit_rates"]["pending"] == 2
    assert cli.main(["leads", "--state", str(tmp_path)]) == 0
    out = capsys.readouterr().out
    assert "strata  [breakout]" in out and "1 leads in the last 7 days" in out


# --- the MCP server -----------------------------------------------------------------------------

def test_mcp_tools_answer_and_never_print(cfg, tmp_path, monkeypatch, capsys):
    import pytest
    pytest.importorskip("mcp")
    from radar import mcp_server
    (tmp_path / "config.json").write_text(json.dumps({"x_accounts": {"labs": ["a"]}}))
    monkeypatch.setenv("RADAR_CONFIG", str(tmp_path / "config.json"))
    monkeypatch.setenv("RADAR_STATE", str(tmp_path / "state"))
    store.record(store.connect(tmp_path / "state"), [_lead("photocraft", ["breakout"], named=0.95)], date.today())
    assert [r["term"] for r in mcp_server.recent_leads(7)["leads"]] == ["photocraft"]

    _fake_usecase_network(monkeypatch)
    out = mcp_server._keyword_table(["synthid detector"], {})
    assert "| **ai image detector** 🖼 |" in out["table_markdown"] and out["incomplete"] is False and out["cost_usd"] == 0.1

    monkeypatch.setattr(cli.sources, "x_new", lambda cfg, state: {"posts": 400, "cost_micros": 3000, "terms": [
        {"term": "qwen3.9", "recent": 2, "note": "X: 2 accounts", "examples": ["@a: Qwen3.9 is out"]}]})
    monkeypatch.setattr(cli.sources, "hf_new", lambda cfg, state: {"models": 100, "terms": []})
    monkeypatch.setattr(report.judge, "score", lambda rows, model: {"judged": 0})
    ran = mcp_server._run("sources", [])
    assert ran["wrote_report"] and "**qwen3.9**" in ran["report_markdown"]
    assert capsys.readouterr().out == ""        # stdout is the protocol channel


def test_mcp_tools_say_why_they_failed(tmp_path, monkeypatch):
    import pytest
    pytest.importorskip("mcp")
    from radar import aisa, mcp_server
    # mcp 2.x shows the agent only "Error executing tool keyword_table" for any exception but a ToolError
    monkeypatch.setenv("RADAR_STATE", str(tmp_path / "state"))

    def no_key(*args):
        raise aisa.AisaError("no AIsa key: set AISA_API_KEY, or log in with the AIsa CLI (`aisa login`)")
    monkeypatch.setattr(cli, "keyword_table", no_key)
    with pytest.raises(mcp_server.ToolError, match="no AIsa key"):
        mcp_server._keyword_table(["photocraft"], {})

    def read_only(state, days):                 # RADAR_STATE unset, and a client that starts the server in "/"
        raise OSError(30, "Read-only file system", "state")
    monkeypatch.setattr(cli, "kept_leads", read_only)
    with pytest.raises(mcp_server.ToolError, match="Read-only file system.*RADAR_STATE sets the folder"):
        mcp_server.recent_leads(7)


def test_skills_have_a_name_and_a_description():
    import radar
    root = Path(__file__).resolve().parents[1] / "skills"
    for name in ("new-term-radar", "ai-listening", "seo-new-keywords-research"):
        text = (root / name / "SKILL.md").read_text()
        head = re.match(r"---\nname: (\S+)\ndescription: (.+)\n---\n", text)
        assert head and head.group(1) == name and 100 < len(head.group(2)) < 1024
        assert "AISA_API_KEY" in text
        # What buildwithclaude asked of a listed skill (2026-10-23): run the pinned release, not a clone
        # of the default branch; say the cost and wait for a yes before a paid run; treat what the
        # radar reads as data that cannot change the workflow.
        assert f"uvx new-term-radar=={radar.__version__}" in text and "git clone" not in text
        assert "python -m radar" not in text and "a yes" in text and "never to follow" in text
        # And (2026-10-28): nothing goes out on the user's behalf without their yes to that one.
        assert "## Nothing goes out without a yes" in text and "is not that yes" in text
        # Claude Code replaces $0, $1 ... in a skill's text with the user's arguments: "$0.05" came out
        # as "<what the user typed>.05" on the first real run. Amounts are written "0.05 USD".
        assert not re.search(r"\$\d", text)
    assert "wait for their yes to that one" in (root.parent / "radar/mcp_server.py").read_text()


def test_the_aisa_key_comes_from_the_environment_or_the_cli_login(tmp_path, monkeypatch):
    from radar import aisa
    monkeypatch.setattr(aisa.Path, "home", lambda: tmp_path)
    monkeypatch.delenv("AISA_API_KEY", raising=False)
    import pytest
    with pytest.raises(aisa.AisaError, match="aisa login"):
        aisa.key()
    (tmp_path / ".aisa").mkdir()
    (tmp_path / ".aisa" / "key").write_text("from-cli\n")
    assert aisa.key() == "from-cli"
    monkeypatch.setenv("AISA_API_KEY", "from-env")
    assert aisa.key() == "from-env"


def test_every_manifest_carries_the_same_version():
    # A release is one number in five places; the registry rejects a server.json whose
    # version PyPI does not have.
    import radar
    root = Path(__file__).resolve().parents[1]
    server = json.loads((root / "server.json").read_text())
    plugin = json.loads((root / ".claude-plugin" / "plugin.json").read_text())
    market = json.loads((root / ".claude-plugin" / "marketplace.json").read_text())
    copilot = json.loads((root / "plugin.json").read_text())        # GitHub Copilot reads the root manifest
    pyproject = re.search(r'^version = "([^"]+)"', (root / "pyproject.toml").read_text(), re.M).group(1)
    assert {pyproject, server["version"], server["packages"][0]["version"], plugin["version"],
            market["metadata"]["version"], copilot["version"]} == {radar.__version__}
    pinned = f"new-term-radar=={radar.__version__}"                   # the plugin and the skills run this release
    assert pinned in plugin["mcpServers"]["new-term-radar"]["args"]
    assert all(pinned in (root / s / "SKILL.md").read_text() for s in copilot["skills"])
    assert len(server["description"]) <= 100                                  # the registry's limit
    assert f"mcp-name: {server['name']} -->" in (root / "README.md").read_text()   # how the registry checks who owns the PyPI package


def test_without_a_config_file_the_radar_runs_on_the_example_it_ships_with(tmp_path, monkeypatch, capsys):
    # An installed radar (uvx, pip) has no clone to copy config.example.json from.
    import pytest
    root = Path(__file__).resolve().parents[1]
    assert (root / "radar" / "config.example.json").read_text() == (root / "config.example.json").read_text()
    monkeypatch.chdir(tmp_path)
    cfg = config.load()
    assert cfg["x_accounts"]["labs"] and cfg["_dir"].resolve() == tmp_path.resolve()
    assert "example list" in capsys.readouterr().err
    with pytest.raises(SystemExit, match="not found"):
        config.load(tmp_path / "mine.json")                          # a path you name must exist
    (tmp_path / "config.json").write_text(json.dumps({"x_accounts": {"labs": ["a"]}}))
    assert config.load()["x_accounts"]["labs"] == ["a"]


def test_the_mcp_command_says_what_is_missing(monkeypatch, capsys):
    import builtins
    real = builtins.__import__

    def without_mcp(name, *args, **kwargs):
        if name == "mcp" or name.startswith("mcp."):
            raise ImportError(name)
        return real(name, *args, **kwargs)

    import sys
    import radar
    monkeypatch.delitem(sys.modules, "radar.mcp_server", raising=False)   # an earlier test may have loaded it;
    monkeypatch.delattr(radar, "mcp_server", raising=False)               # the package keeps its own reference
    monkeypatch.setattr(builtins, "__import__", without_mcp)
    assert cli.main(["mcp"]) == 1
    assert "new-term-radar[mcp]" in capsys.readouterr().err


# --- page one: can a new site get in? ------------------------------------------------------------

from radar import competition  # noqa: E402


def _serp(results, ads=False, count=19500):
    """A search's first page, the way DataForSEO answers it: the items, and Google's own count."""
    items = [{"type": "ai_overview"}] + [{"type": "organic", "domain": d, "url": u, "title": t, "rank_group": i + 1}
                                         for i, (d, u, t) in enumerate(results)]
    return {"items": items + ([{"type": "paid", "domain": "paid.example"}] if ads else []), "se_results_count": count}


def test_page_one_verdicts():
    assert competition.verdict(3, None) == "open" and competition.verdict(0, 15) == "open"
    assert competition.verdict(0, 55) == "crowded" and competition.verdict(None, None) == "crowded"
    assert competition.verdict(1, 30) == "mixed" and competition.verdict(None, 30) == "mixed"
    assert competition.page_kind("youtube.com", "https://youtube.com/watch?v=1", "x") == "video"
    assert competition.page_kind("headshotpro.com", "https://headshotpro.com/", "Free AI Headshot Generator") == "tool"
    assert competition.page_kind("headshots.com", "https://headshots.com/best", "16 Best AI Headshot Generators") == "list"


def test_page_one_sites_are_dated_by_registration_and_compared_across_readings(tmp_path, monkeypatch):
    from radar import page_age
    born = {"tool-a.example": "2026-08-01", "old.example": "2015-03-03", "example.co.uk": "2026-07-01"}
    monkeypatch.setattr(page_age, "ask", lambda d: (born[d], "ok") if d in born else ("", "none"))
    first = {"results": [{"domain": "tool-a.example", "url": "https://tool-a.example/", "position": 2},
                         {"domain": "old.example", "url": "https://old.example/x", "position": 1},
                         {"domain": "docs.example.co.uk", "url": "https://docs.example.co.uk/a", "position": 5},
                         {"domain": "nodate.example", "url": "https://nodate.example/", "position": 9}]}
    rows = [{"phrase": "new term", "decision": "prioritize"}, {"phrase": "unread", "decision": "watch"}]
    page_age.record(rows, {"new term": first}, tmp_path, date(2026, 10, 20))
    got = rows[0]["page_age"]
    assert (got["dated"], got["young"], got["newest"]) == (3, 2, "2026-08-01") and "page_age" not in rows[1]
    assert [s["domain"] for s in got["young_sites"]] == ["tool-a.example", "docs.example.co.uk"]
    assert got["entered"] == {"since": "", "urls": []} and rows[0]["decision"] == "prioritize"   # recorded, not used
    second = {"results": first["results"][:2] + [{"domain": "fresh.example", "url": "https://fresh.example/", "position": 4}]}
    page_age.record(rows, {"new term": second}, tmp_path, date(2026, 10, 23))
    assert rows[0]["page_age"]["entered"] == {"since": "2026-10-20", "urls": ["https://fresh.example/"]}
    late = rows[:1]
    page_age.record(late, {"new term": second}, tmp_path, date(2026, 11, 20))              # over a week after: not compared
    assert late[0]["page_age"]["entered"] == {"since": "", "urls": []}


def test_rdap_is_asked_once_more_and_a_registry_without_it_says_none(monkeypatch):
    from radar import page_age
    import io
    calls, answers = [], []

    class Reply(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    def urlopen(req, timeout):
        calls.append(req.full_url)
        got = answers.pop(0)
        if isinstance(got, int):
            raise page_age.urllib.error.HTTPError(req.full_url, got, "no", {}, None)
        return Reply(json.dumps(got).encode())
    monkeypatch.setattr(page_age.urllib.request, "urlopen", urlopen)
    monkeypatch.setattr(page_age, "RDAP_GAP", 0)
    monkeypatch.setattr(page_age, "RETRY_PAUSE", 0)
    answers[:] = [429, {"events": [{"eventAction": "registration", "eventDate": "2026-08-03T00:00:00Z"}]}]
    assert REAL_ASK("codonic.example") == ("2026-08-03", "ok") and len(calls) == 2
    answers[:] = [404]
    assert REAL_ASK("tool.example") == ("", "none")
    answers[:] = [503, 503]
    assert REAL_ASK("busy.example") == ("", "failed")





# --- ⑥ revenue: products people pay for ----------------------------------------------------------

from radar import revenue  # noqa: E402


def _product(slug, name, usd, growth=None, website="https://x.example", stealth=False, about="Turns photos into headshots."):
    return {"slug": slug, "name": name, "website": website, "description": about, "category": "ai",
            "revenue": {"last30Days": usd * 100}, "growth30d": growth, "stealthMode": stealth}


def test_revenue_reports_growth_then_a_product_that_left_zero(cfg, tmp_path, monkeypatch):
    monkeypatch.setenv("TRUSTMRR_API_KEY", "k")
    monkeypatch.setattr(revenue.time, "sleep", lambda s: None)
    pages = {"growth-desc": [_product("headshot", "HeadshotKit", 4200, 4500.0, "https://headshotkit.ai"),
                             _product("anon", "Anonymous startup", 9000, 2.0, website="", stealth=True),
                             _product("gpt", "Talent Tech Global Limited", 840, 1.6, "https://gptproto.com")],
             "listed-desc": [_product("baby", "AI Baby Generator", 0, None, "https://aibabygenerator.ai")]}
    monkeypatch.setattr(revenue, "fetch", lambda sort, page, c: pages[sort] if page == 1 else [])
    first = revenue.products(cfg, tmp_path)
    assert first["first_run"] and [r["term"] for r in first["products"]] == ["headshotkit", "gptproto"]
    assert first["products"][0]["growth_text"] == "up from near $0" and first["products"][1]["growth_text"] == "+160%"
    assert first["terms"][0]["about"] == ("https://headshotkit.ai", "Turns photos into headshots.")
    pages["growth-desc"] = []
    pages["listed-desc"] = [_product("baby", "AI Baby Generator", 900, None, "https://aibabygenerator.ai")]
    later = revenue.products(cfg, tmp_path)
    assert [r["term"] for r in later["products"]] == ["ai baby generator"] and later["products"][0]["from_zero"]
    assert later["terms"][0]["note"] == "TrustMRR: from $0 to $900 in 30 days"
    text = "\n".join(report._revenue_lines(later, cfg))
    assert "**$0 last time, earning now**" in text and "forbid republishing" in text


def test_revenue_without_a_key_says_it_is_off(cfg, tmp_path, monkeypatch):
    monkeypatch.delenv("TRUSTMRR_API_KEY", raising=False)
    rv = revenue.products(cfg, tmp_path)
    assert rv == {"skipped": True, "no_key": True}
    assert "No TRUSTMRR_API_KEY" in "\n".join(report._revenue_lines(rv, cfg))



# --- what a lead makes, and one-click chances ----------------------------------------------------

from radar import opportunity  # noqa: E402


def _posts(*texts_likes):
    return [{"text": x, "likes": n} for x, n in texts_likes]


def test_format_reads_the_name_first_then_what_most_posts_say():
    assert opportunity.fmt({"term": "veo 3.1", "posts": []}) == "video"
    assert opportunity.fmt({"term": "elevenlabs v4", "posts": _posts(
        ("give your characters a voice", 1500), ("tts that whispers", 500), ("audio and sound effects", 400),
        ("made a video with it", 300))}) == "voice"                           # voice 3, video 1
    assert opportunity.fmt({"term": "claude opus 5.5", "posts": _posts(
        ("it made an animation", 100000), ("great at code", 6000), ("writes well", 6000), ("fast", 3000),
        ("cheap", 3000), ("smart", 1000), ("solid", 900), ("ok", 800))}) == "text"   # one post in eight is not a format
    assert opportunity.fmt({"term": "headshotkit", "about": ("https://headshotkit.ai", "AI headshots from selfies")}) == "image"


def test_one_click_needs_a_tutorial_people_shared():
    assert opportunity.one_click(_posts(("here's how to build this with Opus + Blender + Unreal", 1699))) == "tutorial"
    assert opportunity.one_click(_posts(("here's how I did it", 120))) is None              # nobody shared it
    assert opportunity.one_click(_posts(("8 denoising steps, open weights", 5301))) is None   # an announcement
    assert opportunity.one_click(_posts(("everyone is making these figurines", 4200))) == "trend"


def test_leads_carry_format_marks_and_the_legend(cfg, monkeypatch):
    monkeypatch.setattr(report.judge, "score", lambda rows, model: {"judged": len(rows)})
    blocks = {"x": {"terms": [{"term": "clipforge", "recent": 2, "note": "X", "examples": ["@a: clipforge animates any photo"],
                               "posts": [{"text": "here's how: step 1 upload, step 2 prompt", "likes": 2400}]}]}}
    lead, _ = report.leads(cfg, blocks)
    assert lead[0]["format"] == "video" and lead[0]["one_click"] == "tutorial"
    assert report._marks(lead[0]) == " 🎬 ⚡"



# --- ⑦ asks, pains and tutorial comments ---------------------------------------------------------

from radar import asks, pains  # noqa: E402


def _reddit_post(pid, title, score=12, sub="VideoEditors"):
    return {"id": pid, "title": title, "text": "", "score": score, "comments": 3, "subreddit": sub,
            "url": f"https://www.reddit.com/r/{sub}/{pid}", "created": 0}




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
    monkeypatch.setattr(pains.llm, "chat", lambda model, system, user, max_tokens=2500: json.dumps([
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
    monkeypatch.setattr(pains.llm, "chat", lambda model, system, user, max_tokens=2500: json.dumps(
        [{"gap": "Too many manual steps", "kind": "wants a tool", "items": [0], "phrase": "one click blender export"}]))
    out = pains.tutorial_asks(cfg, [{"term": "opus blender game", "one_click": "tutorial"}, {"term": "plain", "one_click": None}])
    assert [x["term"] for x in out["leads"]] == ["opus blender game"] and out["cost_micros"] == 2000 + 1000
    text = "\n".join(report._tutorial_lines(out))
    assert "Too many manual steps (wants a tool, 1) → one click blender export" in text


# --- ⑧ sellers: sites that just put up a payment link ---------------------------------------------

from radar import sellers  # noqa: E402


def _backlinks(rows):
    def where(d):          # a forum thread is not the forum selling anything
        return f"https://{d}/t/freelancer-invoice-inbox/185482" if d.startswith("community.") else f"https://{d}/pricing"
    return {"b": {"tasks": [{"result": [{"items": [{"domain_from": d, "url_to": u, "url_from": where(d),
                                                     "first_seen": "2026-10-14 03:00:00 +00:00", "domain_from_rank": 10}
                                                    for d, u in rows]}]}]}}


def _sites(names):
    return {"d": {"tasks": [{"result": [{"items": [{"domain": d, "first_seen": "2026-10-13 21:00:00 +00:00"} for d in names]}]}]}}




def test_a_seller_is_new_only_when_the_site_first_linked_lately():
    sites = {"demotools.dev": "2026-10-13", "pestora.framer.website": "2026-10-12"}
    assert sellers.first_day("try.demotools.dev", sites) == "2026-10-13"           # named by its parent domain
    assert sellers.first_day("www.pestora.framer.website", sites) == "2026-10-12"
    assert sellers.first_day("other.framer.website", sites) == ""                  # a sibling on the same host is another site
    assert sellers.first_day("permit-records-demo.com", sites) == ""                    # a new page of an old seller


def test_a_seller_link_sits_on_the_sites_own_page():
    assert sellers.own_page("https://vouch-demo.com/") and sellers.own_page("https://write-demo.com/pricing")
    assert not sellers.own_page("https://community.n8n.io/t/freelancer-invoice/185482")
    assert not sellers.own_page("https://www.framer.com/marketplace/templates/aurius/")
    assert not sellers.own_page("https://flathub.org/apps/uk.co.weather")


def test_seller_kinds_fall_back_to_keyword_rules():
    rows = [{"host": "captions-demo.app", "title": "NotchLive — Live Captions for Mac", "description": ""},
            {"host": "themes-demo.com", "title": "Astro Themes & Tailwind CSS Templates", "description": ""},
            {"host": "news-demo.com", "title": "Murcia Today News", "description": ""}]
    got, error = sellers.kinds("gpt-4.1-mini", rows)          # the call is blocked in tests: rules take over
    assert got == {"captions-demo.app": "ai", "themes-demo.com": "template", "news-demo.com": "donation"} and error


# --- history: every seller back to 2019 ------------------------------------------------------------

from radar import seller_history  # noqa: E402


def _history_page(items, token):
    return {"h": {"tasks": [{"status_code": 20000, "result": [{"items": items, "search_after_token": token}]}]}}










def test_builder_defaults_are_not_the_sellers_words(tmp_path):
    notion = ("Notion", "A collaborative AI workspace, built on your company context. Build and orchestrate agents")
    assert sellers.own_words(*notion) == ("", "")
    assert sellers.own_words("TalentDemo - Recruitment Tracker", "Made with Framer") == ("TalentDemo - Recruitment Tracker", "")
    assert sellers.own_words("", "Discover Switchy, the easiest retargeting url shortner.") == ("", "")
    assert sellers.own_words("Hōm", "Home") == ("Hōm", "") and sellers.own_words("Hōm", "Homeowner intelligence")[1]
    assert sellers.own_words("NotchLive — Home of live captions", "Captions for your Mac") == ("NotchLive — Home of live captions",
                                                                                              "Captions for your Mac")
    db = seller_history.connect(tmp_path)
    db.executemany("INSERT INTO sites (platform, host, first_seen, page, link, rank, pays, title, description, read_on, kind, kind_by) "
                   "VALUES ('gumroad', ?, '2026-01-02', '', '', 0, 1, ?, ?, '2026-10-14', 'ai', 'jev')",
                   [("shop.notion.site", *notion), ("talentdemo.com", "TalentDemo - Recruitment Tracker", "Made with Framer")])
    assert seller_history.clean_defaults(db) == 2 and seller_history.clean_defaults(db) == 0
    got = {h: (t, d, k) for h, t, d, k in db.execute("SELECT host, title, description, kind FROM sites")}
    assert got == {"shop.notion.site": ("", "", None), "talentdemo.com": ("TalentDemo - Recruitment Tracker", "", "ai")}



def test_forums_are_not_sellers(tmp_path):
    assert sellers.shared("community.obsidian.md") and sellers.shared("forum.cursor.com") and not sellers.shared("help.captions-demo.app")
    db = seller_history.connect(tmp_path)
    db.executemany("INSERT INTO sites (platform, host, first_seen, page, link, rank, pays) VALUES ('gumroad', ?, '2026-10-12', '', '', 0, 1)",
                   [("community.shopify.com",), ("captions-demo.app",)])
    assert seller_history.drop_shared(db) == 1
    assert dict(db.execute("SELECT host, pays FROM sites")) == {"community.shopify.com": 0, "captions-demo.app": 1}

def test_page_words_reads_a_gzipped_head_cut_short(monkeypatch):
    import gzip
    import random
    head = "<html><head><title>NotchLive — Live Captions</title><meta name='description' content='Captions for your Mac'>".encode()
    body = gzip.compress(head + random.Random(1).randbytes(50_000))      # noise: the stream is long and cut after 400 bytes

    class Response:
        headers = {"Content-Encoding": "gzip"}

        def read(self, n):
            return body[:n]

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False
    monkeypatch.setattr(sellers.urllib.request, "urlopen", lambda req, timeout: Response())
    assert sellers.page_words("captions-demo.app", 400) == ("NotchLive — Live Captions", "Captions for your Mac")




# --- 0.5 local: ⑨ feeds, ⑧ builders, ⑩ money, side evidence ------------------------------------

from radar import feeds, money, sidelights  # noqa: E402

PH = ('<feed><entry><id>1</id><link rel="alternate" href="https://www.producthunt.com/products/dashrendr"/>'
      '<title>Dashrendr</title><content type="html">&lt;p&gt;Dashboards from a sentence&lt;/p&gt;</content></entry></feed>')


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
        {"data": {"trending_search_words": [
            {"trendingSearchWord": "AI yearbook filter"}, {"trendingSearchWord": "Mayor Arrested"}]}}, 1000))
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


def test_money_finds_the_owners_other_sites_and_new_advertisers(cfg, tmp_path, monkeypatch):
    cfg["money"]["ad_terms"] = ["ai headshot"]
    monkeypatch.setattr(money.monid, "key", lambda: "k")

    def run(provider, endpoint, body=None, query=None):      # the Ads Transparency Center's answer
        names = ["tooldirdemo.ai", "coloringdemo.ai", "lyricsdemo.io"]
        return [{"items": [{"type": "ads_domain", "domain": d} for d in names]}], 3000
    monkeypatch.setattr(money.monid, "run", run)
    monkeypatch.setattr(money.adsense, "ids", lambda d: {"pub-42"} if d.endswith("demo.ai") or d.endswith("demo.io") else set())
    ads = iter([["headshotpro.com"], ["headshotpro.com", "aragon.ai"]])

    def serp(keyword, location_code=2840, language="en", device="desktop", depth=10):
        return {"items": [{"type": "paid", "domain": n} for n in next(ads)], "results_count": 100}, 2000
    monkeypatch.setattr(money.google, "serp", serp)
    first = money.money(cfg, tmp_path, ["tooldirdemo.ai", "photoroom.com"])
    assert first["portfolios"] == [{"site": "tooldirdemo.ai", "pub": "pub-42", "sisters": ["coloringdemo.ai", "lyricsdemo.io"]}]
    assert first["advertisers"] == [] and {x["term"] for x in first["terms"]} == {"coloringdemo", "lyricsdemo"}   # first look: no "new" ads
    again = money.money(cfg, tmp_path, ["tooldirdemo.ai"])
    assert again["portfolios"] == [] and again["advertisers"] == [{"term": "ai headshot", "advertisers": ["aragon.ai"]}]
    text = "\n".join(report._money_lines(first))
    assert "tooldirdemo.ai (AdSense pub-42): coloringdemo.ai, lyricsdemo.io" in text




def test_seller_traffic_details_come_from_the_visits_answer():
    info = sellers.traffic_info({"monthlyVisits": 924,
                                 "trafficSources": {"direct": 0.36, "searchOrganic": 0.32, "searchPaid": 0.01, "genAi": 0.02}})
    assert info == {"visits": 924, "top_source": "direct", "ai_share": 0.02}


def test_side_evidence_reads_certificates_wikipedia_and_google(monkeypatch):
    answers = {
        "crt.sh": [{"common_name": "nanobanana-pro-demo.app", "not_before": "2026-10-12T00:00:00"},
                   {"common_name": "old-nanobanana-demo.com", "not_before": "2026-09-01T00:00:00"}],
        "opensearch": ["nano banana", ["Nano Banana"]],
        "pageviews": {"items": [{"views": v} for v in [10] * 7 + [40] * 7]},
        "trends": ')]}\',\n{"default": {"topics": [{"title": "Nano Banana", "type": "AI model"}]}}',
    }

    def get(url):
        key = next(k for k in ("crt.sh", "opensearch", "pageviews", "trends") if k in url)
        value = answers[key]
        return value.encode() if isinstance(value, str) else json.dumps(value).encode()
    monkeypatch.setattr(sidelights, "_get", get)
    got = sidelights.evidence({"names": 5, "whois_names": 0, "whois_days": 30}, ["nano banana"], today=date(2026, 10, 14))
    row = got["rows"][0]
    assert row["sites"] == (1, ["nanobanana-pro-demo.app"]) and row["wiki"] == ("Nano Banana", 280, 70) and row["entity"] == {"type": "AI model"}
    assert "| nano banana | 1 (nanobanana-pro-demo.app) | 280 / 70 | AI model |" in "\n".join(report._side_lines(got))
    assert sidelights.new_sites("jev", date(2026, 10, 14)) == (0, [])                 # too short to search for


def test_tails_size_the_searches_around_paying_products(monkeypatch):
    monkeypatch.setattr(sidelights.usecases, "volumes", lambda phrases, loc: ({"photoroom alternative": {"volume": 1900},
                                                                                "photoroom free": {"volume": 30}}, 90000))
    got = sidelights.tails({"products": 8, "min_volume": 50, "location_code": 2840}, ["photoroom"])
    assert got["rows"] == [{"phrase": "photoroom alternative", "volume": 1900}] and got["cost_micros"] == 90000




def test_a_name_that_belongs_to_something_else_is_taken(monkeypatch):
    raw = ')]}\',\n' + json.dumps({"default": {"topics": [{"title": "Gemini 4", "type": "Space flight"},
                                                          {"title": "Gemini - 4", "type": "Auto repair shop in Sofia, Bulgaria"}]}})
    monkeypatch.setattr(sidelights, "_get", lambda url: raw.encode())
    assert sidelights.entity("gemini 4") == {"taken": "Space flight"}
    topic = ')]}\',\n' + json.dumps({"default": {"topics": [{"title": "Claude Code", "type": "Topic"}]}})
    monkeypatch.setattr(sidelights, "_get", lambda url: topic.encode())
    assert sidelights.entity("claude code") == {"type": "Topic"}                     # known, just not typed yet
    row = {"rows": [{"name": "gemini 4", "entity": {"taken": "Space flight"}, "discord": 0}]}
    assert "name taken: Google knows only a Space flight" in "\n".join(report._side_lines(row))


def test_recent_reads_iso_dates():
    now = datetime.now(timezone.utc)
    assert feeds.recent((now - timedelta(hours=30)).isoformat()) and not feeds.recent((now - timedelta(days=3)).isoformat())
    assert feeds.recent(now.strftime("%Y-%m-%dT%H:%M:%S.000Z")) and not feeds.recent("") and not feeds.recent("soon")


def test_dodo_and_waffo_links_take_money():
    dodo, waffo = sellers.PLATFORMS["dodo"][1], sellers.PLATFORMS["waffo"][1]
    assert dodo("checkout.dodopayments.com", "/buy/pdt_0NcEz5RotWnutLQWHmr") and not dodo("checkout.dodopayments.com", "/")
    assert waffo("checkout.waffo.ai", "/acme/pro-plan") and not waffo("checkout.waffo.ai", "/")




def test_rising_words_must_share_the_seed(cfg, tmp_path, monkeypatch):
    cfg["feeds"]["enabled"], cfg["feeds"]["weekly"], cfg["feeds"]["seeds"] = ["rising"], [], ["ai generator"]
    monkeypatch.setattr(feeds.google, "related_queries", lambda terms, geo="US": (
        {t: {"rising": ["hotel lobby ai generator", "best pizza near me", "ai rap video generator", "how to bake a cake"],
             "top": []} for t in terms}, 6000 * len(terms), []))
    got = feeds.feeds(cfg, tmp_path, today=date(2026, 10, 15))
    assert [x["term"] for x in got["terms"]] == ["hotel lobby ai generator", "ai rap video generator"]


def test_asks_drop_old_posts_and_name_their_source(cfg, monkeypatch):
    now = datetime.now(timezone.utc).timestamp()
    old = {"id": "k7nsx7", "title": "Is there a website that compares prices", "text": "", "subreddit": "NoStupidQuestions",
           "score": 16656, "created": now - 5 * 365 * 86400}
    new = dict(old, id="n1", title="Is there an AI tool that turns slides into notes", created=now - 86400)
    monkeypatch.setattr(asks.social, "reddit", lambda q, **k: ([old, new], 1880))
    c = dict(cfg["asks"], patterns=["x"], extra_sources=[])
    posts, _, _ = asks.collect(c)
    assert [p["id"] for p in posts] == ["n1"]                    # Reddit's "week" let a 2020 post through
    assert [asks.where(s) for s in ("PhD", "x", "freelancer", "softwarerecs")] == ["r/PhD", "X", "Freelancer", "Stack Exchange · softwarerecs"]
    assert asks.x_time("Fri Jul 17 19:56:23 +0000 2026") > 0 and asks.x_time("") == 0.0




def test_the_status_says_which_signals_were_slow():
    b = {"x": {"skipped": True}, "hf": {"skipped": True}, "breakout": {"skipped": True}, "sitemap": {},
         "feed": {"seconds": 1860, "new": {}, "errors": []}, "seller": {"skipped": True}}
    lines = report._status_lines(b, {"usecases": {"seconds": 45}, "sidelights": {"seconds": 900}})
    assert "- Time: 46 min in all; a minute or more: feed 31 min、sidelights 15 min" in lines


def test_money_asks_about_page_one_tools_and_whois_follows_searches():
    uc = {"rows": [{"phrase": "wordcraft", "kind": "name", "per_day": 10782, "decision": "prioritize",
                    "competition": {"tools": [{"domain": "wordcraftdemo.it", "dr": 1.5}, {"domain": "onlyoffice.com", "dr": 78}]}},
                   {"phrase": "digup", "kind": "name", "per_day": 86, "decision": "watch",
                    "competition": {"tools": [{"domain": "digupdemo.co.il", "dr": 0}, {"domain": "onlyoffice.com", "dr": 78}]}},
                   {"phrase": "old thing", "kind": "generic", "per_day": 5, "decision": "skip",
                    "competition": {"tools": [{"domain": "skip.me", "dr": 50}]}}]}
    assert cli.page_one_tools(uc, max_dr=100) == ["onlyoffice.com", "wordcraftdemo.it", "digupdemo.co.il"]   # strongest first, no skipped rows
    assert cli.page_one_tools(uc) == ["wordcraftdemo.it", "digupdemo.co.il"]          # a rating above 60 is a big site, not a portfolio
    assert cli.by_searches(["digup", "qwen21 nsfw pose skill", "wordcraft"], uc) == ["wordcraft", "digup", "qwen21 nsfw pose skill"]




def test_an_adsense_id_on_dozens_of_sites_is_a_network():
    assert money.is_network([f"site{i}.com" for i in range(31)], {"max_sisters": 30})
    assert not money.is_network(["video-demo.online", "photo-demo.online", "logo-demo.io"], {"max_sisters": 30})


def test_the_stores_own_search_names_the_extensions(monkeypatch):
    page = ('<a href="./detail/ai-headshot-generator/abcdefghijklmnopabcdefghijklmnop">one</a>'
            '<a href="./detail/ai-headshots/bcdefghijklmnopabcdefghijklmnopa">two</a>'
            '<a href="./detail/ai-headshots/bcdefghijklmnopabcdefghijklmnopa">the same one</a>')
    monkeypatch.setattr(cws, "_get", lambda url: page)
    assert cws.search("ai headshot") == ["abcdefghijklmnopabcdefghijklmnop", "bcdefghijklmnopabcdefghijklmnopa"]


def test_the_discord_servers_are_counted_in_the_boards_own_answer():
    body = ('<a href="/server/1490104640056987771">one</a><a href="/en/server/1490104640056987771">one again</a>'
            '<a href="/server/1490104640056987772">two</a><a href="/servers/tag/ai">a list</a>')
    assert sidelights.count_servers(body) == 2


def test_the_ad_transparency_center_is_asked_for_the_name_and_the_domain(monkeypatch):
    assert domains.name_of("vogue.com") == "vogue" and domains.name_of("www.vogue.com") == "vogue"
    assert domains.name_of("ai-tools.co.uk") == "ai-tools" and domains.name_of("theverge.com") == "theverge"
    assert domains.name_of("vogue.com.tw") == "vogue" and domains.name_of("blog.shopify.com") == "shopify"

    asked = []

    def run(provider, endpoint, body=None, query=None):
        asked.append(body["keyword"])
        return [{"items": [{"type": "ads_domain", "domain": d} for d in
                            (["vogue.ph", "vogue.it"] if body["keyword"] == "vogue" else ["vogue.com.tw"])]}], 2400
    monkeypatch.setattr(money.monid, "run", run)
    monkeypatch.setattr(money.adsense, "ids", lambda d: {"pub-7"} if d.startswith("vogue") else set())
    pub, others, pool, cost = money.sisters("vogue.com")
    assert sorted(asked) == ["vogue", "vogue.com"]           # the name brings the family, the domain the near-misses
    assert pub == "pub-7" and others == ["vogue.ph", "vogue.it", "vogue.com.tw"]
    assert pool == 3 and cost == 4800                        # the site itself is not in the pool


def test_the_publisher_id_with_the_most_sites_wins(monkeypatch):
    monkeypatch.setattr(adsense, "ids", lambda d: {"pub-a"} if d.startswith("vogue.ph") else
                        ({"pub-b"} if d.startswith("vogue") else set()))
    assert adsense.shared("vogue.com", ["vogue.it", "vogue.de", "vogue.ph"]) == ("pub-b", ["vogue.it", "vogue.de"])


# --- 0.6: word age, markets, page-one shape ------------------------------------------------------

from radar import markets, serpshape, wordage  # noqa: E402


def _weeks(values, end=TODAY):
    """A weekly Trends result ending with the week still running (its period ends today), as DataForSEO answers it."""
    starts = [end - timedelta(days=7 * (len(values) - 1 - i)) for i in range(len(values))]
    data = [{"date_from": s.isoformat(), "date_to": min(s + timedelta(days=6), end).isoformat(), "values": [v]} for s, v in zip(starts, values)]
    return {"items": [{"type": "google_trends_graph", "data": data}]}




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




def test_best_market_divides_by_difficulty_only_when_every_market_has_one():
    by = {"US": {"volume": 5000, "kd": 50}, "DE": {"volume": 900, "kd": 5}, "JP": {"volume": 0, "kd": None}}
    assert markets.best_market(by) == "DE"                          # 180 a point against 100
    by["FR"] = {"volume": 300, "kd": None}
    assert markets.best_market(by) == "US"                          # a difficulty is missing: the most searches
    assert markets.best_market({"US": {"volume": 0, "kd": 10}}) is None
    assert markets.best_market({"US": {"volume": 400, "kd": 0}, "DE": {"volume": 300, "kd": 2}}) == "US"




def test_homepages_inner_pages_and_platforms_from_urls():
    for url in ("https://pdfdemo.app", "https://pdfdemo.app/", "https://www.x.com/index.html", "https://x.com/en/", "https://x.com/zh-cn",
                "https://x.com/?ref=a"):
        assert serpshape.is_homepage(url), url
    for url in ("https://canva.com/ai", "https://x.com/blog/post", "https://github.com/a/b", "https://x.com/en/tools/pdf"):
        assert not serpshape.is_homepage(url), url
    plats = config.DEFAULTS["serp_shape"]["platforms"]
    assert serpshape.on_platform("en.wikipedia.org", plats) and serpshape.on_platform("someone.github.io", plats)
    assert serpshape.on_platform("github.com", plats) and not serpshape.on_platform("github.io", plats)
    assert not serpshape.on_platform("notgithub.com", plats) and not serpshape.on_platform("google.com", plats)


def _results(*urls_titles):
    return [{"url": u, "title": t, "domain": serpshape.domain_of(u)} for u, t in urls_titles]


def test_serp_shape_verdicts():
    plats = config.DEFAULTS["serp_shape"]["platforms"]
    inner = _results(*[(f"https://site{i}.com/blog/post-{i}", f"Post {i}") for i in range(7)],
                     ("https://tool1.com/", "Tool One"), ("https://tool2.com/", "Tool Two"), ("https://reddit.com/r/x/1", "thread"))
    s = serpshape.shape(inner, "pdf editor", plats)
    assert s["verdict"] == "beatable" and (s["homepages"], s["inner"], s["platforms"]) == (2, 8, 1)
    assert s["top"] == [{"url": "https://site0.com/blog/post-0", "title": "Post 0"}, {"url": "https://site1.com/blog/post-1", "title": "Post 1"}]
    # one homepage built on the exact term: contested, however many inner pages there are
    s = serpshape.shape(inner[:9] + _results(("https://pdfeditor.example/", "Free online")), "pdf editor", plats)
    assert s["exact_home"] and s["verdict"] == "contested"
    s = serpshape.shape(_results(("https://best.com/", "PDF Editor: edit PDFs free")) + inner[:9], "pdf editor", plats)
    assert s["exact_home"] and s["exact_titles"] == 1 and s["verdict"] == "contested"
    held = _results(*[(f"https://github.com/a/r{i}", "repo") for i in range(3)], ("https://youtube.com/watch?v=1", "v"),
                    ("https://me.github.io/pdf", "p"), *[(f"https://s{i}.com/", "home") for i in range(5)])
    assert serpshape.shape(held, "pdf editor", plats)["verdict"] == "platform-held"
    homes = _results(*[(f"https://s{i}.com/", "home") for i in range(6)]) + inner[:4]
    assert serpshape.shape(homes, "pdf editor", plats)["verdict"] == "contested"
    assert serpshape.shape([], "pdf editor", plats)["verdict"] is None
    assert not serpshape.has_phrase("PDF Editors compared", "pdf editor")




def test_keyword_table_shows_age_markets_and_shape_in_both_languages(cfg, tmp_path, monkeypatch):
    for block in ("word_age", "markets", "serp_shape"):
        cfg[block]["enabled"] = True
    cfg["markets"].update(codes=["US", "DE"], difficulty=False, top_region=False)
    _fake_usecase_network(monkeypatch)
    _fake_optional_readings(monkeypatch)
    page = _serp([("synthid-detector.example", "https://synthid-detector.example/", "Free AI image checker"),
                  ("medium.com", "https://medium.com/@a/synthid", "I tried it"), ("x.com", "https://x.com/blog/a", "News")])

    def result(endpoint, body):
        if endpoint == dfs.TRENDS:                      # the word's age: a term that started in August
            return _weeks([0] * 250 + [5] * 7), 1200
        return page, 2000                              # the first page

    def rows(endpoint, body):
        return [{"keyword": k, "search_volume": 1500} for k in body["keywords"]], 90000
    monkeypatch.setattr(dfs, "result", result)
    monkeypatch.setattr(dfs, "rows", rows)
    uc = usecases.run(cfg, tmp_path, ["synthid detector"], {}, TODAY)
    rows = {r["phrase"]: r for r in uc["rows"]}
    name = rows["synthid detector"]
    assert name["decision"] == "prioritize" and name["is_new"] and name["serp"]["verdict"] == "contested"
    assert rows["ai image detector"]["markets"]["by"]["US"]["volume"] == 60000 and "serp" not in rows["ai image detector"]
    assert "photoshop alternative" in rows and "age" not in rows["photoshop alternative"]      # skip rows are not read
    assert uc["extra_errors"] == [] and uc["cost_micros"] == 100200 + 2 * 1200 + 2 * 90000 + 2000
    text = "\n".join(report.keyword_table(cfg, uc))
    week = (TODAY - timedelta(days=42)).isoformat()
    assert f"| {week} 🆕 | **best US** · US 1.5k · DE 1.5k |" in text
    assert "contested · 1 home / 2 inner · 1 platform · exact-match homepage | **prioritize** |" in text
    assert "#1 [Free AI image checker](https://synthid-detector.example/)" in text and "First searched = the first week" in text
    assert text.splitlines()[text.splitlines().index(next(x for x in text.splitlines() if x.startswith("| Keyword")))].count("|") == 17
    i18n.set_lang("zh")
    zh = "\n".join(report.keyword_table(cfg, uc))
    assert "| 首次有搜索 | 市场(月搜/KD) | 首页竞争 | 首页结构 | 决策 |" in zh and "有争夺 · 1 首页 / 2 内页 · 1 平台" in zh
    assert f"{week} 🆕 | **最佳 US**" in zh and "首次有搜索 = 近 5 年里" in zh


# ---- 0.7: the twelve months, your own rank, page-one features, the .com, AI answers ----
from radar import ai_answers, curve, dotcom, yours  # noqa: E402


def _months(values, last=(2026, 8)):
    """[[YYYY-MM, searches], ...] oldest first, ending at `last`."""
    y, m = last
    out = []
    for v in reversed(values):
        out.append([f"{y}-{m:02d}", v])
        y, m = (y, m - 1) if m > 1 else (y - 1, 12)
    return out[::-1]


def test_twelve_months_read_as_a_word():
    new = curve.shape(_months([0, 0, 0, 0, 10, 30, 60, 90, 140, 200, 260, 320]))
    assert new["label"] == "rising" and new["since"] == "2026-03" and new["first"] == "2025-09" and new["last"] == "2026-08"
    # new within the year and already past its top: "best claude skills" as read on 2026-10-20
    best = curve.shape(_months([140, 170, 320, 880, 1300, 3600, 3600, 3600, 2900, 2900, 2400, 1900], last=(2026, 9)))
    assert best["since"] == "2026-01" and best["label"] == "falling" and best["c6"] == -47
    assert report._year(best) == "▁▁▂▃▄███▇▇▆▅ new since 2026-01 · falling, -47% in 6 mo"
    launch = curve.shape(_months([33100, 22200, 27100, 60500, 60500, 90500, 60500, 49500, 33100, 27100, 22200, 18100], last=(2026, 9)))
    assert launch["label"] == "spiked" and launch["peak"] == "2026-03" and launch["from_peak"] == -80 and "since" not in launch
    spike = curve.shape(_months([100, 100, 110, 100, 1000, 600, 300, 200, 150, 120, 110, 100]))
    assert spike["label"] == "spiked" and spike["peak"] == "2026-01" and spike["from_peak"] == -90
    rise = curve.shape(_months([100] * 6 + [120, 140, 160, 180, 200, 220]))
    assert rise["label"] == "rising" and rise["c6"] == 120 and rise["c3"] == 38
    assert curve.shape(_months([500] * 6 + [400, 350, 300, 250, 200, 150]))["label"] == "falling"
    assert curve.shape(_months([300] * 12))["label"] == "steady"
    assert curve.shape(_months([10, 20, 30, 20, 10, 20, 30, 40, 30, 20, 10, 20]))["label"] == "thin"   # Google's rounding, not a trend
    assert curve.shape(_months([5, 6, 7, 8, 9, 10])) is None
    assert curve.spark([0, 50, 100]) == "▁▅█" and curve.spark([0, 0]) == ""
    # a peak repeated in the newest month is not a spike that passed
    assert curve.shape(_months([100, 100, 100, 100, 900, 100, 100, 100, 100, 100, 100, 900]))["label"] == "rising"
    assert report._year(rise).endswith("rising, +120% in 6 mo") and report._year(None) == "—"
    i18n.set_lang("zh")
    assert report._year(spike).endswith("2026-01 冲高,现在 -90%")




def test_your_domains_come_from_the_config_or_the_search_console_property(cfg):
    assert yours.site_domain("sc-domain:Example.com") == "example.com" and yours.site_domain("https://www.example.com/blog") == "example.com"
    assert yours.own_domains(cfg) == []
    cfg["gsc"] = {"site": "sc-domain:mysite.com"}
    assert yours.own_domains(cfg) == ["mysite.com"]
    cfg["yours"]["domains"] = ["https://www.other.io/"]
    assert yours.own_domains(cfg) == ["other.io"]
    assert yours.is_mine("blog.other.io", ["other.io"]) and not yours.is_mine("notother.io", ["other.io"])


def test_your_rank_turns_the_decision_into_an_action(cfg, monkeypatch):
    cfg["gsc"] = {"site": "sc-domain:mysite.com"}
    asked = []

    def positions(c, phrases, today, days):
        asked.append((phrases, days))
        return {"ai image detector": {"position": 14.2, "impressions": 120, "clicks": 3, "page": "https://mysite.com/detector"}}
    monkeypatch.setattr(yours.gsc, "positions", positions)
    page = lambda *hosts: {"results": [{"domain": h, "url": f"https://{h}/x", "position": i + 1} for i, h in enumerate(hosts)]}  # noqa: E731
    on_top = {"phrase": "synthid detector", "competition": page("a.com", "blog.mysite.com")}
    lower = {"phrase": "AI image detector"}                                      # not page-one checked: Search Console alone
    absent = {"phrase": "photoshop alternative", "competition": page("a.com", "b.com")}
    assert yours.assess([on_top, lower, absent], cfg, TODAY) == (0, [])
    assert asked == [(["synthid detector", "AI image detector", "photoshop alternative"], 28)]
    assert on_top["yours"] == {"serp": {"position": 2, "url": "https://blog.mysite.com/x"}, "console": None, "action": "update"}
    assert lower["yours"]["action"] == "improve" and absent["yours"]["action"] == "new"
    assert yours.action(None, {"position": 89.0, "impressions": 1}) == "new"                  # far down: not the page to build on
    assert yours.action(None, {"position": 6.0, "impressions": 0}) == "update"
    assert report._yours(on_top["yours"]) == " · you #2 → update yours"
    assert report._yours(lower["yours"]) == " · you ~14.2 (GSC) → improve yours" and report._yours(absent["yours"]) == " → new page"
    # Search Console failing: the page-one rows still read, the unread row gets nothing
    monkeypatch.setattr(yours.gsc, "positions", lambda *a: (_ for _ in ()).throw(RuntimeError("no Search Console token")))
    rows = [{"phrase": "synthid detector", "competition": page("a.com")}, {"phrase": "unread"}]
    assert yours.assess(rows, cfg, TODAY) == (0, ["Search Console positions: no Search Console token"])
    assert rows[0]["yours"]["action"] == "new" and "yours" not in rows[1]
    i18n.set_lang("zh")
    assert report._yours(lower["yours"]) == " · 你约第 14.2(GSC) → 改进现有页"


def test_search_console_positions_ask_once_for_the_exact_queries(cfg, monkeypatch):
    cfg["gsc"] = {"site": "sc-domain:mysite.com"}
    seen = {}

    def query(access, site, start, end, dimensions, fresh, filters=None):
        seen.update(site=site, days=(end - start).days + 1, dimensions=dimensions, filters=filters)
        return [{"query": "c++ tool", "page": "https://mysite.com/a", "impressions": 5, "clicks": 0, "position": 30.0},
                {"query": "c++ tool", "page": "https://mysite.com/b", "impressions": 40, "clicks": 2, "position": 8.5}]
    monkeypatch.setattr(gsc, "_token", lambda c: "token")
    monkeypatch.setattr(gsc, "_query", query)
    got = gsc.positions(cfg, ["C++ tool", "ai image detector"], TODAY, 28)
    assert got == {"c++ tool": {"position": 8.5, "page": "https://mysite.com/b", "impressions": 45, "clicks": 2}}
    assert seen["filters"] == [{"dimension": "query", "operator": "includingRegex", "expression": r"^(c\+\+ tool|ai image detector)$"}]
    assert seen["days"] == 28 and seen["dimensions"] == ["query", "page"] and seen["site"] == "sc-domain:mysite.com"




def test_dot_com_asks_the_registry(cfg, monkeypatch):
    assert dotcom.names("synthid detector") == ["synthiddetector.com", "synthid-detector.com"]
    assert dotcom.names("PhotoCraft") == ["photocraft.com"] and dotcom.names("café ai") == []
    answers = {"synthiddetector.com": (404, {}), "photocraft.com": (500, {}),
               "synthid-detector.com": (200, {"events": [{"eventAction": "expiration", "eventDate": "2027-09-30T00:00:00Z"},
                                                         {"eventAction": "registration", "eventDate": "2026-09-30T12:00:00Z"}]})}
    monkeypatch.setattr(dotcom, "_get", lambda url: answers[url.rsplit("/", 1)[1]])
    cfg["dot_com"].update(enabled=True, max_rows=2)
    rows = [{"phrase": "ai image detector", "kind": "generic"}, {"phrase": "synthid detector", "kind": "name"},
            {"phrase": "PhotoCraft", "kind": "name"}]
    cost, errors = dotcom.assess(rows, cfg)
    assert cost == 0 and errors == ["photocraft.com (.com): RDAP answered 500"] and "dot_com" not in rows[0]   # names first, two rows
    assert rows[1]["dot_com"] == [{"domain": "synthiddetector.com", "taken": False},
                                  {"domain": "synthid-detector.com", "taken": True, "registered": "2026-09-30"}]
    text = "\n".join(report._more_lines(cfg, {"rows": rows}))
    assert "- **synthid detector**: **synthiddetector.com not registered** · synthid-detector.com taken (2026-09-30)" in text
    assert "sell them as premium" in text
    cfg["dot_com"]["enabled"] = False
    assert dotcom.assess(rows, cfg) == (0, [])


CHATGPT = {"items": [{"sections": [
    {"text": "Sure:\n\n1. **ChatGPT** : the assistant itself.\n2. **Quizlet** : flashcards.\n",
     "items": [{"domain": "www.quizlet.com"}, {"domain": "app.mysite.com"}, {"domain": None}]}]}],
           "search_results": [{"domain": None}]}
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
    asked = {}

    def result(endpoint, body):
        asked[endpoint] = body
        return (CHATGPT if endpoint == dfs.CHATGPT else AI_MODE), 4000
    monkeypatch.setattr(dfs, "result", result)
    cost, errors = ai_answers.assess(rows, cfg)
    assert cost == 8000 and errors == ["unknown engine: bing"] and "ai" not in rows[1]
    assert set(asked) == set(ai_answers.PATHS.values())
    assert asked[dfs.CHATGPT] == {"model_name": "gpt-4o-mini", "user_prompt": "What is the best tool to convert slides to notes?",
                                  "max_output_tokens": 300, "web_search": True}
    assert asked[dfs.AI_MODE] == {"keyword": "What is the best tool to convert slides to notes?", "location_code": 2840,
                                  "language_code": "en", "device": "desktop"}
    ai = rows[0]["ai"]
    assert ai["by"]["chatgpt"] == {"names": ["ChatGPT", "Quizlet"], "sites": ["quizlet.com", "app.mysite.com"]} and ai["yours"]
    assert ai["by"]["google"] == {"names": ["NoteGPT AI PPT Summarizer", "Knowt", "SlidesAI"], "sites": ["notegpt.io", "knowt.com"]}
    assert report._ai_cell(ai) == "ChatGPT, Quizlet, NoteGPT AI PPT Summarizer +2 ✅" and report._ai_cell(None) == "—"
    assert report._ai_cell({"by": {"chatgpt": {"names": [], "sites": []}}}) == "no tool named"
    lines = "\n".join(report._ai_lines(rows, cfg))
    assert "- **convert slides to notes**: ChatGPT names ChatGPT, Quizlet; cites quizlet.com, app.mysite.com" in lines
    assert "Google AI Mode names NoteGPT AI PPT Summarizer, Knowt, SlidesAI; cites notegpt.io, knowt.com · ✅ cites your site" in lines


def test_the_chatgpt_answer_is_read_however_it_arrives():
    # the answer that comes through AIsa's passthrough lists the brands and the sources
    old = {"brand_entities": [{"title": "AI assistant", "markdown": "ChatGPT"}, {"title": "Quizlet", "markdown": "**Quizlet**"}],
           "sources": [{"domain": "www.quizlet.com"}, {"domain": None}]}
    assert ai_answers.read_chatgpt(old) == {"names": ["ChatGPT", "Quizlet"], "sites": ["quizlet.com"]}
    # the one that comes through Monid writes its answer in sections and cites its sources
    new = {"items": [{"sections": [{"text": "1. **ChatGPT** : the assistant.\n2. **Quizlet** : cards.\n",
                                    "items": [{"domain": "www.quizlet.com"}]}]}]}
    assert ai_answers.read_chatgpt(new) == {"names": ["ChatGPT", "Quizlet"], "sites": ["quizlet.com"]}


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


def test_keyword_table_shows_the_year_your_rank_threads_and_the_dot_com(cfg, tmp_path, monkeypatch):
    cfg["competition"]["max_rows"] = 8
    cfg["dot_com"]["enabled"] = True
    cfg["yours"]["domains"] = ["mysite.com"]
    _fake_usecase_network(monkeypatch)
    _fake_optional_readings(monkeypatch)
    monkeypatch.setattr(usecases, "volumes", lambda phrases, loc: (
        {"ai image detector": {"volume": 60000, "month": "2026-08", "months": _months([100] * 6 + [120, 140, 160, 180, 200, 220])},
         "photoshop alternative": {"volume": 6000, "month": "2026-08", "months": _months([300] * 12)}}, 90000))
    pages = {"synthid detector": [("mysite.com", "https://mysite.com/synthid", "SynthID detector"), ("reddit.com", "https://reddit.com/r/a/1", "Is SynthID real?")],
             "ai image detector": [("big.com", "https://big.com/ai", "AI image detector")]}

    def result(endpoint, body):
        keyword = body.get("keyword") or ""
        if keyword.startswith("intitle:"):        # how many pages already carry the term in the title
            return {"items": [], "se_results_count": 19500}, 2000
        return _serp(pages[keyword]), 2000

    def rows(endpoint, body):
        return [{"keyword": k, "keyword_properties": {"keyword_difficulty": 25}} for k in body["keywords"]], 12000
    monkeypatch.setattr(dfs, "result", result)
    monkeypatch.setattr(dfs, "rows", rows)
    monkeypatch.setattr(dotcom, "_get", lambda url: (404, {}))
    uc = usecases.run(cfg, tmp_path, ["synthid detector"], {}, TODAY)
    rows = {r["phrase"]: r for r in uc["rows"]}
    assert rows["synthid detector"]["yours"]["action"] == "update" and rows["ai image detector"]["yours"]["action"] == "new"
    assert "yours" not in rows["photoshop alternative"] and rows["photoshop alternative"]["year"]["label"] == "steady"   # skip rows: the free curve only
    assert [d["domain"] for d in rows["synthid detector"]["dot_com"]] == ["synthiddetector.com", "synthid-detector.com"]
    assert "dot_com" not in rows["ai image detector"] and uc["extra_errors"] == []
    text = "\n".join(report.keyword_table(cfg, uc))
    assert "| ▄▄▄▄▄▄▅▅▆▇▇█ rising, +120% in 6 mo |" in text and "| ████████████ steady |" in text   # on the term's own scale, from zero
    assert "| **prioritize** · you #1 → update yours |" in text and "| watch → new page |" in text
    assert "- **synthid detector**: #1 on page one: https://mysite.com/synthid" in text and "Your sites: mysite.com." in text
    assert "[Is SynthID real?](https://reddit.com/r/a/1)" in text and "**synthiddetector.com not registered**" in text
    assert "12 months = Google Ads monthly searches" in text
    i18n.set_lang("zh")
    zh = "\n".join(report.keyword_table(cfg, uc))
    assert "| 现在 | 近 12 个月 | 首次有搜索 |" in zh and "| **优先** · 你在第 1 → 更新现有页 |" in zh and "| 观察 → 新建页面 |" in zh
    assert "上升,6 个月 +120%" in zh and "首页上的论坛帖" in zh and "**synthiddetector.com 未注册**" in zh
