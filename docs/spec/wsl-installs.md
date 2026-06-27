# WSL2 で `make dev` を動かすために入れた install 項目

> WSL(WSLg, Ubuntu-22.04, native docker, Intel GPU/D3D12)で `make dev` を動かし、
> output log と capture 結果で動作確認するまでに**追加でインストールした項目**の記録。

## ホスト側（WSL の Ubuntu）

| 項目 | 用途 | 必須か | 備考 |
|---|---|---|---|
| (追加インストールなし) | — | — | docker / docker compose / WSLg は既存。`/dev/dxg`・`/usr/lib/wsl`(libd3d12)・`lo` multicast・`net.core.rmem_max` も既に揃っていた |

## コンテナ側（aichallenge-2025-dev イメージ = `packages.txt`）

| 項目 | 用途 | 必須か | 備考 |
|---|---|---|---|
| **`xvfb`** | capture(screen_recorder)を WSL で機能させる仮想フレームバッファ。`packages.txt` に追加済み（反映には `./docker_build.sh dev` で再ビルド、暫定は `apt-get install -y xvfb`） | capture を録る場合に必須 | WSLg の `:0` は各ウィンドウを Wayland コンポジットするため `QScreen::grabWindow(0)`（X ルート全体取得）が**真っ黒**になる。Xvfb 上で RViz を描画して録ると実画面が録れる |

llvmpipe(ソフトGL)・libopencv 4.5・libavcodec は**既にイメージ同梱**のため追加 install 不要。

## 診断のみ（恒久化していない / 使い捨て `docker run --rm` 内のみ）

| 項目 | 用途 | 備考 |
|---|---|---|
| `mesa-utils` | `glxinfo` で GL ドライバ(D3D12 vs llvmpipe)確認 | イメージには残していない |

## 設定変更（install ではないが再現に必要）

- `.env`: `COMPOSE_FILE=docker-compose.yml:docker-compose.wsl.yml`
- overlay `docker-compose.wsl.yml`: `AWSIM_HEADLESS=1` / `LIBGL_ALWAYS_SOFTWARE=1` / devices=`/dev/dxg` / WSLg マウント

## capture 取得手順（WSL）

WSLg の `:0` をそのまま録ると黒画面になるため、**Xvfb 上で RViz を動かして録画**する:

1. `make dev` で AWSIM(headless)+Autoware を起動（RViz データが domain 1 に流れる）
2. 別コンテナ等で `Xvfb :99 -screen 0 1920x1080x24 &` → `DISPLAY=:99`
3. `rviz2 -d <autoware.rviz>` を `:99` で起動（`LIBGL_ALWAYS_SOFTWARE=1`）
4. `ros2 run aichallenge_screen_recorder screen_recorder_node -p output_dir:=<dir>` を `:99` で起動
5. `ros2 service call /debug/service/capture_screen std_srvs/srv/Trigger` で開始→数秒→もう一度呼んで停止
6. 出力 `*.mp4` を確認（実測: 1920x1080@10Hz、約1MB/8s、RViz のコース・タイマー・車両が記録される）
