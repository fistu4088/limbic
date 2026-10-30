"""Export Hindsight memories as JSONL for `limbic replay`.

    python contrib/hindsight/export.py --api http://127.0.0.1:8888 --bank my-bank \
        --type experience --out data/my_experience.jsonl
"""
import argparse
import json
import sys
import urllib.request
from datetime import datetime


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--api", required=True)
    ap.add_argument("--bank", required=True)
    ap.add_argument("--type", default="experience")
    ap.add_argument("--out", required=True)
    ap.add_argument("--page", type=int, default=500)
    a = ap.parse_args()
    base = f"{a.api.rstrip('/')}/v1/default/banks/{a.bank}/memories/list"
    offset, n = 0, 0
    with open(a.out, "w", encoding="utf-8") as f:
        while True:
            with urllib.request.urlopen(f"{base}?type={a.type}&limit={a.page}&offset={offset}", timeout=60) as r:
                d = json.loads(r.read().decode())
            items = d.get("items") or []
            for it in items:
                ts = it.get("occurred_start") or it.get("mentioned_at") or it.get("date")
                if not ts:
                    continue
                t = datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp()
                text = (it.get("text") or "").split(" | When:")[0].strip()
                f.write(json.dumps({"id": it["id"], "t": t, "date": ts, "text": text}, ensure_ascii=False) + "\n")
                n += 1
            offset += len(items)
            if not items or offset >= d.get("total", 0):
                break
    print(f"exported {n} {a.type} memories to {a.out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
