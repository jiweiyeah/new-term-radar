"""The tests of the sites modules (new sites, sellers, the seller history, the side evidence),
moved out of tests/test_radar.py when their readings went from AIsa to Monid.

The paid calls are stubbed at the gateway: `dfs.rows` and `dfs.result` for DataForSEO's
answers, `monid.chat` for the model's.
"""
import json
from datetime import date
from pathlib import Path

from radar import dfs, llm, monid, new_sites, report, seller_history, sellers, sidelights, site_words

FIXTURE = Path(__file__).parent / "fixtures/whois_new_ai_sites.json"   # a real answer, 2026-10-06


# --- ③ new sites (whois overview, weekly) -----------------------------------------------

def test_new_site_name_terms():
    assert new_sites.name_terms("ai-hailuo.com") == ["hailuo"]
    assert new_sites.name_terms("livephoto.video") == ["livephoto"]
    assert new_sites.name_terms("ai-video-tool.com") == []   # only stop words left


def test_new_sites_drop_domains_ranking_for_a_handful_of_terms(cfg, tmp_path, monkeypatch):
    sent = []
    cfg["new_sites"]["words"] = 0                              # the words each site ranks for: their own test below

    def rows(endpoint, body):
        sent.append((endpoint, body))
        return json.loads(FIXTURE.read_text()), 121200
    monkeypatch.setattr(dfs, "rows", rows)
    out = new_sites.new_sites(cfg, tmp_path, date(2026, 10, 6))
    domains = [r["domain"] for r in out["sites"]]
    assert "ai-hailuo.com" in domains and "neonwin386.ai" not in domains and "oyo88.ai" not in domains
    assert all(r["new"] for r in out["sites"]) and "hailuo" in [t["term"] for t in out["terms"]]
    assert sent[0][0] == dfs.WHOIS and out["cost_micros"] == 121200 * len(cfg["new_sites"]["queries"])
    again = new_sites.new_sites(cfg, tmp_path, date(2026, 10, 13))
    assert not any(r["new"] for r in again["sites"]) and again["terms"] == []


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


def test_a_new_site_comes_with_the_first_words_it_ranks_for(cfg, tmp_path, monkeypatch):
    asked = []

    def rows(endpoint, body):
        asked.append((endpoint, body.get("target")))
        return json.loads(FIXTURE.read_text()), 121200

    def result(endpoint, body):
        asked.append((endpoint, body.get("target")))
        if len(asked) == 2:                                    # the first site asked ranks; the second does not yet
            return {"total_count": 7, "items": [ranked("typing test 15 seconds", 1),
                                                 ranked("股价走势", 6, loc=2158, vol=1000, kd=None, new=False)]}, 12240
        return {"total_count": None, "items": None}, 12000
    monkeypatch.setattr(dfs, "rows", rows)
    monkeypatch.setattr(dfs, "result", result)
    cfg["new_sites"]["words"] = 2
    out = new_sites.new_sites(cfg, tmp_path, date(2026, 10, 6))
    worded = [r for r in out["sites"] if "words" in r]
    assert len(worded) == 2 and [p for p, d in asked].count(site_words.PATH) == 2
    hailuo = next(r for r in out["sites"] if r["domain"] == asked[1][1])
    assert hailuo["top"] == 7 and hailuo["words"][0] == {"word": "typing test 15 seconds", "position": 1, "volume": 30, "kd": 0,
                                                         "country": "US", "url": "/typing-test-15-seconds",
                                                         "since": "2026-08-26..2026-10-06"}
    assert hailuo["words"][1]["country"] == "TW" and hailuo["words"][1]["since"] == ""
    assert out["cost_micros"] == 121200 * len(cfg["new_sites"]["queries"]) + 12240 + 12000
    lines = "\n".join(report._site_word_lines(worded))
    assert f"- {asked[1][1]}: ⏱ typing test 15 seconds #1 US, 30/mo, KD 0; 股价走势 #6 TW, 1000/mo, KD —" in lines
    assert "(of 7 in the top 20)" in lines and "none in the top 20 yet" in lines
    again = new_sites.new_sites(cfg, tmp_path, date(2026, 10, 13))       # seen before: no words asked again
    assert not any("words" in r for r in again["sites"])


def test_a_failed_words_call_is_named_and_the_sites_still_come(cfg, tmp_path, monkeypatch):
    def rows(endpoint, body):
        return json.loads(FIXTURE.read_text()), 121200

    def result(endpoint, body):
        raise dfs.DfsError("Internal Server Error.")
    monkeypatch.setattr(dfs, "rows", rows)
    monkeypatch.setattr(dfs, "result", result)
    cfg["new_sites"]["words"] = 1
    out = new_sites.new_sites(cfg, tmp_path, date(2026, 10, 6))
    assert out["sites"] and len(out["errors"]) == 1 and out["errors"][0].endswith("words: Internal Server Error.")


# --- ⑧ sellers: sites that just put up a payment link ---------------------------------

def _backlinks(rows):
    def where(d):          # a forum thread is not the forum selling anything
        return f"https://{d}/t/freelancer-invoice-inbox/185482" if d.startswith("community.") else f"https://{d}/pricing"
    return [{"domain_from": d, "url_to": u, "url_from": where(d),
             "first_seen": "2026-10-14 03:00:00 +00:00", "domain_from_rank": 10} for d, u in rows]


def _sites(names):
    return [{"domain": d, "first_seen": "2026-10-13 21:00:00 +00:00"} for d in names]


def test_sellers_keep_payment_links_and_leave_out_the_rest(cfg, tmp_path, monkeypatch):
    cfg["sellers"]["platforms"] = ["stripe", "lemonsqueezy", "gumroad", "creem"]
    answers = {
        "buy.stripe.com": [("pestora.framer.website", "https://buy.stripe.com/aFa28rbpG9"), ("github.com", "https://buy.stripe.com/x"),
                           ("permit-records-demo.com", "https://buy.stripe.com/old"), ("try.demotools.dev", "https://buy.stripe.com/t")],
        "lemonsqueezy.com": [("meeting-demo.app", "https://merit.lemonsqueezy.com/checkout/buy/c0b375"), ("seo.shop", "https://lemonsqueezy.com/"),
                             ("x.app", "https://www.lemonsqueezy.com/checkout")],
        "gumroad.com": [("janemaker.com", "https://janemaker.gumroad.com/l/toxins-in-textiles"), ("spam-network-demo.com", "https://spamseller.gumroad.com/"),
                        ("community.n8n.io", "https://x.gumroad.com/l/invoice-flow")],
        "creem.io": [("launch-demo.app", "https://www.creem.io/payment/prod_59Tt3"), ("docs.example", "https://docs.creem.io/api-reference")],
    }
    # the sites whose first link is new; permit-records-demo.com has sold for years, demotools.dev is named by its parent domain
    new = {target: [d for d, _ in rows if d not in ("permit-records-demo.com", "try.demotools.dev")] for target, rows in answers.items()}
    new["buy.stripe.com"].append("demotools.dev")

    def rows(endpoint, body):
        target = body["target"]
        return (_sites(new[target]) if endpoint == sellers.DOMAINS else _backlinks(answers[target])), 27600
    monkeypatch.setattr(dfs, "rows", rows)
    monkeypatch.setattr(sellers, "page_words", lambda host: ("Pestora — pest control software", "Book and bill pest jobs") if host.startswith("pestora")
                        else ("", ""))
    kinds = {"pestora.framer.website": "ai", "meeting-demo.app": "ai", "janemaker.com": "content", "launch-demo.app": "ai", "try.demotools.dev": "ai"}

    def chat(model, system, user, max_tokens=1500):
        return json.dumps([{"i": x["i"], "kind": kinds[x["domain"]]} for x in json.loads(user)])
    monkeypatch.setattr(llm, "chat", chat)
    first = sellers.sellers(cfg, tmp_path)
    hosts = {s["host"]: s for s in first["sellers"]}
    assert [s["kind"] for s in first["sellers"]] == ["ai", "ai", "ai", "ai", "content"]                 # AI and software first
    assert set(hosts) == {"pestora.framer.website", "meeting-demo.app", "janemaker.com", "launch-demo.app", "try.demotools.dev"}
    assert first["old"] == 1                                                                            # an old seller's new page
    assert hosts["pestora.framer.website"]["first_seen"] == "2026-10-13"                                # the day the site first linked
    assert hosts["pestora.framer.website"]["term"] == "pestora" and hosts["janemaker.com"]["term"] == "janemaker"
    assert first["first_run"] and first["cost_micros"] == 8 * 27600
    lead = [x for x in first["terms"] if x["term"] == "pestora"][0]
    assert lead["about"] == ("https://pestora.framer.website/", "Pestora — pest control software. Book and bill pest jobs")
    text = "\n".join(report._seller_lines(first))
    assert "| [meeting-demo.app](https://meeting-demo.app/) | AI / software | Lemon Squeezy |" in text and "not that it sells well" in text
    again = sellers.sellers(cfg, tmp_path)
    assert again["sellers"] == [] and not again["first_run"]                                              # each site reported once


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


def test_builders_keep_the_badge_and_leave_posts_about_the_tool(cfg, tmp_path, monkeypatch):
    cfg["builders"]["platforms"] = ["lovable", "beehiiv"]
    rows = {"lovable.dev": [("formdemo.app", "https://lovable.dev/projects/lovp_1", "Edit with"),
                            ("blog.example", "https://lovable.dev/", "Lovable")],
            "beehiiv.com": [("radar.newsletter-demo.com", "https://www.beehiiv.com/powered-by?publication_name=Radar", "Powered by beehiiv")]}

    def answer(endpoint, body):
        target = body["target"]
        if endpoint == sellers.DOMAINS:
            return _sites([d for d, _, _ in rows[target]]), 27600
        return [{"domain_from": d, "url_to": u, "anchor": a, "url_from": f"https://{d}/deep/page",
                 "first_seen": "2026-10-14 03:00:00 +00:00", "domain_from_rank": 5}
                for d, u, a in rows[target]], 27600
    monkeypatch.setattr(dfs, "rows", answer)
    monkeypatch.setattr(sellers, "page_words", lambda host: ("FormDemo — forms that fill themselves", "") if host == "formdemo.app"
                        else ("Radar", "A weekly letter on climate startups"))
    got = sellers.builders(cfg, tmp_path)
    assert {s["host"] for s in got["sites"]} == {"formdemo.app", "radar.newsletter-demo.com"}      # the post about Lovable is out
    assert "| [formdemo.app](https://formdemo.app/) | Lovable |" in "\n".join(report._builder_lines(got))


def test_seller_traffic_details_come_from_the_visits_answer():
    info = sellers.traffic_info({"monthlyVisits": 924,
                                 "trafficSources": {"direct": 0.36, "searchOrganic": 0.32, "searchPaid": 0.01, "genAi": 0.02}})
    assert info == {"visits": 924, "top_source": "direct", "ai_share": 0.02}


def test_dodo_and_waffo_links_take_money():
    dodo, waffo = sellers.PLATFORMS["dodo"][1], sellers.PLATFORMS["waffo"][1]
    assert dodo("checkout.dodopayments.com", "/buy/pdt_0NcEz5RotWnutLQWHmr") and not dodo("checkout.dodopayments.com", "/")
    assert waffo("checkout.waffo.ai", "/acme/pro-plan") and not waffo("checkout.waffo.ai", "/")


def test_only_software_built_with_a_builder_becomes_a_lead(cfg, tmp_path, monkeypatch):
    cfg["builders"]["platforms"] = ["lovable"]
    rows = [("formdemo.app", "Edit with"), ("pizzademo.it", "Edit with")]

    def answer(endpoint, body):
        if endpoint == sellers.DOMAINS:
            return _sites([d for d, _ in rows]), 27600
        return [{"domain_from": d, "url_to": "https://lovable.dev/projects/x", "anchor": a,
                 "url_from": f"https://{d}/", "first_seen": "2026-10-15 03:00:00 +00:00",
                 "domain_from_rank": 5} for d, a in rows], 27600
    monkeypatch.setattr(dfs, "rows", answer)
    monkeypatch.setattr(sellers, "page_words", lambda host: ("FormDemo — forms that fill themselves with AI", "") if host == "formdemo.app"
                        else ("Pizzeria Demo — Pizza napoletana a Roma", ""))
    got = sellers.builders(cfg, tmp_path)
    assert [x["term"] for x in got["terms"]] == ["formdemo"] and {s["host"] for s in got["sites"]} == {"formdemo.app", "pizzademo.it"}
    assert got["by_kind"]["ai"] == 1


def test_a_payment_link_on_several_new_sites_is_a_network(cfg, tmp_path, monkeypatch):
    cfg["sellers"]["platforms"] = ["stripe"]
    rows = [("dachecker-demo.shop", "https://buy.stripe.com/9B6a"), ("dachecker-demo2.space", "https://buy.stripe.com/9B6a"),
            ("formdemo.app", "https://buy.stripe.com/own1")]

    def answer(endpoint, body):
        if endpoint == sellers.DOMAINS:
            return _sites([d for d, _ in rows]), 27600
        return _backlinks(rows), 27600
    monkeypatch.setattr(dfs, "rows", answer)
    got = sellers.fresh(cfg["sellers"], {})
    assert set(got["found"]) == {"formdemo.app"} and got["networked"] == 2


# --- history: every seller back to 2019 ------------------------------------------------

def _history_page(items, token):
    """The one result of a DataForSEO backlinks page: its rows and the token for the next page."""
    return {"items": items, "search_after_token": token}


def test_history_pulls_every_page_and_picks_up_where_it_stopped(tmp_path, monkeypatch):
    monkeypatch.setattr(seller_history, "PAGE", 2)
    item = lambda d, url, day: {"domain_from": d, "url_from": f"https://{d}/", "url_to": url, "first_seen": f"{day} 08:00:00 +00:00",  # noqa: E731
                                "domain_from_rank": 40}
    pages = {"": ([item("janemaker.com", "https://janemaker.gumroad.com/l/toxins", "2019-03-02"),
                   item("spam-network-demo.com", "https://spamseller.gumroad.com/", "2019-04-01")], "t1"),
             "t1": ([item("nocode.school", "https://nocode.gumroad.com/l/course", "2024-05-06")], "")}
    sent = []

    def result(endpoint, body):
        sent.append(body)
        assert body["order_by"] == ["first_seen,asc"] and body["mode"] == "one_per_domain"
        return _history_page(*pages[body.get("search_after_token", "")]), 60000
    monkeypatch.setattr(dfs, "result", result)
    db = seller_history.connect(tmp_path)
    first = seller_history.pull(db, ["gumroad"], 50000)                        # the budget runs out after one page
    assert first["platforms"]["gumroad"] == {"rows": 2, "done": False, "cost_micros": 60000, "spent_micros": 60000, "error": ""}
    rest = seller_history.pull(db, ["gumroad"], 1_000_000)
    assert sent[-1]["search_after_token"] == "t1" and rest["platforms"]["gumroad"]["done"]   # it went on from the saved token
    assert seller_history.pull(db, ["gumroad"], 1_000_000)["cost_micros"] == 0               # done: nothing more to buy
    got = {h: (day, pays) for h, day, pays in db.execute("SELECT host, first_seen, pays FROM sites")}
    assert got == {"janemaker.com": ("2019-03-02", 1), "spam-network-demo.com": ("2019-04-01", 0), "nocode.school": ("2024-05-06", 1)}


def test_history_update_adds_the_new_sellers_back_to_the_newest_day(tmp_path, monkeypatch):
    monkeypatch.setattr(seller_history, "PAGE", 2)
    item = lambda d, url, day: {"domain_from": d, "url_from": f"https://{d}/", "url_to": url, "first_seen": f"{day} 08:00:00 +00:00",  # noqa: E731
                                "domain_from_rank": 40}
    db = seller_history.connect(tmp_path)
    db.executemany("INSERT INTO sites (platform, host, first_seen, page, link, rank, pays) VALUES (?, ?, ?, '', '', 0, 1)",
                   [("polar", "old.app", "2026-10-10"), ("stripe", "moveddemo.app", "2025-01-01")])
    pages = {"": ([item("new.app", "https://buy.polar.sh/polar_cl_1", "2026-10-14"),
                   item("old.app", "https://buy.polar.sh/polar_cl_2", "2026-10-13")], "t1"),     # an old seller's new page
             "t1": ([item("moveddemo.app", "https://buy.polar.sh/polar_cl_3", "2026-10-12"),
                     item("older.app", "https://buy.polar.sh/polar_cl_4", "2026-10-02")], "t2"),   # past 10-07: the last page read
             "t2": ([item("never.app", "https://buy.polar.sh/polar_cl_5", "2026-09-01")], "")}
    sent = []

    def result(endpoint, body):
        sent.append(body.get("search_after_token", ""))
        assert body["order_by"] == ["first_seen,desc"]
        return _history_page(*pages[body.get("search_after_token", "")]), 60000
    monkeypatch.setattr(dfs, "result", result)
    got = seller_history.update(db, ["polar"], 1_000_000, date(2026, 10, 15))["platforms"]["polar"]
    assert sent == ["", "t1"] and got["since"] == "2026-10-07"
    assert got["new"] == 3 and got["paying"] == 3 and got["elsewhere"] == 1 and got["cost_micros"] == 120000   # moveddemo.app sold via Stripe
    hosts = {h for (h,) in db.execute("SELECT host FROM sites WHERE platform = 'polar'")}
    assert hosts == {"old.app", "new.app", "moveddemo.app", "older.app"}
    assert db.execute("SELECT day, new, elsewhere FROM updates").fetchall() == [("2026-10-15", 3, 1)]


def test_history_stops_on_a_refused_page(tmp_path, monkeypatch):
    def result(endpoint, body):
        raise dfs.DfsError("Invalid Field: 'search_after_token'.")
    monkeypatch.setattr(dfs, "result", result)
    db = seller_history.connect(tmp_path)
    got = seller_history.pull(db, ["polar"], 1_000_000)["platforms"]["polar"]
    assert not got["done"] and "Invalid Field" in got["error"]


def test_history_reads_sorts_and_counts(tmp_path, monkeypatch):
    db = seller_history.connect(tmp_path)
    db.executemany("INSERT INTO sites (platform, host, first_seen, page, link, rank, pays) VALUES (?, ?, ?, '', '', 0, ?)",
                   [("stripe", "captions-demo.app", "2025-02-03", 1), ("polar", "captions-demo.app", "2025-03-01", 1),
                    ("stripe", "gone.example", "2021-07-01", 1), ("gumroad", "spam-network-demo.com", "2019-04-01", 0)])
    monkeypatch.setattr(seller_history.sellers, "page_words",
                        lambda host, limit: ("NotchLive — Live Captions for Mac", "") if host == "captions-demo.app" else ("", ""))
    assert seller_history.read(db, 2) == {"step": "read", "read": 2, "answered": 1}            # spam-network-demo.com is no seller: not read
    monkeypatch.setattr(llm, "chat", lambda model, system, user, max_tokens=1500: '[{"i": 0, "kind": "ai"}]')
    sorted_ = seller_history.sort(db, "gpt-4.1-mini", 1_000_000)
    assert sorted_["sorted"] == 1 and sorted_["by_rules"] == 0                                 # the dead site is left unsorted
    out = tmp_path / "sellers.csv"
    s = seller_history.summary(db, out)
    assert ["2025", "ai", 2] in s["by_year_kind"] and ["2021", "no answer", 1] in s["by_year_kind"]
    assert ["2025-02", "stripe", 1] in s["by_month"] and out.read_text(encoding="utf-8-sig").count("captions-demo.app") == 2
    assert "| Stripe | 2 | 2 |" in seller_history.text(s)


def test_history_sorts_by_rules_for_free_and_the_model_redoes_them(tmp_path, monkeypatch):
    db = seller_history.connect(tmp_path)
    db.executemany("INSERT INTO sites (platform, host, first_seen, page, link, rank, pays, title, description, read_on) "
                   "VALUES ('stripe', ?, '2026-01-02', '', '', 0, 1, ?, '', '2026-10-14')",
                   [("captions-demo.app", "NotchLive — Live Captions for Mac"), ("themes-demo.com", "Astro Themes & Tailwind CSS Templates")])
    free = seller_history.sort(db, "", 0)                                            # no model: rules, whatever the budget
    assert free["sorted"] == 2 and free["by_rules"] == 2 and free["cost_micros"] == 0
    monkeypatch.setattr(llm, "chat",
                        lambda model, system, user, max_tokens=1500: '[{"i": 0, "kind": "ai"}, {"i": 1, "kind": "template"}]')
    paid = seller_history.sort(db, "gpt-4.1-mini", 1_000_000)
    assert paid["sorted"] == 2 and paid["by_rules"] == 0
    assert dict(db.execute("SELECT host, kind_by FROM sites")) == {"captions-demo.app": "model", "themes-demo.com": "model"}


# --- side evidence -----------------------------------------------------------------------

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


def test_side_evidence_asks_whois_for_the_first_names(monkeypatch):
    asked = []

    def result(endpoint, body):
        asked.append(body["filters"][0][2])
        return {"total_count": 4, "items": [{"domain": "nanobanana-demo.app"}, {"domain": "nano-banana-demo.ai"}]}, 121200
    monkeypatch.setattr(dfs, "result", result)
    monkeypatch.setattr(sidelights, "_get", lambda url: b"[]" if "crt.sh" in url else (_ for _ in ()).throw(OSError("offline")))
    got = sidelights.evidence({"names": 2, "whois_names": 1, "whois_days": 30}, ["nano banana", "seedream five"], today=date(2026, 10, 15))
    assert asked == ["%nanobanana%"] and got["cost_micros"] == 121200
    assert got["rows"][0]["sites"] == (4, ["nanobanana-demo.app", "nano-banana-demo.ai"]) and got["rows"][0]["sites_from"] == "whois"
    assert got["rows"][1]["sites"] == (0, []) and "sites_from" not in got["rows"][1]           # crt.sh for the rest
    assert "4 (nanobanana-demo.app, nano-banana-demo.ai) · whois" in "\n".join(report._side_lines(got))
