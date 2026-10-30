"""Self-update from the git remote (the dashboard's "Update" button).

Only works when limbic runs from a git checkout. It is deliberately conservative:

* fast-forward only — never merges, never discards anything;
* refuses when tracked files were edited locally, or the branch has its own commits;
* backs up the database before restarting;
* restarts by re-executing the same command line (``sys.orig_argv``), so it works the same
  under systemd, tmux or a plain terminal.

Disable with ``LIMBIC_SELF_UPDATE=0``.
"""
from __future__ import annotations

import os
import subprocess
import sys
import threading
import time
from pathlib import Path

from . import __version__

REPO = Path(__file__).resolve().parent.parent
_CACHE: dict = {"t": 0.0, "data": None}
_LOCK = threading.Lock()


def enabled() -> bool:
    return os.environ.get("LIMBIC_SELF_UPDATE", "1").strip().lower() not in ("0", "false", "no", "off")


def _git(*args: str, timeout: float = 30.0, strip: bool = True) -> str:
    r = subprocess.run(["git", "-C", str(REPO), *args], capture_output=True, text=True, timeout=timeout)
    if r.returncode != 0:
        raise RuntimeError((r.stderr or r.stdout).strip().splitlines()[-1] if (r.stderr or r.stdout).strip()
                           else f"git {args[0]} failed")
    return r.stdout.strip() if strip else r.stdout.rstrip("\n")


def _commits(rng: str) -> list[dict]:
    out = _git("log", "--format=%h%x1f%ct%x1f%s", rng, "-n", "50")
    res = []
    for line in out.splitlines():
        h, ts, subj = (line.split("\x1f") + ["", ""])[:3]
        res.append({"hash": h, "time": int(ts or 0), "subject": subj})
    return res


def status(fetch: bool = True, max_age: float = 60.0) -> dict:
    """What is installed, what is on the remote, and whether updating is possible."""
    base = {"version": __version__, "enabled": enabled(), "repo": str(REPO)}
    if not (REPO / ".git").exists():
        return {**base, "supported": False, "reason": "not a git checkout (installed with pip?)"}
    with _LOCK:
        if not fetch and _CACHE["data"]:
            return _CACHE["data"]
        if fetch and _CACHE["data"] and time.time() - _CACHE["t"] < max_age:
            return _CACHE["data"]
        try:
            branch = _git("rev-parse", "--abbrev-ref", "HEAD")
            try:
                upstream = _git("rev-parse", "--abbrev-ref", "@{u}")
            except RuntimeError:
                return {**base, "supported": False, "branch": branch, "reason": "branch has no upstream"}
            fetch_error = None
            if fetch:
                try:
                    _git("fetch", "--quiet", timeout=40)
                except Exception as exc:  # offline etc. — still report the local view
                    fetch_error = str(exc)
            behind = int(_git("rev-list", "--count", "HEAD..@{u}") or 0)
            ahead = int(_git("rev-list", "--count", "@{u}..HEAD") or 0)
            dirty = [l[3:] for l in _git("status", "--porcelain", "--untracked-files=no", strip=False).splitlines() if l.strip()]
            incoming = _commits("HEAD..@{u}") if behind else []
            changed = _git("diff", "--name-only", "HEAD..@{u}").splitlines() if behind else []
            reason = None
            if dirty:
                reason = "local changes in tracked files"
            elif ahead:
                reason = "local commits not on the remote"
            data = {**base, "supported": True, "branch": branch, "upstream": upstream,
                    "head": _git("rev-parse", "--short", "HEAD"),
                    "head_time": int(_git("log", "-1", "--format=%ct") or 0),
                    "head_subject": _git("log", "-1", "--format=%s"),
                    "behind": behind, "ahead": ahead, "dirty": dirty[:20], "incoming": incoming,
                    "integration_changed": any(c.startswith("contrib/") for c in changed),
                    "can_update": bool(behind and not reason and enabled()),
                    "reason": reason, "fetch_error": fetch_error, "checked": time.time()}
        except Exception as exc:
            data = {**base, "supported": False, "reason": str(exc)}
        _CACHE.update(t=time.time(), data=data)
        return data


def apply(hub=None, restart: bool = True) -> dict:
    """Fast-forward to the remote, back up the database, then restart in place."""
    if not enabled():
        raise PermissionError("self-update is disabled (LIMBIC_SELF_UPDATE=0)")
    st = status(fetch=True, max_age=0)
    if not st.get("supported"):
        raise RuntimeError(st.get("reason") or "update not supported")
    if st.get("reason"):
        raise RuntimeError(st["reason"])
    if not st.get("behind"):
        return {"updated": False, "message": "already up to date", **_short(st)}
    before = st["head"]
    backup = None
    if hub is not None:
        backup = _backup_db(hub)
    _git("merge", "--ff-only", "--quiet", "@{u}", timeout=60)
    after = _git("rev-parse", "--short", "HEAD")
    _CACHE.update(t=0.0, data=None)
    if restart:
        threading.Thread(target=_restart, args=(hub,), daemon=True).start()
    return {"updated": True, "from": before, "to": after, "commits": st["incoming"], "backup": backup,
            "restarting": restart, "integration_changed": st["integration_changed"]}


def _short(st: dict) -> dict:
    return {k: st.get(k) for k in ("head", "behind", "ahead")}


def _backup_db(hub) -> str | None:
    import sqlite3
    path = getattr(hub.store, "path", None)
    if not path or path == ":memory:":
        return None
    dst = f"{path}.bak-update-{time.strftime('%Y%m%d-%H%M%S')}"
    with hub.lock, hub.store.lock:
        out = sqlite3.connect(dst)
        try:
            hub.store.db.backup(out)
        finally:
            out.close()
    return dst


def _restart(hub=None) -> None:
    time.sleep(0.8)  # let the HTTP response reach the browser
    try:
        if hub is not None:
            with hub.lock:
                hub.stop()
                for mind in list(hub.agents.values()):
                    try:
                        mind.save()
                    except Exception:
                        pass
                hub.store.close()
    finally:
        argv = list(getattr(sys, "orig_argv", None) or [sys.executable, *sys.argv])
        os.execv(sys.executable, [sys.executable, *argv[1:]])
