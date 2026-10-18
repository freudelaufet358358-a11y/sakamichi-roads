#!/usr/bin/env python3
"""地域ごとの meta.json（site/{地域}/meta.json）を、アプリが最初に読む 1 つの site/meta.json にまとめる。

site/meta.json:
  {"version": 1, "built": "…", "cellLat": 0.03, "cellLon": 0.04,
   "regions": {"chugoku": {"built": "…", "cells": ["1140_3288", …]}, …}}
アプリは区画がどの地域に入っているかを見て、site/{地域}/cells/{区画}.json を読む（どこにも無ければ Overpass）
"""

import datetime
import json
import os
import sys


def main():
    site = sys.argv[1]
    regions = sys.argv[2:]
    out = {
        "version": 1,
        "built": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "cellLat": 0.03,
        "cellLon": 0.04,
        "regions": {},
    }
    seen = set()
    for r in regions:
        with open(os.path.join(site, r, "meta.json"), encoding="utf-8") as f:
            m = json.load(f)
        # 地域の境界が少し重なるときは、先に書いた地域の方を使う
        cells = [c for c in m["cells"] if c not in seen]
        seen.update(cells)
        out["regions"][r] = {"built": m["built"], "cells": cells}
        print(f"{r}: {len(cells)} 区画")
    with open(os.path.join(site, "meta.json"), "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, separators=(",", ":"))


if __name__ == "__main__":
    main()
