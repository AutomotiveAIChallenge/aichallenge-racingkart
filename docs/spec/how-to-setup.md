# 環境構築

> 仕様ドキュメント（現仕様の正）。最終確認: 2026-06-14。文書運用方針は [docs/README.md](../README.md) を参照。

> 想定: Ubuntu 22.04。まずは CPU で動作確認できれば OK です（GPU は後回し）。

## 0) まずこれ（入口）

以下のコマンドで、環境構築から起動確認まで対話形式で一括実行できます。

```bash
sudo apt update && sudo apt install -y curl
curl -fsSL "https://raw.githubusercontent.com/AutomotiveAIChallenge/aichallenge-racingkart/main/setup.bash" | bash
```

- `bootstrap` では必要ステップを **y/N で確認**しながら進められます
- すべての確認が終わると自動でセットアップが進みます

## 1) setup.bash bootstrap が行うステップ

| #  | ステップ                      | 対応コマンド（個別実行時）       |
|----|-------------------------------|----------------------------------|
| 1  | 基本パッケージの導入          | `sudo apt install -y ...`        |
| 2  | Docker の導入                 | bootstrap 内で自動実行           |
| 3  | docker グループへの追加       | `sudo usermod -aG docker $USER`  |
| 4  | DDS ホストチューニング        | `./setup.bash network tune`      |
| 5  | リポジトリの取得              | `git clone ...`                  |
| 6  | 環境診断                      | `./setup.bash doctor`            |
| 7  | .env 作成（GPU/CPU 自動検出） | `./setup.bash env`               |
| 8  | Autoware ベースイメージ取得   | `./setup.bash pull image`        |
| 9  | AWSIM ダウンロード・展開      | `./setup.bash download awsim`    |
| 10 | 開発用イメージのビルド        | `./docker_build.sh dev`          |
| 11 | ワークスペースビルド          | `make autoware-build`            |
| 12 | 起動確認                      | `make dev` → 停止: `make down`   |

## 2) チェックリスト（上から順にやるだけ）

### (A) 診断する（最初に必ず）

- `./setup.bash doctor` を実行して、足りないものを洗い出す
- "Docker" や "Repository" の欄で次にやるべきことを確認する

### (B) `.env` を作成する（GPU / CPU の選択）

```bash
./setup.bash env
```

GPU の有無を自動検出して `.env` を作成します。手動で編集したい場合は `cp .env.example .env` でコピーしてから修正してください。

eval サービス（`autoware-simulator-evaluation`）はベースの `docker-compose.yml` に含まれます。

GPU / CPU の切り替え:

- **CPU + サウンド（デフォルト）**:
  ```
  COMPOSE_FILE=docker-compose.yml:docker-compose.sound.yml
  ```
- **GPU（NVIDIA）+ サウンド**（GPU あり環境ではこちら）:
  ```
  COMPOSE_FILE=docker-compose.yml:docker-compose.gpu.yml:docker-compose.sound.yml
  ```
- **ヘッドレス（サウンドなし）**:
  ```
  COMPOSE_FILE=docker-compose.yml
  ```

完了の目安: `.env` が存在し、`COMPOSE_FILE` の設定が自分の環境に合っている。

### (C) DDS ホストチューニングを行う（推奨）

```bash
./setup.bash network tune
```

CycloneDDS が大きいメッセージをやり取りできるよう、ホスト OS の UDP バッファと loopback マルチキャストを設定します。`sudo` で `/etc/sysctl.d/` に書き込むため、再起動後も永続します。

完了の目安: `sysctl net.core.rmem_max` が `2147483647` になっている。
