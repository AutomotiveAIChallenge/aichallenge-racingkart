# multi_purpose_mpc_ros

## setup
```
cd /aichallenge/workspace/src/aichallenge_submit/
git clone git@github.com:Roborovsky-Racers/multi_purpose_mpc_ros.git
cd multi_purpose_mpc_ros
git clone git@github.com:Roborovsky-Racers/Multi-Purpose-MPC.git -b aic-2024
```

## build
```
cd /aichallenge/workspace/
cb
```

- virtual env will be created to ${ROS_WS}/install/multi_purpose_mpc_ros/.venv when build time

## run
### sample simple publisher node
```
ros2 run multi_purpose_mpc_ros run.bash
```

### Multi-Purpose-MPC simulation
```
ros2 run multi_purpose_mpc_ros simulation.bash
```

### both
```
ros2 launch multi_purpose_mpc_ros test.launch.xml
```