# TinyLiDARNet Workspace

このworkspaceでは、[TinyLidarNet](https://arxiv.org/abs/2410.07447)用のデータ変換・学習・deployコードを提供しています。

- 参考: [TinyLidarNet: 2D LiDAR-based End-to-End Deep Learning Model for F1TENTH Autonomous Racing](https://arxiv.org/abs/2410.07447)

- TinyLiDARNetについての解説は[こちら](https://automotiveaichallenge.github.io/aichallenge-documentation-racingkart/ml_sample/algorithms.html#tinylidarnet)を参照してください。

- データ収集から走行までの一連の手順は、ドキュメントの[TinyLidarNet](https://automotiveaichallenge.github.io/aichallenge-documentation-racingkart/ml_sample/tiny_lidar_net.html)のページを参照してください。

- TinyLiDARNetの実行用コードは、[tiny_lidar_net_controller](../../workspace/src/aichallenge_submit/tiny_lidar_net_controller)を参照してください。

## 学習用データの作成
以下２つのTopicを含むrosbagを記録した後, extract_data_from_bag.pyを実行します。

- [`sensor_msgs/msg/LaserScan`](https://github.com/ros2/common_interfaces/blob/humble/sensor_msgs/msg/LaserScan.msg) : 2D LiDAR点群のtopic
- [`autoware_auto_control_msgs/msg/AckermannControlCommand`](https://github.com/tier4/autoware_auto_msgs/blob/tier4/main/autoware_auto_control_msgs/msg/AckermannControlCommand.idl) : 学習のtarget(教師)となる、アクセルとステアリングの情報を含むtopic

訓練用と検証用の rosbag を分けて置き、それぞれ変換します。出力先は `config/train.yaml` の `data.train_dir` / `data.val_dir` の既定値（`dataset/train` / `dataset/val`）に合わせています。

```bash
cd /aichallenge/ml_workspace/tiny_lidar_net
python3 extract_data_from_bag.py --bags-dir /aichallenge/ml_workspace/train/ --outdir ./dataset/train/
python3 extract_data_from_bag.py --bags-dir /aichallenge/ml_workspace/val/ --outdir ./dataset/val/
```

## 学習
各種パラメータは `config/train.yaml` で設定し、[Hydra](https://hydra.cc/) のオーバーライドでコマンドラインから上書きできます。損失の重みは `train:` の下にあるので、キーは `train.loss.*` です（`loss.*` と書くと `Key 'loss' is not in struct` で止まります）。

`train.loss.accel_weight=0.0` にすることで、ステアのみ学習を行うことが可能です。
アクセルの学習がうまく行かなかったため、まずはステアのみで学習することを推奨します。
```bash
python3 train.py \
    data.train_dir=/path/to/train_dir \
    data.val_dir=/path/to/val_dir \
    model.name='TinyLidarNet' \
    train.loss.steer_weight=1.0 \
    train.loss.accel_weight=0.0
```

エラー文は `+loss.accel_weight=0.0` のように `+` を付けることを勧めますが、`+` を付けると設定の最上位に `loss` という別のキーが追加されるだけで、`train.loss.accel_weight` は 1.0 のまま学習が進みます。`+` は付けないでください。

ステアのみを学習した重みで走らせる場合は、[tiny_lidar_net_controller の設定](../../workspace/src/aichallenge_submit/tiny_lidar_net_controller/config/tiny_lidar_net_node.param.yaml) の `control_mode` を `"fixed"`（既定値）のままにしてください。

## 重みの形式変換
採点環境において実行できるように、pytorchではなくnumpyを用います。そのため、`.pth`から`.npy/.npz`に重みを変換します。
学習した重みは `config/train.yaml` の `train.save_dir`（既定は `checkpoints/`）に `best_model.pth` として保存されます。
```bash
python3 convert_weight.py --model tinylidarnet --ckpt ./checkpoints/best_model.pth --output ./weights/converted_weights.npy
```
