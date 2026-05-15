std_msgs/Header header                   # 観測時刻 (stamp) と座標フレーム (frame_id, e.g. "map")
string vehicle_id                        # 車両を識別する ID (e.g. "d1".."d4")
geometry_msgs/Point position             # 各車両の位置 (header の frame_id 系)
geometry_msgs/Vector3 covariance         # 位置不確実性 (x/y/z 軸の標準偏差 [m])
