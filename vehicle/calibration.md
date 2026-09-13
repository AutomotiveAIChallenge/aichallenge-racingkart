# 車両別校正値と提出物の展開

`make submission-extract` は提出物 ZIP を一時展開したあと、車両固有の
accel/brake map と IMU ジャイロバイアスを適用してから提出コードを入れ替えます。
参加者 TUI の `extract` も同じ処理です。

## 保存元

```text
vehicle/.calibration/
└── A2/
    ├── accel_map.csv
    ├── brake_map.csv
    └── imu_bias.yaml
```

車両 ID は環境変数 `VEHICLE_ID`、リポジトリ直下の `.env` の順で取得します。
車両を推測するフォールバックはありません。ID が未設定・不正な場合は失敗します。
`test` も独立した保存ディレクトリを使います。

保存元は `.gitignore` 対象で、`aichallenge/workspace/` の外にあるため
`make workspace-clean` では消えません。運営が各車両 PC に実測値を配置してください。
この変更には校正値そのものや配布機能は含めません。

`imu_bias.yaml` は次の **3 キーだけ**を持つフラットな YAML です。
値は有限の数値で、単位は rad/s。以下は形式の例で、実測値ではありません。

```yaml
angular_velocity_offset_x: 0.001
angular_velocity_offset_y: -0.002
angular_velocity_offset_z: 0.003
```

## 初回配置

1. `.env` の `VEHICLE_ID` を実際の車両に合わせる。
2. `vehicle/.calibration/<VEHICLE_ID>/` を作り、その車両で校正した
   `accel_map.csv` と `brake_map.csv` を置く。
3. 同じ車両の実測 IMU バイアスを上記形式で `imu_bias.yaml` に保存する。

既に車両上で IMU を測定済みなら、現在の提出コードの値を保存できます。
チェックアウト直後の値を実測値として使わないでください。

```bash
python3 - <<'PY'
from pathlib import Path
import sys

sys.path.insert(0, "vehicle")
from calibration import DEFAULT_CALIBRATION_DIR, read_current_offsets, save_bias, vehicle_id

param = "aichallenge/workspace/src/aichallenge_submit/imu_corrector/config/imu_corrector.param.yaml"
offsets = read_current_offsets(param)
if offsets is None:
    raise SystemExit("IMU bias could not be read")
save_bias(DEFAULT_CALIBRATION_DIR / vehicle_id(Path.cwd()) / "imu_bias.yaml", offsets)
PY
```

初回測定は既存のビルド済み提出コードで Autoware を起動して
`vehicle/setup_check.sh --phase runtime` を実行することでも行えます。
正常な測定ならバイアスの保存元を自動作成します。map は運営が配置します。

## 展開時の適用

```bash
make submission-extract SUBMISSION_ID=<id>
# 車両を明示する場合（.env より優先）
VEHICLE_ID=A2 make submission-extract SUBMISSION_ID=<id>
```

| 保存元 | 提出物内の適用先 | 適用内容 |
| --- | --- | --- |
| `accel_map.csv` | `aichallenge_submit_launch/data/accel_map.csv` | ファイル全体をコピー |
| `brake_map.csv` | `aichallenge_submit_launch/data/brake_map.csv` | ファイル全体をコピー |
| `imu_bias.yaml` | `imu_corrector/config/imu_corrector.param.yaml` | 3 軸オフセットだけ置換 |

提出物に含まれる IMU のノイズ値、その他の設定やコメントは保持します。
map は `default` ヘッダ、長方形の数値テーブル、有限値、速度・ペダル軸の昇順を
確認します。IMU バイアスは 3 軸の不足・重複・不正な数値を拒否します。
提出物側に適用先のファイルや 3 軸オフセットがない場合も失敗します。

校正値の検証・適用は一時ディレクトリ内で行います。失敗しても既存の提出コードは
変更しません。入れ替えの最後は既存処理と同じ削除→rename であり、
その間のプロセス中断からの復旧は保証しません。
ビルド成果物の削除は引き続き `workspace-clean` の責務です。

## IMU 再計測時の更新

runtime チェックは既存の静止確認・ノイズ検証後、正常な測定値を
提出コードの `imu_corrector.param.yaml` と
`vehicle/.calibration/<VEHICLE_ID>/imu_bias.yaml` に保存します。
次チームの `extract` は更新後のバイアスを使います。map は自動更新しません。
車両移動、サンプル不足、ノイズ超過、計測スキップでは保存元を変更しません。

各ファイルは一時ファイルから rename して保存します。param.yaml と保存元の
2 ファイルをまとめたトランザクションではありません。param.yaml 更新後に保存元の
書き込みが失敗した場合は失敗として報告し、保存元には旧値が残ります。
エラーを解消して再計測してください。

IMU の新しい値は Autoware 再起動後に反映されます。
単体の `check_imu_bias.py` は従来どおり param.yaml を更新し、
`--bias-output <path>` を指定した場合だけ保存元にも記録します。
