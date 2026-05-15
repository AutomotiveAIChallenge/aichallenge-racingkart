# PilotNet Workspace

[NVIDIA PilotNet (DAVE-2)](https://arxiv.org/abs/1604.07316) 用のデータ抽出・学習・デプロイコード。

- ROS 推論ノードは [pilot_net_controller](../../workspace/src/aichallenge_submit/pilot_net_controller) を参照

## 学習用データの作成

以下 2 つの topic を含む rosbag を記録した後、`extract_data_from_bag.py` を実行する。

- `sensor_msgs/msg/Image` : `/sensing/camera/image_raw` (フロントカメラ)
- `autoware_auto_control_msgs/msg/AckermannControlCommand` : `/control/command/control_cmd` (教師信号)

```bash
python3 extract_data_from_bag.py --bags-dir /path/to/bags --outdir ./dataset/all/
python3 prepare_data.py
```

`extract_data_from_bag.py` はデフォルトで上部 37.5% をクロップした上で 66x200 にリサイズして保存する。

## 学習

```bash
python3 train.py
# loss.accel_weight=0.0 でステアのみ学習可能 (アクセル学習が不安定な場合に推奨)
```

## 重みの形式変換

`.pth` から `.npy` に変換 (推論は NumPy のみで動かすため)。

```bash
python3 convert_weight.py --ckpt ./checkpoints/best_model.pth
```

## ワンショット実行

extract → prepare → train → convert を一気に実行:

```bash
./run_pipeline.bash <bag_dir>
```

引数で解像度等を変更できる:

```bash
./run_pipeline.bash <bag_dir> <image_height> <image_width> <color_space> <output_dim> <crop_top_ratio>
```
