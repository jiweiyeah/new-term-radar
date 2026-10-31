"""One HTTP call, asked again when the line broke before the request was delivered.

On this machine about one call in six to api.monid.ai ends the TLS handshake early
("UNEXPECTED_EOF_WHILE_READING"), and a reading lost to that is a reading the run paid for
and did not get. A handshake that broke sends nothing, so the call is free to ask again: it
is asked up to `TRIES` times, with a short pause between.

A request that was delivered and whose answer was cut is not asked again: it may already
have been billed. So only the failures that mean "nothing was sent" are tried again.
"""
from __future__ import annotations

import socket
import ssl
import time
import urllib.error
import urllib.request

TRIES = 3
PAUSE = 0.5


def broken(exc: OSError) -> bool:
    """Whether the request never left: the handshake broke, the name did not resolve, the
    connection was refused.

    urllib wraps the failures of the connect phase in a URLError. A bare SSL error comes
    from reading the answer instead, so the request was delivered: asking again could
    bill the same reading twice. A certificate that is refused is not asked again either:
    that answer will not change."""
    if not isinstance(exc, urllib.error.URLError):
        return False
    reason = exc.reason
    if isinstance(reason, ssl.SSLCertVerificationError):
        return False
    return isinstance(reason, (ssl.SSLError, socket.gaierror, ConnectionRefusedError))


def fetch(req: urllib.request.Request, timeout: int) -> urllib.response.addinfourl:
    """The response to one request, to read like `urlopen` answers. A connection that broke
    before the request was delivered is asked again."""
    last: OSError = OSError("the request failed")
    for attempt in range(TRIES):
        try:
            return urllib.request.urlopen(req, timeout=timeout)
        except urllib.error.HTTPError:
            raise                    # the server answered: that answer is the answer
        except OSError as exc:
            if not broken(exc):
                raise
            last = exc
            if attempt + 1 < TRIES:
                time.sleep(PAUSE * (attempt + 1))
    raise last
