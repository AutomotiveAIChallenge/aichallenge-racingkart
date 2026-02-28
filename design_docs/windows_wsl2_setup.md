# Windows（WSL2）で動かすためのセットアップメモ

このリポジトリは **Linux（Ubuntu）** を主ターゲットとして設計されています。  
Windows で使う場合は、**Windows ネイティブ移植**ではなく **WSL2 上で Linux として動かす**のが最短です。

> スコープ: WSL2（Ubuntu）上で `make` / `docker compose` を実行する手順と、ハマりどころのメモ。  
> スコープ外（別途対応が必要）: PowerShell だけで完結する Windows ネイティブ実行、Docker Desktop 直下での完全サポート。

---

## 推奨構成（最短で動かす）

- Windows 11 + WSL2 + Ubuntu 22.04 など
- リポジトリは **WSL の Linux ファイルシステム（例: `~/aichallenge-racingkart`）に配置**
  - `/mnt/c/...` 配下（Windows 側のドライブ）に置くと、**改行コード/実行権限/性能**でハマりやすいです
- Docker は **WSL 内で Docker Engine を動かす**（推奨）
  - Docker Desktop でも動く場合はありますが、`network_mode: host` などで差分が出やすいです

---

## まず確認すること（WSL 側で実行）

### 1) どこに clone したか

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

WSLg 環境なら通常 `:0` のような値が入ります（空なら GUI が出ません）。

---

## 実行（WSL 側で）

基本は Linux と同じです。まずはチェックから始めます。

```bash
./setup.bash preflight
```

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
- Windows 側のエディタ設定や Git 設定で、改行が CRLF になっている

対策（推奨）:
- リポジトリを WSL の Linux FS に置く
- Git を LF 固定にする（例）
  - `git config --global core.autocrlf false`

暫定復旧:
- `dos2unix <file>`（入っていない場合は `sudo apt-get install dos2unix`）

### (B) 実行ビット（`chmod +x`）が保持されない

症状:
- `Permission denied`（shebang があるのに実行できない）

原因:
- `/mnt/c` 配下など、Windows 側の FS 上で権限が期待通りにならない

対策:
- WSL 側の `~/...` に置く
- 暫定的に `bash aichallenge/run_evaluation.bash` のように `bash` 経由で実行する

### (C) `docker compose` が `/dev/*` の bind mount で落ちる

症状（例）:
- `bind source path does not exist: /dev/dri`
- `/dev/video0` や `/dev/input` が存在せずに compose が起動できない

背景:
- `docker-compose.yml` は Linux ホストのデバイスを前提にしている箇所があります

現状:
- WSL 環境によってはそのままだと起動できない可能性があります

> ここは将来的に `docker-compose.wsl.yml` を追加してオーバーライドするのが本筋（下の TODO 参照）。

### (D) `XAUTHORITY` が空で compose の volume 定義が壊れる

症状:
- `invalid spec` や空パスの bind mount エラー

対策（暫定）:
- WSL 側で `XAUTHORITY` を明示
  - `export XAUTHORITY="$HOME/.Xauthority"`

### (E) Windows 側のパス/シンボリックリンク

`/output/latest` を固定参照ディレクトリとして使います（`output/latest` への symlink 依存は前提にしません）。  
Windows ドライブ上だと symlink の扱いが厳しくなるため、やはり `~/...` 配下運用を推奨します。

---

## TODO（未実装 / 将来の改善）

以下は「Windows（WSL2）でもストレスなく動かす」ために将来入れたい変更です（現時点では未対応）。

- `.gitattributes` を追加し、`*.bash` / `Makefile` / `*.yml` を `eol=lf` で固定（CRLF 混入防止）
- `docker-compose.wsl.yml` を追加し、WSL で存在しない `devices:` / `volumes:` を安全にオーバーライド
  - 併せて WSLg（`/mnt/wslg` 等）向けの環境変数/マウントを整理
- `Makefile` 側で WSL を自動検出し、`-f docker-compose.wsl.yml` を自動付与
- `make doctor`（または既存 doctor の拡張）で、CRLF/GUI/Docker の前提を起動前にチェック
