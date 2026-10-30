"""HTTP API + dashboard + autonomy scheduler. stdlib only.

    python -m limbic serve --db limbic.db --host 0.0.0.0 --port 8770

Write endpoints require `Authorization: Bearer <token>` if LIMBIC_TOKEN (or
--token) is set. Read endpoints are open on the bound interface, so bind to
a trusted network.
"""
from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

from .agent import Limbic
from .appraisal import KeywordAppraiser, LLMAppraiser
from .store import Store

DASHBOARD = Path(__file__).parent / "dashboard" / "index.html"
log = logging.getLogger("limbic")
ID_RE = re.compile(r"[\w.\-]{1,64}")


STARTED = time.time()


class Hub:
    """One Limbic instance per agent. A single re-entrant lock serialises all
    state changes (requests + scheduler); the engine is cheap, so this is fine."""

    def __init__(self, store: Store, appraiser_factory=None, poster=None) -> None:
        self.store = store
        self.agents: dict[str, Limbic] = {}
        self.lock = threading.RLock()
        self.appraiser_factory = appraiser_factory or (lambda temp: KeywordAppraiser(temp))
        self.poster = poster or _post_json
        self._stop = threading.Event()

    def get(self, agent_id: str, create: bool = False, temperament: dict | None = None) -> Limbic | None:
        with self.lock:
            if agent_id in self.agents:
                if temperament is not None:
                    self.agents[agent_id].set_temperament(temperament)
                return self.agents[agent_id]
            if not create and self.store.get_agent(agent_id) is None:
                return None
            mind = Limbic(agent_id, store=self.store, temperament=temperament)
            mind.appraiser = self.appraiser_factory(mind.temperament)
            self.agents[agent_id] = mind
            return mind

    def drop(self, agent_id: str) -> None:
        with self.lock:
            self.agents.pop(agent_id, None)
            self.store.delete_agent(agent_id)

    # ---- autonomy scheduler ------------------------------------------
    def run_autonomy_once(self) -> list[dict]:
        out = []
        with self.lock:
            for a in self.store.list_agents():
                mind = self.get(a["id"])
                if mind is None:
                    continue
                for it in mind.evaluate_intents():
                    out.append({"agent": a["id"], **it})
                    url = mind.temperament.webhook_url
                    if url:
                        payload = {"agent": a["id"], "intent": it,
                                   "instruction": mind.instruction(it["id"], lang="en"),
                                   "instruction_hu": mind.instruction(it["id"], lang="hu")}
                        threading.Thread(target=self._safe_post, args=(url, payload), daemon=True).start()
        return out

    def _safe_post(self, url: str, payload: dict) -> None:
        try:
            self.poster(url, payload)
        except Exception as exc:  # pragma: no cover - network
            log.warning("webhook %s failed: %s", url, exc)

    def start_scheduler(self, interval_s: float = 60.0) -> threading.Thread:
        def loop():
            while not self._stop.wait(interval_s):
                try:
                    self.run_autonomy_once()
                except Exception:  # pragma: no cover
                    log.exception("autonomy tick failed")
        th = threading.Thread(target=loop, name="limbic-autonomy", daemon=True)
        th.start()
        return th

    def stop(self) -> None:
        self._stop.set()


def _version() -> str:
    from . import __version__
    return __version__


def _post_json(url: str, payload: dict) -> None:
    req = urllib.request.Request(url, data=json.dumps(payload, ensure_ascii=False).encode(),
                                 headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=15) as r:
        r.read()


def make_handler(hub: Hub, token: str | None):
    class Handler(BaseHTTPRequestHandler):
        server_version = "limbic/0.2"

        def log_message(self, fmt, *args):
            if os.environ.get("LIMBIC_LOG"):
                super().log_message(fmt, *args)

        def _send(self, code: int, body, ctype: str = "application/json") -> None:
            data = body if isinstance(body, bytes) else (
                body.encode() if isinstance(body, str) else json.dumps(body, ensure_ascii=False).encode())
            self.send_response(code)
            self.send_header("Content-Type", ctype + ("; charset=utf-8" if ("json" in ctype or "text" in ctype) else ""))
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization")
            self.send_header("Access-Control-Allow-Methods", "GET, POST, PUT, DELETE, OPTIONS")
            self.end_headers()
            self.wfile.write(data)

        def _body(self) -> dict:
            n = int(self.headers.get("Content-Length") or 0)
            return json.loads(self.rfile.read(n).decode() or "{}") if n else {}

        def _authorized(self) -> bool:
            return not token or self.headers.get("Authorization", "") == f"Bearer {token}"

        def do_OPTIONS(self):
            self._send(204, b"")

        def do_GET(self):
            self._route("GET")

        def do_POST(self):
            self._route("POST")

        def do_PUT(self):
            self._route("PUT")

        def do_DELETE(self):
            self._route("DELETE")

        def _route(self, method: str) -> None:
            url = urlparse(self.path)
            q = {k: v[-1] for k, v in parse_qs(url.query).items()}
            parts = [unquote(p) for p in url.path.split("/") if p]
            try:
                if method != "GET" and not self._authorized():
                    return self._send(401, {"error": "unauthorized"})
                if not parts or parts == ["index.html"]:
                    return self._send(200, DASHBOARD.read_bytes(), "text/html")
                if parts[0] != "api":
                    return self._send(404, {"error": "not found"})
                if parts[1:2] == ["update"]:
                    return self._update(method, parts[2:], q)
                with hub.lock:
                    return self._api(method, parts[1:], q)
            except KeyError as exc:
                return self._send(404, {"error": f"not found: {exc}"})
            except StopIteration:
                return self._send(404, {"error": "not found"})
            except (ValueError, TypeError, json.JSONDecodeError) as exc:
                return self._send(400, {"error": str(exc)})
            except (BrokenPipeError, ConnectionResetError):
                return None   # the client went away (e.g. a one-shot CLI exited); the work is already saved
            except Exception as exc:  # pragma: no cover
                log.exception("request failed")
                return self._send(500, {"error": f"{type(exc).__name__}: {exc}"})

        def _update(self, method: str, p: list[str], q: dict) -> None:
            from . import update
            if method == "GET" and not p:
                return self._send(200, update.status(fetch=q.get("fetch", "1") != "0"))
            if method == "POST" and p == ["apply"]:
                try:
                    return self._send(200, update.apply(hub))
                except PermissionError as exc:
                    return self._send(403, {"error": str(exc)})
                except RuntimeError as exc:
                    return self._send(409, {"error": str(exc)})
            return self._send(404, {"error": "not found"})

        def _api(self, method: str, p: list[str], q: dict) -> None:
            if p == ["health"]:
                return self._send(200, {"ok": True, "time": time.time(), "started": STARTED, "version": _version()})
            if p == ["autonomy", "run"] and method == "POST":
                return self._send(200, hub.run_autonomy_once())
            if p == ["agents"] and method == "GET":
                out = []
                for a in hub.store.list_agents():
                    mind = hub.get(a["id"])
                    mind.tick()
                    snap = mind.snapshot()
                    out.append({**a, "label": snap["label"], "label_hu": snap["label_hu"],
                                "mood": snap["mood"], "alerts": snap["alerts"],
                                "pending_intents": len(mind.intents("pending"))})
                return self._send(200, out)
            if p == ["agents"] and method == "POST":
                b = self._body()
                aid = str(b.get("id") or "").strip()
                if not ID_RE.fullmatch(aid):
                    return self._send(400, {"error": "id must match [\\w.-]{1,64}"})
                mind = hub.get(aid, create=True, temperament=b.get("temperament"))
                return self._send(201, {"id": aid, "temperament": mind.temperament.to_dict()})
            if len(p) < 2 or p[0] != "agents":
                return self._send(404, {"error": "not found"})

            aid, rest = p[1], p[2:]
            mind = hub.get(aid)
            if mind is None:
                return self._send(404, {"error": f"unknown agent {aid}"})
            who = q.get("interlocutor") or None
            sub = rest[0] if rest else ""
            G, P = method == "GET", method in ("POST", "PUT")

            if sub in ("", "state") and G:
                mind.tick()
                return self._send(200, mind.snapshot(who))
            if sub == "" and method == "DELETE":
                hub.drop(aid)
                return self._send(200, {"deleted": aid})
            if sub == "temperament":
                if P:
                    mind.set_temperament(self._body())
                return self._send(200, mind.temperament.to_dict())
            if sub == "relationships" and len(rest) == 2 and P:
                b = self._body()
                keys = ("consent", "reachable", "contact", "affection", "trust", "resentment", "fear",
                        "longing", "intimacy", "attraction")
                r = mind.set_relationship(rest[1], **{k: b[k] for k in keys if k in b})
                return self._send(200, r)
            if sub == "emotion" and P:
                b = self._body()
                try:
                    return self._send(200, mind.set_emotion(str(b["name"]), b.get("target"), float(b.get("intensity", 0))))
                except ValueError as e:
                    return self._send(400, {"error": str(e)})
            if sub == "drives" and P:
                b = self._body()
                return self._send(200, mind.set_drives(**{str(k): float(v) for k, v in b.items()
                                                          if isinstance(v, (int, float))}))
            if sub == "mood" and P:
                b = self._body()
                return self._send(200, mind.set_mood(**{k: float(v) for k, v in b.items()
                                                        if k in ("p", "a", "d", "baseline_p", "baseline_a", "baseline_d")}))
            if sub == "sexuality" and P:
                return self._send(200, mind.set_sexuality(float(self._body()["openness"])))
            if sub == "prompt" and G:
                text = mind.prompt(lang=q.get("lang", "en"), interlocutor=who)
                mind.save()
                if q.get("format") == "text":
                    return self._send(200, text, "text/plain")
                return self._send(200, {"prompt": text, "modulators": mind.modulators(who),
                                        "steering": mind.snapshot(who)["steering"]})
            if sub == "events" and G:
                since = time.time() - float(q.get("hours", 168)) * 3600
                return self._send(200, hub.store.events(aid, since=since, limit=int(q.get("limit", 300))))
            if sub == "events" and P:
                b = self._body()
                items = b.get("appraisals") or [b.get("appraisal") or b]
                res = [mind.feel(x, source=b.get("source", "api"), speaker=b.get("speaker"),
                                 private=bool(b.get("private"))) for x in items]
                return self._send(200, {"results": res, "state": mind.snapshot(b.get("speaker"))})
            if sub == "observe" and P:
                b = self._body()
                if not b.get("text"):
                    return self._send(400, {"error": "text required"})
                res = mind.observe(b["text"], speaker=b.get("speaker"), context=b.get("context"),
                                   private=bool(b.get("private")), channel=b.get("channel"))
                return self._send(200, {"results": res,
                                        "appraiser_error": getattr(mind.appraiser, "last_error", None),
                                        "state": mind.snapshot(b.get("speaker"))})
            if sub == "see" and P:
                b = self._body()
                if not b.get("text"):
                    return self._send(400, {"error": "text required"})
                at = b.get("at")
                res = mind.see(b["text"], question=b.get("question"), speaker=b.get("speaker"),
                               private=bool(b.get("private")),
                               at=float(at) if isinstance(at, (int, float)) else None)
                return self._send(200, {"results": res,
                                        "appraiser_error": getattr(mind.appraiser, "last_error", None),
                                        "mood": mind.snapshot()["label"]})
            if sub == "read" and P:
                b = self._body()
                if not b.get("text"):
                    return self._send(400, {"error": "text required"})
                res = mind.read(b["text"], title=b.get("title"), memory=b.get("memory"))
                return self._send(200, {"results": res,
                                        "appraiser_error": getattr(mind.appraiser, "last_error", None),
                                        "mood": mind.snapshot()["label"]})
            if sub == "leisure":
                b = self._body() if P else {}
                act = rest[1] if len(rest) > 1 else ""
                if act == "" and G:
                    mind.tick()
                    return self._send(200, mind.snapshot()["leisure"])
                if act == "decide" and P:
                    return self._send(200, mind.leisure_decide(available=b.get("available"),
                                                               force=bool(b.get("force"))))
                if act == "start" and P:
                    return self._send(200, mind.leisure_start(str(b["activity"]), resume=bool(b.get("resume")),
                                                              topic=b.get("topic")))
                if act == "experience" and P:
                    res = mind.leisure_experience(text=b.get("text"), kind=b.get("kind", "web"),
                                                  title=b.get("title"), interests=b.get("interests"),
                                                  appraisals=b.get("appraisals"), bookmark=b.get("bookmark"),
                                                  note=b.get("note"))
                    res["appraiser_error"] = getattr(mind.appraiser, "last_error", None)
                    res["mood"] = mind.snapshot()["label"]
                    return self._send(200, res)
                if act == "continue" and G:
                    return self._send(200, mind.leisure_should_continue())
                if act == "finish" and P:
                    return self._send(200, mind.leisure_finish(rating=b.get("rating"), summary=b.get("summary"),
                                                               reason=b.get("reason")))
                if act == "resume" and P:
                    return self._send(200, mind.leisure_resume())
                if act == "pause" and P:
                    return self._send(200, mind.leisure_pause(b.get("by")) or {})
            if sub == "channel" and P:
                b = self._body()
                mind.note_channel(str(b["speaker"]), str(b["channel"]))
                mind.save()
                return self._send(200, {"ok": True})
            if sub == "reply" and P:
                mind.note_reply()
                return self._send(200, {"ok": True})
            if sub == "tick" and P:
                return self._send(200, mind.tick())
            if sub == "consolidate" and P:
                return self._send(200, mind.consolidate(window_s=self._body().get("window_s")))
            if sub == "recall" and len(rest) == 2 and P:
                return self._send(200, mind.recall(int(rest[1])))
            if sub == "reset" and P:
                return self._send(200, mind.reset())
            if sub == "history" and G:
                since = time.time() - float(q.get("hours", 168)) * 3600
                return self._send(200, hub.store.history(aid, since=since, max_points=int(q.get("points", 600))))
            if sub == "intents":
                if len(rest) == 1 and G:
                    return self._send(200, mind.intents(q.get("status")))
                if rest[1:] == ["evaluate"] and P:
                    return self._send(200, mind.evaluate_intents())
                if len(rest) == 3:
                    iid, action = int(rest[1]), rest[2]
                    if action == "claim" and P:
                        it = mind.claim(iid)
                        return self._send(200, {"intent": it,
                                                "instruction": mind.instruction(iid, q.get("lang", "en"))})
                    if action == "dismiss" and P:
                        return self._send(200, mind.dismiss(iid))
                    if action == "instruction" and G:
                        return self._send(200, mind.instruction(iid, q.get("lang", "en")), "text/plain")
            return self._send(404, {"error": "not found"})

    return Handler


def appraiser_factory_from_env():
    base = os.environ.get("LIMBIC_LLM_URL")
    model = os.environ.get("LIMBIC_LLM_MODEL")
    if not (base and model):
        return None
    key = os.environ.get("LIMBIC_LLM_KEY")
    return lambda temp: LLMAppraiser(base, model, api_key=key, temperament=temp)


def serve(db: str = "limbic.db", host: str = "127.0.0.1", port: int = 8770,
          token: str | None = None, autonomy_interval: float = 60.0) -> ThreadingHTTPServer:
    hub = Hub(Store(db), appraiser_factory_from_env())
    httpd = ThreadingHTTPServer((host, port), make_handler(hub, token or os.environ.get("LIMBIC_TOKEN")))
    httpd.hub = hub  # type: ignore[attr-defined]
    if autonomy_interval > 0:
        hub.start_scheduler(autonomy_interval)
    return httpd
