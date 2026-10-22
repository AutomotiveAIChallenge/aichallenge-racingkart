# practice（SIM 決勝練習: `make practice-4car`）

SIM 決勝と同じ AWSIM 条件（4台・6周・420秒・sync 開始・ハンディキャップ/ランキング on・NPC なし、S2R はオーバーテイクレーン on）で、
**任意の提出物 tar.gz を最大4つ**、1台の PC 上でレースさせます。自チームのビルド×4、自チーム vs 3チームの過去ビルドなど。

```bash
make practice-4car SUBMISSIONS="submit/a.tar.gz submit/b.tar.gz submit/c.tar.gz submit/d.tar.gz"
```

- 出走位置 N = `ROS_DOMAIN_ID` N = `SUBMISSIONS` の N 番目（SIM 決勝の「出走位置 N のチームは Autoware PC N（`ROS_DOMAIN_ID=N`）」と同じ対応）。
- 各 tar.gz は `./create_submit_file.bash` と同じ形（中身が `aichallenge_submit/` 以下）であること。
- tar.gz ごとに `output/practice/ws/<sha256>/` に workspace を作ってビルドし、再利用します（system パッケージが更新されたら再ビルド）。
- 結果は `output/<timestamp>/` に出ます: `result-summary.json` / `dN-result-details.json`（AWSIM）、`practice-manifest.json`（誰がどの位置か）、`practice-summary.md`（順位・ラップ・ペナルティ種別・順位入れ替わり）。

| 変数 | 既定 | 意味 |
|---|---|---|
| `CLASS` | `s2r` | `s2r` = `s2r-final.sh` 相当、`e2e` = `e2e-final.sh` 相当 |
| `HANDICAP` | `on` | 順位ハンディキャップ（決勝は on） |
| `NPC` | `0` | NPC 台数（決勝は 0） |
| `GRID` | `fixed` | `fixed` = 指定順、`shuffle` = `SEED` で並べ替え、`rotate` = `ROUND` だけずらす（4回回すと全員が全位置を走る） |
| `VIZ` / `HEADLESS` | `0` | `VIZ=1` で出走位置1の RViz を表示 / `HEADLESS=1` で AWSIM を `-headless`（S2R のみ。`CLASS=e2e` と併用するとカメラ・LiDAR が無効になるため起動前にエラー） |
| `PIN` | `0` | `1` で各車を3コアに固定（出走位置 N は CPU 5+3(N-1) から3コア。17 CPU 以上） |
| `KEEP` | `0` | `1` でレース後もコンテナを残す（`make down` で停止） |

全位置を回す例: `for r in 0 1 2 3; do make practice-4car SUBMISSIONS="..." GRID=rotate ROUND=$r; done`

## English

`make practice-4car SUBMISSIONS="a.tar.gz b.tar.gz c.tar.gz d.tar.gz"` races up to four arbitrary submission
tarballs on one machine under the SIM-final AWSIM settings (no NPC). Slot N is ROS_DOMAIN_ID N and start
position N, as in the SIM final. Each tarball gets its own cached workspace under `output/practice/ws/`.
Results and a summary (`practice-summary.md`) land in `output/<timestamp>/`. Options are in the table above.

## 既存環境への影響 / Effect on the existing setup

追加のみです。`docker-compose*.yml`・`run_autoware.bash`・既存の make ターゲットと simulator スクリプトは変更していません。
コンテナ設定は `aichallenge/practice/compose.practice.yml` にあり、`make practice-4car` の実行中だけ `.env` の
`COMPOSE_FILE`（gpu / sound の指定を含む）の後ろに足されます。`COMPOSE_FILE` に `docker-compose.gpu.yml` が含まれる
場合は `compose.practice.gpu.yml` も足され、各車（`autoware-slot`）にも `autoware` と同じ GPU 設定が入ります。

Additive only: `docker-compose*.yml`, `run_autoware.bash`, the existing make targets and simulator scripts are
unchanged. The container settings live in `aichallenge/practice/compose.practice.yml`, which is appended to your
`.env` `COMPOSE_FILE` (gpu / sound overlays included) only while `make practice-4car` runs. When `COMPOSE_FILE`
includes `docker-compose.gpu.yml`, `compose.practice.gpu.yml` is added too, so every car (`autoware-slot`) gets the
same GPU settings as `autoware`.

## WSL2

`vehicle/cyclonedds.xml` は CycloneDDS を `lo` に固定しています。WSL2 では `lo` に `10.255.255.254` も付くため DDS が参加者を作れず、
AWSIM が `WaitStart` のまま、各車が `wait until clock received` のまま止まります。シミュレーション用の PC では
`<NetworkInterface autodetermine="true" priority="default" multicast="default" />` に変えてください（実車ではこの変更をしないこと）。

`vehicle/cyclonedds.xml` pins CycloneDDS to `lo`. Under WSL2, `lo` also carries `10.255.255.254`, DDS cannot create
participants, and the run stops with AWSIM at `WaitStart` and every car at `wait until clock received`. On a
simulation PC, switch it to `<NetworkInterface autodetermine="true" priority="default" multicast="default" />`
(do not do this on the real kart).

## Limits

- One PC runs AWSIM and four Autoware stacks; in the SIM final each stack has its own PC (i7-8700, 16 GB).
  Timing-sensitive code can behave differently. Use `PIN=1` and compare with a solo `make eval`.
- Position swaps are counted at lap-line resolution. A pass and a re-pass in the same lap cancel out.
- Tests: `python3 -m unittest discover -s aichallenge/practice -p 'test_*.py' -v`
