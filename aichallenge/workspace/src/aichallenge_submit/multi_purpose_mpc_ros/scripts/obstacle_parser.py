#!/usr/bin/env python3

import sys
import pandas as pd

# ROS 2
import rclpy
import rclpy.serialization
import rosbag2_py
from std_msgs.msg import Float64MultiArray

def main(argv = sys.argv) -> None:
    ROSBAG_PATH = "/aichallenge/workspace/bag/rosbag2_2024_09_16-12_17_03/rosbag2_2024_09_16-12_17_03_0.db3"

    reader = rosbag2_py.SequentialReader()
    storage_options = rosbag2_py.StorageOptions(uri=ROSBAG_PATH, storage_id='sqlite3')
    converter_options = rosbag2_py.ConverterOptions(input_serialization_format='cdr', output_serialization_format='cdr')
    reader.open(storage_options, converter_options)

    obstacles = pd.DataFrame(columns=["wp_x", "wp_y"])
    while reader.has_next():
        (topic, data, timestamp) = reader.read_next()
        if topic == "/aichallenge/objects":
            msg = rclpy.serialization.deserialize_message(data, Float64MultiArray)

            for i in range(0, len(msg.data), 4):
                x = msg.data[i]
                y = msg.data[i + 1]
                obstacles = obstacles.append({"wp_x": x, "wp_y": y}, ignore_index=True)

    obstacles = obstacles.drop_duplicates()

    csv_filname = "obstacles_" + ROSBAG_PATH.split("/")[-1].replace(".db3", ".csv")
    obstacles.to_csv(csv_filname, index=False)

if __name__ == "__main__":
    main(sys.argv)
