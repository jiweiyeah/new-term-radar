"""The retry: a line that broke before the request was delivered is asked again."""
import http.client
import socket
import ssl
import urllib.error
import urllib.request

import pytest

from radar import net


def _answer(text="{}"):
    class Answer:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def read(self):
            return text.encode()

    return Answer()


def test_a_handshake_that_broke_is_asked_again(monkeypatch):
    calls = []

    def urlopen(req, timeout=None):
        calls.append(req)
        if len(calls) == 1:
            raise urllib.error.URLError(ssl.SSLEOFError("UNEXPECTED_EOF_WHILE_READING"))
        return _answer()

    monkeypatch.setattr(urllib.request, "urlopen", urlopen)
    monkeypatch.setattr(net.time, "sleep", lambda s: None)
    with net.fetch(urllib.request.Request("https://example.com"), timeout=10) as res:
        assert res.read() == b"{}"
    assert len(calls) == 2


def test_a_refused_connection_is_asked_again(monkeypatch):
    calls = []

    def urlopen(req, timeout=None):
        calls.append(req)
        if len(calls) < 3:
            raise urllib.error.URLError(ConnectionRefusedError("refused"))
        return _answer()

    monkeypatch.setattr(urllib.request, "urlopen", urlopen)
    monkeypatch.setattr(net.time, "sleep", lambda s: None)
    net.fetch(urllib.request.Request("https://example.com"), timeout=10)
    assert len(calls) == 3


def test_an_answer_the_server_gave_is_not_asked_again(monkeypatch):
    calls = []

    def urlopen(req, timeout=None):
        calls.append(req)
        raise urllib.error.HTTPError("https://example.com", 402, "no money", {}, None)

    monkeypatch.setattr(urllib.request, "urlopen", urlopen)
    with pytest.raises(urllib.error.HTTPError):
        net.fetch(urllib.request.Request("https://example.com"), timeout=10)
    assert len(calls) == 1


def test_a_request_that_was_delivered_is_not_asked_again(monkeypatch):
    # the answer was cut: it may already have been billed, so it is not asked again
    calls = []

    def urlopen(req, timeout=None):
        calls.append(req)
        raise http.client.RemoteDisconnected("the answer was cut")

    monkeypatch.setattr(urllib.request, "urlopen", urlopen)
    with pytest.raises(http.client.RemoteDisconnected):
        net.fetch(urllib.request.Request("https://example.com"), timeout=10)
    assert len(calls) == 1


def test_what_counts_as_never_having_left():
    assert net.broken(urllib.error.URLError(ssl.SSLEOFError("EOF")))
    assert net.broken(urllib.error.URLError(socket.gaierror("no such name")))
    assert net.broken(urllib.error.URLError(ConnectionRefusedError("refused")))
    assert not net.broken(urllib.error.URLError(socket.timeout("timed out")))
    assert not net.broken(http.client.RemoteDisconnected("cut"))
    # a bare SSL error comes from reading the answer: the request was delivered, and asking
    # again could bill the same reading twice
    assert not net.broken(ssl.SSLError("the answer was cut"))
    # a certificate that is refused is not going to change
    assert not net.broken(urllib.error.URLError(ssl.SSLCertVerificationError("bad cert")))
