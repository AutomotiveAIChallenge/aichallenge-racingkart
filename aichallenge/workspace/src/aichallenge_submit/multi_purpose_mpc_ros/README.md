# multi_purpose_mpc_ros

このパッケージはリポジトリ内（`aichallenge/workspace/src/aichallenge_submit/multi_purpose_mpc_ros/`）に直接収録されています。別途 `git clone` は不要です。

## build

autoware コンテナ内で実行します（`make autoware-bash` または `make autoware-build`）：

```bash
cd /aichallenge/workspace
colcon build --symlink-install --allow-overriding gyro_odometer \
  --cmake-args -DCMAKE_BUILD_TYPE=Release
```

- ビルド時に仮想環境が `${ROS_WS}/install/multi_purpose_mpc_ros/.venv` に作成されます。

## run

### MPC コントローラー
```bash
ros2 run multi_purpose_mpc_ros run_mpc_controller.bash
```

### MPC シミュレーション
```bash
ros2 run multi_purpose_mpc_ros run_mpc_simulation.bash
```

### まとめて起動（コントローラー + シミュレーション）
```bash
ros2 launch multi_purpose_mpc_ros test.launch.xml
```

### Attribution
This repository includes code derived from:

Multi-Purpose-MPC  
Author: Mats Steinweg  
Original repository: https://github.com/matssteinweg/Multi-Purpose-MPC

Used with permission from the author.
