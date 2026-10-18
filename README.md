# 坂道サイクルマップ 道路データ置き場

坂道サイクルマップ（iOS 版）が読む道路データを、区画（緯度 0.03° × 経度 0.04°）ごとのファイルにして GitHub Pages で配ります。
これまで使っていた Overpass API（無料の公開サーバー）は混んでいて 1 区画 30 秒以上かかるため、前もって作っておいたものを配ります（1 区画 1 秒かからない）。

- データ: © OpenStreetMap contributors（ODbL）。Geofabrik の地域データから作っています
- 範囲: 日本全国（Geofabrik の 8 地方を地方ごとに作り、最後に区画ごとに合わせる。地方の境目の区画も欠けない）
- 更新: 毎月 1 日に GitHub Actions が自動で作り直します（手動でもできます）
- 置いていない区画（日本の外・日本の境界の線にかかる沖の区画）は、アプリが今までどおり Overpass から取ります

## 中身

| ファイル | 役目 |
|---|---|
| `build_cells.py` | `extract`: 地方の OSM データ（.osm.pbf）から区画ごとの中間ファイルを作る。`merge`: 全地方を区画ごとに合わせ、配るファイル（`japan/cells/*.b`・`japan/names/*.z`）と目録（`meta.json`）を作る |
| `.github/workflows/build.yml` | 毎月、データを取ってきて作り、GitHub Pages に置く |

区画のファイルは、道・点・信号を詰めた独自の形（SKC1。`build_cells.py` の先頭に説明）を DEFLATE で縮めたものです。
JSON のままより 6 割ほど小さく、全国でも GitHub Pages の 1 GB に収まります。

## はじめの設定（1 回だけ）

1. GitHub で新しいリポジトリを作る
   - 右上の「＋」→「New repository」
   - Repository name: `sakamichi-roads`
   - **Public** を選ぶ（無料のアカウントで GitHub Pages を使うため）
   - 「Add a README file」などのチェックは付けない →「Create repository」
2. このフォルダ（`roaddata`）の中身をアップロードする（どちらかの方法で）
   - **ブラウザで**: リポジトリの画面の「uploading an existing file」を押し、Finder から `build_cells.py`・`README.md`・`.github` フォルダをまとめてドラッグ →「Commit changes」
     （`.github` は Finder では隠れています。Finder で `command + shift + .` を押すと出ます）
   - **ターミナルで**:
     ```
     cd ~/BIKEMAP/roaddata
     git init -b main
     git add .
     git commit -m "道路データの置き場"
     git remote add origin https://github.com/あなたのユーザー名/sakamichi-roads.git
     git push -u origin main
     ```
3. GitHub Pages を「GitHub Actions で置く」にする
   - リポジトリの「Settings」→ 左の「Pages」→「Build and deployment」の「Source」を **GitHub Actions** にする
4. 1 回目を手動で作る
   - 「Actions」タブ →（「I understand my workflows…」が出たら押す）→ 左の「build road data」→「Run workflow」→「Run workflow」
   - 全国で 30〜60 分ほどで緑のチェックになります
5. できたか確かめる
   - ブラウザで `https://あなたのユーザー名.github.io/sakamichi-roads/meta.json` を開いて、文字がたくさん出れば成功
6. アプリに置き場の URL を入れる
   - `Sakamichi/Engine/RoadDataServer.swift` の `static let defaultBase` を `URL(string: "https://あなたのユーザー名.github.io/sakamichi-roads/")` にしてビルド

## 注意

- 公開リポジトリは、60 日間リポジトリに何も変更が無いと、毎月の自動実行が止まります（GitHub からメールが来ます）。
  そのときは「Actions」タブ →「build road data」→「Enable workflow」で再開できます。
- GitHub Pages は 1 サイト 1 GB までです。全国で約 600 MB の見込みです（中国地方で約 56 MB）。
