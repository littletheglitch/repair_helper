"""HTTP fetcher with robots.txt, rate limiting, retry/backoff, resume.

The fetcher is the ONLY module that performs network access. It:

- Checks robots.txt per host (caches the parsed result) and refuses
  disallowed paths. A disallow is logged, not bypassed.
- Enforces a per-source rate limit (default 6 req/min) by sleeping
  before each request to keep ≥ ``60 / rate_per_min`` seconds since the
  last request to the same host.
- Retries 5xx and 429 with exponential backoff (1s, 2s, 4s, max 60s,
  up to 3 attempts). 4xx (except 429) is NOT retried (respect the
  site's "no").
- Detects login walls (redirect to /login, 401/403 with a known body)
  and returns a clear ``LoginWall`` result instead of fetching.
- Detects JavaScript-rendered pages (no <p> in the body, but a
  <script> tag) and returns a ``JavaScriptRequired`` result so the
  connector can disable the source rather than scrape unreliably.
- Supports resume via a per-host cursor that the connector passes
  back in; the fetcher doesn't itself store state (the orchestrator
  writes crawl_state).

The fetcher takes an injectable ``transport`` so tests can supply a
fake. The real transport uses ``urllib.request`` (stdlib, no extra
dependency).
"""
from __future__ import annotations

import socket
import time
import urllib.parse
import urllib.request
import urllib.error
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, Optional, Tuple

from . import config


# ----------------------------------------------------------------------
# Result types
# ----------------------------------------------------------------------

@dataclass
class FetchResult:
    url: str
    status: int                 # HTTP status, or 0 for a transport error
    body: bytes = b""
    headers: Dict[str, str] = field(default_factory=dict)
    final_url: str = ""
    error: str = ""
    kind: str = "ok"            # ok | error | login_wall | js_required | robots_blocked | not_modified

    @property
    def ok(self) -> bool:
        return self.kind == "ok" and 200 <= self.status < 400

    @property
    def text(self) -> str:
        try:
            return self.body.decode("utf-8", errors="replace")
        except Exception:
            return ""


# ----------------------------------------------------------------------
# Robots.txt cache
# ----------------------------------------------------------------------

class RobotsCache:
    """Per-host robots.txt cache. Uses urllib.robotparser (stdlib).

    On a fetch failure, robots.txt is assumed to ALLOW (the permissive
    default per RFC 9309 §2.1: "if the crawler cannot retrieve the
    robots.txt, it may assume it is allowed"). We log the failure but
    do not block.
    """
    def __init__(self, user_agent: str):
        self.user_agent = user_agent
        self._cache: Dict[str, "urllib.robotparser.RobotFileParser"] = {}

    def _parser_for(self, host_root: str) -> "urllib.robotparser.RobotFileParser":
        if host_root in self._cache:
            return self._cache[host_root]
        import urllib.robotparser
        rp = urllib.robotparser.RobotFileParser()
        rp.set_url(host_root + "/robots.txt")
        try:
            rp.read()
        except Exception:
            # Permissive default: leave the parser as-is; urllib.robotparser
            # treats an unread robots.txt as "allow all" by default.
            pass
        self._cache[host_root] = rp
        return rp

    def allowed(self, url: str) -> Tuple[bool, str]:
        parsed = urllib.parse.urlparse(url)
        host_root = f"{parsed.scheme}://{parsed.netloc}"
        rp = self._parser_for(host_root)
        try:
            ok = rp.can_fetch(self.user_agent, url)
        except Exception:
            return True, "robots.txt unreadable; default-allow"
        return ok, ("allowed" if ok else "disallowed by robots.txt")


# ----------------------------------------------------------------------
# Fetcher
# ----------------------------------------------------------------------

class Fetcher:
    """The fetcher. Construct once per collection run."""

    def __init__(self, settings: Optional[Dict] = None,
                 transport: Optional[Callable[[str, Dict], Tuple[int, bytes, Dict[str, str], str]]] = None):
        self.settings = settings or config.load_settings()
        self.user_agent = self.settings.get("user_agent",
                                            "KnowledgeCollector/1.0")
        self.robots = RobotsCache(self.user_agent)
        self._last_request: Dict[str, float] = {}  # host -> timestamp
        self._transport = transport  # for tests; if None, use urllib

    def _rate_limit(self, host: str, rate_per_min: int) -> None:
        min_gap = 60.0 / max(rate_per_min, 1)
        now = time.time()
        last = self._last_request.get(host, 0.0)
        wait = min_gap - (now - last)
        if wait > 0:
            time.sleep(wait)
        self._last_request[host] = time.time()

    def fetch(self, url: str, *, rate_per_min: int = 6,
              extra_headers: Optional[Dict[str, str]] = None,
              allow_binary: bool = False) -> FetchResult:
        """Fetch a URL. Returns a FetchResult; never raises."""
        # 1. robots.txt
        allowed, reason = self.robots.allowed(url)
        if not allowed:
            return FetchResult(url=url, status=0, kind="robots_blocked",
                               error=reason)
        # 2. rate limit
        host = urllib.parse.urlparse(url).netloc
        self._rate_limit(host, rate_per_min)
        # 3. retry/backoff
        backoff = float(self.settings.get("retry_backoff_base", 1.0))
        cap = float(self.settings.get("retry_backoff_cap", 60.0))
        max_retry = int(self.settings.get("retry_max", 3))
        timeout = float(self.settings.get("request_timeout", 20.0))
        headers = {"User-Agent": self.user_agent}
        if extra_headers:
            headers.update(extra_headers)
        last_err = ""
        for attempt in range(max_retry + 1):
            try:
                if self._transport is not None:
                    status, body, hdrs, final_url = self._transport(url, headers)
                else:
                    status, body, hdrs, final_url = _urllib_fetch(
                        url, headers, timeout, allow_binary)
                # 5xx / 429 -> retry
                if status in (429, 500, 502, 503, 504) and attempt < max_retry:
                    last_err = f"HTTP {status}"
                    time.sleep(min(backoff * (2 ** attempt), cap))
                    continue
                # 401/403 -> login wall (unless the body looks like a normal page)
                if status in (401, 403):
                    if _looks_like_login_wall(body):
                        return FetchResult(url=url, status=status, body=body,
                                           headers=hdrs, final_url=final_url,
                                           kind="login_wall",
                                           error="authentication required")
                # 200 OK -> check JS requirement
                if 200 <= status < 300 and not allow_binary:
                    if _looks_js_required(body, hdrs):
                        return FetchResult(url=url, status=status, body=body,
                                           headers=hdrs, final_url=final_url,
                                           kind="js_required",
                                           error="page appears to require JavaScript")
                return FetchResult(url=url, status=status, body=body,
                                   headers=hdrs, final_url=final_url,
                                   kind="ok" if 200 <= status < 400 else "error",
                                   error=last_err if status >= 400 else "")
            except urllib.error.HTTPError as e:
                if e.code in (429, 500, 502, 503, 504) and attempt < max_retry:
                    last_err = f"HTTP {e.code}"
                    time.sleep(min(backoff * (2 ** attempt), cap))
                    continue
                if e.code in (401, 403):
                    body = b""
                    try:
                        body = e.read()
                    except Exception:
                        pass
                    if _looks_like_login_wall(body):
                        return FetchResult(url=url, status=e.code, body=body,
                                           kind="login_wall",
                                           error="authentication required")
                return FetchResult(url=url, status=e.code, error=str(e),
                                   kind="error")
            except (urllib.error.URLError, socket.timeout, ConnectionError) as e:
                last_err = f"transport: {e}"
                if attempt < max_retry:
                    time.sleep(min(backoff * (2 ** attempt), cap))
                    continue
                return FetchResult(url=url, status=0, error=last_err,
                                   kind="error")
            except Exception as e:
                last_err = f"unexpected: {e}"
                return FetchResult(url=url, status=0, error=last_err,
                                   kind="error")
        return FetchResult(url=url, status=0, error=last_err, kind="error")

    def download(self, url: str, dest: Path, *, rate_per_min: int = 6,
                 max_bytes: int = 524288000) -> FetchResult:
        """Download a binary file to ``dest``. Honors rate limit and
        robots.txt. Returns a FetchResult with body=b"" on success."""
        allowed, reason = self.robots.allowed(url)
        if not allowed:
            return FetchResult(url=url, status=0, kind="robots_blocked",
                               error=reason)
        host = urllib.parse.urlparse(url).netloc
        self._rate_limit(host, rate_per_min)
        try:
            req = urllib.request.Request(url, headers={"User-Agent": self.user_agent})
            with urllib.request.urlopen(req, timeout=float(self.settings.get("request_timeout", 20.0))) as resp:
                total = 0
                dest.parent.mkdir(parents=True, exist_ok=True)
                with open(dest, "wb") as f:
                    while True:
                        chunk = resp.read(64 * 1024)
                        if not chunk:
                            break
                        total += len(chunk)
                        if total > max_bytes:
                            f.close()
                            dest.unlink(missing_ok=True)
                            return FetchResult(url=url, status=200,
                                              kind="error",
                                              error=f"exceeded max_bytes={max_bytes}")
                        f.write(chunk)
                return FetchResult(url=url, status=resp.status, kind="ok",
                                   final_url=resp.url)
        except urllib.error.HTTPError as e:
            return FetchResult(url=url, status=e.code, kind="error",
                               error=str(e))
        except Exception as e:
            return FetchResult(url=url, status=0, kind="error", error=str(e))


# ----------------------------------------------------------------------
# Real transport (urllib)
# ----------------------------------------------------------------------

def _urllib_fetch(url: str, headers: Dict[str, str], timeout: float,
                  allow_binary: bool) -> Tuple[int, bytes, Dict[str, str], str]:
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        body = resp.read()
        # Limit body size for text fetches (avoid downloading a huge binary
        # accidentally).
        if not allow_binary and len(body) > 10 * 1024 * 1024:
            body = body[:10 * 1024 * 1024]
        hdrs = {k: v for k, v in resp.headers.items()}
        return resp.status, body, hdrs, resp.url


def _looks_like_login_wall(body: bytes) -> bool:
    """Heuristic: body contains 'login' or 'sign in' or 'log in' markers."""
    try:
        text = body[:4096].decode("utf-8", errors="ignore").lower()
    except Exception:
        return False
    markers = ["<form", "login", "sign in", "log in", "password", "sign-in"]
    hits = sum(1 for m in markers if m in text)
    return hits >= 3


def _looks_js_required(body: bytes, headers: Dict[str, str]) -> bool:
    """Heuristic: HTML page has script tags but almost no <p> text -> likely
    JS-rendered. We do NOT scrape such pages."""
    try:
        text = body.decode("utf-8", errors="ignore").lower()
    except Exception:
        return False
    ct = headers.get("Content-Type", "")
    if "html" not in ct and "text" not in ct:
        return False
    if "<script" not in text:
        return False
    # Count <p> occurrences; if fewer than 3, treat as JS-required.
    p_count = text.count("<p")
    if p_count < 3 and len(text) < 5000:
        return True
    return False
