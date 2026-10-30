"""Command line: serve the API/dashboard, seed a demo, poke an agent."""
from __future__ import annotations

import argparse
import json
import sys

from . import __version__


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="limbic", description="artificial emotion system for agents")
    ap.add_argument("--version", action="version", version=__version__)
    sub = ap.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("serve", help="run the HTTP API + dashboard")
    s.add_argument("--db", default="limbic.db")
    s.add_argument("--host", default="127.0.0.1")
    s.add_argument("--port", type=int, default=8770)
    s.add_argument("--token", default=None, help="bearer token for write endpoints")
    s.add_argument("--autonomy-interval", type=float, default=60.0,
                   help="seconds between autonomy evaluations (0 = off)")

    r = sub.add_parser("outreach", help="claim one pending intent via the HTTP API and print its instruction, or {\"wakeAgent\": false}")
    r.add_argument("agent")
    r.add_argument("--url", default="http://127.0.0.1:8770")
    r.add_argument("--token-file", default=None)
    r.add_argument("--lang", default="en")
    r.add_argument("--kinds", default="reach_out,check_in")

    d = sub.add_parser("demo", help="simulate a few weeks of an agent's life into the db")
    d.add_argument("--db", default="limbic.db")
    d.add_argument("--agent", default="demo")
    d.add_argument("--days", type=int, default=21)
    d.add_argument("--seed", type=int, default=7)

    p = sub.add_parser("prompt", help="print the prompt block for an agent")
    p.add_argument("agent")
    p.add_argument("--db", default="limbic.db")
    p.add_argument("--lang", default="en")
    p.add_argument("--interlocutor", default=None)

    o = sub.add_parser("observe", help="appraise and feel a message")
    o.add_argument("agent")
    o.add_argument("text")
    o.add_argument("--speaker", default=None)
    o.add_argument("--db", default="limbic.db")

    c = sub.add_parser("consolidate", help="run the periodic memory consolidation")
    c.add_argument("agent")
    c.add_argument("--db", default="limbic.db")

    args = ap.parse_args(argv)

    if args.cmd == "serve":
        from .server import serve
        httpd = serve(args.db, args.host, args.port, args.token, args.autonomy_interval)
        print(f"limbic {__version__} on http://{args.host}:{args.port}  (db: {args.db})", flush=True)
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            pass
        return 0
    if args.cmd == "outreach":
        return outreach(args)
    if args.cmd == "demo":
        from .demo import run_demo
        print(json.dumps(run_demo(args.db, args.agent, args.days, args.seed), ensure_ascii=False, indent=2))
        return 0

    from .agent import Limbic
    from .server import appraiser_factory_from_env
    from .store import Store
    store = Store(args.db)
    if store.get_agent(args.agent) is None and args.cmd != "observe":
        print(f"unknown agent {args.agent}", file=sys.stderr)
        return 1
    mind = Limbic(args.agent, store=store)
    factory = appraiser_factory_from_env()
    if factory:
        mind.appraiser = factory(mind.temperament)
    if args.cmd == "prompt":
        print(mind.prompt(lang=args.lang, interlocutor=args.interlocutor))
    elif args.cmd == "observe":
        print(json.dumps(mind.observe(args.text, speaker=args.speaker), ensure_ascii=False, indent=2))
    elif args.cmd == "consolidate":
        print(json.dumps(mind.consolidate(), ensure_ascii=False, indent=2))
    return 0


def outreach(args) -> int:
    import urllib.request
    from urllib.parse import quote
    token = open(args.token_file).read().strip() if args.token_file else None
    base = f"{args.url.rstrip('/')}/api/agents/{quote(args.agent)}"

    def call(method: str, path: str):
        req = urllib.request.Request(base + path, method=method, data=b"{}" if method == "POST" else None,
                                     headers={"Content-Type": "application/json",
                                              **({"Authorization": f"Bearer {token}"} if token else {})})
        with urllib.request.urlopen(req, timeout=20) as r:
            return json.loads(r.read().decode())

    try:
        call("POST", "/intents/evaluate")
        kinds = set(args.kinds.split(","))
        pending = [i for i in call("GET", "/intents?status=pending") if i["kind"] in kinds]
    except Exception as exc:
        print(f"limbic unreachable: {exc}", file=sys.stderr)
        print(json.dumps({"wakeAgent": False}))
        return 0
    if not pending:
        print(json.dumps({"wakeAgent": False}))
        return 0
    it = max(pending, key=lambda i: i.get("urgency", 0))
    res = call("POST", f"/intents/{it['id']}/claim?lang={args.lang}")
    meta = res["intent"]
    print(f"[limbic intent #{meta['id']} {meta['kind']} -> {meta['target']}"
          + (f" via {meta['contact']}" if meta.get("contact") else "") + "]")
    print(res["instruction"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
