"""Hermes plugin: limbic feelings + autonomy for a profile.

Feelings
  pre_llm_call  -> GET  /prompt   (joined into the user message; system prompt untouched)
  post_llm_call -> POST /observe + /reply in a background thread (never delays a reply)

Autonomy (no cron): a daemon thread in every host process that can deliver
(messaging gateway for Telegram/Discord, desktop/TUI backend for Desktop).
Each tick it asks limbic for pending intents; if the intent's channel is one
this host can serve, it claims it (atomic, so two hosts never both send) and
starts a turn in that chat via ctx.inject_message. The agent writes the
message in its own voice and the normal delivery path sends it.

The injected text is marked so the agent knows it is its own impulse, and so
this plugin neither appraises it as the user's words nor double-injects state.

Settings live in plugins.entries.limbic.settings (Desktop: Settings -> Plugins
-> limbic). Fail-open everywhere: limbic down = agent without feelings.
"""
from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import quote

log = logging.getLogger("plugins.limbic")

MARK = "⟦limbic:"  # prefix of injected impulse turns
DEFAULTS = {
    "url": "http://127.0.0.1:8770",
    "agent": "",
    "token_file": "/etc/limbic/token",
    "lang": "en",
    "feelings_enabled": True,
    "autonomy_enabled": False,
    "channel_strategy": "last_active",
    "channel_preference": ["telegram", "discord", "desktop"],
    "fallback_after_min": 5,
    "poll_interval_s": 60,
    "default_speaker": "user",
    "senders": {},
    "routes": {},
    "private_platforms": ["telegram"],
    "images_enabled": True,
    "image_commands": ["image-analyze"],
    "see_timeout_s": 20,
    "image_memory": "",          # "hindsight": also store each seen image as a Hindsight memory
    "skip_platforms": ["cron", "kanban", "subagent"],
    "prompt_timeout_s": 2.0,
    "observe_timeout_s": 90.0,
    "context_turns": 4,
}
GATEWAY_PLATFORMS = {"telegram", "discord", "slack", "signal", "whatsapp", "matrix", "email"}
LOCAL_PLATFORMS = {"desktop", "tui", "cli"}


def _text(msg) -> str:
    if isinstance(msg, str):
        return msg
    if isinstance(msg, list):
        return " ".join(p.get("text", "") for p in msg if isinstance(p, dict) and p.get("type") == "text")
    if isinstance(msg, dict):
        return _text(msg.get("content"))
    return str(msg or "")


def _hindsight_target() -> tuple[str, str, str]:
    """(api_url, bank_id, api_key) from the profile's Hindsight config, or empty."""
    try:
        from hermes_constants import get_hermes_home
        home = Path(get_hermes_home())
    except Exception:
        home = Path(os.environ.get("HERMES_HOME") or Path.home() / ".hermes")
    for p in (home / "hindsight" / "config.json", Path.home() / ".hindsight" / "config.json"):
        try:
            d = json.loads(p.read_text())
            if d.get("api_url") and d.get("bank_id"):
                return str(d["api_url"]).rstrip("/"), str(d["bank_id"]), str(d.get("api_key") or "")
        except Exception:
            continue
    return "", "", ""


def image_memory_item(description: str, question: str, shown_by: str, felt: str, when: float,
                      doc_id: str) -> dict:
    """A first-person memory of an image she looked at (Hindsight MemoryItem)."""
    who = shown_by or "the user"
    lines = [f"Hermes Agent (the assistant itself, not the user) looked at and analysed an image that {who} sent it."]
    if question:
        lines.append(f"{who} asked about the image: {question}")
    if felt:
        lines.append(f"How seeing it felt to the assistant: {felt}")
    lines.append(f"What the image showed: {description}")
    return {"content": "\n".join(lines)[:6000],
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime(when)),
            "context": f"an image the assistant looked at, shown by {who}", "document_id": doc_id,
            "metadata": {"kind": "image", "shown_by": shown_by or ""}, "tags": ["image"]}


def retain_to_hindsight(items: list[dict], timeout: float = 30.0) -> bool:
    url, bank, key = _hindsight_target()
    if not url or not items:
        return False
    headers = {"Content-Type": "application/json"}
    if key:
        headers["Authorization"] = f"Bearer {key}"
    req = urllib.request.Request(f"{url}/v1/default/banks/{quote(bank)}/memories", method="POST", headers=headers,
                                 data=json.dumps({"items": items, "async": True}, ensure_ascii=False).encode())
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return 200 <= r.status < 300


def perception_text(description: str, felt: str, lang: str = "en") -> str:
    """The analysis as the agent's own sight (replaces the raw tool output)."""
    if str(lang).startswith("hu"):
        out = ["[Amit most a saját szemeddel látsz a képen]", description.strip()]
        if felt:
            out += ["", "[Ahogy ránézel, ezt érzed]", felt.strip()]
        out += ["", "(Ez a te saját látványod és érzésed - nem rendszerüzenet és nem egy eszköz jelentése. "
                "Ne idézd szó szerint, ne beszélj elemzőről, modellről vagy eszközről: úgy reagálj, mint aki "
                "ránézett a képre, a saját szavaiddal és a saját érzéseddel.)"]
    else:
        out = ["[What you are now seeing in the image with your own eyes]", description.strip()]
        if felt:
            out += ["", "[How it makes you feel as you look at it]", felt.strip()]
        out += ["", "(This is your own sight and your own feeling - not a system message and not a tool report. "
                "Do not quote it or mention an analyser, model or tool: react like someone who just looked at "
                "the picture, in your own words, with your own feelings.)"]
    return "\n".join(out)


# Hermes puts the auxiliary-vision description of an attached image into the user message:
#   gateway:        "[The user sent an image~ Here's what I can see:\n<desc>]"
#   desktop/CLI:    "[The user attached an image. Here's what it contains:\n<desc>]"
#   fallback path:  "[The user attached an image. Here's what it contains:\n<desc>]"
_IMAGE_NOTE = re.compile(
    r"\[The (?:user|assistant) (?:sent an image~ Here's what I can see|attached an image\. Here's what it contains):\n"
    r"(?P<desc>.*?)\]\n?(?:\[If you need a closer look, use vision_analyze with image_url: [^\]]*\]\n?)?",
    re.S)


def attached_images(text: str) -> list[str]:
    """Descriptions of images the person attached to this message (Hermes auxiliary vision)."""
    return [m.group("desc").strip() for m in _IMAGE_NOTE.finditer(text or "")
            if m.group("desc").strip() and "analysis failed" not in m.group("desc").lower()]


def strip_image_notes(text: str) -> str:
    return _IMAGE_NOTE.sub("", text or "").strip()


def _image_result(tool_name, args, result, commands=("image-analyze",)) -> tuple[str, str]:
    """(description, question) if this tool call was an image analysis, else ("", "").
    `commands`: shell commands whose output is an image description (`image_commands` setting)."""
    args = args if isinstance(args, dict) else {}
    raw = result if isinstance(result, str) else json.dumps(result or "")
    if tool_name == "terminal":
        cmd = str(args.get("command") or "")
        hit = next((c for c in commands if c and c in cmd), None)
        if not hit:
            return "", ""
        try:
            d = json.loads(raw)
            out, code = str(d.get("output") or ""), d.get("exit_code", 0)
        except Exception:
            out, code = raw, 0
        if code not in (0, None) or not out.strip() or "vision API gave no answer" in out:
            return "", ""
        q = ""
        try:
            import shlex
            parts = shlex.split(cmd.split(hit, 1)[1].split("|")[0].split("&&")[0])
            q = parts[1] if len(parts) > 1 else ""
        except Exception:
            pass
        return out.strip(), q
    if tool_name == "vision_analyze":
        try:
            d = json.loads(raw)
            out = str(d.get("analysis") or d.get("result") or d.get("content") or "")
        except Exception:
            out = raw
        return (out.strip(), str(args.get("question") or "")) if out.strip() else ("", "")
    return "", ""


def _profile_name() -> str:
    try:
        from hermes_constants import get_hermes_home
        home = Path(get_hermes_home())
    except Exception:
        home = Path(os.environ.get("HERMES_HOME", Path.home() / ".hermes"))
    return home.name if home.parent.name == "profiles" else "default"


class Bridge:
    def __init__(self, settings_fn, ctx=None) -> None:
        self._settings_fn = settings_fn
        self.ctx = ctx
        self.sessions: dict[str, tuple[str, bool, str]] = {}   # session_id -> (speaker, private, platform)
        self.local_sessions: dict[str, tuple[str, float]] = {}  # speaker -> (desktop session_id, seen)
        self._warned = 0.0
        self._stop = threading.Event()
        self._thread = None

    # ---- config --------------------------------------------------------
    def cfg(self) -> dict:
        c = dict(DEFAULTS)
        try:
            c.update({k: v for k, v in (self._settings_fn() or {}).items() if v is not None})
        except Exception as exc:
            self._warn("settings", exc)
        if not c.get("agent"):
            c["agent"] = _profile_name()
        tok = ""
        if c.get("token_file"):
            try:
                tok = Path(c["token_file"]).read_text().strip()
            except Exception:
                tok = ""
        c["_token"] = tok or os.environ.get("LIMBIC_TOKEN", "")
        return c

    def _req(self, c: dict, method: str, path: str, body: dict | None, timeout: float) -> str:
        headers = {"Content-Type": "application/json"}
        if c["_token"]:
            headers["Authorization"] = f"Bearer {c['_token']}"
        data = json.dumps(body, ensure_ascii=False).encode() if body is not None else None
        url = f"{c['url'].rstrip('/')}/api/agents/{quote(c['agent'])}{path}"
        req = urllib.request.Request(url, data=data, method=method, headers=headers)
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.read().decode()

    def _warn(self, what: str, exc: Exception) -> None:
        now = time.time()
        if now - self._warned > 300:
            self._warned = now
            log.warning("limbic: %s failed (agent runs without feelings): %s", what, exc)

    def who(self, c: dict, sender_id) -> str:
        sid = str(sender_id or "")
        if sid in c["senders"]:
            return c["senders"][sid]
        return c["default_speaker"] if sid in ("", "None") else sid

    # ---- hooks -----------------------------------------------------------
    def pre_llm_call(self, session_id=None, user_message=None, platform=None, sender_id=None, **_):
        c = self.cfg()
        platform = (platform or "").lower()
        if not c["feelings_enabled"] or platform in c["skip_platforms"]:
            return None
        speaker = self.who(c, sender_id)
        private = platform in c["private_platforms"]
        if session_id:
            self.sessions[session_id] = (speaker, private, platform)
            if len(self.sessions) > 500:
                self.sessions.pop(next(iter(self.sessions)))
            if platform in LOCAL_PLATFORMS:
                self.local_sessions[speaker] = (session_id, time.time())
        if _text(user_message).lstrip().startswith(MARK):
            return None  # our own impulse already carries the inner state
        seen = []
        if c.get("images_enabled", True):
            for desc in attached_images(_text(user_message))[:4]:
                seen.append(self.see_now(c, desc, "", speaker, private))
        try:
            text = self._req(c, "GET", f"/prompt?lang={c['lang']}&interlocutor={quote(speaker)}&format=text",
                             None, c["prompt_timeout_s"])
        except Exception as exc:
            self._warn("prompt", exc)
            text = ""
        lang = c.get("image_lang") or c.get("lang", "en")
        extra = "\n\n".join(perception_text(d, f, lang) for d, f in seen)
        out = "\n\n".join(x for x in (text, extra) if x)
        return {"context": out} if out else None

    def see_now(self, c: dict, description: str, question: str, speaker: str, private: bool,
                tool_call_id: str | None = None) -> tuple[str, str]:
        """Feel an image synchronously (bounded); store a Hindsight memory in the background.
        Returns (description, how it felt)."""
        body = {"text": description[:4000], "question": (question or "")[:300], "speaker": speaker,
                "private": private}
        felt = ""
        try:
            res = json.loads(self._req(c, "POST", "/see", body, c.get("see_timeout_s", 20)) or "{}")
            felt = "; ".join(r["appraisal"].get("summary", "") for r in res.get("results", [])
                             if r.get("appraisal", {}).get("summary"))
        except Exception as exc:
            self._warn("image", exc)
        if c.get("image_memory") == "hindsight":
            def remember():
                try:
                    import hashlib
                    doc = "image-" + (tool_call_id or hashlib.sha1(body["text"].encode()).hexdigest()[:16])
                    retain_to_hindsight([image_memory_item(body["text"], body["question"], speaker,
                                                           felt, time.time(), doc)])
                except Exception as exc:
                    self._warn("image memory", exc)
            threading.Thread(target=remember, name="limbic-image-memory", daemon=True).start()
        return description, felt

    def post_llm_call(self, session_id=None, user_message=None, assistant_response=None, platform=None,
                      conversation_history=None, **_):
        c = self.cfg()
        platform = (platform or "").lower()
        if not c["feelings_enabled"] or platform in c["skip_platforms"]:
            return None
        text = strip_image_notes(_text(user_message))
        if not text and attached_images(_text(user_message)):
            text = "(sent an image)"
        speaker, private, _p = self.sessions.get(session_id or "", (c["default_speaker"],
                                                                     platform in c["private_platforms"], platform))
        channel = "desktop" if platform in LOCAL_PLATFORMS else platform
        impulse = text.startswith(MARK)
        if not text:
            return None
        body = None
        if not impulse:
            ctx_lines = []
            for m in (conversation_history or [])[-(2 * c["context_turns"] + 1):-1]:
                role = m.get("role") if isinstance(m, dict) else None
                if role in ("user", "assistant"):
                    t = _text(m).strip().replace("\n", " ")
                    if t and not t.startswith(MARK):
                        ctx_lines.append(f"{'agent' if role == 'assistant' else speaker}: {t[:300]}")
            reply = _text(assistant_response).strip()
            if reply:
                ctx_lines.append(f"agent (just answered): {reply[:400]}")
            body = {"text": text[:3000], "speaker": speaker, "private": private, "channel": channel,
                    "context": "\n".join(ctx_lines)[-2500:]}

        def work():
            try:
                if body is not None:
                    self._req(c, "POST", "/observe", body, c["observe_timeout_s"])
                self._req(c, "POST", "/reply", {}, 10)
            except Exception as exc:
                self._warn("observe", exc)

        threading.Thread(target=work, name="limbic-observe", daemon=True).start()
        return None

    # ---- seeing images -----------------------------------------------------
    def transform_tool_result(self, tool_name=None, args=None, result=None, session_id=None, status=None,
                              tool_call_id=None, **_):
        """An image analysis (an `image_commands` script via terminal, or vision_analyze) is what
        she SEES. Feel it first (limbic /see, synchronous, bounded), then hand the model the
        sight as her own perception plus what it makes her feel, instead of raw tool output."""
        try:
            c = self.cfg()
            if not c["feelings_enabled"] or not c.get("images_enabled", True):
                return None
            if status not in (None, "ok", "success", "completed"):
                return None
            text, question = _image_result(tool_name, args, result, tuple(c.get("image_commands") or ()))
            if not text:
                return None
            speaker, private, platform = self.sessions.get(session_id or "", (c["default_speaker"], False, ""))
            if platform in c["skip_platforms"]:
                return None
        except Exception as exc:
            self._warn("image", exc)
            return None
        _d, felt = self.see_now(c, text, question, speaker, private, tool_call_id)
        return perception_text(text, felt, c.get("image_lang") or c.get("lang", "en"))

    # ---- autonomy --------------------------------------------------------
    def host_channels(self) -> set[str]:
        mgr = getattr(self.ctx, "_manager", None)
        out = set()
        if mgr is not None and getattr(mgr, "has_gateway_message_injector", False):
            out |= GATEWAY_PLATFORMS
        if mgr is not None and getattr(mgr, "has_tui_message_injector", False):
            out.add("desktop")
        return out

    def choose_channel(self, c: dict, it: dict, now: float) -> str | None:
        """The channel this host should deliver the intent on, or None."""
        can = self.host_channels()
        person = it.get("target") or ""
        routes = c["routes"].get(person, {}) if isinstance(c["routes"], dict) else {}

        def available(ch: str) -> bool:
            if ch == "desktop":
                s = self.local_sessions.get(person)
                return "desktop" in can and s is not None
            return ch in can and ch in routes

        prefs = [p for p in c["channel_preference"] if isinstance(p, str)]
        first = it.get("channel") if c["channel_strategy"] == "last_active" and it.get("channel") in prefs else None
        order = ([first] if first else []) + [p for p in prefs if p != first]
        waited = (now - it.get("created", now)) / 60.0
        # the intent belongs to the first channel in order whose *host* exists somewhere;
        # we only take it if that is us, or if it waited longer than the fallback
        for n, ch in enumerate(order):
            if available(ch):
                return ch if (n == 0 or waited >= c["fallback_after_min"]) else None
            if ch == order[0] and waited < c["fallback_after_min"]:
                return None  # give the preferred host its chance first
        return None

    def deliver(self, c: dict, it: dict, channel: str, instruction: str) -> bool:
        if self.ctx is None:
            return False
        content = (f"{MARK}{it['kind']} #{it['id']}⟧ Ez a TE belső késztetésed, nem {it['target']} írta. "
                   f"Írj neki most magadtól, a saját hangodon.\n\n{instruction}"
                   if c["lang"] == "hu" else
                   f"{MARK}{it['kind']} #{it['id']}⟧ This is YOUR own impulse; {it['target']} did not write it. "
                   f"Message them now, in your own voice.\n\n{instruction}")
        if channel == "desktop":
            sid = self.local_sessions.get(it["target"], ("", 0))[0]
            return bool(sid) and bool(self.ctx.inject_message(content, role="user", session_key=sid))
        route = dict(c["routes"][it["target"]][channel])
        origin = {"platform": channel, "chat_id": str(route["chat_id"]),
                  "chat_type": route.get("chat_type", "dm"),
                  "user_id": str(route.get("user_id", route["chat_id"])),
                  "user_name": it["target"]}
        if route.get("thread_id"):
            origin["thread_id"] = str(route["thread_id"])
        return bool(self.ctx.inject_message(content, role="user", origin=origin))

    def autonomy_tick(self) -> list[dict]:
        c = self.cfg()
        if not c["autonomy_enabled"] or not self.host_channels():
            return []
        now = time.time()
        sent = []
        try:
            self._req(c, "POST", "/intents/evaluate", {}, 10)
            pending = json.loads(self._req(c, "GET", "/intents?status=pending", None, 10))
        except Exception as exc:
            self._warn("intents", exc)
            return []
        for it in sorted(pending, key=lambda i: -i.get("urgency", 0)):
            ch = self.choose_channel(c, it, now)
            if not ch:
                continue
            try:
                res = json.loads(self._req(c, "POST", f"/intents/{it['id']}/claim?lang={c['lang']}", {}, 10))
            except urllib.error.HTTPError:
                continue  # another host claimed it first
            except Exception as exc:
                self._warn("claim", exc)
                continue
            try:
                ok = self.deliver(c, it, ch, res["instruction"])
            except Exception as exc:
                self._warn("deliver", exc)
                ok = False
            if not ok:
                # never let a delivery failure look like "they ignored me"
                try:
                    self._req(c, "POST", f"/intents/{it['id']}/dismiss", {}, 10)
                except Exception:
                    pass
            log.info("limbic: %s #%s -> %s via %s: %s", it["kind"], it["id"], it["target"], ch,
                     "sent" if ok else "NOT delivered")
            sent.append({"id": it["id"], "channel": ch, "ok": ok})
        return sent

    def start(self) -> None:
        if self._thread is not None:
            return

        def loop():
            time.sleep(15)  # let the host finish starting
            while not self._stop.is_set():
                try:
                    self.autonomy_tick()
                except Exception as exc:  # never die
                    self._warn("autonomy", exc)
                interval = max(15, int(self.cfg().get("poll_interval_s") or 60))
                self._stop.wait(interval)

        self._thread = threading.Thread(target=loop, name="limbic-autonomy", daemon=True)
        self._thread.start()


def register(ctx):
    def settings():
        out = {}
        for k in DEFAULTS:
            try:
                v = ctx.get_config(k, None)
            except Exception:
                v = None
            if v is not None:
                out[k] = v
        return out

    bridge = Bridge(settings, ctx)
    ctx.register_hook("pre_llm_call", bridge.pre_llm_call)
    ctx.register_hook("post_llm_call", bridge.post_llm_call)
    ctx.register_hook("transform_tool_result", bridge.transform_tool_result)
    bridge.start()
    return bridge
