# Docker Compose GPU 統合: 2ファイル → 1ファイル化の検討

## 現状の構成

現在、GPU 対応は **2ファイル + Makefile** で管理されている:

| ファイル | 役割 |
|---|---|
| `docker-compose.yml` | ベース定義 (全サービス、YAML anchor `x-autoware-base`) |
| `docker-compose.gpu.yml` | GPU オーバーライド (`deploy.resources.reservations.devices` + NVIDIA env) |
| `Makefile` | GPU 自動検出 → `-f` フラグの切り替え |

```makefile
# 現在のMakefile抜粋
ifeq ($(GPU_ENABLED),1)
DC := docker compose -f docker-compose.yml -f docker-compose.gpu.yml
else
DC := docker compose -f docker-compose.yml
endif
```

**問題点:**
- GPU サービス追加時に 2 ファイルを同期して編集する必要がある
- `docker-compose.gpu.yml` は全サービスに同じ `<<: *gpu` を繰り返すだけのボイラープレート
- `make` を経由しないと GPU が有効にならない (直接 `docker compose up` すると CPU モードになる)

---

## アプローチ比較

### A. Profiles (推奨)

Docker Compose の [profiles](https://docs.docker.com/compose/how-tos/profiles/) 機能を使い、GPU 設定を条件付きで有効化する。

**仕組み:** `profiles` 属性付きのサービスは、`--profile` で明示的に有効化しない限り起動されない。これを応用し、GPU 用の「シャドーサービス」を定義せず、**YAML anchor + profiles の組み合わせ**で制御する。

ただし、**profiles は「サービス単位」の制御**であり、「同じサービスの deploy セクションだけを条件付きで有効にする」ことはできない。そのため、2つのサブパターンがある:

#### A-1. GPU/CPU サービスを分離するパターン

```yaml
x-autoware-common: &autoware-common
  image: "aichallenge-2025-dev"
  privileged: true
  # ... 共通設定

x-gpu-resources: &gpu-resources
  deploy:
    resources:
      reservations:
        devices:
          - driver: nvidia
            count: all
            capabilities: [gpu]
  environment:
    - NVIDIA_VISIBLE_DEVICES=all
    - NVIDIA_DRIVER_CAPABILITIES=all

services:
  autoware:
    <<: *autoware-common
    profiles: [cpu]
    command: [...]

  autoware-gpu:
    <<: [*autoware-common, *gpu-resources]
    profiles: [gpu]
    command: [...]   # autoware と同じ command を重複定義
```

```bash
docker compose --profile gpu up -d autoware-gpu   # GPU モード
docker compose --profile cpu up -d autoware        # CPU モード
```

**問題点:**
- 全サービスが GPU/CPU の 2 つずつ必要 → サービス数が倍増
- command などの設定が重複する
- YAML anchor の `<<:` マージは**浅いマージ (shallow merge)** なので、`environment` リストなどが上書きされる可能性がある

#### A-2. profiles でオプショナルな GPU sidecar を定義するパターン

```yaml
services:
  autoware:
    <<: *autoware-base
    command: [...]

  gpu-enabler:
    profiles: [gpu]
    # GPU デバイスの検証用 (実用性は低い)
```

**問題点:** profiles はサービス単位なので、既存サービスに GPU deploy を「注入」する用途には不向き。

**Profiles の評価:**

| 項目 | 評価 |
|---|---|
| 1ファイル化 | ○ |
| サービス重複の回避 | × (A-1) or 目的外使用 (A-2) |
| Makefile 不要化 | △ (プロファイル名の指定は必要) |
| 保守性 | × サービス倍増で逆に悪化するリスク |

---

### B. `include` ディレクティブ

Docker Compose の [`include`](https://docs.docker.com/compose/how-tos/multiple-compose-files/include/) を使って GPU ファイルを取り込む。

```yaml
# docker-compose.yml (include 版)
include:
  - path: docker-compose.gpu.yml  # 条件付きでは読み込めない

services:
  autoware:
    <<: *autoware-base
    # ...
```

**問題点:**
- `include` は**無条件**で読み込まれる → GPU がないホストでエラーになる
- `include` されたファイル内のリソースが現在のモデルと衝突するとエラー
- 結局「GPU あり/なし」の切り替えは外部 (Makefile や環境変数) が必要

**評価:** 現在の `-f` パターンと本質的に同じ。条件分岐ができないため不向き。

---

### C. 環境変数 + NVIDIA デフォルトランタイム (推奨)

**ホスト側で `nvidia` を Docker のデフォルトランタイムに設定**し、compose ファイルからは `deploy` セクションを完全に削除するアプローチ。

#### ホスト設定 (`/etc/docker/daemon.json`)

```json
{
  "default-runtime": "nvidia",
  "runtimes": {
    "nvidia": {
      "path": "nvidia-container-runtime",
      "runtimeArgs": []
    }
  }
}
```

参考: [NVIDIA Container Toolkit - Docker Specialized Configurations](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/docker-specialized.html)

#### compose ファイル (1ファイルに統合)

```yaml
x-autoware-base: &autoware-base
  image: "aichallenge-2025-dev"
  privileged: true
  environment:
    # GPU 制御は環境変数のみで行う
    # GPU 無効時は Makefile から空文字を渡す → NVIDIA runtime がデバイスを公開しない
    - NVIDIA_VISIBLE_DEVICES=${NVIDIA_VISIBLE_DEVICES:-}
    - NVIDIA_DRIVER_CAPABILITIES=${NVIDIA_DRIVER_CAPABILITIES:-}
  # ... 他の設定

services:
  autoware:
    <<: *autoware-base
    command: [...]
```

```makefile
# Makefile (簡素化)
ifeq ($(GPU_ENABLED),1)
NVIDIA_VISIBLE_DEVICES ?= all
NVIDIA_DRIVER_CAPABILITIES ?= all
else
NVIDIA_VISIBLE_DEVICES :=
NVIDIA_DRIVER_CAPABILITIES :=
endif
export NVIDIA_VISIBLE_DEVICES NVIDIA_DRIVER_CAPABILITIES
DC := docker compose
```

**メリット:**
- `docker-compose.gpu.yml` が完全に不要
- 既に `docker-compose.yml` の `x-autoware-base` に `NVIDIA_VISIBLE_DEVICES` があるため、**ほぼ現状のまま**
- `deploy` セクション不要 → compose ファイルがシンプル
- `NVIDIA_VISIBLE_DEVICES=` (空文字) で GPU を無効化、`all` で有効化

**デメリット:**
- ホスト側の `daemon.json` 設定が**前提条件**になる (全参加者の環境統一が必要)
- `privileged: true` が既に設定されている場合はデバイスアクセスは問題ないが、`deploy.resources` による正式な GPU 予約がない
- Docker Swarm モードでは動作しない (今回は関係なし)

**評価:**

| 項目 | 評価 |
|---|---|
| 1ファイル化 | ◎ |
| サービス重複の回避 | ◎ |
| Makefile 簡素化 | ◎ |
| 保守性 | ◎ |
| 環境依存性 | △ (daemon.json が必要) |

---

### D. deploy セクションを直接埋め込み + 環境変数で制御

`deploy.resources.reservations.devices` を YAML anchor 内に直接書き、GPU の有無は環境変数で制御するアプローチ。

```yaml
x-autoware-base: &autoware-base
  image: "aichallenge-2025-dev"
  privileged: true
  environment:
    - NVIDIA_VISIBLE_DEVICES=${NVIDIA_VISIBLE_DEVICES:-}
    - NVIDIA_DRIVER_CAPABILITIES=${NVIDIA_DRIVER_CAPABILITIES:-}
  deploy:
    resources:
      reservations:
        devices:
          - driver: nvidia
            count: all
            capabilities: [gpu]
  # ... 他の設定

services:
  autoware:
    <<: *autoware-base
    command: [...]
```

**問題点:**
- **GPU がないホストで `deploy.resources.reservations.devices` にnvidia driver を指定するとエラーになる**
- `count` や `device_ids` を環境変数で動的に制御する方法が公式にはない
- `NVIDIA_VISIBLE_DEVICES=` (空) にしても `deploy` セクション自体がデバイス予約を試みてしまう

**評価:** GPU なし環境でエラーになるため、単独では使えない。

---

### E. CDI (Container Device Interface) ドライバー

Docker 25+ で利用可能な [CDI](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/cdi-support.html) を使うアプローチ。

```yaml
services:
  autoware:
    <<: *autoware-base
    deploy:
      resources:
        reservations:
          devices:
            - driver: cdi
              device_ids:
                - nvidia.com/gpu=all
```

**メリット:**
- `nvidia` ドライバーの代わりに標準化された CDI を使用
- 将来的に Docker のデフォルトになる可能性

**デメリット:**
- Docker 25 以上 + experimental モードが必要
- CDI spec の事前生成が必要 (`nvidia-ctk cdi generate`)
- アプローチ D と同様、GPU なしホストでのエラー問題は残る
- 現時点でのエコシステム成熟度が低い

**評価:** 将来性はあるが、現時点では安定性に欠ける。

---

### F. 現状パターンの維持 + 軽微な改善

現在の **`-f` フラグによるファイルマージ**は、Docker 公式ドキュメントでも推奨されているパターン ([Merge Compose files](https://docs.docker.com/compose/how-tos/multiple-compose-files/merge/))。

改善案:
1. `docker-compose.gpu.yml` のボイラープレートを減らす
2. `.env` ファイルで `COMPOSE_FILE` を動的設定

```bash
# .env (GPU ありホスト)
COMPOSE_FILE=docker-compose.yml:docker-compose.gpu.yml
```

```bash
# .env (GPU なしホスト)
COMPOSE_FILE=docker-compose.yml
```

これにより `make` を経由しなくても `docker compose up` が正しく動作する。

**評価:** 最小限の変更で改善できるが、2ファイル構成は維持される。

---

## 総合比較

| アプローチ | 1ファイル | GPU/CPU 切替 | 保守性 | 環境依存 | 互換性 |
|---|---|---|---|---|---|
| **A. Profiles** | ◎ | ○ | × (サービス倍増) | なし | ◎ |
| **B. include** | △ (実質2ファイル) | × (条件分岐不可) | △ | なし | ◎ |
| **C. デフォルトランタイム** | ◎ | ◎ (env var) | ◎ | △ (daemon.json) | ○ |
| **D. deploy 直接埋め込み** | ◎ | × (GPUなしでエラー) | ○ | なし | × |
| **E. CDI** | ◎ | × (GPUなしでエラー) | ○ | × (Docker 25+) | × |
| **F. 現状改善** | × (2ファイル) | ◎ | ◎ | なし | ◎ |

---

## 推奨: ハイブリッドアプローチ (C + F の組み合わせ)

### 結論

**完全な 1 ファイル化の最善策は「C. デフォルトランタイム + 環境変数」**だが、`daemon.json` の設定がホスト依存であるという制約がある。

現実的な推奨は以下のハイブリッド:

1. **`docker-compose.yml` をメインの唯一のファイルとして設計**
   - `deploy` セクションは書かない
   - `NVIDIA_VISIBLE_DEVICES` / `NVIDIA_DRIVER_CAPABILITIES` 環境変数で GPU 制御
   - `privileged: true` (既存) でデバイスアクセスは確保済み

2. **`docker-compose.gpu.yml` はフォールバックとして残す**
   - `daemon.json` が設定されていないホスト用
   - Makefile が自動で `-f` フラグを追加 (既存ロジック維持)

3. **`setup.bash` に daemon.json の自動設定を追加**
   - `nvidia` をデフォルトランタイムに設定するオプションを提供
   - 設定済みなら `docker-compose.gpu.yml` が不要であることを案内

### 具体的な変更案

```yaml
# docker-compose.yml (変更なし、現状でほぼ対応済み)
x-autoware-base: &autoware-base
  image: "aichallenge-2025-dev"
  privileged: true
  environment:
    # Makefile が GPU_ENABLED=1 のとき all を export、それ以外は空
    - NVIDIA_VISIBLE_DEVICES=${NVIDIA_VISIBLE_DEVICES:-}
    - NVIDIA_DRIVER_CAPABILITIES=${NVIDIA_DRIVER_CAPABILITIES:-}
  # deploy セクションなし → daemon.json で nvidia runtime がデフォルトなら GPU が有効
  # daemon.json 未設定なら -f docker-compose.gpu.yml で補完
```

```makefile
# Makefile (変更案)
# daemon.json に nvidia runtime が設定されているか確認
HAS_NVIDIA_RUNTIME := $(shell docker info 2>/dev/null | grep -q "Default Runtime: nvidia" && echo 1 || echo 0)

ifeq ($(GPU_ENABLED),1)
  ifeq ($(HAS_NVIDIA_RUNTIME),1)
    # daemon.json 設定済み → 1 ファイルで OK
    DC := docker compose
  else
    # daemon.json 未設定 → GPU overlay が必要
    DC := docker compose -f docker-compose.yml -f docker-compose.gpu.yml
  endif
else
  DC := docker compose
endif
```

この方式なら:
- daemon.json 設定済みホスト → **完全 1 ファイル運用**
- daemon.json 未設定ホスト → **自動で 2 ファイルにフォールバック**
- どちらのケースも `make dev` で透過的に動作

---

## 参考リンク

- [Docker Compose GPU Support](https://docs.docker.com/compose/how-tos/gpu-support/)
- [Compose Deploy Specification](https://docs.docker.com/reference/compose-file/deploy/)
- [Docker Compose Profiles](https://docs.docker.com/compose/how-tos/profiles/)
- [Compose Merge Rules](https://docs.docker.com/reference/compose-file/merge/)
- [Docker Compose include directive](https://docs.docker.com/compose/how-tos/multiple-compose-files/include/)
- [NVIDIA Container Toolkit - Docker Specialized Configurations](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/docker-specialized.html)
- [NVIDIA CDI Support](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/cdi-support.html)
- [Docker Community Forum - Proper NVIDIA Toolkit usage](https://forums.docker.com/t/what-is-the-latest-proper-way-to-use-the-nvidia-container-toolkit-with-docker-compose/144729)
- [Multiple Compose Files](https://docs.docker.com/compose/how-tos/multiple-compose-files/)
