# ml_workspace

機械学習（ML）関連の作業用ディレクトリです。データ収集（rosbag記録）や、学習・重み変換などの補助スクリプトを置きます。

## ディレクトリ構成

```text
ml_workspace/
├─ README.md
├─ .gitignore
├─ record_data.bash
├─ rawdata/                 # rosbag（生データ）保存先
│  └─ YYYYMMDD-HHMMSS/...
├─ train/                   # 学習用に分けたrosbag置き場（任意）
│  └─ YYYYMMDD-HHMMSS/...
├─ val/                     # 検証用に分けたrosbag置き場（任意）
│  └─ YYYYMMDD-HHMMSS/...
├─ tiny_lidar_net/
│  ├─ README.md
│  ├─ requirements.txt
│  ├─ train.py
│  ├─ config/
│  │  └─ train.yaml
│  ├─ datasets/             # extract_data_from_bag.py の出力先（実行時に生成；コミットされない）
│  │  ├─ train/...
│  │  └─ val/...
│  ├─ lib/
│  │  ├─ __init__.py
│  │  ├─ data.py
│  │  ├─ loss.py
│  │  └─ model.py
│  ├─ outputs/              # 学習ログ出力先（Hydraの既定；実行時に生成；コミットされない）
│  ├─ extract_data_from_bag.py
│  ├─ osm2csv.py
│  └─ convert_weight.py
├─ pilot_net/               # PilotNet 用のデータ変換・学習コード一式
└─ reinforcement_learning/  # 強化学習用の学習・評価コード一式
```

## 各項目の説明

- `.gitignore`: 学習データや生成物をリポジトリに含めないための設定です。
- `record_data.bash`: 学習用データ作成のために rosbag（mcap）を `rawdata/` 配下へ記録する補助スクリプトです。
- `rawdata/`: 記録した rosbag（mcap）の保存先です（タイムスタンプ名のディレクトリが作られます）。
- `train/`, `val/`: `rawdata/` から分けた rosbag（mcap）を置くためのディレクトリです（運用に応じて使います）。
- `tiny_lidar_net/`: TinyLiDARNet 用のデータ変換・学習・重み変換コード一式です。使い方は `aichallenge/ml_workspace/tiny_lidar_net/README.md` を参照してください。
- `pilot_net/`: PilotNet 用のデータ変換・学習コード一式です。
- `reinforcement_learning/`: 強化学習用の学習・評価コード一式です。
