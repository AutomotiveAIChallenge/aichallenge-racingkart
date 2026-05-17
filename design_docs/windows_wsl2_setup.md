# Windows（WSL2）で動かすためのセットアップ

このリポジトリは **Linux（Ubuntu）** を主ターゲットとして設計されていますが、
**WSL2 上の Ubuntu** でも `make` / `docker compose` がそのまま使えるようになっています。

> スコープ: WSL2（Ubuntu）上で AWSIM + Autoware（評価/開発フロー）を動かす手順。
> スコープ外: PowerShell から直接動かす Windows ネイティブ実行、Docker Desktop だけで完結する構成。

---

## 1. 推奨構成

- Windows 11 + WSL2 + Ubuntu 22.04
- リポジトリは **WSL の Linux FS 配下**（例: `~/aichallenge-racingkart`）に置く
  - `/mnt/c/...` 配下に置くと、**改行コード/実行権限/I-O 性能**でハマる
- Docker は **WSL 内の Docker Engine**（推奨）
  - Docker Desktop でも動くが `network_mode: host` 周りで挙動が変わる
- GPU を使う場合は **Windows 側に NVIDIA ドライバ**を入れて、WSL 内で
  [NVIDIA Container Toolkit](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/install-guide.html) を入れる

---

## 2. 自動で行われること

以下はコードベース側で **自動的に**やってくれます（手で何かする必要はありません）。

### 2.1 `Makefile` が WSL を検出して compose を切り替える

`make` ターゲットを呼ぶと、`Makefile` が `/proc/sys/kernel/osrelease` を見て WSL を判定し、
`docker-compose.wsl.yml` を `COMPOSE_FILE` に自動追加します。

- `.env` で `COMPOSE_FILE` が未設定 → `docker-compose.yml:docker-compose.wsl.yml`
- 既に GPU 等を設定済み → 末尾に `:docker-compose.wsl.yml` を追記
- 既に WSL ファイルが含まれている → そのまま

### 2.2 `docker-compose.wsl.yml` の役割

- `/dev/video0` `/dev/input` を `devices:` から外す（WSL に存在しないため）
- WSLg 用の環境変数 `PULSE_SERVER` / `WAYLAND_DISPLAY` / `XDG_RUNTIME_DIR` をセット
- WSLg のソケットを `/mnt/wslg` / `/usr/lib/wsl` でマウント
- `XAUTHORITY` 未設定時のフォールバックを `/mnt/wslg/.Xauthority` にする

Docker Compose は **v2.24 以上**が必要です（`!override` タグを使うため）。
`docker compose version` で確認してください。

### 2.3 `.gitattributes` で改行コードを LF に固定

Windows 側で `git clone` した場合でも、`*.bash` / `Makefile` / `*.yml` 等が
CRLF に変換されないようリポジトリ側で固定済み。

### 2.4 GPU 判定が WSL2 でも通る

`/dev/nvidia0` だけでなく WSL2 が出す `/dev/dxg` も見て NVIDIA GPU を検出します。
影響範囲: `setup.bash` / `docker_run.sh` / `aichallenge/run_simulator.bash` /
`aichallenge/utils/move_window.bash`

---

## 3. 手順（クイック）

WSL2 の Ubuntu を立ち上げて、Linux ホームに clone してから:

```bash
# 0) 環境チェック（WSL/WSLg/GPU/CRLF などを一括診断）
./setup.bash doctor

# 1) Docker + 依存物のインストール（必要なら）
./setup.bash bootstrap   # 対話で必要ステップを y/N 選択

# 2) .env を作る（CPU/GPU/WSL を自動検出して COMPOSE_FILE を設定）
./setup.bash env

# 3) ベースイメージ取得 + AWSIM ダウンロード
./setup.bash pull image
./setup.bash download awsim

# 4) dev イメージビルド + Autoware ワークスペースビルド
./docker_build.sh dev
make autoware-build

# 5) 起動
make dev ROS_DOMAIN_ID=1
# 停止
make down
```

---

## 4. よくあるハマりどころ

### (A) `^M`（CRLF）で bash が壊れる

症状: `#!/bin/bash^M: bad interpreter: No such file or directory`

対策:
- リポジトリを **WSL の Linux FS** 配下に置く
- Git を LF 固定にする: `git config --global core.autocrlf false`
- 暫定: `sudo apt install dos2unix && dos2unix <file>`

リポジトリ側の `.gitattributes` でも防いでいますが、既存 clone には反映されません。
その場合 `git rm --cached -r . && git reset --hard` で再展開してください。

### (B) GUI が出ない（DISPLAY 関連）

- `echo $DISPLAY` が空 → WSLg が動いていない（Windows を再起動 or `wsl --update`）
- それでも出ない場合: `wsl --shutdown` 後に再起動

`./setup.bash doctor` で `WSL` セクションを確認してください。

### (C) `bind source path does not exist: /dev/video0`

→ `docker-compose.wsl.yml` がロードされていません。次のどれかで直ります:
- `make` 経由で起動する（自動的にロードされる）
- `.env` に `COMPOSE_FILE=docker-compose.yml:docker-compose.wsl.yml` を書く
- `docker compose -f docker-compose.yml -f docker-compose.wsl.yml ...` を直接渡す

### (D) GPU が認識されない

WSL2 では `/dev/nvidia0` は存在せず、代わりに `/dev/dxg` が出ます。
判定はコード側で対応済みですが、コンテナから NVIDIA を使うには以下が前提:

1. Windows 側に **NVIDIA Game Ready/Studio ドライバ**（GeForce）または **データセンタードライバ**
2. WSL 内に [NVIDIA Container Toolkit](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/install-guide.html)
3. `nvidia-smi` を WSL シェルで叩いて GPU が見えること
4. `.env` に GPU 付きの `COMPOSE_FILE` を設定（例: `docker-compose.yml:docker-compose.gpu.yml:docker-compose.wsl.yml`）

### (E) `XAUTHORITY` が空でも動く（WSLg）

WSLg では `XAUTHORITY` 未設定が通常で、コンテナ側はソケット越しに描画します。
`docker-compose.wsl.yml` がフォールバックを設定しているのでそのままで動きます。
