# simulator_scripts（設計メモ）

`make simulator-<mode>` / `dev-<mode>` から `<mode>.sh` として呼ばれる、モード別 AWSIM 起動スクリプト。

**あえてモード別 1 ファイルにしている**（config 集約しない）。起動引数の正本は各 `<mode>.sh`。
1 ファイルで完結し、コピーしてモードを増やせ、`safety-gate`/`parallel` のような差分も素直に書ける。
そのため `1p.sh` と `eval.sh` のようなほぼ同一ファイルもあるが、意図した重複であり DRY 化しない。

新モードは近いものを `cp` して引数を直すだけ（Makefile が `*.sh` を wildcard で拾うため登録不要）。
末尾の GPU 切り替えコメントは編集対象行の隣に置くガイドなので、共通化せず各ファイルに残す。
