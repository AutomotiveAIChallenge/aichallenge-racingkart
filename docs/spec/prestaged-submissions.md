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
  SYMLINK_INSTALL=0                        イメージ ID 照合
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
  "image": { "tag": "aichallenge-2025-dev", "id": "sha256:..." },
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
- 使用イメージの **ID**（`docker image inspect --format '{{.Id}}'`、config のダイジェスト）を
  `manifest.json` の `image.id` に記録し、`stage_team.sh` が車両 PC 側で同じコマンドを実行した
  結果と照合する。不一致なら**中断**する。
  `aichallenge-2025-dev` は `docker_build.sh` でローカルビルドするイメージであり
  **`RepoDigests` を持たない**（レジストリに push していない）ため、レジストリ由来の
  「イメージダイジェスト」は使えない。`.Id`（ローカルの config ダイジェスト）だけが
  ローカルビルドイメージに対して安定して取得できる識別子である。

### `--symlink-install` を外す必要がある

`aichallenge/build_autoware.bash` は既定（`SYMLINK_INSTALL=1`）で `colcon build --symlink-install` を実行し、
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

`docker-compose.yml` の変更は**不要**だった。
`docker compose run --rm --no-deps -e SYMLINK_INSTALL=0 autoware-build` のように
呼び出しごとに `-e` で環境変数を注入できるため、`autoware-build` サービス定義に
`SYMLINK_INSTALL` を追加する必要はない。

## スクリプト仕様

| ファイル | 実行場所 | 役割 |
|----------|----------|------|
| `vehicle/prestage/prestage_all.sh` | 運営 PC | チーム一覧をループし、取得 → ビルド → ボールト書き込み |
| `vehicle/prestage/stage_team.sh` | 車両 PC | マウント → 1 チーム展開 → アンマウント |
| `vehicle/prestage/unstage_team.sh` | 車両 PC | 平文・ビルド成果物・eval イメージのクリーンアップ |
| `vehicle/prestage/teams.tsv.example` | — | チーム一覧の書式例（コミットするのは example のみ） |

**シグナルハンドリング**: ボールトをマウントするスクリプト（`prestage_all.sh` / `stage_team.sh`）は
`trap cleanup EXIT` と `trap 'exit 130' INT` / `trap 'exit 143' TERM` を**別々に**張る。
`trap cleanup EXIT INT TERM` のように 1 つの trap にまとめると、
シグナル受信時にハンドラを実行した**あと bash がスクリプトの続きを再開してしまう**ため、
アンマウント済みのマウントポイントが素の `/tmp` ディレクトリとしてループ内で再作成され、
以降のチームの提出物がそこへ平文で書き込まれる。レビュー中に実際に再現した不具合であり、
本機能の秘匿という目的そのものを無効化するため、EXIT と INT/TERM の trap は分離を必須とする。

### prestage_all.sh

- 入力: `teams.tsv`（TSV: `team_id`, `user_id`, 任意で `submission_id`）
- 認証は環境変数 `PRESTAGE_USERNAME` / `PRESTAGE_PASSWORD`、無ければ**開始時に 1 回だけ**対話入力。
  素の `USERNAME` / `PASSWORD` ではなく意図的にこの名前にしている —
  ログインシェルは `USERNAME` を既に export していることがあり（このホストでは `USERNAME=taikitanaka`）、
  素の名前を使うと `[ -z "${USERNAME-}" ]` が偽になってプロンプトが出ず、
  全チームが誤ったユーザーとしてダウンロードされ `failed` になる。
  `vehicle/download_submission.sh` が文書化する裸の `USERNAME` / `PASSWORD` とは別の名前である。
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
4. gocryptfs を `-ro` でマウント。`trap cleanup EXIT` + `trap 'exit 130' INT` + `trap 'exit 143' TERM`
   （分離した trap。理由は「スクリプト仕様」節の注記）で必ず `fusermount -u` する
5. `manifest.json` のイメージ ID と車両 PC の `aichallenge-2025-dev` を照合、不一致で中断
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
`run()`（`vehicle/download_submission.py:452`）が `get_user_selection()`（同 `:206`）で**対話選択に落ちる**ため、
32 チームのループが組めない。`--latest` 指定時は `select_latest()`（同 `:192`、`list_recent_submissions()`
の結果から `submission_time` 最大を選ぶ）の結果を既存の `download_submission()`（`--submission-id` 指定時に
使う `download_by_id()` とは別経路）に渡す。

`--dest-file <path>` も追加した。既存の `--output`（既定 `./downloads/`）には**既知の挙動**があり、
`download_submission()` / `download_by_id()` の実装（`:313`, `:406` 付近の `if dest_file: ... else: ...`）は
`output_dir` 引数を実質使わず、`--dest-file` 未指定時は常にスクリプトと同じディレクトリの
`vehicle/download/<filename>` に書く。本設計ではこの既存挙動には触れず、
`prestage_all.sh` が確定パスへ書き出せるように `--dest-file` を新設するだけに留めた。

## ディスク見積り

実測値（`aichallenge/workspace/`、`--symlink-install` あり）:

| 対象 | サイズ |
|------|--------|
| `install/` 全体 | 465 MB |
| うち `install/multi_purpose_mpc_ros/.venv` | 451 MB |
| `build/` | 113 MB |

`--symlink-install` を外すと実体コピーのぶん増える。MPC を使うチームが多い場合、
32 チームで最大 20 GB 程度（見込み）。zstd 圧縮後のボールトは venv が高圧縮なので
数 GB に収まる見込みだが、**zstd 圧縮後のボールト実サイズとチームあたりのビルド時間は
実イメージでの E2E（未実施、下記「検証方法」参照）が済んでおらず未計測**である。
上記はいずれも見込み値であり、実測値として扱わないこと。
`.venv` の重複排除は効果が大きいが、まずは実測してから判断する（本 spec の対象外）。

## 運用手順

`Makefile` の `prestage-build` / `prestage-stage` / `prestage-unstage` / `prestage-test` の
4 ターゲットから呼ぶ。コマンド例・環境変数・注意事項の手順は `vehicle/prestage/README.md` に
まとめてある（本 spec は「なぜ」、README は「どうやって」を担当する）。ここでは概要のみ示す。

- **会場前（運営 PC）**: `teams.tsv` を用意し `make prestage-build VAULT=<path> TEAMS=teams.tsv`
  でボールトを構築、サマリで `failed` のチームを確認、ボールトを車両 PC へ搬入する。
- **走行枠ごと（車両 PC・運営が実行）**: 枠の頭にチームがログアウトしている状態で
  `make prestage-stage VAULT=<path> TEAM=<team_id>`、枠の終わりに
  `make prestage-unstage [KEEP_OUTPUT=<保管先>]`。

## 非対象（やらないこと）

- **32 ワークスペースの同時展開と `TEAM_ID` による source 切り替え** —
  1 枠 1 チームなので展開先は常に 1 つで足りる。同時展開は他チームの平文が並ぶことになり秘匿が崩れる。
  commit `5fba900` で追加された `d1`〜`d3` の並列ワークスペースは
  「同時に 3 台走らせる parallel 用」として維持し、32 個へは拡張しない。
- **rootless docker 化** — `privileged: true` / `/dev/dri` / `group_add` / `network_mode: host` を
  rootless で通す検証コストが大きく、得られる境界は本設計で既に確保できる。
- **チーム自身によるステージング** — パスフレーズがチームに渡れば他チームの復号も可能になるため設計外。
- **`.venv` の重複排除**。

## テスト用の環境変数フック

`vehicle/prestage/tests/` のスタブ差し替え、および運用時の設定変更に使う環境変数。

| 変数 | 既定値 | 用途 |
|------|--------|------|
| `PRESTAGE_DOWNLOAD_CMD` | `python3 <repo>/vehicle/download_submission.py` | ダウンロードコマンドの差し替え（テストでは偽コマンドに置換） |
| `PRESTAGE_BUILD_CMD` | `docker compose run --rm --no-deps -e SYMLINK_INSTALL=0 autoware-build` | ビルドコマンドの差し替え（テストでは偽コマンドに置換） |
| `PRESTAGE_IMAGE` | `aichallenge-2025-dev` | `image_id()` が問い合わせるイメージタグ |
| `PRESTAGE_IMAGE_ID_CMD` | 未設定（既定は `docker image inspect --format '{{.Id}}'`） | イメージ ID 解決コマンドの差し替え（テスト用） |
| `PRESTAGE_WORKSPACE_ROOT` | リポジトリルート | `aichallenge/workspace` の親ディレクトリ（テストでは一時ディレクトリに向ける） |
| `PRESTAGE_VAULT` | 未設定 | `stage_team.sh` で `--vault` を省略したときの既定 cipherdir |
| `PRESTAGE_PASSFILE` | 未設定 | gocryptfs のパスフレーズファイル（運営 PC / テスト専用。車両 PC では非推奨、警告を出す） |
| `PRESTAGE_DOCKER_CMD` | `docker` | `unstage_team.sh` が呼ぶ docker コマンドの差し替え（テスト用） |
| `PRESTAGE_EVAL_IMAGE` | `aichallenge-2025-eval` | `unstage_team.sh` が削除する eval イメージ名 |

認証用の `PRESTAGE_USERNAME` / `PRESTAGE_PASSWORD` は上記とは別枠（「スクリプト仕様」節を参照）。

## 検証方法

擬似提出 tar 2 チーム分で end-to-end を確認する。

1. `prestage_all.sh` を実行し、ボールトに 2 チーム分と `manifest.json` ができること
2. マウントせずにボールトを覗いて平文が読めないこと
3. `stage_team.sh t01` 後に `make dev` が起動し、t01 のノードが上がること
4. ステージ中に `find` して t02 の平文がどこにも存在しないこと
5. `unstage_team.sh` 後に平文・`install/`・eval イメージが残っていないこと
6. イメージ ID を意図的に不一致にすると `stage_team.sh` が中断すること

上記のうち 1・2 は `vehicle/prestage/tests/prestage_test.sh`、4・6 は `stage_test.sh`、
5 は `unstage_test.sh` で検証済み（ダウンロード・ビルド・docker をスタブに差し替え、
ネットワークも docker も使わない。`make prestage-test` でまとめて実行できる）。
3 の**実イメージでの `make dev` E2E のみ未実施**（作業中の未コミット差分を消してしまう
おそれがあり保留中）。
