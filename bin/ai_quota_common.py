"""Shared Conky quota cache, OAuth I/O, and reset-time rendering."""

import base64
from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
import json
import math
import os
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request

TTL = 240  # default seconds between fetches; a script may pass its own
MODES = ("percent", "remaining", "days", "color", "status", "fetch")
# Conky renders these from the cache only; a threaded `fetch` call does the
# network work, so a slow provider can never freeze the widget.
CACHED_MODES = ("percent", "days", "color")
DAYS_WIDTH = 7  # "06d03h*"; padded so a monospace column keeps bars aligned
# No data because the login is missing or was rejected.
AUTH_ERRORS = ("HTTP 400", "HTTP 401", "HTTP 403", "PermissionError", "FileNotFoundError")
NORMAL_COLOR = "${color}"
ALERT_COLOR = "${color #f38ba8}"
STALE_COLOR = "${color #9399b2}"
USER_AGENT = "conky-ai-quota/1.0"


def number(value):
    if isinstance(value, bool):
        raise ValueError("expected a number")
    result = float(value)
    if not math.isfinite(result):
        raise ValueError("expected a finite number")
    return result


def percent(value):
    return min(100.0, max(0.0, number(value)))


def timestamp(value):
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        return number(value)
    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.timestamp()


def jwt_expiry(token):
    # Decode only the expiry hint. The provider still authenticates the token.
    try:
        part = token.split(".")[1]
        claims = json.loads(base64.urlsafe_b64decode(part + "=" * (-len(part) % 4)))
        return number(claims["exp"])
    except (ValueError, KeyError, IndexError, TypeError):
        return 0


def read_json(path):
    data = json.loads(path.read_text())
    if not isinstance(data, dict):
        raise ValueError("expected a JSON object")
    return data


def write_json(path, data):
    """Atomic replacement, with private permissions from creation onward."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=path.name + ".", dir=path.parent)
    try:
        with os.fdopen(fd, "w") as stream:
            json.dump(data, stream, indent=2, allow_nan=False)
            stream.write("\n")
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


@contextmanager
def locked(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_CREAT | os.O_RDWR, 0o600)
    with os.fdopen(fd, "w") as stream:
        fcntl.flock(stream, fcntl.LOCK_EX)
        yield


def request_json(url, *, headers=None, body=None, form=False):
    hdr = {"Accept": "application/json", "User-Agent": USER_AGENT, **(headers or {})}
    data = None
    if body is not None:
        hdr["Content-Type"] = "application/x-www-form-urlencoded" if form else "application/json"
        data = (urllib.parse.urlencode(body) if form else json.dumps(body)).encode()
    req = urllib.request.Request(url, data=data, headers=hdr)
    with urllib.request.urlopen(req, timeout=10) as response:
        result = json.loads(response.read())
    if not isinstance(result, dict):
        raise ValueError("expected a JSON object")
    return result


def refresh_credentials(path, entry, access_key, refresh_key, expired, refresh, force=False):
    """Serialize quota refreshes and preserve unrelated CLI credential fields.

    refresh returns (credential_updates, root_updates). Reread before writing
    to preserve changes made by the CLI while the HTTP request was running.
    """
    with locked(path.with_name(path.name + ".quota.lock")):
        root = read_json(path)
        creds = root if entry is None else root[entry]
        if not force and not expired(creds):
            return creds
        updates, root_updates = refresh(creds)
        if not updates.get(access_key):
            raise ValueError("refresh response has no access token")
        latest = read_json(path)
        current = latest if entry is None else latest[entry]
        if any(current.get(k) != creds.get(k) for k in (access_key, refresh_key)):
            return current
        current.update(updates)
        latest.update(root_updates)
        write_json(path, latest)
        return current


def authenticated_json(url, token, headers=None):
    for force in (False, True):
        access = token(force)
        try:
            return request_json(url, headers={**(headers or {}), "Authorization": f"Bearer {access}"})
        except urllib.error.HTTPError as error:
            if error.code != 401 or force:
                raise
            error.close()


def read_cache(path):
    """The cached snapshot, or {} when missing or unreadable. Never blocks:
    writers replace the file atomically."""
    try:
        cached = read_json(path)
        if "percent" in cached:
            cached["percent"] = percent(cached["percent"])
            cached["fetched_at"] = number(cached["fetched_at"])
            cached["reset_at"] = timestamp(cached["reset_at"])
        return cached
    except (OSError, ValueError, KeyError, TypeError):
        return {}


def load_cache(path, fetch, ttl=TTL):
    # One fetch/refresh per cache at a time prevents refresh-token races and
    # duplicate failed requests.
    with locked(path.with_name(path.name + ".lock")):
        now = time.time()
        cached = read_cache(path)
        last_attempt = cached.get("attempted_at", cached.get("fetched_at", 0))
        try:
            age = now - number(last_attempt)
        except (ValueError, TypeError):
            age = ttl
        crossed_reset = (cached.get("reset_at") is not None
                         and cached.get("fetched_at", 0) < cached["reset_at"] <= now)
        if cached.get("schema") == 2 and 0 <= age < ttl and (cached.get("error") or not crossed_reset):
            return cached
        try:
            data = fetch()
            data["percent"] = percent(data["percent"])
            data["reset_at"] = timestamp(data.get("reset_at"))
            data["fetched_at"] = time.time()
        except Exception as error:
            data = cached.copy()
            # Never cache response bodies, URLs, tokens, or exception strings.
            data["error"] = f"HTTP {error.code}" if isinstance(error, urllib.error.HTTPError) else type(error).__name__
            if isinstance(error, urllib.error.HTTPError):
                error.close()
        data["attempted_at"] = time.time()
        data["schema"] = 2
        write_json(path, data)
        return data


def seconds_remaining(data, now=None):
    if data.get("reset_at") is None:
        return None
    return max(0.0, data["reset_at"] - (time.time() if now is None else now))


def stale(data, now=None, ttl=TTL):
    # Stale only after a missed fetch cycle, not while one is merely due.
    now = time.time() if now is None else now
    age = now - data.get("fetched_at", 0)
    return ("percent" not in data or bool(data.get("error")) or age < 0 or age >= 2 * ttl
            or (data.get("reset_at") is not None and data["reset_at"] <= now))


def fmt_remaining(secs):
    if secs is None:
        return "reset time unavailable"
    days, rem = divmod(max(0, int(secs)), 86400)
    hours, rem = divmod(rem, 3600)
    minutes = rem // 60
    if days:
        return f"{days} day{'s' if days != 1 else ''} {hours}h to reset"
    if hours:
        return f"{hours}h {minutes}m to reset"
    return f"{minutes}m to reset" if minutes else "<1m to reset"


def days_label(data, secs, old):
    if "percent" not in data:
        if not data:
            return "--"  # nothing fetched yet
        return "auth!" if data.get("error") in AUTH_ERRORS else "error"
    if secs == 0:
        return "stale"  # the reset has passed and no live number replaced it
    if secs is None:
        return "?*" if old else "?"
    days, rem = divmod(int(secs), 86400)
    hours = rem // 3600
    label = f"{days:02d}d{hours:02d}h" if days else (f"{hours:02d}h" if hours else f"{int(rem // 60):02d}m")
    return label + ("*" if old else "")


def run(mode, path, fetch, ttl=TTL):
    if mode not in MODES:
        sys.exit(f"unknown mode: {mode}")
    if mode == "fetch":
        load_cache(path, fetch, ttl)
        return
    data = read_cache(path) if mode in CACHED_MODES else load_cache(path, fetch, ttl)
    print(render(data, mode, ttl))


def render(data, mode, ttl=TTL):
    now = time.time()
    old = stale(data, now, ttl)
    secs = seconds_remaining(data, now)
    if mode == "percent":
        # Past the reset the cached number describes a window that is over.
        return "0" if secs == 0 else f"{percent(data.get('percent', 0)):.0f}"
    if mode == "color":
        if old:
            return STALE_COLOR
        short_reset = data.get("short_reset_at")
        hot = data.get("short_percent", 0) >= 95 and (short_reset is None or short_reset > now)
        return ALERT_COLOR if hot or data.get("percent", 0) >= 95 else NORMAL_COLOR
    if mode == "days":
        return days_label(data, secs, old).rjust(DAYS_WIDTH)
    if mode == "remaining":
        if old and secs == 0:
            return "stale: cached reset has passed; awaiting live usage"
        return ("stale: " if old else "") + fmt_remaining(secs)
    if mode == "status":
        fetched = data.get("fetched_at")
        reset = data.get("reset_at")
        return json.dumps({
            **data, "stale": old, "remaining_seconds": secs,
            "fetched_local": datetime.fromtimestamp(fetched).astimezone().isoformat() if fetched else None,
            "reset_local": datetime.fromtimestamp(reset).astimezone().isoformat() if reset else None,
        }, indent=2)
    raise ValueError("unknown mode")
