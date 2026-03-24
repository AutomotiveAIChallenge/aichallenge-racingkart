#!/bin/bash
set -e
cd /aichallenge/ml_workspace/pilot_net

BAG_DIR="${1:?Usage: run_pipeline.bash <bag_dir>}"

echo "=== 1. Extract data ==="
rm -rf dataset
python3 extract_data_from_bag.py \
    --seq-dirs "$BAG_DIR" \
    --outdir ./dataset/all \
    --image-topic /sensing/camera/image_raw \
    --control-topic /control/command/control_cmd \
    --debug

echo "=== 2. Prepare data ==="
python3 prepare_data.py

echo "=== 3. Train ==="
rm -rf checkpoints logs
trap 'echo "Training interrupted, continuing pipeline..."' INT
python3 train.py train.num_workers=0 +train.loss_type=mse || true
trap - INT

echo "=== 4. Convert weights ==="
python3 convert_weight.py \
    --ckpt ./checkpoints/best_model.pth \
    --output /aichallenge/workspace/src/aichallenge_submit/pilot_net_controller/ckpt/pilotnet_weights.npy \
    --image-height 256 --image-width 384 --output-dim 2

echo "=== Pipeline complete ==="
