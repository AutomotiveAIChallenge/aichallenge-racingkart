# PilotNet Controller

NVIDIA PilotNet (DAVE-2) アーキテクチャに基づくカメラ画像 End-to-End 自律走行コントローラー。

フロントカメラの RGB 画像から直接 `AckermannControlCommand` (加速度 + 操舵角) を出力する。MPC エキスパートからの模倣学習で訓練し、NumPy のみで推論を行う。

## Reference

> Bojarski, M., Del Testa, D., Dworakowski, D., Firner, B., Flepp, B., Goyal, P., Jackel, L.D., Monfort, M., Muller, U., Zhang, J., Zhang, X., Zhao, J., & Zieba, K. (2016).
> **End to End Learning for Self-Driving Cars.**
> arXiv:1604.07316. https://arxiv.org/abs/1604.07316

本実装は原論文の DAVE-2 アーキテクチャを本プロジェクトに適応したものである。主な変更点:

| | 原論文 (DAVE-2) | 本実装 |
|---|---|---|
| 入力 | YUV 66x200 | RGB 256x384 |
| 出力 | 1/r (逆旋回半径) | [accel, steer] (2次元) |
| FC層 | 1164-100-50-10-1 | flatten-100-50-10-2 |
| 活性化 | 不明 (論文未記載) | ReLU + Tanh (出力層) |
| 正規化 | ネットワーク内 BatchNorm | 前処理で /255.0 |
| 推論 | Torch 7 (GPU) | NumPy (CPU) |
| 学習 | Torch 7 | PyTorch |

## Architecture

```
Input: RGB Image (batch, 3, 256, 384)
  |
  v
Conv2d(3, 24, 5x5, stride=2)  + ReLU  -> (24, 126, 190)
Conv2d(24, 36, 5x5, stride=2) + ReLU  -> (36, 61, 93)
Conv2d(36, 48, 5x5, stride=2) + ReLU  -> (48, 29, 45)
Conv2d(48, 64, 3x3, stride=1) + ReLU  -> (64, 27, 43)
Conv2d(64, 64, 3x3, stride=1) + ReLU  -> (64, 25, 41)
  |
  v  Flatten -> 65,600
  |
Linear(65600, 100) + ReLU
Linear(100, 50)    + ReLU
Linear(50, 10)     + ReLU
Linear(10, 2)      + Tanh
  |
  v
Output: [acceleration, steering_angle]  (range: [-1, 1])
```

## Package Structure

```
pilot_net_controller/
  pilot_net_controller/
    __init__.py
    pilot_net_controller_node.py   # ROS2 ノード (Image 購読 → 制御出力)
    pilot_net_controller_core.py   # 推論パイプライン (前処理 + モデル + 後処理)
    model/
      __init__.py                  # NumPy レイヤー再エクスポート
      pilotnet.py                  # PilotNet (PyTorch) + PilotNetNp (NumPy)
      numpy/
        layers.py                  # conv2d, linear, relu, tanh, flatten, etc.
        initializers.py            # kaiming_normal_init, zeros_init
  config/
    pilot_net_node.param.yaml      # ノードパラメータ
  launch/
    pilot_net.launch.xml           # Launch ファイル
  ckpt/                            # 変換済み重みファイル (.npy)
  CMakeLists.txt
  package.xml
```

## Usage

### 1. データ収集 (MPC エキスパート走行の記録)

```bash
# AWSIM + MPC で走行中にカメラ画像 + 制御コマンドを記録
ros2 bag record --storage mcap \
  /sensing/camera/image_raw \
  /control/command/control_cmd \
  -o bag_pilotnet_data
```

### 2. データ抽出

```bash
cd /aichallenge/ml_workspace/pilot_net

python extract_data_from_bag.py \
  --bags-dir /path/to/bags \
  --outdir ./dataset/train \
  --image-height 256 \
  --image-width 384
```

出力: `images.npy` (N, 256, 384, 3) uint8, `steers.npy` (N,), `accelerations.npy` (N,)

### 3. 学習

```bash
cd /aichallenge/ml_workspace/pilot_net

python train.py
# または Hydra でパラメータ上書き:
python train.py train.epochs=200 train.lr=0.0005
```

### 4. 重み変換 (PyTorch -> NumPy)

```bash
python convert_weight.py \
  --ckpt ./checkpoints/best_model.pth \
  --output /path/to/pilot_net_controller/ckpt/pilotnet_weights.npy
```

### 5. 推論 (ROS2)

```bash
# reference.launch.xml 経由
ros2 launch aichallenge_submit_launch reference.launch.xml \
  control_method:=pilot_net \
  ckpt_path:=/path/to/pilotnet_weights.npy \
  simulation:=true \
  use_sim_time:=true
```

## ROS2 Interface

### Subscriptions

| Topic | Type | QoS | Description |
|-------|------|-----|-------------|
| `/sensing/camera/image_raw` | `sensor_msgs/msg/Image` | BEST_EFFORT, depth=1 | フロントカメラ画像 |

### Publications

| Topic | Type | Description |
|-------|------|-------------|
| `/control/command/control_cmd` | `autoware_auto_control_msgs/msg/AckermannControlCommand` | 制御コマンド |

### Parameters

| Parameter | Default | Description |
|-----------|---------|-------------|
| `model.image_height` | 256 | 入力画像の高さ |
| `model.image_width` | 384 | 入力画像の幅 |
| `model.output_dim` | 2 | 出力次元 [accel, steer] |
| `model.ckpt_path` | `""` | NumPy 重みファイルパス |
| `control_mode` | `"fixed"` | `"ai"` (全出力NN) / `"fixed"` (加速度固定) |
| `acceleration` | 0.3 | `fixed` モード時の加速度 |
| `debug` | false | 推論速度ログ出力 |

## Training Pipeline

```
AWSIM + MPC Expert
       |
ros2 bag record (camera + control)
       |
extract_data_from_bag.py
       |
images.npy + steers.npy + accelerations.npy
       |
train.py (PyTorch, Hydra, TensorBoard)
       |
best_model.pth
       |
convert_weight.py (PyTorch -> NumPy)
       |
pilotnet_weights.npy -> deploy to ckpt/
```

## Notes

- 推論は **NumPy のみ** (PyTorch 不要)。40Hz ターゲットだが 256x384 の conv2d は計算負荷が高い。速度が不足する場合は入力解像度の縮小または ONNX Runtime への移行を検討。
- 画像エンコーディングは `bgr8`, `rgb8`, `bgra8`, `rgba8` に対応。`cv2.cvtColor` で RGB に変換。
- `control_mode: "fixed"` では加速度を固定値にし、ステアリングのみ NN 出力を使用。初期テストに推奨。
