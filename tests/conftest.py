import copy

import pytest

from radar import adsense, aisa, config, cws, dotcom, i18n, monid, notify, page_age, rdap, secrets, sidelights


@pytest.fixture(autouse=True)
def english(monkeypatch):
    i18n.set_lang("en")
    monkeypatch.delenv("MONID_API_KEY", raising=False)   # the optional source is off unless a test turns it on
    for key in notify.ENV_KEYS:   # configured notifications must never send a real report from a test
        monkeypatch.delenv(key, raising=False)
    for key in secrets.DATA_KEYS:   # a key on the machine must never turn a test into a paid call
        monkeypatch.delenv(key, raising=False)
    for key in secrets.SETTINGS:    # the gateway's address is the machine's, not the test's
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setattr(secrets, "load_env", lambda *a, **k: None)   # the .env file beside config.json stays out of the tests
    monkeypatch.setattr(aisa, "has_key", lambda: False)   # a key the AIsa CLI stored on the machine is out of the tests too

    def no_network(*a, **k):
        raise monid.MonidError("no network in tests")
    monkeypatch.setattr(monid, "run", no_network)

    def no_paid_calls(*a, **k):       # a key on the machine must never turn a test into a paid call
        raise aisa.AisaError("no network in tests")
    monkeypatch.setattr(aisa, "_post", no_paid_calls)
    monkeypatch.setattr(page_age, "ask", lambda domain: ("", "none"))      # RDAP: a test that needs it stubs it

    def no_rdap(url):                 # the .com lookup is free, but a test never reaches the registry
        raise OSError("no network in tests")
    monkeypatch.setattr(dotcom, "_get", no_rdap)
    monkeypatch.setattr(rdap, "registered", lambda domain: "")
    monkeypatch.setattr(adsense, "ids", lambda domain: set())

    def no_board(name):               # Disboard's own search is free, but a test never reaches the board
        raise OSError("no network in tests")
    monkeypatch.setattr(sidelights, "discord", no_board)

    def no_store(*a, **k):           # the store's reviews are free, but a test never reaches the store
        raise cws.CwsError("no network in tests")
    monkeypatch.setattr(cws, "_get", no_store)
    monkeypatch.setattr(cws, "_post", no_store)
    # The page-one check runs only in the tests that set it up, whatever loads the config (the MCP server too).
    monkeypatch.setitem(config.DEFAULTS["competition"], "max_rows", 0)
    for block in ("word_age", "markets", "serp_shape", "dot_com"):       # 0.6's extra readings and 0.7's .com: on in the tests that set them up
        monkeypatch.setitem(config.DEFAULTS[block], "enabled", False)
    monkeypatch.setitem(config.DEFAULTS["asks"], "weekday", None)          # weekly signals stay off unless a test asks
    monkeypatch.setitem(config.DEFAULTS["tutorials"], "max_leads", 0)
    monkeypatch.setitem(config.DEFAULTS["sellers"], "weekday", None)
    # 0.5's free sources reach the network without a key: off unless a test turns them on
    monkeypatch.setitem(config.DEFAULTS["builders"], "weekday", None)
    monkeypatch.setitem(config.DEFAULTS["money"], "weekday", None)
    monkeypatch.setitem(config.DEFAULTS["feeds"], "enabled", [])
    monkeypatch.setitem(config.DEFAULTS["sidelights"], "names", 0)
    monkeypatch.setitem(config.DEFAULTS["sidelights"], "products", 0)
    monkeypatch.setitem(config.DEFAULTS["pains"], "apps", 0)
    monkeypatch.setitem(config.DEFAULTS["asks"], "extra_sources", [])


@pytest.fixture
def cfg(tmp_path):
    c = copy.deepcopy(config.DEFAULTS)
    c["_dir"] = tmp_path
    c["competition"]["max_rows"] = 0     # the page-one check runs only in the tests that set it up
    return c
