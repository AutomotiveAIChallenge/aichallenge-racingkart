
# 単一Docker内の複数Autoware並列実行

## 概要

1つのDockerコンテナ内で3つの異なる参加者コードを **同時実行** できる仕組み。
各参加者コードは異なるROS Domain IDで独立した通信空間を持つ。

## アーキテクチャ

```
1つのDocker (aichallenge-2025-parallel)
├── /aichallenge/workspace/              ← D1（eval から継承）
├── /aichallenge/d2/workspace/           ← D2 提出物
└── /aichallenge/d3/workspace/           ← D3 提出物

各ワークスペースは独立した colcon build（--symlink-install）

↓ parallel.launch.xml が全体を起動

├── Domain ID 0: AWSIM Simulator + awsim_state_manager
├── Domain ID 1: Autoware D1 （<include> で直接起動）
├── Domain ID 2: Autoware D2 （bash -c で D2 workspace を source して起動）
└── Domain ID 3: Autoware D3 （bash -c で D3 workspace を source して起動）

↓ domain_bridge で Domain 0 ↔ Domain 1-3 を接続

出力ディレクトリ:
/output/<timestamp>/
├── d1/ros/log/    ← D1 の ROS ログ
├── d2/ros/log/    ← D2 の ROS ログ
└── d3/ros/log/    ← D3 の ROS ログ
```

## 実装詳細

### 1. Dockerfile（parallel ターゲット）

eval ステージを継承し、D2-D3 ワークスペースを追加ビルド：

```dockerfile
FROM eval AS parallel

ARG SUBMIT_TAR_D2=submit/aichallenge_submit2.tar.gz
ARG SUBMIT_TAR_D3=submit/aichallenge_submit3.tar.gz

COPY ${SUBMIT_TAR_D2} /tmp/s2.tgz
COPY ${SUBMIT_TAR_D3} /tmp/s3.tgz
RUN mkdir -p /aichallenge/d2/workspace/src /aichallenge/d3/workspace/src \
 && tar zxf /tmp/s2.tgz -C /aichallenge/d2/workspace/src \
 && tar zxf /tmp/s3.tgz -C /aichallenge/d3/workspace/src \
 && rm /tmp/s2.tgz /tmp/s3.tgz

# D2-D3 を並列ビルド
RUN bash -c ' \
    source /aichallenge/workspace/install/setup.bash; \
    for d in 2 3; do \
        ( cd /aichallenge/d${d}/workspace; \
          colcon build --symlink-install ...; \
          chmod -R a+rwX /aichallenge/d${d}/workspace/install || true; \
        ) & \
    done; \
    wait'

CMD ["bash", "/aichallenge/run_parallel.bash"]
```

**ポイント:**

- D2-D3 ビルド時に D1 workspace を source（`aichallenge_system_launch` 等の依存解決）
- `chmod -R a+rwX` で install dir の書き込み権限を付与（rocker 非root ユーザー対応）
- D2-D3 は並列ビルド（独立ワークスペース）

### 2. parallel.launch.xml

1つの launch ファイルで Simulator + 3台の Autoware を起動。
各ドメインは `<set_env>` で `ROS_DOMAIN_ID`、`ROS_HOME`、`ROS_LOG_DIR` を分離：

```xml
<!-- D1: entrypoint で source 済みなので直接 include -->
<group>
    <set_env name="ROS_DOMAIN_ID" value="1"/>
    <set_env name="ROS_HOME" value="$(var log_dir)/d1/ros"/>
    <set_env name="ROS_LOG_DIR" value="$(var log_dir)/d1/ros/log"/>
    <include file=".../aichallenge_system.launch.xml">
        <arg name="domain_id" value="1"/>
    </include>
</group>

<!-- D2-D3: bash -c で各 workspace を source してから launch -->
<group if="$(eval $(var vehicles)>=2)">
    <set_env name="ROS_DOMAIN_ID" value="2"/>
    <set_env name="ROS_HOME" value="$(var log_dir)/d2/ros"/>
    <set_env name="ROS_LOG_DIR" value="$(var log_dir)/d2/ros/log"/>
    <executable name="autoware_d2" output="screen" cmd="bash -c &quot;
        source /aichallenge/d2/workspace/install/setup.bash &amp;&amp;
        exec ros2 launch aichallenge_system_launch aichallenge_system.launch.xml
            simulation:=$(var simulation) domain_id:=2 ...
    &quot;"/>
</group>
```

**設計判断:**

- D1 は `<include>` で起動（entrypoint が D1 workspace を source 済み）
- D2-D3 は `<executable cmd="bash -c ...">` で起動（`<set_env AMENT_PREFIX_PATH>` は `$(find-pkg-share)` に効かないため）
- D2-D3 の `capture`/`rosbag` 等のパラメータは親の `$(var ...)` から伝搬
- 各ドメインの `ROS_HOME`/`ROS_LOG_DIR` を `<set_env>` で分離し、ログの混在を防止

### 3. run_parallel.bash

`parallel.launch.xml` を起動するエントリポイント：

```bash
#!/usr/bin/env bash
ts="$(date +%Y%m%d-%H%M%S)"
out_dir="/output/${ts}"
# ... ログ設定 ...
exec ros2 launch aichallenge_system_launch parallel.launch.xml \
    "log_dir:=${out_dir}" "simulation:=true" ...
```

## ビルドと実行

```bash
# ビルド（3つの submission を指定）
./docker_build.sh parallel --submit a.tar.gz b.tar.gz c.tar.gz

# 実行
./docker_run.sh parallel
```

## 実装状況

| ファイル | 説明 | 状態 |
| --- | --- | --- |
| `Dockerfile` | parallel ターゲット（D2-D3 ワークスペース） | done |
| `docker_build.sh` | ビルドスクリプト（`--submit` で3つ指定） | done |
| `docker_run.sh` | 実行スクリプト（parallel ターゲット対応） | done |
| `parallel.launch.xml` | Simulator + D1-D3 並列起動 | done |
| `aichallenge_system.launch.xml` | `rviz_config` 引数の外部指定対応 | done |
| `run_parallel.bash` | parallel.launch.xml のエントリポイント | done |

## 動作フロー

```text
./docker_build.sh parallel --submit a.tar.gz b.tar.gz c.tar.gz
    ↓
1. docker build --target parallel
   ├─ eval ステージで D1 ビルド
   └─ parallel ステージで D2-D3 並列ビルド
    ↓
./docker_run.sh parallel
    ↓
2. rocker で aichallenge-2025-parallel コンテナ起動
   └─ /aichallenge/run_parallel.bash が実行
    ↓
3. parallel.launch.xml が起動
   ├─ AWSIM (Domain 0, --vehicles 3, --start-mode sync)
   ├─ awsim_state_manager (Domain 0)
   ├─ Autoware D1 (Domain 1, <include>)
   ├─ Autoware D2 (Domain 2, bash -c + source d2 workspace)
   └─ Autoware D3 (Domain 3, bash -c + source d3 workspace)
    ↓
4. 結果出力: /output/<timestamp>/d{1-3}/
```

## 技術的な注意点

- eval stage は `--target eval` でも `--target parallel` でも同一の処理。parallel は `FROM eval AS parallel` で継承するだけなので、eval の動作保証は壊れない
- D2-D3 ビルド時は `/aichallenge/workspace/install/setup.bash`（D1）を source する（`aichallenge_system_launch` の依存解決に必要）
- 各ドメインの `ROS_HOME`/`ROS_LOG_DIR` は `<set_env>` でドメインごとに分離（`/output/<ts>/d{N}/ros/`）
