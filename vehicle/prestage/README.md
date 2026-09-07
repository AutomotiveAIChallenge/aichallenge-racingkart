# prestage — 提出物の事前ビルドと暗号化配置

設計の根拠は `docs/spec/prestaged-submissions.md` を参照。ここは運用手順のみ。

**前提**: `gocryptfs` / `fusermount` / `zstd` / `python3`。ステージングは**運営のみ**が行う。

ここで「運営 PC」と呼ぶのは、SIM 決勝では席 A〜D の Autoware PC とリハーサル卓 PC、
実機決勝では車両 PC のこと。手順はどちらも同じ。

## 会場前（ビルド担当の運営 PC）

```bash
# 1. ボールトを初期化（初回のみ）。パスフレーズは運営で共有し、会場の PC には置かない
gocryptfs -init /path/to/vault

# 2. チーム一覧を用意（書式は teams.tsv.example）
#    team_id は <クラス>-<予選順位 2 桁>（general-03 / student-12）。席 (A-D) は入れない
cp vehicle/prestage/teams.tsv.example teams.tsv && $EDITOR teams.tsv

# 3. ビルドに使うイメージを用意（全 PC で同一 ID である必要がある。下記「イメージの配布」）
./docker_build.sh dev

# 4. 32 チーム分をビルドしてボールトへ（実測: 1 チーム約 40 秒、参照提出物の場合）
make prestage-build VAULT=/path/to/vault TEAMS=teams.tsv
```

`prestage_all.sh` は aic-next の認証情報を環境変数 `PRESTAGE_USERNAME` / `PRESTAGE_PASSWORD` から読む
（未設定なら開始時に 1 回だけ対話入力）。`vehicle/download_submission.sh` が使う裸の `USERNAME` /
`PASSWORD` とは**別の変数名**なので注意する。シェルの `USERNAME` は他の目的で既にエクスポートされて
いることがあり、裸の名前を使うと気付かないまま別ユーザーとして全チームを認証してしまう。

失敗したチームは `build_status: failed` として記録され、処理は継続する。
最後のサマリを確認し、必要なら `--team <id> --force` で個別に再実行する。
提出締切後に提出を更新したチームがいる場合も、同じコマンドでそのチームだけ再 prestage する
（ネットワークとビルド時間が必要なので、会場では締切を守ってもらうのが前提）。

ボールトを各運営 PC へコピー（または USB で搬入）する。**パスフレーズは持ち込まない。**

`PRESTAGE_PASSFILE`（ボールトのパスフレーズを平文ファイルから読む）はテスト用、および無人で流す
ビルド担当 PC 上の実行専用のオプションである。**会場の PC では使わない** — パスフレーズをファイルに
置いた時点でその機体上の秘匿が崩れる。

### イメージの配布

`stage_team.sh` はボールトに記録されたイメージ ID とその PC の `aichallenge-2025-dev` の ID が
一致しないと展開を拒否する。`./docker_build.sh dev` を各 PC で個別に実行すると ID が揃わないため、
ビルド担当 PC のイメージを書き出し、他の全 PC へ配って読み込ませる。

```bash
# ビルド担当 PC で書き出す
make prestage-image-export IMAGE_TAR=/path/to/aichallenge-2025-dev.tar.zst

# ボールトと一緒に運び、各 Autoware PC / リハーサル卓 PC / 車両 PC で読み込む
make prestage-image-import IMAGE_TAR=/path/to/aichallenge-2025-dev.tar.zst

# 全 PC で ID が一致することを確認する
docker image inspect --format '{{.Id}}' aichallenge-2025-dev
```

### 会場 PC の初期化（前日まで）

各運営 PC で `aichallenge/workspace/install` が**存在しない**状態にしておく（存在すると
`stage_team.sh` が拒否する）。開発で使った PC なら 1 回 `make prestage-unstage` を流す。
`unstage_team.sh` は git 管理下の checkout であれば `aichallenge/workspace/src/aichallenge_submit`
を削除せず、git から参照提出物を復元する（`git clean` + `git checkout`）。これにより
`prestage-unstage` の後も `make dev` / `make autoware-build` がそのまま動く。
`.env` の `ROS_DOMAIN_ID` は席ごとに固定する（A=1, B=2, C=3, D=4）。チーム ID には席を含めない。

`make autoware-build` は `aichallenge/workspace/.staged_team`（=誰かが stage 中）を検知すると
既定の `--symlink-install` ビルドを拒否する。事前ビルドした実体コピーの `install/` を
シンボリックリンクで上書きして壊してしまうためで、`make prestage-unstage` で unstage してから
ビルドする（詳細は `docs/spec/prestaged-submissions.md`）。

## 走行枠ごと（運営が実行）

```bash
# 枠の頭（チームがログアウトしている状態で）
make prestage-stage VAULT=/path/to/vault TEAM=general-03

# チームに引き渡す。チームは通常どおり make autoware-simulator（実機は make autoware-vehicle）を使う

# 枠の終わり（install/ build/ log/ を削除し、src/aichallenge_submit は git から参照提出物に復元）
make prestage-unstage KEEP_OUTPUT=/path/to/logs
```

`prestage-stage` はパスフレーズを対話入力させる（会場の PC 上に passfile は置かない）。
展開は数秒（実測 2 秒）で終わる。

`stage_team.sh` は以下を検証してから展開する。1 つでも合わなければ中断する。

- ボールトに記録されたイメージ ID と、この PC の `aichallenge-2025-dev` の一致
- 対象チームの `build_status` が `ok`
- `install.tar.zst` の sha256

チームは枠中、`aichallenge/workspace/install/` 配下の launch XML / param YAML（平文コピー）を
直接編集して調整できる。`src/` は無いので C++ の再ビルドはできない。

### SIM 決勝の 1 試合（20 分枠）での流れ

| 時刻 | リハーサル卓 PC | ステージ PC（席 A〜D） |
|------|----------------|------------------------|
| T-20 | `prestage-stage TEAM=<次の試合の 4 チーム>`（卓が複数あれば各卓 1 チーム） | 前の試合が走行中 |
| T-5 | チーム退席後 `prestage-unstage` | 前の試合の `prestage-unstage KEEP_OUTPUT=...` |
| T+0 | — | 席ごとに `prestage-stage TEAM=<その席のチーム>`、チームが `make autoware-simulator` |
| T+18 | — | 走行終了。チーム退席後 `prestage-unstage KEEP_OUTPUT=...` |

各チームはリハーサル卓とステージで計 2 回ステージされる。AWSIM PC は `make simulator` のまま
変更なし。他チームの Autoware が起動中に AWSIM を再起動すると一部ノードが落ちることがあるので、
再起動は全席の Autoware を止めてから行う。

## テスト

```bash
make prestage-test
```

ダウンロードと colcon build はスタブに差し替わるため、ネットワークも docker も不要。
gocryptfs / fusermount / zstd / python3 のいずれかが未インストールの場合は exit 77（SKIP）になる。

実イメージでの end-to-end（ビルド → ボールト → stage → `make autoware-simulator` → ノード確認 → unstage）:

```bash
./create_submit_file.bash   # 参照提出物を submit/aichallenge_submit.tar.gz に固める
make prestage-e2e SUBMIT=submit/aichallenge_submit.tar.gz
```

docker と `aichallenge-2025-dev`、`.env` が必要。`aichallenge/workspace/src/aichallenge_submit`
を一時的に置き換えるので、そこに未コミットの変更があるマシンでは実行しない（終了時に git から復元する）。
ビルド時間・アーカイブサイズ・ステージ時間を出力するので、spec の実測値を更新するときに使う。
