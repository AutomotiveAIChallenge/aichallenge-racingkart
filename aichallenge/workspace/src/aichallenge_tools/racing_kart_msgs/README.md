# racing_kart_msgs（部分複製）

**[tier4/racing_kart_interface](https://github.com/tier4/racing_kart_interface) の `src/racing_kart_msgs` から、必要なメッセージだけを取り出したものです。ここで編集しないでください。**

| 項目 | 値 |
| --- | --- |
| 取得元 | `racing_kart_interface/src/racing_kart_msgs` |
| 取得時のコミット | `c84e260f99f484d3dc4ec6f7f02aca625c7b112f` |
| 取得日 | 2026-08-14 |
| 含むメッセージ | `VehicleDebug` のみ（上流は11個） |

## なぜ必要か

遠隔操作PCは `aichallenge-2025-dev` イメージで動くが、このイメージには
`racing_kart_msgs` が入っていない。一方 `racing_kart_manager` は停止プロトコルで
`/<VEHICLE_ID>/racing_kart/debug/status`（`VehicleDebug`）を購読し、
`emergency` がラッチされたかを確認する必要がある。

`VehicleDebug` の依存は `builtin_interfaces` だけなので、このワークスペースで
一緒にビルドすれば足りる。

## なぜ VehicleDebug だけか

aichallenge 側のソースで他の10個を使っている箇所は無い
（`racing_kart_gnss_poser` の `package.xml` に依存宣言があるが、
CMakeLists にもソースにも参照が無く実体は未使用）。
使わないものを持つと、上流との同期対象が無駄に増える。

必要になったら上流から該当の `.msg` を追加し、`CMakeLists.txt` の
`rosidl_generate_interfaces` と、必要なら `package.xml` の依存にも足すこと。

## 注意1: 定義がずれると黙ってデータが来なくなる

**メッセージ定義が上流とずれると、DDS の型ハッシュが変わって購読が成立しなくなる。**
エラーにはならず、単にデータが来ない。manager から見ると `emergency` が永久に
`UNKNOWN` のままになり、全操作が塞がれる。

上流で `VehicleDebug.msg` を変更したら、必ずここも同じ内容に更新すること。

## 注意2: パッケージ名が上流と衝突する

型名を一致させる必要があるため、パッケージ名は `racing_kart_msgs` のままでなければ
ならない。したがって racing_kart_interface の install を同時に source する環境では
**同名パッケージが2つ存在する**。

該当するのは rosbag 収録（`aichallenge/utils/record_all_rosbag.bash`）で、
source 順が次のようになっている。

```
/opt/ros/humble/setup.bash
/aichallenge/workspace/install/setup.bash          ← この部分複製
${racing_kart_interface_dir}/install/setup.bash    ← 上流の完全版
```

**後から source した方が優先される**ため、収録時は上流の完全版が使われ、
11種すべてを記録できる。この順序を入れ替えると、部分複製が完全版を隠して
他のメッセージ型が解決できなくなるので変更しないこと。
