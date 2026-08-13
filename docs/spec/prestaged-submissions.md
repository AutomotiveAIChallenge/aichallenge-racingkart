# 提出物の事前ビルド・暗号化配置（prestaged submissions）

決勝など**オフライン会場**で、参加チームが自分で車両 PC 上で走行準備を行う運用を前提に、
32 チーム分の提出物を**事前にビルドして暗号化した状態で車両 PC に配置**し、
走行枠ごとに**運営が該当チーム 1 件だけを復号・展開**する仕組みの設計。

## 背景と課題

会場に十分な回線がある場合はこの仕組みは不要で、走行枠の頭に運営が
`make download USER_ID=<id>` を実行すれば足りる（他チームのファイルがそもそも車両 PC 上に存在しない）。
本 spec が対象とするのは、当日ダウンロードに依存できないケースである。

このとき素朴に「32 チーム分を車両 PC に平文で置く」と、以下の理由でチーム間の秘匿が成立しない。

- `setup.bash` はユーザーを **docker グループに追加する**前提で、rootless docker のサポートはない。
  docker ソケットが使える = 実質 root であり、`docker run -v /:/host` で他チームのファイルが読める。
- `docker_build.sh eval --submit` は提出物をイメージに焼き込むため、
  ビルド済み eval イメージが共有 docker デーモンに残っていると他チームが `docker run` して中身を読める。

したがって**ファイルパーミッションでも、車両 PC 上に鍵を置く暗号化でも境界にならない**。
唯一有効なのは「**他チームの平文と鍵が、その時点で車両 PC 上に存在しない**」状態を作ることである。

## 脅威モデル

| 対象 | 防げるか | 根拠 |
|------|----------|------|
| 車両 PC / ディスクの持ち出し | 防げる | ボールトは gocryptfs 暗号、鍵は運営のみが保持 |
| 走行枠外での他チームソースの閲覧 | 防げる | 平文は枠中のみ、枠終了時に削除 |
| 他チームによるボールトの直接コピー | 防げる | 暗号文のみ取得でき、パスフレーズは車両 PC 上に置かない |
| 自分の枠中に常駐バックドアを仕込み、次チームのステージングを盗む | **防げない** | 緩和策: 枠間クリーンアップ、ステージングはチーム未ログイン時のみ実施 |
| 枠中に自チームの Python / 設定 / 重みを読む | 対象外 | 自分の提出物なので問題にならない |

**設計上の秘匿レベル**: 配布するのはビルド済み `install/` のみで `src/` は含まない。
したがって C++ は `.so` になるが、Python ノード・launch XML・param YAML・学習済み重みは平文で含まれる。
これは「ソース完全非公開」ではなく「**その枠のチーム以外は何も見えない**」という保証である。

## 全体フロー

```
[運営 PC / 会場前]                          [車両 PC]
prestage_all.sh
  teams.tsv (team_id, user_id)
      |
      v
  download_submission.py --latest          (1 枠の頭・チーム未ログイン)
      |  submission.tar.gz                 stage_team.sh <team_id>
      v                                        |  パスフレーズ手入力
  src/aichallenge_submit/ へ展開               v
      |                                    gocryptfs マウント (-ro)
      v                                        |
  SYMLINK_INSTALL=0                        イメージダイジェスト照合
  make autoware-build  (コンテナ内)             |
      |  install/                          install.tar.zst の sha256 照合
      v                                        |
  install.tar.zst                          /aichallenge/workspace/install/ へ展開
      |                                        |
      v                                    fusermount -u  (trap で保証)
  gocryptfs ボールトへ書き込み                   |
      |                                        v
      |   ボールトをコピー / USB で搬入 ---> チームが make dev / autoware-vehicle
      v                                        |
  次のチームへ (src を削除)                  (枠終了)
                                           unstage_team.sh
                                             install/ build/ log/
                                             src/aichallenge_submit/ を削除
                                             eval イメージを削除
                                             output/ は保管先へ退避
```

## ボールト構成

gocryptfs の cipherdir を車両 PC の固定パスに置く。平文マウントポイントは一時ディレクトリとし、
運営 PC では書き込み可（`prestage_all.sh` がボールトを構築する間）、
車両 PC では読み取り専用（`stage_team.sh` が展開する間のみ）でマウントする。

前提パッケージ: 両マシンに `gocryptfs`・`fuse`・`zstd`（`tar --zstd` 用）が必要。

マウント後の論理レイアウト:

```
manifest.json
team_<team_id>/
  install.tar.zst        # ビルド済み install/ の中身（アーカイブ直下が install/ の中身）
  submission.tar.gz      # 原本（会場での再ビルドが必要になった場合用）
  build.log              # 事前ビルドのログ（失敗調査用）
```

`manifest.json`:

```json
{
  "schema": 1,
  "created_at": "2026-08-13T10:00:00+09:00",
  "image": { "tag": "aichallenge-2025-dev", "digest": "sha256:..." },
  "teams": [
    {
      "team_id": "t01",
      "user_id": "<aic-next user id>",
      "submission_id": "<uuid>",
      "submitted_at": 1755000000,
      "build_status": "ok",
      "install_sha256": "...",
      "submission_sha256": "..."
    }
  ]
}
```

`build_status` が `ok` 以外のチームは `stage_team.sh` が拒否する（原本から会場で再ビルドする導線に落とす）。

## ビルドの一貫性

事前ビルドした `install/` は、**走行時と同一のコンテナイメージ内でビルドしないと ABI が合わない**。

- 事前ビルドは既存の `autoware-build` サービス（`docker-compose.yml`、
  `command` は `/aichallenge/build_autoware.bash` を実行）内で行う。
- 使用イメージのダイジェストを `manifest.json` に記録し、
  `stage_team.sh` が車両 PC 側の `docker image inspect` 結果と照合する。不一致なら**中断**する。

### `--symlink-install` を外す必要がある

現状 `aichallenge/build_autoware.bash:31` は `colcon build --symlink-install` を固定で使っており、
`install/` 内に **1045 個のシンボリックリンク**が生成され、
`/aichallenge/workspace/src/...` と `/aichallenge/workspace/build/...` を**絶対パスで**指している。

```
install/rl_train_controller/lib/.../rl_train_controller_node.py
  -> /aichallenge/workspace/src/aichallenge_tools/.../rl_train_controller_node.py
```

そのため `src/` を含めずに `install/` だけを配布すると**ワークスペースが壊れる**。
事前ビルドでは実体コピーにする必要がある。

通常の開発ループでは `--symlink-install` が有用なのでグローバルには外さず、
`build_autoware.bash` に環境変数 `SYMLINK_INSTALL`（既定 `1`）の分岐を追加し、
事前ビルド時のみ `SYMLINK_INSTALL=0` で呼ぶ。
`autoware-build` サービスに `SYMLINK_INSTALL: ${SYMLINK_INSTALL:-1}` の環境変数受け渡しを追加する。

## スクリプト仕様

| ファイル | 実行場所 | 役割 |
|----------|----------|------|
| `vehicle/prestage/prestage_all.sh` | 運営 PC | チーム一覧をループし、取得 → ビルド → ボールト書き込み |
| `vehicle/prestage/stage_team.sh` | 車両 PC | マウント → 1 チーム展開 → アンマウント |
| `vehicle/prestage/unstage_team.sh` | 車両 PC | 平文・ビルド成果物・eval イメージのクリーンアップ |
| `vehicle/prestage/teams.tsv.example` | — | チーム一覧の書式例（コミットするのは example のみ） |

### prestage_all.sh

- 入力: `teams.tsv`（TSV: `team_id`, `user_id`, 任意で `submission_id`）
- 認証は環境変数 `USERNAME` / `PASSWORD`、無ければ**開始時に 1 回だけ**対話入力
- ボールトのパスフレーズも開始時に 1 回だけ対話入力（運営 PC 上なので passfile も許容）
- チームごとに: 取得 → `src/aichallenge_submit/` へ展開 → `SYMLINK_INSTALL=0` でビルド →
  `install/` を zstd で固める → ボールトへ → `src/` `install/` `build/` を削除
- **1 チームの失敗で全体を止めない**。`build_status: failed` として記録し次に進み、最後にサマリを出す
- 冪等: 同一 `submission_id` かつ `install_sha256` が既にボールトにあればスキップ（`--force` で再ビルド）

### stage_team.sh

1. 引数 `<team_id>` 必須
2. 既にステージ済み（`aichallenge/workspace/.staged_team` が存在）なら拒否し、`unstage_team.sh` を促す
3. パスフレーズを対話入力する。**車両 PC 上に passfile を置かない**
   （無人運用が要る場合のみ `PRESTAGE_PASSFILE` を受けるが、秘匿が崩れることを警告として出す）
4. gocryptfs を `-ro` でマウント。`trap ... EXIT INT TERM` で必ず `fusermount -u` する
5. `manifest.json` のイメージダイジェストと車両 PC の `aichallenge-2025-dev` を照合、不一致で中断
6. 対象チームの `build_status` が `ok` であることを確認
7. `install.tar.zst` の sha256 を照合し `/aichallenge/workspace/install/` へ展開
8. `.staged_team` に `team_id` と時刻を記録

展開先は既存の固定パス `/aichallenge/workspace/install` なので、
`docker-entrypoint.sh:5-7` がそのまま `install/setup.bash` を source する。
**`docker-entrypoint.sh` と `run_autoware.bash` の変更は不要**。

### unstage_team.sh

- `aichallenge/workspace/` の `install/` `build/` `log/` `src/aichallenge_submit/` を削除
- `output/` は削除せず `--keep-output <dir>` で指定した保管先へ移動する（運営が走行ログを保管するため）
- `aichallenge-2025-eval` イメージが存在すれば `docker image rm`
- `.staged_team` を削除
- 最後に `find` で残存平文を走査し、残っていれば非ゼロ終了で報告する

### download_submission.py への追加

`--latest` フラグを追加する。現状 `--submission-id` を省略すると
`run()`（`vehicle/download_submission.py:430`）が `get_user_selection()`（同 `:192`）で**対話選択に落ちる**ため、
32 チームのループが組めない。`--latest` 指定時は `list_recent_submissions()`（同 `:78`）の結果から
最新（`submissionTime` 最大）を選び `download_by_id()` に渡す。

## ディスク見積り

実測値（`aichallenge/workspace/`、`--symlink-install` あり）:

| 対象 | サイズ |
|------|--------|
| `install/` 全体 | 465 MB |
| うち `install/multi_purpose_mpc_ros/.venv` | 451 MB |
| `build/` | 113 MB |

`--symlink-install` を外すと実体コピーのぶん増える。MPC を使うチームが多い場合、
32 チームで最大 20 GB 程度。zstd 圧縮後のボールトは venv が高圧縮なので数 GB に収まる見込み。
`.venv` の重複排除は効果が大きいが、まずは実測してから判断する（本 spec の対象外）。

## 運用手順

**会場前（運営 PC）**

1. `teams.tsv` を用意（`user_id` は aic-next 管理画面 / DB から取得）
2. `vehicle/prestage/prestage_all.sh --vault <path> --teams teams.tsv`
3. サマリで `failed` のチームを確認し、必要なら個別に再実行
4. ボールトを車両 PC の所定パスへコピー（または USB で搬入）

**走行枠ごと（車両 PC・運営が実行）**

1. チームがログアウトしている状態で `vehicle/prestage/stage_team.sh <team_id>`
2. チームに引き渡し、チームは通常どおり `make dev` / `make autoware-vehicle` を使う
3. 枠終了後 `vehicle/prestage/unstage_team.sh --keep-output <保管先>`

## 非対象（やらないこと）

- **32 ワークスペースの同時展開と `TEAM_ID` による source 切り替え** —
  1 枠 1 チームなので展開先は常に 1 つで足りる。同時展開は他チームの平文が並ぶことになり秘匿が崩れる。
  commit `5fba900` で追加された `d1`〜`d3` の並列ワークスペースは
  「同時に 3 台走らせる parallel 用」として維持し、32 個へは拡張しない。
- **rootless docker 化** — `privileged: true` / `/dev/dri` / `group_add` / `network_mode: host` を
  rootless で通す検証コストが大きく、得られる境界は本設計で既に確保できる。
- **チーム自身によるステージング** — パスフレーズがチームに渡れば他チームの復号も可能になるため設計外。
- **`.venv` の重複排除**。

## 検証方法

擬似提出 tar 2 チーム分で end-to-end を確認する。

1. `prestage_all.sh` を実行し、ボールトに 2 チーム分と `manifest.json` ができること
2. マウントせずにボールトを覗いて平文が読めないこと
3. `stage_team.sh t01` 後に `make dev` が起動し、t01 のノードが上がること
4. ステージ中に `find` して t02 の平文がどこにも存在しないこと
5. `unstage_team.sh` 後に平文・`install/`・eval イメージが残っていないこと
6. イメージダイジェストを意図的に不一致にすると `stage_team.sh` が中断すること
