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

- 現在位置で `preflight`（環境診断）を実行します
- 初期セットアップを進める場合は `./setup.bash bootstrap` を実行します
- `bootstrap` では必要ステップを **y/N で確認**しながら進められます

> PR版（Testing）を入口にしたい場合（必要な時だけ）: PRのIDを入れてください
>
> ```bash
> curl -fsSL "https://raw.githubusercontent.com/AutomotiveAIChallenge/aichallenge-racingkart/refs/pull/<PR_ID>/head/setup.bash" | bash -s -- preflight
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
  - `./setup.bash preflight`
- 完了の目安: “Docker” や “Repository” の欄で、次に何をすべきかが分かる
