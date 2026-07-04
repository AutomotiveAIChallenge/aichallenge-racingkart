# rl_train_controller

このディレクトリは、ROS2 の reset トピックを別ドメイン間で中継するための最小パッケージです。

- DOMAIN=1 の `/awsim/reset` を購読
- DOMAIN=0 の `/admin/awsim/reset` に転送


## ディレクトリ構成

```text
rl_train/
  CMakeLists.txt
  package.xml
  launch/
    rl_train.launch.xml
  rl_train_controller/
    __init__.py
    rl_train_controller_node.py
```

## ノード概要

実装: `rl_train_controller/rl_train_controller_node.py`

- ノード名: `rl_train_node`
- 購読トピック (parameter): `src_topic` (default: `/awsim/reset`)
- 配信トピック (parameter): `dst_topic` (default: `/admin/awsim/reset`)
- メッセージ型: `std_msgs/msg/Empty`

内部で ROS2 Context を 2 つ作成し、DOMAIN=0 と DOMAIN=1 を同時に扱います。
