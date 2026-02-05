# HowToSetup: まず「試せる状態」まで持っていく

技術的な仕組みの説明はせず、**セットアップで「何をやるか」**だけを書いたチェックリストです。  
迷ったらこの順に上から進めてください。

> 想定: Ubuntu（推奨は 22.04）。まずは **CPUで動作確認**できればOKです（GPUは後回し）。

---

## 0) まずこれ（入口）

まずは「とにかく一回動くか」を試します。**ホームディレクトリ（`~/aichallenge-racingkart/...`）に環境を作って試走**できる入口です。

```bash
curl -fsSL "https://raw.githubusercontent.com/AutomotiveAIChallenge/aichallenge-racingkart/main/setup.bash" | bash
```

このコマンドで起きること（ざっくり）:

- いくつかのステップを **y/N で確認**しながら進みます（不安なら `N` にして止めてOK）
- Docker / AWSIM / ビルドなど、必要な準備を揃えて「試せる状態」へ持っていきます
- 終わると “次にやること（Next steps）” が表示されます

> PR版（Testing）を入口にしたい場合（必要な時だけ）:
>
> ```bash
> curl -fsSL "https://raw.githubusercontent.com/AutomotiveAIChallenge/aichallenge-racingkart/refs/pull/175/head/setup.bash" | bash -s -- test
> ```

---

## 1) セットアップでやること一覧（超ハイレベル）

1. **今のPCが足りているか診断する**
2. **Docker を使える状態にする**
3. **リポジトリを用意する**
4. **AWSIM（シミュレータのデータ）を用意する**
5. **開発用Dockerイメージを用意する**
6. **ワークスペースをビルドする**
7. **起動して、止められることを確認する**

---

## 2) チェックリスト（上から順にやるだけ）

ここは「やること」→「代表コマンド1つ」→「完了の目安」だけを書きます。

### (A) 診断する（最初に必ず）

- やること: 足りないものを洗い出す
- 代表コマンド:
  - `./setup.bash doctor`
- 完了の目安: “Docker” や “AWSIM asset” の欄で、次に何をすべきかが分かる

### (B) Docker を使える状態にする

- やること: Docker / Compose をインストールし、権限も整える
- 代表コマンド:
  - `./setup.bash show docker`
- 完了の目安: `./setup.bash doctor` の Docker 欄が OK になる

### (C) リポジトリを用意する

- やること: このリポジトリを作業できる場所に置く（clone済ならスキップ）
- 代表コマンド:
  - `./setup.bash show workspace`
- 完了の目安: リポジトリのルートで `./setup.bash doctor` が実行できる

### (D) AWSIM を用意する（シミュレータ）

- やること: AWSIM を所定の場所に置く
- 代表コマンド:
  - `./setup.bash download awsim`
- 完了の目安: `./setup.bash doctor` の “AWSIM asset” が OK になる

### (E) 開発用Dockerイメージを用意する

- やること: 開発用イメージ（`aichallenge-2025-dev`）を作る
- 代表コマンド:
  - `./docker_build.sh dev`
- 完了の目安: `make dev` を実行しても「イメージが無い」系で止まらない

### (F) ワークスペースをビルドする（初回だけ重い）

- やること: 起動に必要なビルド成果物（`install/`）を作る
- 代表コマンド:
  - `make autoware-build`
- 完了の目安: `./setup.bash doctor` の次の案内が “Run evaluation / Start dev” になる

### (G) 起動して止められることを確認する（まずCPU）

- やること: まずCPUで起動できることを確認する（GPUは後でOK）
- 代表コマンド:
  - `DEVICE=cpu make dev`
- 完了の目安: 起動できて、終了時に `make down` で止められる

---

## 3) 次に読む（使い方）

- 使い方の入口（`make dev` / `./run_evaluation.bash` の使い分け）: `design_docs/Introduction.md`
- ログの考え方（困った時の見方）: `design_docs/log_design.md`
