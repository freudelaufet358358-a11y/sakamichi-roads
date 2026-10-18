#!/usr/bin/env python3
"""坂道サイクルマップ用の道路データ（区画ごとの JSON）を、OSM の地域データ（.osm.pbf）から作る。

アプリは道路データを「区画」（緯度 0.03° × 経度 0.04°）ごとに読み込む。これまでは Overpass API から
取っていたが遅いので、同じ形の JSON を前もって作っておき、GitHub Pages から配る。

出力（--out の下）:
  cells/{i}_{j}.z     道（way）と、その点（node）と信号（id だけの node）。Overpass の結果をアプリが
                      区画に分けたものと同じ形の JSON・同じ分け方（区画を通る道は、その区画のファイルに入る）
  names/{i}_{j}.z     名前の付いた信号・交差点（案内で「◯◯交差点を右」と言うため）
                      どちらも JSON を raw DEFLATE で縮めたもの（GitHub Pages の 1 GB に収めるため。約 1/6 になる）
  meta.json           作った日時と、配っている区画の一覧（地域の境界の中に丸ごと入る区画だけ。
                      境界にかかる区画は、境界の外の道が抜けるので配らない → アプリは Overpass から取る）

使い方:
  osmium tags-filter region.osm.pbf w/highway n/highway=traffic_signals n/junction=yes -o roads.osm.pbf
  python build_cells.py roads.osm.pbf --poly region.poly --out site --source chugoku
"""

import argparse
import array
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


# MARK: - 地域の境界（Geofabrik の .poly）


def read_poly(path):
    """外側の輪と穴（! で始まる輪）。座標は (lon, lat)"""
    rings, holes = [], []
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
        (holes if name.startswith("!") else rings).append(ring)
    return rings, holes


def point_in_ring(x, y, ring):
    inside = False
    n = len(ring)
    j = n - 1
    for k in range(n):
        xi, yi = ring[k]
        xj, yj = ring[j]
        if (yi > y) != (yj > y) and x < (xj - xi) * (y - yi) / (yj - yi) + xi:
            inside = not inside
        j = k
    return inside


def segments_cross(a, b, c, d):
    def orient(p, q, r):
        return (q[0] - p[0]) * (r[1] - p[1]) - (q[1] - p[1]) * (r[0] - p[0])

    o1, o2, o3, o4 = orient(a, b, c), orient(a, b, d), orient(c, d, a), orient(c, d, b)
    return (o1 > 0) != (o2 > 0) and (o3 > 0) != (o4 > 0)


class Region:
    """区画が地域の境界の中に丸ごと入っているか"""

    def __init__(self, rings, holes):
        self.rings = rings
        self.holes = holes
        self.edges = []
        for ring in rings + holes:
            for k in range(len(ring)):
                self.edges.append((ring[k - 1], ring[k]))
        xs = [p[0] for r in rings for p in r]
        ys = [p[1] for r in rings for p in r]
        self.box = (min(ys), min(xs), max(ys), max(xs))  # s, w, n, e

    def contains(self, lon, lat):
        if not any(point_in_ring(lon, lat, r) for r in self.rings):
            return False
        return not any(point_in_ring(lon, lat, h) for h in self.holes)

    def contains_cell(self, i, j):
        s, w = i * CELL_LAT, j * CELL_LON
        n, e = s + CELL_LAT, w + CELL_LON
        corners = [(w, s), (e, s), (e, n), (w, n)]
        if not all(self.contains(x, y) for x, y in corners):
            return False
        # 境界の線が区画を横切っていない・区画の中に境界の点が無い
        sides = [(corners[k - 1], corners[k]) for k in range(4)]
        for a, b in self.edges:
            if w < a[0] < e and s < a[1] < n:
                return False
            for c, d in sides:
                if segments_cross(a, b, c, d):
                    return False
        return True

    def cells(self):
        s, w, n, e = self.box
        i0, j0 = cell_of(s, w)
        i1, j1 = cell_of(n, e)
        out = []
        for i in range(i0, i1 + 1):
            for j in range(j0, j1 + 1):
                if self.contains_cell(i, j):
                    out.append((i, j))
        return out


# MARK: - 区画に分けて書き出す


def build(ways, signals, names, covered, out_dir, source):
    """ways: [(id, node id の並び, 緯度の並び, 経度の並び, tags)]、signals: 信号の node id、names: [(id, lat, lon, name)]"""
    covered = set(covered)
    by_cell = {c: [] for c in covered}
    for w in ways:
        for c in touched_cells(w[2], w[3]):
            if c in by_cell:
                by_cell[c].append(w)
    os.makedirs(os.path.join(out_dir, "cells"), exist_ok=True)
    os.makedirs(os.path.join(out_dir, "names"), exist_ok=True)
    total = 0
    for (i, j), ws in by_cell.items():
        elements = []
        coords = {}
        for wid, refs, lats, lons, tags in ws:
            elements.append({"type": "way", "id": wid, "nodes": list(refs), "tags": tags})
            for ref, la, lo in zip(refs, lats, lons):
                coords[ref] = (la, lo)
        for ref, (la, lo) in coords.items():
            elements.append({"type": "node", "id": ref, "lat": round(la, 7), "lon": round(lo, 7)})
        for ref in coords:
            if ref in signals:
                elements.append({"type": "node", "id": ref})
        total += write_json(os.path.join(out_dir, "cells", f"{i}_{j}.z"), {"elements": elements}, deflate=True)

    by_name = {c: [] for c in covered}
    for nid, la, lo, name in names:
        c = cell_of(la, lo)
        if c in by_name:
            by_name[c].append({"type": "node", "id": nid, "lat": round(la, 7), "lon": round(lo, 7), "tags": {"name": name}})
    for (i, j), els in by_name.items():
        total += write_json(os.path.join(out_dir, "names", f"{i}_{j}.z"), {"elements": els}, deflate=True)

    meta = {
        "version": 1,
        "built": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "source": source,
        "cellLat": CELL_LAT,
        "cellLon": CELL_LON,
        "cells": sorted(f"{i}_{j}" for i, j in covered),
    }
    write_json(os.path.join(out_dir, "meta.json"), meta)
    return total


def write_json(path, obj, deflate=False):
    data = json.dumps(obj, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    if deflate:
        # raw DEFLATE（ヘッダーなし）。iOS の NSData.decompressed(using: .zlib) でそのまま戻せる形
        c = zlib.compressobj(9, zlib.DEFLATED, -15)
        data = c.compress(data) + c.flush()
    with open(path, "wb") as f:
        f.write(data)
    return len(data)


# MARK: - PBF を読む（pyosmium）


def read_pbf(path):
    import osmium

    class Collector(osmium.SimpleHandler):
        def __init__(self):
            super().__init__()
            self.ways = []
            self.signals = set()
            self.names = []

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
            # 点の並びは array で持つ（地域全体の道でもメモリを食いすぎないように）
            refs, lats, lons = array.array("q"), array.array("d"), array.array("d")
            for nd in w.nodes:
                if not nd.location.valid():
                    continue
                refs.append(nd.ref)
                lats.append(nd.location.lat)
                lons.append(nd.location.lon)
            if len(refs) < 2:
                return
            self.ways.append((w.id, refs, lats, lons, {k: v for k, v in tags.items() if k in KEEP_TAGS}))

    h = Collector()
    h.apply_file(path, locations=True, idx="flex_mem")
    return h.ways, h.signals, h.names


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("pbf", help="道と信号・交差点だけにした .osm.pbf")
    ap.add_argument("--poly", required=True, help="地域の境界（Geofabrik の .poly）")
    ap.add_argument("--out", default="site", help="書き出すフォルダ")
    ap.add_argument("--source", default="", help="meta.json に書く地域の名前")
    args = ap.parse_args()

    region = Region(*read_poly(args.poly))
    covered = region.cells()
    print(f"区画: {len(covered)}（境界の中に丸ごと入るもの）", flush=True)
    ways, signals, names = read_pbf(args.pbf)
    print(f"道: {len(ways)}  信号: {len(signals)}  名前の付いた交差点: {len(names)}", flush=True)
    total = build(ways, signals, names, covered, args.out, args.source)
    print(f"書き出し: {total / 1e6:.1f} MB → {args.out}", flush=True)


if __name__ == "__main__":
    sys.exit(main())
