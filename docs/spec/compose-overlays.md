# Docker Compose 構成: `.env` COMPOSE_FILE によるオーバーレイ設計

> 仕様ドキュメント（現仕様の正）。最終確認: 2026-06-14。文書運用方針は [docs/README.md](../README.md) を参照。

## 現在の構成

GPU/サウンド の有無は **`.env` の `COMPOSE_FILE` 変数**で切り替える。
`docker compose` は `.env` を自動で読み込むため、`make` 経由でも直接 `docker compose up` でも同じオーバーレイが適用される。

### ファイル構成（4 ファイル）

| ファイル | 役割 | 備考 |
|---|---|---|
| `docker-compose.yml` | ベース定義（全サービス、YAML anchor `x-autoware-base`） | 常時必須 |
| `docker-compose.gpu.yml` | NVIDIA GPU オーバーレイ（`NVIDIA_VISIBLE_DEVICES=all`、`NVIDIA_DRIVER_CAPABILITIES=all`、`deploy.resources` nvidia） | NVIDIA 環境のみ |
| `docker-compose.sound.yml` | PulseAudio（`simulator` サービスのみ） | 音声が必要な場合 |

`autoware-simulator-evaluation` サービスはベースの `docker-compose.yml` に含まれる（別ファイル不要）。

### `x-autoware-base` アンカーの主要設定

```yaml
x-autoware-base: &autoware-base
  network_mode: host
  privileged: true
  stop_grace_period: 10s
  user: "${HOST_UID:-1000}:${HOST_GID:-1000}"
  group_add:
    - "${HOST_GID_RENDER:-110}"
    - "${HOST_GID_VIDEO:-44}"
    - "${HOST_GID_INPUT:-107}"
```

`group_add` はグループ名ではなく **数値 GID**（Makefile が `getent` で取得して export する `HOST_GID_RENDER`/`VIDEO`/`INPUT` 等）を指定する。コンテナ内に同名グループが存在しない場合でも GID により `/dev/dri` などのデバイスへ確実にアクセスできる。`driver` サービスはこれに加えて `HOST_GID_DIALOUT` を付与する。

volumes: `./output:/output`, `./aichallenge:/aichallenge`, `./remote:/remote`, `./vehicle:/vehicle`,
`./vehicle/cyclonedds.xml:/opt/autoware/cyclonedds.xml`, X11, `/dev/dri`

### サービス一覧（docker-compose.yml）

| サービス | 用途 |
|---|---|
| `autoware` | Autoware メインプロセス（シミュレータ / 実車） |
| `autoware-build` | colcon build 専用（`make autoware-build`） |
| `simulator` | AWSIM シミュレータ |
| `autoware-command` | ros2 service/topic の単発操作（`make autoware-request-*`） |
| `zenoh` | Zenoh ブリッジ（実車遠隔操作） |
| `driver` | 実車ドライバスタック（racing_kart_interface） |
| `rviz2` | RViz2 可視化 |

eval サービス `autoware-simulator-evaluation`（image: `aichallenge-2025-eval`）は `docker-compose.yml` に定義する。
このサービスは `/aichallenge` をマウントせず、イメージに焼き込まれた状態で `aichallenge/run_evaluation.bash` を実行する。

---

## COMPOSE_FILE の選択肢

`.env.example` に記載の選択肢（いずれか 1 行を `.env` に有効化する）:

```bash
# CPU + サウンド（デフォルト）
COMPOSE_FILE=docker-compose.yml:docker-compose.sound.yml

# GPU（NVIDIA）+ サウンド
COMPOSE_FILE=docker-compose.yml:docker-compose.gpu.yml:docker-compose.sound.yml

# ヘッドレス（サウンドなし）
COMPOSE_FILE=docker-compose.yml
```

`./setup.bash env` を実行すると `/dev/nvidia0` の有無で GPU/CPU を自動判定し、`.env` を生成する。

---

## ホスト UID/GID

Makefile は `HOST_UID=$(id -u)` / `HOST_GID=$(id -g)` を計算・エクスポートし、
さらに `HOST_GID_RENDER`/`VIDEO`/`INPUT`/`DIALOUT` を `getent` で取得する。
compose の `user:` と `group_add:` がこれらを参照するため、`output/` 配下の成果物はホストユーザー所有で作成される（root での chown 不要）。

---

## DDS 設定

イメージには `RMW_IMPLEMENTATION=rmw_cyclonedds_cpp` と `CYCLONEDDS_URI=file:///opt/autoware/cyclonedds.xml` が焼き込まれている。
`vehicle/cyclonedds.xml` がバインドマウントされ、実際の設定を上書きする。
ホスト側のカーネルパラメータ（rmem_max、lo マルチキャスト）のチューニングは `./setup.bash network tune` で永続化する。

---

## 検討の経緯 / なぜ COMPOSE_FILE-in-.env を選んだか

以下のアプローチを検討した（詳細は過去の設計ノート参照）:

| アプローチ | 結論 |
|---|---|
| **A. Profiles** | サービス単位の制御のみ; 既存サービスへの `deploy` 注入には不向き。サービスが倍増するリスク |
| **B. include** | 無条件読み込みで GPU なし環境がエラーになる。条件分岐不可 |
| **C. デフォルトランタイム（daemon.json）** | `daemon.json` がホスト依存になる; 全参加者環境の統一が必要 |
| **D. deploy 直接埋め込み** | GPU なしホストで `deploy.resources.reservations.devices` がエラー |
| **E. CDI** | Docker 25+ 限定; CDI spec 事前生成が必要; 成熟度不足 |
| **F. COMPOSE_FILE in .env** | `make` 不要でも `docker compose up` が正しく動作; 最小変更で 4 ファイル構成に拡張可能 ✓ |

**採用理由:** F が最もシンプルで互換性が高い。`make` 経由・直接 `docker compose` 経由どちらでも同じオーバーレイが適用され、`setup.bash env` で自動生成できるため参加者の設定ミスも減らせる。

---

## 参考リンク

- [Docker Compose GPU Support](https://docs.docker.com/compose/how-tos/gpu-support/)
- [Compose Deploy Specification](https://docs.docker.com/reference/compose-file/deploy/)
- [Docker Compose Profiles](https://docs.docker.com/compose/how-tos/profiles/)
- [Compose Merge Rules](https://docs.docker.com/reference/compose-file/merge/)
- [Docker Compose include directive](https://docs.docker.com/compose/how-tos/multiple-compose-files/include/)
- [NVIDIA Container Toolkit - Docker Specialized Configurations](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/docker-specialized.html)
- [NVIDIA CDI Support](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/cdi-support.html)
- [Multiple Compose Files](https://docs.docker.com/compose/how-tos/multiple-compose-files/)
