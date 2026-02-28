# Dockerで音を出す（Linux / PipeWire・Pulse）

AWSIM（`simulator` コンテナ）からホストへ音を出したい場合のメモです。

## 前提

- ホストOS: Linux（Ubuntu想定）
- ホストで PipeWire（`pipewire-pulse`）または PulseAudio が動作している
- ソケットがあること:
  - `test -S /run/user/$(id -u)/pulse/native`

## 使い方

通常通り起動すればOKです（`simulator` の `PULSE_SERVER` を compose 側で設定しています）。

```bash
make simulator
# または
make dev
```

## 音が出ないとき

- `HOST_UID` が正しく渡っているか確認:
  - `make` 経由なら通常は自動設定されます
  - `docker compose` を直接叩く場合は `HOST_UID=$(id -u)` を明示すると改善することがあります
- ソケットが存在しない場合:
  - `pipewire-pulse` / `pulseaudio` の起動状態を確認してください
