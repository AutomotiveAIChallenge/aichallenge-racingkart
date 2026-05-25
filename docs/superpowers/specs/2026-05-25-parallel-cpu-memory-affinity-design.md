# Parallel Launch CPU/Memory Affinity 設計書

**Status:** **TODO — 未実装** (設計のみ確定、コード変更は未着手)
**Date:** 2026-05-25
**Author:** taiki.tanaka@tier4.jp
**対象ファイル:**

- `aichallenge/workspace/src/aichallenge_system/aichallenge_system_launch/launch/parallel.launch.xml`
- `aichallenge/workspace/src/aichallenge_system/aichallenge_screen_recorder/launch/screen_recorder.launch.xml`
- `aichallenge/run_parallel.bash`

---

## 1. 目的

`parallel.launch.xml` で同一コンテナ内に同時起動される 5 プロセスを **CPU コアと仮想メモリ単位で隔離** し、本命提出 (autoware_d1) を「単独起動相当」の性能で評価できるようにする。同時に d2/d3 を d1 と完全対称配分にして公平性を担保する。

副次的に、フィラー (d2/d3) や recorder の暴走 (リーク / 高負荷) が d1 や AWSIM を巻き添えにする事故を防ぐ。

## 2. 対象環境

| 項目       | 値                                             |
| ---------- | ---------------------------------------------- |
| 評価環境   | AWS EC2 4xlarge クラス (g4dn/g5/g6.4xlarge 等) |
| vCPU       | 16 (0-15、順序固定、P/E-core 区別なし)         |
| RAM        | 64 GB                                          |
| GPU        | 1                                              |
| OS         | Ubuntu 22.04 in Docker container (AWS Batch)   |
| 必須ツール | util-linux: `taskset`, `prlimit`               |

## 3. 設計方針

### 3.1 CPU 隔離: `taskset -c`

各プロセスに排他的なコア集合を割り当てる (重複なし)。`taskset` はカーネルの `sched_setaffinity` を呼ぶだけで、コンテナ特権を必要としない。

### 3.2 メモリ隔離: `prlimit --as=<size>`

各プロセスに **仮想メモリ (RLIMIT_AS) の上限** を設定する。fork() で子プロセスにも継承される。

- **採用理由:** コンテナ特権不要、launch XML 内で完結、暴走プロセスを単独で OOM kill できる
- **限界:** プロセスごとの上限であり、グループ合計上限ではない。cgroups v2 (`memory.max`) は AWS Batch 環境で writable cgroup が保証されないため不採用
- **値設定の原則:** **RSS 予算と同等**を既定 (タイト enforcement、リーク早期検知重視)。起動失敗が出る場合は launch arg で `mem_*:=<RSS の 1.5×>` に緩める運用とする

### 3.3 コアクリア (システムプロセス排除): `taskset -pc 0 <pid>`

`taskset` で自プロセスを target core に固定しても、Docker init / monitor / system daemon が同じ core 上でスケジュールされる可能性がある。完全隔離のため、`parallel.launch.xml` 起動前に **既存全プロセスを core 0 (余白) に集約** する。

- **方法:** `ps -eo pid=` で全 PID を列挙し、`taskset -pc 0 <pid>` で affinity を core 0 のみに変更
- **副作用:** OS daemon やシェル等は core 0 にしか乗らなくなるが、計測中は問題なし。eval 終了後に状態は (コンテナ破棄により) リセットされる
- **配置:** `aichallenge/run_parallel.bash` 冒頭、ROS 2 launch 起動前のステップとして追加

### 3.4 パラメータ化

CPU コア範囲とメモリ上限はすべて `<arg>` として宣言し、launch 時に上書き可能にする:

```
ros2 launch aichallenge_system_launch parallel.launch.xml cpu_d1:=0-7 mem_d1:=32G
```

## 4. リソース割り当て (デフォルト)

### 4.1 割り当て表

| プロセス               | CPU コア | vCPU 数 | RSS 予算  | `prlimit --as` | ROS_DOMAIN_ID |
| ---------------------- | -------- | ------- | --------- | -------------- | ------------- |
| 余白 (spare)           | 0        | 1       | 2 GB      | —              | —             |
| aichallenge_awsim_eval | 1-4      | 4       | 14 GB     | 14G            | 0             |
| autoware_d1            | 5-7      | 3       | 12 GB     | 12G            | 1             |
| autoware_d2            | 8-10     | 3       | 12 GB     | 12G            | 2             |
| autoware_d3            | 11-13    | 3       | 12 GB     | 12G            | 3             |
| screen_recorder        | 14-15    | 2       | 4 GB      | 4G             | 1             |
| **小計**               | —        | **16**  | **56 GB** | —              | —             |
| OS / Docker / AWS      | core 0   | (共有)  | 8 GB      | —              | —             |

合計 16 vCPU / 64 GB ちょうど。affinity マスク OR 検算 `0xffff` (全 16 ビット網羅、重複なし)。

### 4.2 設計根拠

| プロセス               | 設計意図                                                                                                                              |
| ---------------------- | ------------------------------------------------------------------------------------------------------------------------------------- |
| 余白 (spare)           | core 0 / 2 GB は OS / Docker / monitor / 突発スパイク用バッファ。3.3 で全システムプロセスをここに集約する                              |
| aichallenge_awsim_eval | Unity 物理 + 3 車両管理 + ROS 通信。観測 peak 2.5 vCPU に対し 4 vCPU (1.6× 余裕)、RSS 2.1 GB に対し 14 GB                              |
| autoware_d1 / d2 / d3  | **3 ノード CPU/RAM 完全対称配分** (公平性最優先)。観測 peak 1.7 vCPU/proc に対し 3 vCPU (1.76× 余裕)、RSS 0.5 GB/proc に対し 12 GB     |
| screen_recorder        | ffmpeg `-preset veryfast` 1080p10Hz エンコード。経験値 0.5-1 vCPU に対し 2 vCPU                                                        |

(観測値は `output/20260525-113939/cpu_group.log` 等のローカル taskset なしランの集計)

### 4.3 全プロセス共通の前提

- AWS Batch / Docker / OS 用に **core 0 (1 vCPU) と 8 GB** を恒常的に確保
- **d1/d2/d3 は CPU/RAM ともに完全対称** (評価公平性最優先)
- AWSIM は GPU 主体だが Unity 物理に 2.5 vCPU 程度使用、4 vCPU で十分
- 残り 2 GB は突発スパイク / カーネルページキャッシュ用余白

## 5. 実装方式

### 5.1 `<executable>` ラッパ (parallel.launch.xml)

直接 exec タイプ (AWSIM) は `cmd` 先頭に挿入:

```xml
<executable cmd="prlimit --as=$(var mem_awsim) taskset -c $(var cpu_awsim) /aichallenge/simulator/AWSIM/AWSIM.x86_64 ..."/>
```

`bash -c` タイプ (autoware_d1/d2/d3) は `exec` の直後に挿入:

```xml
<executable cmd="bash -c 'source ... &amp;&amp; exec prlimit --as='$(var mem_d1)' taskset -c '$(var cpu_d1)' ros2 launch ...'"/>
```

`$(var ...)` は bash 内では `'...'` で囲んで substitution 後の値を bash literal にする (memory: [[project_ros2_launch_xml_subst_quotes]])。

### 5.2 `<node>` ラッパ (screen_recorder.launch.xml)

`screen_recorder.launch.xml` 側に `launch_prefix` arg を追加し、`<node launch-prefix=...>` で受ける:

```xml
<arg name="launch_prefix" default=""/>
<node ... launch-prefix="$(var launch_prefix)">
```

`parallel.launch.xml` の include で:

```xml
<arg name="launch_prefix" value="prlimit --as=$(var mem_recorder) taskset -c $(var cpu_recorder)"/>
```

### 5.3 prlimit と taskset の順序

`prlimit --as=X taskset -c Y CMD` の順とする。

- `prlimit` が `--as` を設定し、`taskset` を exec
- `taskset` が affinity を設定し、`CMD` を exec
- 結果: `CMD` は AS 制限 + affinity 適用済みで起動

### 5.4 コアクリア処理 (run_parallel.bash)

`run_parallel.bash` の monitor 起動の **後**、`ros2 launch` の **前** に以下を挿入:

```bash
# Confine all pre-existing processes (including parent shell, monitor, OS daemons)
# to core 0 (the spare core), so cores 1-15 are reserved exclusively for
# AWSIM / autoware_dN / screen_recorder pinned by taskset in parallel.launch.xml.
echo "[cpu-affinity] Confining existing processes to core 0..."
moved=0
failed=0
for pid in $(ps -eo pid=); do
    if taskset -pc 0 "$pid" >/dev/null 2>&1; then
        moved=$((moved + 1))
    else
        failed=$((failed + 1))
    fi
done
echo "[cpu-affinity] Done. moved=${moved} failed=${failed} (failed = kernel threads or already-exited)"
```

監視スクリプト (`log_cpu_per_group.py`) も core 0 に乗るが、CPU 使用率は最小 (1 Hz サンプリング) なので問題なし。

## 6. インターフェース

### 6.1 追加 `<arg>` (parallel.launch.xml)

```xml
<arg name="cpu_awsim" default="1-4" description="taskset cores for AWSIM"/>
<arg name="cpu_d1" default="5-7" description="taskset cores for autoware_d1 (primary submission)"/>
<arg name="cpu_d2" default="8-10" description="taskset cores for autoware_d2"/>
<arg name="cpu_d3" default="11-13" description="taskset cores for autoware_d3"/>
<arg name="cpu_recorder" default="14-15" description="taskset cores for screen_recorder"/>
<arg name="mem_awsim" default="14G" description="prlimit --as for AWSIM (virtual mem cap)"/>
<arg name="mem_d1" default="12G" description="prlimit --as for autoware_d1"/>
<arg name="mem_d2" default="12G" description="prlimit --as for autoware_d2"/>
<arg name="mem_d3" default="12G" description="prlimit --as for autoware_d3"/>
<arg name="mem_recorder" default="4G" description="prlimit --as for screen_recorder"/>
```

### 6.2 追加 `<arg>` (screen_recorder.launch.xml)

```xml
<arg name="launch_prefix" default=""/>
```

## 7. 変更ファイル

| ファイル                                                                                                     | 変更内容                                                                                       |
| ------------------------------------------------------------------------------------------------------------ | ---------------------------------------------------------------------------------------------- |
| `aichallenge/workspace/src/aichallenge_system/aichallenge_system_launch/launch/parallel.launch.xml`          | arg 10 個追加 / 4 つの cmd を `prlimit + taskset` でラップ / include に `launch_prefix` を渡す |
| `aichallenge/workspace/src/aichallenge_system/aichallenge_screen_recorder/launch/screen_recorder.launch.xml` | `launch_prefix` arg 追加 / `<node>` に `launch-prefix` 属性追加                                |
| `aichallenge/run_parallel.bash`                                                                              | ROS 2 launch 起動前に全プロセスを core 0 に集約するブロックを追加                              |

## 8. 検証

### 8.1 起動後の affinity 確認

```bash
# CPU affinity (各 PID)
taskset -p <pid>

# 仮想メモリ上限 (各 PID)
prlimit --pid <pid> --as
# または
grep "Max address space" /proc/<pid>/limits

# システムプロセスが core 0 に集約されたか確認
for pid in $(ps -eo pid=); do
    mask=$(taskset -p "$pid" 2>/dev/null | awk '{print $NF}')
    cmd=$(cat /proc/$pid/comm 2>/dev/null)
    echo "pid=$pid mask=$mask cmd=$cmd"
done | sort -k2 | uniq -c -f1 | sort -rn | head
```

### 8.2 期待値 (16 進 affinity マスク)

| プロセス               | 期待マスク           | 期待 `--as` (bytes) |
| ---------------------- | -------------------- | ------------------- |
| aichallenge_awsim_eval | `1e` (cores 1-4)     | 15032385536 (14 GB) |
| autoware_d1            | `e0` (cores 5-7)     | 12884901888 (12 GB) |
| autoware_d2            | `700` (cores 8-10)   | 12884901888         |
| autoware_d3            | `3800` (cores 11-13) | 12884901888         |
| screen_recorder        | `c000` (cores 14-15) | 4294967296 (4 GB)   |
| その他システムプロセス | `1` (core 0)         | (制限なし)          |

### 8.3 動作確認

`vehicles:=3 capture:=true` で起動 → 30 秒経過後に各 PID で上記マスク/AS が一致することを確認 → 通常停止。

## 9. リスクと緩和策

| リスク                                | 影響                                  | 緩和策                                                          |
| ------------------------------------- | ------------------------------------- | --------------------------------------------------------------- |
| `--as` 値が小さすぎる                 | 起動失敗 / 部分機能の OOM             | 既定は RSS と同等。起動失敗時は `mem_*:=<RSS の 1.5×>` で緩和   |
| recorder が encode 追いつかない       | 録画コマ落ち。評価には影響なし        | `preset veryfast` でエンコードコスト最小化済み                  |
| d2/d3 がリーク → AS 上限到達          | 当該プロセスのみクラッシュ。d1 は無傷 | 設計通り (隔離効果)                                             |
| `taskset`/`prlimit` が PATH にない    | launch 失敗                           | util-linux は Ubuntu base に同梱                                |
| コアクリア時に重要プロセスを巻き込む  | OS / docker init が core 0 に集中     | コンテナ内処理なのでホストには波及せず、コンテナ破棄で復旧      |
| 後発プロセスが core 1-15 に乗る       | 計測中に新たな system daemon が紛れる | 評価コンテナは ephemeral でアプリ用途のみ、実害は限定的         |

## 10. 非対象 (YAGNI)

- `cpulimit` / `nice` / `chrt` による別方式の CPU 制限
- cgroups v2 (`memory.max`) によるグループ合計メモリ制限
- docker-compose の `cpus:` / `mem_limit:` 設定変更
- `isolcpus` カーネルブートパラメータ
- `vehicles` 引数に応じた CPU/メモリの動的配分
- GPU メモリ制限 (`nvidia-smi -lgc` 等)
- 監視用ダッシュボード / 自動アラート

## 11. ロールバック

すべての変更は追記のみ。

- launch XML: `<arg>` を 0 個に戻し `prlimit ... taskset ...` プレフィックスを外せば元の挙動
- `run_parallel.bash`: 追加したコアクリアブロックを削除

---

## Self-Review

- **Placeholder scan:** TBD / TODO なし。すべての値・コマンド・期待値が具体化済み。
- **Internal consistency:** §4.1 配分表 / §6.1 arg 既定値 / §8.2 検証期待値 で CPU/メモリ値一致。OR-mask 検算 `0xffff` で重複・抜けなし。
- **Scope check:** 単一 launch ファイル + 依存 1 ファイル + 1 シェルスクリプト。implementation plan 1 本に収まる。
- **Ambiguity check:** `--as` は仮想メモリ上限と明示。コアクリアの副作用 (OS daemon を core 0 に集約) も §3.3 と §9 で明示。
