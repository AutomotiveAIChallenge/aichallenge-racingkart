# Windows（WSL2）で動かすためのセットアップメモ

> 仕様ドキュメント（現仕様の正）。最終確認: 2026-06-14。文書運用方針は [docs/README.md](../../README.md) を参照。

このリポジトリは **Linux（Ubuntu）** を主ターゲットとして設計されています。  
Windows で使う場合は、Windows ネイティブ移植ではなく **WSL2 上で Linux として動かす**のが最短です。

> スコープ: WSL2（Ubuntu）上で `make` / `docker compose` を実行する手順と、ハマりどころのメモ。  
> スコープ外（別途対応が必要）: PowerShell だけで完結する Windows ネイティブ実行、Docker Desktop 直下での完全サポート。

---

## 推奨構成

- Windows 11 + WSL2 + Ubuntu 22.04 など
- リポジトリは **WSL の Linux ファイルシステム（例: `~/aichallenge-racingkart`）に配置**
  - `/mnt/c/...` 配下に置くと、改行コード・実行権限・性能でハマりやすいです
- Docker は **WSL 内で Docker Engine を動かす**（推奨）
  - Docker Desktop でも動く場合はありますが、`network_mode: host` などで差分が出やすいです

---

## まず確認すること（WSL 側で実行）

### 1) clone 先の確認

```bash
pwd
```

- OK: `/home/<user>/...`
- 非推奨: `/mnt/c/...`（Windows ドライブ直下）

### 2) Docker が動くか

```bash
docker version
docker compose version
```

### 3) GUI（WSLg）が使えるか

```bash
echo "${DISPLAY:-}"
```

WSLg 環境なら通常 `:0` のような値が入ります（空なら GUI は表示されません）。

---

## 実行（WSL 側で）

基本は Linux と同じです。まずはチェックから始めます。

```bash
./setup.bash doctor
```

DDS ホストチューニング（推奨）:

```bash
./setup.bash network tune
```

CycloneDDS の大きいメッセージに必要な UDP バッファ拡張と loopback マルチキャスト設定を永続化します（`sudo` を使います）。WSL2 でも同様に有効です。

起動例:

```bash
make autoware-build
make simulator
make autoware-simulator
```

---

## よくあるハマりどころ（Windows/WSL 特有）

### (A) `^M`（CRLF）で bash が壊れる

症状:
- `#!/bin/bash^M: bad interpreter: No such file or directory`

原因:
- Windows 側のエディタや Git 設定で改行が CRLF になっている

対策（推奨）:
- リポジトリを WSL の Linux FS に置く
- Git を LF 固定にする（例）
  - `git config --global core.autocrlf false`

暫定復旧:
- `dos2unix <file>`（未インストールの場合は `sudo apt-get install dos2unix`）

### (B) 実行ビット（`chmod +x`）が保持されない

症状:
- `Permission denied`（shebang があるのに実行できない）

原因:
- `/mnt/c` 配下など、Windows 側 FS では権限が期待通りにならない

対策:
- WSL 側の `~/...` に置く
- 暫定的に `bash aichallenge/run_evaluation.bash` のように `bash` 経由で実行する

### (C) `docker compose` が `/dev/*` の bind mount で落ちる

症状（例）:
- `bind source path does not exist: /dev/dri`
- `/dev/video0` や `/dev/input` が存在せずに compose が起動できない

背景:
- `docker-compose.yml` は Linux ホストのデバイスを前提にしている箇所があります

対策:
- `.env` の `COMPOSE_FILE` に `docker-compose.wsl.yml` を追加すると、WSL に存在しないデバイスを安全にオーバーライドできます

```bash
# .env の COMPOSE_FILE をこの行に変更（コメントアウト解除）:
COMPOSE_FILE=docker-compose.yml:docker-compose.eval.yml:docker-compose.wsl.yml
```

- このオーバーレイは `/dev/dri`・`/dev/video0`・`/dev/input` への参照を除去し、WSLg の X11 ソケット（`/tmp/.X11-unix`）と PulseAudio（`/mnt/wslg/PulseServer`）を自動で接続します
- **`docker-compose.sound.yml` は追加しないでください**（`wsl.yml` が WSLg 向けのオーディオ設定を担っており、`sound.yml` と併用すると PulseAudio の設定が競合します）
- Docker Compose 2.24 以上が必要です（`docker compose version` で確認）
- 注: `driver` サービスは実車用のため、`docker-compose.wsl.yml` ではカバーされていません。WSL 上で `make driver` を実行すると `/dev/dri` バインドで失敗します（これは仕様です）。

### (D) `XAUTHORITY` が空で compose の volume 定義が壊れる

症状:
- `invalid spec` や空パスの bind mount エラー

対応済み（v2 以降）:
- `docker-compose.yml` および `docker-compose.eval.yml` のベース定義に `${XAUTHORITY:-/dev/null}` のデフォルト値を追加しました
- `XAUTHORITY` が未設定の場合は `/dev/null` を `/dev/null` に bind（無害な no-op）します
- `export XAUTHORITY=...` の暫定対策は不要になりました

### (E) Windows 側のパス/シンボリックリンク

`/output/latest/` は実ディレクトリで、内部エントリだけが最新 run へのシンボリックリンクです（`latest/` 自体は symlink にしない。契約は [`../../interface/evaluation-interface.md`](../../interface/evaluation-interface.md) 約束 9）。  
Windows ドライブ上では symlink の扱いが厳しくなるため、やはり `~/...` 配下での運用を推奨します。

---

## TODO（未実装 / 将来の改善）

WSL2 でのストレスを減らすために将来対応したい項目です（現時点では未対応）。

- `.gitattributes` を追加し、`*.bash` / `Makefile` / `*.yml` を `eol=lf` で固定（CRLF 混入防止）
- ~~`docker-compose.wsl.yml` を追加し、WSL で存在しない `devices:` / `volumes:` を安全にオーバーライド~~  
  ~~- 併せて WSLg（`/mnt/wslg` 等）向けの環境変数/マウントを整理~~  
  **実装済み**: `docker-compose.wsl.yml` を追加。`.env` の `COMPOSE_FILE` に追加するだけで有効。
- `Makefile` 側で WSL を自動検出し、`-f docker-compose.wsl.yml` を自動付与（未実装）
- `make doctor`（または既存 doctor の拡張）で、CRLF/GUI/Docker の前提を起動前にチェック（未実装）
