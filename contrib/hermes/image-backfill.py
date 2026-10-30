#!/usr/bin/env python3
"""Add images an agent analysed in the past (image-command / vision_analyze tool calls in a
Hermes state.db) to limbic as remembered image experiences (POST /see with `at`).

Content is never printed. Already-imported tool calls are remembered in --done.

  image-backfill.py --state-db ~/.hermes/state.db --url http://host:8770 --agent NAME \
      --token-file TOKENFILE [--commands my-analyze] [--senders '{"123":"Name"}'] [--default-speaker Name]
      [--private telegram] [--exclude-session ID ...] [--exclude-window START END ...] [--apply]
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import pathlib
import sqlite3
import sys
import time
import urllib.request

HERE = pathlib.Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("limbic_hermes_plugin", HERE / "plugin" / "__init__.py")
plugin = importlib.util.module_from_spec(spec)
spec.loader.exec_module(plugin)


def collect(db_path: str, commands: tuple[str, ...]) -> list[dict]:
    db = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    db.row_factory = sqlite3.Row
    sessions = {r["id"]: r for r in db.execute("SELECT id, source, user_id FROM sessions")}
    results = {}
    for r in db.execute("SELECT tool_call_id, content, timestamp FROM messages WHERE role='tool' AND tool_call_id IS NOT NULL"):
        results[r["tool_call_id"]] = (r["content"], r["timestamp"])
    out, seen = [], set()
    q = ("SELECT session_id, tool_calls, timestamp FROM messages WHERE role='assistant' AND tool_calls IS NOT NULL "
         "ORDER BY timestamp")
    for r in db.execute(q):
        if not any(c in r["tool_calls"] for c in commands + ("vision_analyze",)):
            continue
        try:
            calls = json.loads(r["tool_calls"])
        except Exception:
            continue
        for call in calls if isinstance(calls, list) else []:
            fn = (call.get("function") or {}) if isinstance(call, dict) else {}
            name = fn.get("name") or call.get("name")
            try:
                args = json.loads(fn.get("arguments") or "{}") if isinstance(fn.get("arguments"), str) else (fn.get("arguments") or {})
            except Exception:
                args = {}
            cid = call.get("id") or call.get("tool_call_id")
            if not cid or cid not in results:
                continue
            content, ts = results[cid]
            text, question = plugin._image_result(name, args, content, commands)
            if not text:
                continue
            key = (cid, text[:200])
            if cid in seen or key in seen:
                continue   # the same call copied into another session (compression / branch)
            seen.update({cid, key})
            s = sessions.get(r["session_id"])
            out.append({"id": cid, "session": r["session_id"], "t": float(ts or r["timestamp"]),
                        "platform": (s["source"] if s else "") or "", "user": (s["user_id"] if s else "") or "",
                        "text": text, "question": question})
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--state-db", required=True)
    ap.add_argument("--url", required=True)
    ap.add_argument("--agent", required=True)
    ap.add_argument("--token-file")
    ap.add_argument("--commands", nargs="*", default=["image-analyze"])
    ap.add_argument("--senders", default="{}", help="JSON, or @file with JSON")
    ap.add_argument("--default-speaker", default="user")
    ap.add_argument("--private", nargs="*", default=["telegram"])
    ap.add_argument("--exclude-session", nargs="*", default=[])
    ap.add_argument("--exclude-window", nargs=2, action="append", type=float, default=[])
    ap.add_argument("--skip-platforms", nargs="*", default=["cron", "kanban", "subagent"])
    ap.add_argument("--until", type=float, default=None, help="ignore analyses after this epoch")
    ap.add_argument("--done", default="image-backfill-done.json")
    ap.add_argument("--past-weight", type=float, default=0.3)
    ap.add_argument("--hindsight", action="store_true",
                    help="also store each image as a Hindsight memory (profile config: $HERMES_HOME/hindsight/config.json)")
    ap.add_argument("--hindsight-done", default="image-backfill-hindsight-done.json")
    ap.add_argument("--last", type=int, default=0, help="only the N most recent analyses")
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args()

    senders = json.loads(pathlib.Path(a.senders[1:]).read_text() if a.senders.startswith("@") else a.senders)
    done_p = pathlib.Path(a.done)
    done = set(json.loads(done_p.read_text())) if done_p.exists() else set()
    items = collect(a.state_db, tuple(a.commands))
    hd_p = pathlib.Path(a.hindsight_done)
    hdone = set(json.loads(hd_p.read_text())) if hd_p.exists() else set()
    keep, skipped = [], {"done": 0, "excluded": 0}
    for it in items:
        if it["id"] in done and (not a.hindsight or it["id"] in hdone):
            skipped["done"] += 1
        elif (it["session"] in a.exclude_session or any(s <= it["t"] <= e for s, e in a.exclude_window)
              or it["platform"] in a.skip_platforms or (a.until and it["t"] > a.until)):
            skipped["excluded"] += 1
        else:
            keep.append(it)
    if a.last:
        keep = keep[-a.last:]
    by = {}
    for it in keep:
        by[it["platform"] or "?"] = by.get(it["platform"] or "?", 0) + 1
    span = (time.strftime("%m-%d %H:%M", time.localtime(keep[0]["t"])),
            time.strftime("%m-%d %H:%M", time.localtime(keep[-1]["t"]))) if keep else ("-", "-")
    print(f"found {len(items)} image analyses; to import {len(keep)} ({span[0]} .. {span[1]}); "
          f"by platform {by}; skipped {skipped}")
    if not a.apply:
        print("dry run - nothing written (add --apply)")
        return 0
    tok = pathlib.Path(a.token_file).read_text().strip() if a.token_file else ""
    H = {"Content-Type": "application/json"}
    if tok:
        H["Authorization"] = f"Bearer {tok}"
    ok = fail = 0
    for i, it in enumerate(keep, 1):
        speaker = senders.get(str(it["user"]), a.default_speaker if it["user"] in ("", "None") else str(it["user"]))
        body = {"text": it["text"][:4000], "question": it["question"][:300], "speaker": speaker,
                "private": it["platform"] in a.private, "at": it["t"]}
        req = urllib.request.Request(f"{a.url.rstrip('/')}/api/agents/{a.agent}/see",
                                     data=json.dumps(body).encode(), headers=H, method="POST")
        try:
            res, evs = {}, "already felt"
            if it["id"] not in done:
                with urllib.request.urlopen(req, timeout=180) as r:
                    res = json.loads(r.read())
                done.add(it["id"])
                done_p.write_text(json.dumps(sorted(done)))
                evs = ",".join(x["appraisal"]["event"] for x in res.get("results", []))
            mem = ""
            if a.hindsight and it["id"] not in hdone:
                felt = "; ".join(x["appraisal"].get("summary", "") for x in res.get("results", [])
                                 if x.get("appraisal", {}).get("summary"))
                plugin.retain_to_hindsight([plugin.image_memory_item(
                    it["text"], it["question"], speaker, felt, it["t"], "image-" + it["id"])])
                hdone.add(it["id"])
                hd_p.write_text(json.dumps(sorted(hdone)))
                mem = " +memory"
            ok += 1
            print(f"[{i}/{len(keep)}] {time.strftime('%m-%d %H:%M', time.localtime(it['t']))} -> {evs}{mem}"
                  + (" (fallback appraiser)" if res.get("appraiser_error") else ""), flush=True)
        except Exception as exc:
            fail += 1
            print(f"[{i}/{len(keep)}] failed: {type(exc).__name__}", flush=True)
    print(f"done: {ok} imported, {fail} failed")
    return 0 if not fail else 1


if __name__ == "__main__":
    sys.exit(main())
