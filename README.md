# 坂道サイクルマップ 道路データ置き場

坂道サイクルマップ（iOS 版）が読む道路データを、区画（緯度 0.03° × 経度 0.04°）ごとの JSON にして GitHub Pages で配ります。
これまで使っていた Overpass API（無料の公開サーバー）は混んでいて 1 区画 30 秒以上かかるため、前もって作っておいたものを配ります（1 区画 1 秒かからない）。

- データ: © OpenStreetMap contributors（ODbL）。Geofabrik の地域データから作っています
- 範囲: 中国地方（`.github/workflows/build.yml` の `REGIONS` に地域を足すと増やせます）
- 更新: 毎月 1 日に GitHub Actions が自動で作り直します（手動でもできます）
- 置いていない区画（範囲の外・地域の境界にかかる区画）は、アプリが今までどおり Overpass から取ります

## 中身

| ファイル | 役目 |
|---|---|
| `build_cells.py` | OSM の地域データ（.osm.pbf）から、区画ごとの JSON（`cells/`・`names/`。容量を抑えるため DEFLATE で縮めて `.z`）と目録（`meta.json`）を作る |
| `merge_meta.py` | 地域ごとの目録を、アプリが最初に読む 1 つの `meta.json` にまとめる |
| `.github/workflows/build.yml` | 毎月、データを取ってきて作り、GitHub Pages に置く |

## はじめの設定（1 回だけ）

1. GitHub で新しいリポジトリを作る
   - 右上の「＋」→「New repository」
   - Repository name: `sakamichi-roads`
   - **Public** を選ぶ（無料のアカウントで GitHub Pages を使うため）
   - 「Add a README file」などのチェックは付けない →「Create repository」
2. このフォルダ（`roaddata`）の中身をアップロードする（どちらかの方法で）
   - **ブラウザで**: リポジトリの画面の「uploading an existing file」を押し、Finder から `build_cells.py`・`merge_meta.py`・`README.md`・`.github` フォルダをまとめてドラッグ →「Commit changes」
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
   - 15〜30 分ほどで緑のチェックになります
5. できたか確かめる
   - ブラウザで `https://あなたのユーザー名.github.io/sakamichi-roads/meta.json` を開いて、文字がたくさん出れば成功
6. アプリに置き場の URL を入れる
   - `Sakamichi/Engine/RoadDataServer.swift` の `static let defaultBase` を `URL(string: "https://あなたのユーザー名.github.io/sakamichi-roads/")` にしてビルド

## 注意

- 公開リポジトリは、60 日間リポジトリに何も変更が無いと、毎月の自動実行が止まります（GitHub からメールが来ます）。
  そのときは「Actions」タブ →「build road data」→「Enable workflow」で再開できます。
- GitHub Pages は 1 サイト 1 GB までです。中国地方は縮めて約 130 MB（縮める前は約 740 MB）。全国に広げると収まらないので、そのときは地域を選ぶか、別の置き場（Cloudflare R2 など）を考えてください。
