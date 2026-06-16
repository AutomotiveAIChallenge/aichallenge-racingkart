# 設計: 全 dev サービスをホスト UID で実行する（rocker 相当を docker compose で再現）

## 実装状況 (2026-06-14)

- DONE: 設計どおり実装済み — `docker-compose.yml` の両 base アンカーに `user:` / `HOME=/tmp` / `group_add` を追加。Makefile がホスト GID を export。コンテナはホスト UID/GID で実行される。
- DEVIATION: `docker-compose.sound.yml` は `simulator` サービスのみパッチ済み（`autoware` サービスへの適用なし）。設計本文の YAML 例には `autoware:` と `simulator:` 両方が記載されている。
- DEVIATION: `group_add` にはグループ名（`render`/`video`/`input`）ではなく数値 GID（`${HOST_GID_RENDER:-110}` 等）を使用。「既知のリスクと対処」に記載した切替策を当初から採用している。
- NOT DONE: `docker-entrypoint.sh` の 2 行目に `for rocker sessions` というコメントが残存（`# Used as ENTRYPOINT in Dockerfile and sourced from .bashrc for rocker sessions.`）。設計の「変更不要」扱いと矛盾しないが、repo-cruft-cleanup 計画の Task 3 Step 4 で予定していたコメント更新は未適用。

## 背景と目的

PulseAudio をコンテナから利用する際、PC によって音が出る／出ない場合がある。原因はホスト側オーディオサーバー（PipeWire か本物の PulseAudio か）と、コンテナ内プロセスの UID がホストユーザーと一致するかの差にある。本物の PulseAudio は UNIX ソケット接続時に「cookie 一致」または「SO_PEERCRED で見たクライアント UID = サーバー UID」のいずれかでのみ認証を通す。現状コンテナは root で動くため、cookie 未マウントだと本物の PulseAudio で拒否される。

`rocker` はホストの UID/GID を持つユーザーをコンテナ内に実行時に作ることで、この問題と生成ファイルの所有者問題を解決している。本設計では同等の効果を docker compose の `user:` 指定で実現し、全サービスをホスト UID に統一する。

## 方針

compose の `user:` 指定のみで実現する（entrypoint でのユーザー生成や Dockerfile での useradd は行わない）。全サービスをホスト UID に統一する。

### 既存コードとの互換性

compose の全サービスが `user:` で非 root 実行されるため、`/output` への書き込みは常にホストユーザー所有になり、root → ホストユーザーの chown 自体が不要になった。

- `aichallenge/build_autoware.bash` — `if [ "$(id -u)" -eq 0 ]` でガードされ、非 root 時は chown をスキップ（こちらは残置）。
- `aichallenge/run_autoware.bash` / `run_evaluation.bash` / `vehicle/run_{driver,zenoh}.bash` / `utils/record_all_rosbag.bash` — 以前は EXIT trap で `fix_ownership.bash` を呼んでいたが、非 root 実行では常に no-op のため、`fix_ownership.bash` 本体ごと削除した。

なお AWS Batch 上の eval ジョブは docker compose を使わず root で動くが、`aichallenge-aws/base_image/aichallenge/` 配下に独自コピーのスクリプトを持つため、本リポジトリの削除の影響は受けない（AWS 側で chown が必要なら、そちらのコピーで個別に維持する）。

## 変更内容

### 1. docker-compose.yml — base アンカーに `user:` / `HOME` / `group_add` を追加

`x-autoware-base` と `x-racing_kart_interface-base` の両方に以下を追加する。

```yaml
x-autoware-base: &autoware-base
  user: "${HOST_UID:-1000}:${HOST_GID:-1000}"
  group_add:
    - video
    - render
    - input
  environment:
    - HOME=/tmp
    # ...既存の環境変数...
```

- `user:` — ホスト UID/GID でプロセスを実行。`HOST_UID`/`HOST_GID` は Makefile が `id -u`/`id -g` から export 済み。未設定時のフォールバックは 1000。
- `HOME=/tmp` — `/etc/passwd` にエントリのない UID で起動すると HOME が `/` になり、ROS（`~/.ros`）・colcon・Qt（rviz）が書き込みエラーを起こす。コンテナごとに独立した `/tmp` を HOME にして回避する。
- `group_add` — `/dev/dri`（render/video）と `/dev/input` のデバイスファイルへアクセスするため。`privileged: true` により cgroup デバイス制限はないが、ファイルパーミッション（660）対策として補助グループを付与する。

`docker-compose.yml` の `autoware-simulator-evaluation` サービスは `x-autoware-base` を継承せず、`user:`/`HOME=/tmp`/`group_add` を同じ値で直接定義している（アンカー継承なし）。このサービスも `user:` で非 root 実行されるため、所有者問題は起きない。

`x-racing_kart_interface-base` は既存の `group_add: [dialout]` に `video`/`render` を統合し、`user:` と `HOME=/tmp` を追加する（`input` は追加していない）。

### 2. docker-compose.sound.yml — ソケット単体マウントからディレクトリマウントへ

```yaml
x-sound: &sound
  environment:
    - PULSE_SERVER=unix:/tmp/pulse/native
  volumes:
    - /run/user/${HOST_UID:-1000}/pulse:/tmp/pulse
```

- UID 一致により cookie 不要で PulseAudio 認証が通る（このPCで `setpriv --reuid 1000` 経由で実証済み）。`PULSE_COOKIE` 行は不要なので削除。
- `pulse` ディレクトリごとマウントすることで、ホスト側オーディオデーモン再起動でソケットの inode が変わっても新しいソケットが見える（ソケットファイル単体マウントだと stale になる問題を回避）。

### 3. docker-entrypoint.sh — 変更不要

entrypoint は `ip link set` / `sysctl` を `|| true` で無視するため、非 root では単にこれらが失敗して何も出力されない。`./setup.bash network tune` 済みであれば実害はない。WARN ロジックや `_aic_rmem_max`・MULTICAST 確認は存在せず、entrypoint 自体には変更は不要。

## 既知のリスクと対処

| リスク | 対処 |
|---|---|
| `group_add` の名前（render/video）がイメージ内 `/etc/group` の GID で解決され、ホストの `/dev/dri/renderD128` の GID と不一致 | `privileged: true` のため cgroup 制限はなく、問題はファイルパーミッションのみ。動作確認で `/dev/dri` が読めなければ Makefile から `getent group render \| cut -d: -f3` でホスト実 GID を渡す方式に切り替える |
| `HOME=/tmp` 共有による設定衝突 | 各コンテナの `/tmp` は独立しており衝突しない |
| AWS Batch 上の eval が影響を受ける | Batch は compose を使わないため対象外。chown 系コードは残すので従来どおり root で動く |
| ホスト UID が 1000 でない PC | Makefile が `HOST_UID`/`HOST_GID` を export 済みのため正しく解決される。`make` 経由が前提 |

## 検証方法

1. `make autoware-build` — ビルドが成功し、`aichallenge/workspace/{build,install,log}` の所有者がホストユーザー（`t4tanaka`）になること。
2. `make dev` → `make autoware-request-initialpose` → `make autoware-request-control` — AWSIM 起動・自動運転が従来どおり動くこと。
3. コンテナ内で `pactl info` が成功し、AWSIM の効果音がホストスピーカーから鳴ること。
4. `make rviz2` — rviz が Qt エラーなく起動すること（HOME 書き込み確認）。
5. `make ps` / `make down` — 起動・停止が正常なこと。

## スコープ外

- AWS Batch の eval 経路の変更（root 実行のまま）。
- Dockerfile での useradd や entrypoint でのユーザー生成（compose `user:` のみで実現）。
- cookie ベース認証への切り替え（UID 一致方式を採用するため不要）。
