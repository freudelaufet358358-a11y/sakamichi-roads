#!/usr/bin/env python3
"""坂道サイクルマップ用の道路データ（区画ごとのファイル）を、OSM の地域データ（.osm.pbf）から作る。

アプリは道路データを「区画」（緯度 0.03° × 経度 0.04°）ごとに読み込む。これまでは Overpass API から
取っていたが遅いので、前もって作っておき、GitHub Pages から配る。

全国は 1 回で読むとメモリが足りないので、地方ごと（Geofabrik の地域）に 2 段階で作る:
  1. extract: 地方の .osm.pbf から、その地方の道が通る区画ごとの中間ファイルを作る（work/{地方}/）
  2. merge:   全地方の中間ファイルを区画ごとに合わせる（地方の境目の区画は、両方の地方の道を合わせると欠けがなくなる）。
              日本全体の境界（japan.poly）の中に丸ごと入る区画を配る

出力（merge の --out の下）:
  {名前}/cells/{i}_{j}.b  道（way）と、その点（node）と信号。下の「区画のファイルの形」を raw DEFLATE で縮めたもの
  {名前}/names/{i}_{j}.z  名前の付いた信号・交差点（案内で「◯◯交差点を右」と言うため）。JSON を raw DEFLATE で縮めたもの
  meta.json               作った日時と、配っている区画（行ごとの範囲）。道の無い区画（海など）はファイルを置かない
                          → アプリは、配っている区画でファイルが無ければ「道なし」とみなす

区画のファイルの形（SKC1。数は可変長、差分は zigzag の可変長）:
  "SKC1"
  文字列の数, (長さ, UTF-8)…                         タグのキー・値
  点の数, (id の差分, 緯度の差分, 経度の差分)…          緯度・経度は 1e-7 度の整数。差分は前の点から
  道の数, (id の差分, 点の数, (点の番号の差分)…, タグの数, (キーの番号, 値の番号)…)…
  信号の数, (点の番号の差分)…

使い方:
  osmium tags-filter chugoku.osm.pbf w/highway n/highway=traffic_signals n/junction=yes -o chugoku-roads.osm.pbf
  python build_cells.py extract chugoku-roads.osm.pbf --out work/chugoku
  python build_cells.py merge --poly japan.poly --work work --out site --name japan chugoku kansai …
"""

import argparse
import array
import bisect
import datetime
import json
import math
import os
import sys
import zlib

# アプリ（RoadEngine.cellLat / cellLon）と同じ値
CELL_LAT = 0.03
CELL_LON = 0.04

# アプリの Overpass の問い合わせと同じ道の種類
HIGHWAYS = set(
    "trunk trunk_link primary primary_link secondary secondary_link tertiary tertiary_link unclassified "
    "residential living_street service track path cycleway footway pedestrian bridleway steps road busway".split()
)

# アプリが読むタグだけ残す（ファイルを小さくする）
KEEP_TAGS = {
    "highway", "name", "ref", "surface", "tracktype", "smoothness", "bicycle", "foot", "access",
    "oneway", "oneway:bicycle", "junction", "cycleway", "cycleway:both", "cycleway:left", "cycleway:right",
    "lanes", "maxspeed", "motorroad", "bridge", "tunnel", "lit", "service",
}


def cell_of(lat, lon):
    return (math.floor(lat / CELL_LAT), math.floor(lon / CELL_LON))


def touched_cells(lats, lons):
    """道が通る区画。点がある区画と、点と点の間の線が横切る区画（アプリの Overpass.split と同じ考え方）"""
    out = set()
    prev = None
    for la, lo in zip(lats, lons):
        out.add(cell_of(la, lo))
        if prev is not None:
            pla, plo = prev
            steps = math.ceil(max(abs(la - pla) / (CELL_LAT / 8), abs(lo - plo) / (CELL_LON / 8)))
            for t in range(1, steps):
                f = t / steps
                out.add(cell_of(pla + (la - pla) * f, plo + (lo - plo) * f))
        prev = (la, lo)
    return out


def keep_way(tags):
    """Overpass の way["highway"~"^(…)$"]["area"!="yes"]["service"!~"^(parking_aisle|drive-through)$"] と同じ"""
    if tags.get("highway") not in HIGHWAYS:
        return False
    if tags.get("area") == "yes":
        return False
    if tags.get("service") in ("parking_aisle", "drive-through"):
        return False
    return True


def deflate(data, level=9):
    """raw DEFLATE（ヘッダーなし）。iOS の NSData.decompressed(using: .zlib) でそのまま戻せる形"""
    c = zlib.compressobj(level, zlib.DEFLATED, -15)
    return c.compress(data) + c.flush()


def inflate(data):
    return zlib.decompress(data, -15)


# MARK: - 境界（Geofabrik の .poly）


def read_poly(path):
    """輪（外側も穴も。中か外かは偶奇で決める）。座標は (lon, lat)"""
    rings = []
    with open(path, encoding="utf-8") as f:
        lines = [l.strip() for l in f]
    i = 1  # 1 行目は名前
    while i < len(lines):
        name = lines[i]
        i += 1
        if name == "END" or name == "":
            continue
        ring = []
        while i < len(lines) and lines[i] != "END":
            parts = lines[i].split()
            if len(parts) >= 2:
                ring.append((float(parts[0]), float(parts[1])))
            i += 1
        i += 1
        rings.append(ring)
    return rings


def covered_cells(rings):
    """境界の中に丸ごと入る区画。境界の線が通る区画（とその隣）を除き、残りは区画の真ん中が中にあるかで決める。
    区画を 1 つずつ境界と比べると全国では遅すぎるので、行ごとに境界の線との交点を求めて数える"""
    edges = [(r[k - 1], r[k]) for r in rings for k in range(len(r))]
    # 境界の線が通る区画（線の上を 1/4 区画ごとにたどり、念のため隣の区画も）
    boundary = set()
    for (x0, y0), (x1, y1) in edges:
        n = max(1, math.ceil(max(abs(x1 - x0) / (CELL_LON / 4), abs(y1 - y0) / (CELL_LAT / 4))))
        for t in range(n + 1):
            f = t / n
            i, j = cell_of(y0 + (y1 - y0) * f, x0 + (x1 - x0) * f)
            for di in (-1, 0, 1):
                for dj in (-1, 0, 1):
                    boundary.add((i + di, j + dj))
    ys = [p[1] for r in rings for p in r]
    xs = [p[0] for r in rings for p in r]
    i0, j0 = cell_of(min(ys), min(xs))
    i1, j1 = cell_of(max(ys), max(xs))
    out = []
    for i in range(i0, i1 + 1):
        y = (i + 0.5) * CELL_LAT
        cross = sorted(x0 + (y - y0) * (x1 - x0) / (y1 - y0) for (x0, y0), (x1, y1) in edges if (y0 > y) != (y1 > y))
        for j in range(j0, j1 + 1):
            if (i, j) in boundary:
                continue
            if bisect.bisect_left(cross, (j + 0.5) * CELL_LON) % 2 == 1:
                out.append((i, j))
    return out


def rows_of(cells):
    """区画の一覧 → 行ごとの範囲 {"1140": [始め, 終わり, 始め, 終わり, …]}（目録を小さくする）"""
    by_row = {}
    for i, j in sorted(cells):
        by_row.setdefault(i, []).append(j)
    out = {}
    for i, js in by_row.items():
        ranges = []
        start = prev = js[0]
        for j in js[1:]:
            if j != prev + 1:
                ranges += [start, prev]
                start = j
            prev = j
        ranges += [start, prev]
        out[str(i)] = ranges
    return out


# MARK: - 区画のファイルの形（SKC1）


def _uv(out, n):
    while True:
        b = n & 0x7F
        n >>= 7
        if n:
            out.append(b | 0x80)
        else:
            out.append(b)
            return


def _sv(out, n):
    """zigzag: 0, -1, 1, -2, 2 … → 0, 1, 2, 3, 4 …"""
    _uv(out, -2 * n - 1 if n < 0 else 2 * n)


def encode_cell(ways, coords, signals):
    """ways: [(id, [node id], tags)]、coords: {node id: (lat1e7, lon1e7)}、signals: 信号の node id の集合"""
    ways = sorted(ways, key=lambda w: w[0])
    order, index = [], {}
    for _, refs, _ in ways:
        for r in refs:
            if r not in index:
                index[r] = len(order)
                order.append(r)
    strings, sidx = [], {}
    for _, _, tags in ways:
        for k, v in tags.items():
            for x in (k, v):
                if x not in sidx:
                    sidx[x] = len(strings)
                    strings.append(x)
    out = bytearray(b"SKC1")
    _uv(out, len(strings))
    for x in strings:
        b = x.encode("utf-8")
        _uv(out, len(b))
        out += b
    _uv(out, len(order))
    pid = pla = plo = 0
    for r in order:
        la, lo = coords[r]
        _sv(out, r - pid)
        _sv(out, la - pla)
        _sv(out, lo - plo)
        pid, pla, plo = r, la, lo
    _uv(out, len(ways))
    pw = 0
    for wid, refs, tags in ways:
        _sv(out, wid - pw)
        pw = wid
        _uv(out, len(refs))
        pi = 0
        for r in refs:
            k = index[r]
            _sv(out, k - pi)
            pi = k
        _uv(out, len(tags))
        for k, v in tags.items():
            _uv(out, sidx[k])
            _uv(out, sidx[v])
    sl = sorted(index[r] for r in signals if r in index)
    _uv(out, len(sl))
    p = 0
    for k in sl:
        _uv(out, k - p)
        p = k
    return bytes(out)


def decode_cell(data):
    """encode_cell の逆 → (ways, coords, signals)"""
    pos = 4

    def uv():
        nonlocal pos
        n = shift = 0
        while True:
            b = data[pos]
            pos += 1
            n |= (b & 0x7F) << shift
            shift += 7
            if b < 0x80:
                return n

    def sv():
        n = uv()
        return (n >> 1) ^ -(n & 1)

    assert data[:4] == b"SKC1"
    strings = []
    for _ in range(uv()):
        n = uv()
        strings.append(data[pos:pos + n].decode("utf-8"))
        pos += n
    ids, coords = [], {}
    pid = pla = plo = 0
    for _ in range(uv()):
        pid += sv()
        pla += sv()
        plo += sv()
        ids.append(pid)
        coords[pid] = (pla, plo)
    ways = []
    pw = 0
    for _ in range(uv()):
        pw += sv()
        refs, pi = [], 0
        for _ in range(uv()):
            pi += sv()
            refs.append(ids[pi])
        tags = {}
        for _ in range(uv()):
            k = strings[uv()]
            tags[k] = strings[uv()]
        ways.append((pw, refs, tags))
    signals, p = set(), 0
    for _ in range(uv()):
        p += uv()
        signals.add(ids[p])
    return ways, coords, signals


# MARK: - 1. extract: 地方の PBF → 区画ごとの中間ファイル


def read_pbf(path):
    import osmium

    class Collector(osmium.SimpleHandler):
        def __init__(self):
            super().__init__()
            self.ways = []
            self.signals = set()
            self.names = []
            self.tagsets = {}

        def node(self, n):
            hw = n.tags.get("highway")
            if hw == "traffic_signals":
                self.signals.add(n.id)
            name = n.tags.get("name")
            if name and (hw == "traffic_signals" or n.tags.get("junction") == "yes"):
                self.names.append((n.id, n.location.lat, n.location.lon, name))

        def way(self, w):
            tags = {t.k: t.v for t in w.tags}
            if not keep_way(tags):
                return
            # 点の並びは array で持つ（地方全体の道でもメモリを食いすぎないように）。緯度・経度は 1e-7 度の整数
            refs, lats, lons = array.array("q"), array.array("i"), array.array("i")
            for nd in w.nodes:
                if not nd.location.valid():
                    continue
                refs.append(nd.ref)
                lats.append(nd.location.y)
                lons.append(nd.location.x)
            if len(refs) < 2:
                return
            # 同じタグの組み合わせは 1 つにまとめる（メモリを減らす）
            kept = tuple(sorted((k, v) for k, v in tags.items() if k in KEEP_TAGS))
            kept = self.tagsets.setdefault(kept, kept)
            self.ways.append((w.id, refs, lats, lons, kept))

    h = Collector()
    h.apply_file(path, locations=True, idx="flex_mem")
    return h.ways, h.signals, h.names


def extract(args):
    ways, signals, names = read_pbf(args.pbf)
    print(f"道: {len(ways)}  信号: {len(signals)}  名前の付いた交差点: {len(names)}", flush=True)
    by_cell = {}
    for w in ways:
        for c in touched_cells([x / 1e7 for x in w[2]], [x / 1e7 for x in w[3]]):
            by_cell.setdefault(c, []).append(w)
    os.makedirs(os.path.join(args.out, "cells"), exist_ok=True)
    os.makedirs(os.path.join(args.out, "names"), exist_ok=True)
    for (i, j), ws in by_cell.items():
        coords, cell_ways = {}, []
        for wid, refs, lats, lons, tags in ws:
            cell_ways.append((wid, list(refs), dict(tags)))
            for r, la, lo in zip(refs, lats, lons):
                coords[r] = (la, lo)
        data = encode_cell(cell_ways, coords, {r for r in coords if r in signals})
        with open(os.path.join(args.out, "cells", f"{i}_{j}.b"), "wb") as f:
            f.write(deflate(data, 1))
    by_name = {}
    for nid, la, lo, name in names:
        by_name.setdefault(cell_of(la, lo), []).append({"type": "node", "id": nid, "lat": round(la, 7), "lon": round(lo, 7), "tags": {"name": name}})
    for (i, j), els in by_name.items():
        with open(os.path.join(args.out, "names", f"{i}_{j}.json"), "w", encoding="utf-8") as f:
            json.dump(els, f, ensure_ascii=False, separators=(",", ":"))
    print(f"区画: {len(by_cell)}  名前のある区画: {len(by_name)}", flush=True)


# MARK: - 2. merge: 全地方の中間ファイルを区画ごとに合わせる


def merge(args):
    covered = set(covered_cells(read_poly(args.poly)))
    print(f"配る区画: {len(covered)}（境界の中に丸ごと入るもの。海も含む）", flush=True)
    out = os.path.join(args.out, args.name)
    os.makedirs(os.path.join(out, "cells"), exist_ok=True)
    os.makedirs(os.path.join(out, "names"), exist_ok=True)
    keys, names = {}, {}
    for r in args.regions:
        for f in os.listdir(os.path.join(args.work, r, "cells")):
            keys.setdefault(f[:-2], []).append(r)
        for f in os.listdir(os.path.join(args.work, r, "names")):
            names.setdefault(f[:-5], []).append(r)
    total = files = 0
    for key, regions in keys.items():
        i, j = map(int, key.split("_"))
        if (i, j) not in covered:
            continue
        ways, coords, signals = {}, {}, set()
        for r in regions:
            with open(os.path.join(args.work, r, "cells", key + ".b"), "rb") as f:
                ws, cs, ss = decode_cell(inflate(f.read()))
            for w in ws:
                ways.setdefault(w[0], w)  # 地方の境目の道は両方に入っている: 1 本に
            coords.update(cs)
            signals |= ss
        data = deflate(encode_cell(list(ways.values()), coords, signals))
        with open(os.path.join(out, "cells", key + ".b"), "wb") as f:
            f.write(data)
        total += len(data)
        files += 1
    for key, regions in names.items():
        i, j = map(int, key.split("_"))
        if (i, j) not in covered:
            continue
        els = {}
        for r in regions:
            with open(os.path.join(args.work, r, "names", key + ".json"), encoding="utf-8") as f:
                for e in json.load(f):
                    els[e["id"]] = e
        data = deflate(json.dumps({"elements": list(els.values())}, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
        with open(os.path.join(out, "names", key + ".z"), "wb") as f:
            f.write(data)
        total += len(data)
        files += 1
    now = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    meta = {
        "version": 2,
        "built": now,
        "format": "skc1",
        "cellLat": CELL_LAT,
        "cellLon": CELL_LON,
        "sources": args.regions,
        "regions": {args.name: {"built": now, "rows": rows_of(covered)}},
    }
    with open(os.path.join(args.out, "meta.json"), "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, separators=(",", ":"))
    print(f"書き出し: {files} ファイル {total / 1e6:.1f} MB → {out}", flush=True)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    e = sub.add_parser("extract", help="地方の .osm.pbf → 区画ごとの中間ファイル")
    e.add_argument("pbf", help="道と信号・交差点だけにした .osm.pbf")
    e.add_argument("--out", required=True, help="中間ファイルのフォルダ（work/{地方}）")
    m = sub.add_parser("merge", help="中間ファイルを合わせて、配るファイルと meta.json を作る")
    m.add_argument("regions", nargs="+", help="合わせる地方（work の下のフォルダ名）")
    m.add_argument("--poly", required=True, help="配る範囲の境界（Geofabrik の .poly。全国なら japan.poly）")
    m.add_argument("--work", default="work")
    m.add_argument("--out", default="site")
    m.add_argument("--name", default="japan", help="配るフォルダの名前（site/{名前}/cells/…）")
    args = ap.parse_args()
    if args.cmd == "extract":
        extract(args)
    else:
        merge(args)


if __name__ == "__main__":
    sys.exit(main())
