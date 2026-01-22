# run_evaluation.bash リファクタリングメモ

本ファイルは `aichallenge/run_evaluation.bash` の「現状の実装」と「意図/残課題」をまとめたメモです。

## 現状の実装（2026-01-22 時点）

### 評価フロー（概略）

`aichallenge/run_evaluation.bash` は以下の流れで評価をオーケストレーションします。

1. 出力ディレクトリ作成（`/output/<timestamp>`、`latest` シンボリックリンク）
2. ROS/Autoware/overlay 環境の `source` と `ROS_DOMAIN_ID` の設定
3. ネットワーク設定（`sudo -n ip link ...` / `sudo -n sysctl ...` を best-effort 実行）
4. AWSIM 起動（`run_simulator.bash eval` を `nohup` で起動）
5. AWSIM 準備待ち（`publish.bash check-awsim` を利用）
6. Autoware 起動（`run_autoware.bash awsim <domain>` を `nohup` で起動）
7. （可能なら）ウィンドウ移動（`wmctrl` がある場合のみ、タイムアウト付き）
8. 初期姿勢/制御要求（`/aichallenge/publish.bash request-initialpose` → `request-control`）
9. 任意で画面キャプチャ・rosbag 開始（フラグ指定時）
10. AWSIM 終了待ち → 結果変換（`result-details.json` を最大待ち）→ 終了
11. 終了時 `trap` により後処理（キャプチャ停止/rosbag停止/権限調整）

### 引数/オプション

- `rosbag` / `--rosbag`: rosbag 記録を有効化
- `capture` / `--capture`: 画面キャプチャを有効化
- `--uid N` / `--gid N`（互換: 末尾に `<uid> <gid>`）: 生成物の `chown` 用
- `--domain-id N`: Autoware 側の `ROS_DOMAIN_ID` として使用
- `--output-root PATH`: 出力先（デフォルト `/output`）
- `--result-wait-seconds N`: `result-details.json` の待ち秒数（デフォルト 60）
- 未知引数: 無視（互換のため保持して警告ログ）

### 「落ちても最低限は残す」ための工夫

- `trap cleanup EXIT SIGINT SIGTERM` により、通常終了/中断（Ctrl+C 等）でも後処理を走らせる
- `sudo` は `sudo -n` を best-effort で呼び、パスワード待ちでハングしないようにする
- `wmctrl` が無い/見つからない場合はウィンドウ制御をスキップし、評価自体は続行する
- `result-details.json` を最大待ちしてから `result-converter.py` を呼ぶ（遅延生成の吸収）

## 現状の注意点 / 残課題

### 1) Domain ID の扱いが分かりづらい
- `check_simulator_ready()` で `ROS_DOMAIN_ID` を一時的に `ROS_DOMAIN_ID_SIM` に切り替えています。
- 切り替え後の復帰先が `ROS_DOMAIN_ID_DEFAULT` 固定のため、`--domain-id` 指定時は意図とズレる可能性があります（「元の値に戻す」のが読みやすい）。

### 2) `publish.bash` 呼び出し結果の扱い
- `run_evaluation.bash` は `/aichallenge/publish.bash request-control` 等を呼びますが、現状の `publish.bash` は基本的に `exit 0` で終わるため、失敗を exit code で受け取りにくいです。
- `request_control` の「成功判定」を `run_evaluation.bash` 側で取りたい場合は、以下のどれかに寄せるのが読みやすいです。
  - `publish.bash` を「関数ライブラリ + CLIラッパ」に分け、関数が適切な終了コードを返す
  - `ros2 service call` の出力（`success:` 等）をパースして終了コード化する
  - サービス応答ではなく、状態トピック（例: control mode state）で遷移確認して判定する

### 3) ネットワーク設定の重複
- `run_evaluation.bash` 側は `sudo -n` の best-effort ですが、`run_simulator.bash` 側にも `sudo ip link ...` 等があり、環境によってはそこがブロッカーになり得ます。
- 「どこが責務を持つか」を整理すると追いやすくなります（例: 片側に寄せる）。

## 参考: さらなる整理案（未反映）

- `run_evaluation.bash` の中を「lib化して source」する場合は、`main "$@"` を直実行時だけ呼ぶ構造（`BASH_SOURCE` チェック）にして、再利用時に副作用が出ないようにする。
