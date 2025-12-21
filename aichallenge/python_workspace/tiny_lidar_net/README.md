# TinyLiDARNet Workspace

このworkspaceでは、[TinyLidarNet](https://arxiv.org/abs/2410.07447)用のデータ変換・学習・deployコードを提供しています。

- 参考: [TinyLidarNet: 2D LiDAR-based End-to-End Deep Learning Model for F1TENTH Autonomous Racing](https://arxiv.org/abs/2410.07447)

- TinyLiDARNetについての解説は[こちら](https://automotiveaichallenge.github.io/aichallenge-documentation-2025/ml_sample/algorithms.html#tinylidarnet)を参照してください。

- TinyLiDARNetの実行用コードは、[tiny_lidar_net_controller](../workspace/src/aichallenge_submit/tiny_lidar_net_controller)を参照してください。

## 学習用データの作成
以下２つのTopicを含むrosbagを記録した後, extract_data_from_bag.pyを実行します。

- [`sensor_msgs/msg/LaserScan`](https://github.com/ros2/common_interfaces/blob/humble/sensor_msgs/msg/LaserScan.msg) : 2D LiDAR点群のtopic
- [`autoware_auto_control_msgs/msg/AckermannControlCommand`](https://github.com/tier4/autoware_auto_msgs/blob/tier4/main/autoware_auto_control_msgs/msg/AckermannControlCommand.idl) : 学習のtarget(教師)となる、アクセルとステアリングの情報を含むtopic

```bash
python3 extract_data_from_bag.py --bags-dir /path/to/record/ --outdir ./datasets/
```

## 学習
loss.accel_weightを0.0にすることで、ステアのみ学習を行うことが可能です。
アクセルの学習がうまく行かなかったため、まずはステアのみで学習することを推奨します。
```bash
python3 train.py \
data.train_dir=/path/to/train_dir \
data.val_dir=/path/to/val_dir \
model.name='TinyLidarNet' \
loss.steer_weight=1.0 \
loss.accel_weight=0.0 \ 
```

## 重みの形式変換
採点環境において実行できるように、pytorchではなくnumpyを用います。そのため、`.pth`から`.npy/.npz`に重みを変換します。
```bash
python3 convert_weight.py --model tinylidarnet --ckpt ./ckpts/weight.pth
```
