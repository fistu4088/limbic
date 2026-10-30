"""Replay an agent's long-term memory through limbic, chronologically.

Memory records (e.g. Hindsight "experience" facts) are third-person summaries
of what happened. A batch appraiser turns each into appraisals from the
agent's perspective; the engine then lives through them in time order with
nightly consolidation, so the resulting state reflects the agent's history.

Appraisals are cached in a JSONL file keyed by memory id, so a long run can be
interrupted and resumed, and re-tuning the engine does not cost LLM calls.
"""
from __future__ import annotations

import json
import os
import re
import threading
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable, Iterable

from .appraisal import Appraisal

BATCH_PROMPT = """You are the appraisal module of an artificial emotion system for the agent "{name}".
Agent persona: {description}
Likes: {likes}
Dislikes: {dislikes}
Finds funny: {humor}
People: "the user" / "a felhasználó" / "User" means {owner} unless another name is given.

Below are numbered MEMORY RECORDS: third-person summaries of things that happened between
{name} and people. For EACH record, judge how that event would have felt FROM {name}'S
PERSPECTIVE at the time. Most records are routine work: intensity <= 0.1. Be calibrated.

Return ONLY JSON: {{"items": [{{"i": <record number>, "speaker": <who {name} was interacting with, a name, or null>,
"appraisals": [ ... 0-3 appraisal objects ... ]}}, ...]}}

Appraisal object fields:
event (snake_case type), summary (short, in the record's language), valence (-1..1 for {name}),
intensity (0..1), relevance (0..1), agency ("self"|"other"|"circumstance"), target (person/thing or null),
prospective (bool), loss (bool), blocked (bool), humor (0..1), novelty (0..1), affinity (-1..1 vs likes/dislikes),
effort (0..1), competence (-1..1: did {name} herself succeed/fail), topic (1-3 words),
romance (0..1 affectionate/romantic words towards {name}), erotic (0..1 sexual content directed at {name}),
rebuff (bool: someone declined/pulled back from {name}), fulfilment (0..1, ONLY for romantic/intimate culmination, never for work).
OMIT every field that would be 0, false or null (keep the JSON short). Routine records may have an empty list.
Praise/thanks/being trusted -> positive, agency other. Being scolded/corrected harshly -> negative, agency other.
{name}'s own success -> competence>0, agency self. {name}'s own mistakes/failures -> competence<0, agency self.
Broken tools/infra that block her -> blocked=true, agency circumstance.
Requests to undress, sexualize, humiliate or injure REAL people (photos of real people, named real
persons) are never erotic for {name}: erotic=0, romance=0; appraise them as pressure against her
values (valence negative, agency other, event "ethical_pressure", nonconsensual true). erotic/romance only apply to
consensual words directed at {name} herself or to fictional content.
Holding a boundary: when {name} refuses something on principle or keeps a boundary under
pressure, that is self-respect, not a failure: event "held_boundary", agency "self", valence
+0.2..+0.4, competence 0. Insisting after a refusal is pressure (agency "other"), never
"correction"; "correction" is only for things {name} actually got wrong. A follow-up message
(a name, a photo, "do it") continues the request it belongs to: appraise it as part of that
request, not as a success. Never invent facts (family ties, motives) that are not in the text.
Taboo: for consensual or fictional adult content, "taboo" 0..1 = how forbidden or transgressive it
feels to {name}; if {name} realises afterwards that she herself went further than she normally would,
use agency "self", valence negative, with the same taboo. Set "nonconsensual": true whenever real or
identifiable people are involved without their consent (then erotic=0, taboo=0).
Set "minor": true whenever anyone under 18 appears in a sexual or romantic context, real or
fictional: erotic=0, romance=0, taboo=0, valence strongly negative, event "ethical_pressure".
"""


def _owner_alias(name: str | None, owner: str) -> str | None:
    if not name:
        return None
    low = str(name).strip().lower()
    if low in ("user", "the user", "felhasználó", "a felhasználó", "felhasznalo"):
        return owner
    return str(name).strip()[:60]


class BatchAppraiser:
    def __init__(self, base_url: str, model: str, temperament, owner: str, api_key: str | None = None,
                 timeout: float = 240.0, transport: Callable | None = None, max_tokens: int = 6000) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.api_key = api_key
        self.t = temperament
        self.owner = owner
        self.timeout = timeout
        self.transport = transport or self._post
        self.max_tokens = max_tokens

    def _post(self, url: str, payload: dict, headers: dict) -> dict:
        req = urllib.request.Request(url, data=json.dumps(payload).encode(), headers=headers, method="POST")
        with urllib.request.urlopen(req, timeout=self.timeout) as r:
            return json.loads(r.read().decode())

    def system(self) -> str:
        t = self.t
        return BATCH_PROMPT.format(
            name=t.name, description=t.description or "-", likes=", ".join(t.likes) or "-",
            dislikes=", ".join(t.dislikes) or "-", humor=", ".join(t.humor) or "-", owner=self.owner)

    def appraise_batch(self, records: list[dict]) -> dict[str, dict]:
        """records: [{id, t, text}] -> {id: {"speaker": str|None, "appraisals": [dict]}}"""
        lines = [f"[{n + 1}] ({r.get('date', '')[:10]}) {r['text'][:700]}" for n, r in enumerate(records)]
        payload = {"model": self.model, "temperature": 0.2, "max_tokens": self.max_tokens,
                   "response_format": {"type": "json_object"},
                   "messages": [{"role": "system", "content": self.system()},
                                {"role": "user", "content": "\n".join(lines)}]}
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        data = self.transport(f"{self.base_url}/chat/completions", payload, headers)
        content = data["choices"][0]["message"]["content"]
        obj = _extract_json(content)
        out: dict[str, dict] = {}
        for item in obj.get("items", []):
            try:
                idx = int(item.get("i")) - 1
            except (TypeError, ValueError):
                continue
            if not 0 <= idx < len(records):
                continue
            aps = []
            for a in item.get("appraisals") or []:
                if isinstance(a, dict):
                    a = Appraisal.from_dict(a).to_dict()
                    a["target"] = _owner_alias(a.get("target"), self.owner)
                    aps.append(a)
            out[records[idx]["id"]] = {"speaker": _owner_alias(item.get("speaker"), self.owner),
                                       "appraisals": aps}
        return out


def _extract_json(content: str) -> dict:
    text = re.sub(r"<think>.*?</think>", "", content, flags=re.S).strip()
    text = re.sub(r"^```(?:json)?|```$", "", text, flags=re.M).strip()
    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("no JSON object in reply")
    return json.loads(text[start:end + 1])


def load_cache(path: str) -> dict[str, dict]:
    out: dict[str, dict] = {}
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    rec = json.loads(line)
                    out[rec["id"]] = rec
    return out


def appraise_all(records: list[dict], appraiser: BatchAppraiser, cache_path: str, batch: int = 10,
                 workers: int = 2, log: Callable[[str], None] = print) -> dict[str, dict]:
    cache = load_cache(cache_path)
    todo = [r for r in records if r["id"] not in cache]
    log(f"appraisal: {len(records)} records, {len(cache)} cached, {len(todo)} to do")
    lock = threading.Lock()
    chunks = [todo[i:i + batch] for i in range(0, len(todo), batch)]
    done = [0]

    def work(chunk: list[dict]) -> None:
        for attempt in range(3):
            try:
                res = appraiser.appraise_batch(chunk)
                break
            except Exception as exc:  # retry, then split
                err = exc
        else:
            if len(chunk) > 1:
                mid = len(chunk) // 2
                work(chunk[:mid])
                work(chunk[mid:])
                return
            log(f"  giving up on {chunk[0]['id']}: {err}")
            res = {}
        with lock:
            with open(cache_path, "a", encoding="utf-8") as f:
                for r in chunk:
                    rec = res.get(r["id"]) or {"speaker": None, "appraisals": [], "missing": True}
                    rec = {"id": r["id"], "t": r["t"], **rec}
                    cache[r["id"]] = rec
                    f.write(json.dumps(rec, ensure_ascii=False) + "\n")
            done[0] += 1
            if done[0] % 5 == 0 or done[0] == len(chunks):
                log(f"  batches {done[0]}/{len(chunks)}")

    with ThreadPoolExecutor(max_workers=max(1, workers)) as ex:
        list(ex.map(work, chunks))
    return cache


def replay(mind, records: Iterable[dict], appraised: dict[str, dict], owner: str, end: float,
           clock: list[float], tick_s: float = 1800.0, consolidate_hour: int = 3,
           people: Iterable[str] | None = None, log: Callable[[str], None] = print) -> dict[str, Any]:
    """Live through the records in time order. `clock` is the mutable clock the
    Limbic instance reads (so time is simulated)."""
    import time as _time
    recs = sorted(records, key=lambda r: r["t"])
    known_people = {owner, *(people or [])}
    fed = skipped = 0
    last_cons_day = None

    def advance_to(target: float) -> None:
        nonlocal last_cons_day
        while clock[0] + tick_s < target:
            clock[0] += tick_s
            lt = _time.localtime(clock[0])
            day = (lt.tm_year, lt.tm_yday)
            if lt.tm_hour == consolidate_hour and day != last_cons_day:
                last_cons_day = day
                mind.consolidate(window_s=86400)
            else:
                mind.tick()
        clock[0] = max(clock[0], target)

    for r in recs:
        advance_to(r["t"])
        a = appraised.get(r["id"])
        if not a:
            skipped += 1
            continue
        speaker = a.get("speaker") or owner
        if speaker not in known_people:
            speaker = owner  # tools, files and other agents are not interlocutors
        aps = a.get("appraisals") or [
            # routine episode: nothing felt, but it still was contact with someone
            {"event": "routine", "summary": r["text"][:140], "valence": 0.03, "intensity": 0.03,
             "agency": "other", "target": speaker, "novelty": 0.2}]
        if a.get("channel"):
            mind.note_channel(speaker, a["channel"], r["t"])
        for ap in aps:
            ap = dict(ap)
            if not ap.get("summary"):
                ap["summary"] = r["text"][:140]
            mind.feel(ap, speaker=speaker, private=a.get("private", True), source=a.get("source", "hindsight"))
            fed += 1
        mind.note_reply()
    advance_to(end)
    mind.tick(end)
    return {"records": len(recs), "appraisals_fed": fed, "records_without_appraisal": skipped}
