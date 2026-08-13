# prestage — 提出物の事前ビルドと暗号化配置

設計の根拠は `docs/spec/prestaged-submissions.md` を参照。ここは運用手順のみ。

**前提**: `gocryptfs` / `fusermount` / `zstd` / `python3`。ステージングは**運営のみ**が行う。

## 会場前（運営 PC）

```bash
# 1. ボールトを初期化（初回のみ）。パスフレーズは運営で共有し、車両 PC には置かない
gocryptfs -init /path/to/vault

# 2. チーム一覧を用意（書式は teams.tsv.example）
cp vehicle/prestage/teams.tsv.example teams.tsv && $EDITOR teams.tsv

# 3. ビルドに使うイメージを用意（車両 PC と同一である必要がある）
./docker_build.sh dev

# 4. 32 チーム分をビルドしてボールトへ
make prestage-build VAULT=/path/to/vault TEAMS=teams.tsv
```

`prestage_all.sh` は aic-next の認証情報を環境変数 `PRESTAGE_USERNAME` / `PRESTAGE_PASSWORD` から読む
（未設定なら開始時に 1 回だけ対話入力）。`vehicle/download_submission.sh` が使う裸の `USERNAME` /
`PASSWORD` とは**別の変数名**なので注意する。シェルの `USERNAME` は他の目的で既にエクスポートされて
いることがあり、裸の名前を使うと気付かないまま別ユーザーとして全チームを認証してしまう。

失敗したチームは `build_status: failed` として記録され、処理は継続する。
最後のサマリを確認し、必要なら `--team <id> --force` で個別に再実行する。

ボールトを車両 PC へコピー（または USB で搬入）する。**パスフレーズは持ち込まない。**

`PRESTAGE_PASSFILE`（ボールトのパスフレーズを平文ファイルから読む）はテスト用、および無人で流す
運営 PC 上の実行専用のオプションである。**車両 PC では使わない** — パスフレーズをファイルに置いた
時点でその機体上の秘匿が崩れる。

## 走行枠ごと（車両 PC・運営が実行）

```bash
# 枠の頭（チームがログアウトしている状態で）
make prestage-stage VAULT=/path/to/vault TEAM=t01

# チームに引き渡す。チームは通常どおり make dev / make autoware-vehicle を使う

# 枠の終わり
make prestage-unstage KEEP_OUTPUT=/path/to/logs
```

`prestage-stage` はパスフレーズを対話入力させる（車両 PC 上に passfile は置かない）。

`stage_team.sh` は以下を検証してから展開する。1 つでも合わなければ中断する。

- ボールトに記録されたイメージ ID と、この車両 PC の `aichallenge-2025-dev` の一致
- 対象チームの `build_status` が `ok`
- `install.tar.zst` の sha256

## テスト

```bash
make prestage-test
```

ダウンロードと colcon build はスタブに差し替わるため、ネットワークも docker も不要。
gocryptfs / fusermount / zstd / python3 のいずれかが未インストールの場合は exit 77（SKIP）になる。
