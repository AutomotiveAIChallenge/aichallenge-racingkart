# reinforcement_learning

強化学習のための環境を整え、AWSIM の環境下で強化学習と推論をできるようにしたプログラム群です。環境構築ができていて、GPU を積んだコンピュータで実行していて、このリポジトリがホームディレクトリにクローンしてあることを想定しています。

## 強化学習の実行方法

まず、コンフィグや学習のログ、学習済みモデルを保存しておくディレクトリをつくります。名前はわかりやすければなんでもいいです。この説明では`202605122237`としますので適宜読み替えてください。aichallenge-racingkartのディレクトリもcloneしたレポジトリのディレクトリに読み替えてください。

```.bash
cd ~/aichallenge-racingkart/aichallenge/ml_workspace/reinforcement_learning/
mkdir workspace/202605122237/ -p
```

コンフィグを作るかコピーします。作ったコンフィグは今作ったディレクトリの中に置きます。この説明ではコピーの方法をかきます。

```.bash
cd ~/aichallenge-racingkart/aichallenge/ml_workspace/reinforcement_learning/
cp ./src/config/config_store/default_config.yaml ./workspace/202605122237/default_condig.yaml
```

次に、AWSIM が GPU を使って描画するように設定をします。`~/aichallenge-racingkart/.env`ファイルの中に以下の一行が存在するようにしてください。コメントアウトされていた場合は先頭の`#`を取り除いてください。

```text
COMPOSE_FILE=docker-compose.yml:docker-compose.gpu.yml
```

また、AWSIMのカメラ画像を有効にするために、
`aichallenge/simulator_scripts/dev.sh`の下記２点を変更してください。

1. `--camera off \`を`--camera cpu \`に変更してください。
2. `--start-count-seconds 5 \`を`--start-count-seconds 0 \`に変更してください。

その後、`aichallenge-racingkart/aichallenge/workspace/src/aichallenge_submit/aichallenge_submit_launch/launch/reference.launch.xml`のL20の下記の内容をrl_trainに書き換えてください。

```.xml
  <arg name="control_method" default="rl_train"
```


次に、Autoware をビルドし、Autoware と強化学習用の設定にした AWSIM を起動します。


```.bash
cd ~/aichallenge-racingkart/
make autoware-build
make dev
```

AWSIM の中に`Race Result`というウィンドウが出てきますが、これは正常な動作です。次に、Autoware を実行している docker コンテナのシェルにアクセスします。

```.bash
cd ~/aichallenge-racingkart/
./docker_exec.sh
```

ここでは、`autoware`が名前に含まれる docker コンテナを選んでください。ここから先は今アクセスした docker コンテナのシェルでコマンドを実行します。

強化学習の実行には以下のコマンドを使用します。

```.bash
cd /aichallenge/ml_workspace/reinforcement_learning/
ROS_DOMAIN_ID=1 python3 ./src/main.py --train --config ./workspace/202605122237/default_config.yaml
```

指定したステップ数だけ強化学習が終わると、モデルが圧縮されたファイルが`/aichallenge/ml_workspace/reinforcement_learning/workspace/202605122237/model.zip`に作られます。

## 推論走行の実行方法

モデルの圧縮ファイルの名前を変えます（今後変えなくてもいいようにするつもりです）。

```.bash
cd /aichallenge/ml_workspace/reinforcement_learning/workspace/202605122237/
mv model.zip awsim_sac_model.zip
```

推論走行を実行します。

```.bash
cd /aichallenge/ml_workspace/reinforcement_learning/workspace/202605122237/
ROS_DOMAIN_ID=1 python3 ../../src/main.py --infer --config ./default_config.yaml
```
