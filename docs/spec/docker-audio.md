# Dockerで音を出す（Linux / PipeWire・Pulse）

> 仕様ドキュメント（現仕様の正）。最終確認: 2026-06-14。文書運用方針は [docs/README.md](../README.md) を参照。

`simulator` コンテナ（AWSIM）からホストに音を出すための設定をまとめます。

## 前提

- ホストOS: Linux（Ubuntu 想定）
- ホストで PipeWire（`pipewire-pulse`）または PulseAudio が動作している
- Pulse ソケットが存在する: `test -S /run/user/$(id -u)/pulse/native`

## 使い方

通常どおり起動するだけです。`simulator` サービスの `PULSE_SERVER` は compose 側で自動設定されます。

```bash
make simulator
# または
make dev
```

`docker-compose.sound.yml` は **`simulator` サービスのみ**を対象としており、autoware サービスへの音声設定は含まれません。`.env` の `COMPOSE_FILE` にこのファイルを含めることで有効になります（デフォルト構成では含まれています）。

## 音が出ないとき

- `HOST_UID` が正しく渡っているか確認する
  - `make` 経由であれば通常は自動設定されます
  - `docker compose` を直接実行する場合は `HOST_UID=$(id -u)` を明示してください
- ソケットが存在しない場合は `pipewire-pulse` / `pulseaudio` の起動状態を確認してください
