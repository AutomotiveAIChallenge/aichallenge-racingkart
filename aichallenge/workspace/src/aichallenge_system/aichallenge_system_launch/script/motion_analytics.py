#!/usr/bin/env python3
import argparse
from datetime import datetime
import os
import sys
from pathlib import Path

import plotly.graph_objects as go
from plotly.subplots import make_subplots

from rosbag2_py import ConverterOptions
from rosbag2_py import SequentialReader
from rosbag2_py import StorageOptions

from rclpy.serialization import deserialize_message
from rosidl_runtime_py.utilities import get_message


def save_and_show_plot(fig, folder_name, file_name):    
    [name, suffix] = file_name.split(".")
    timestamp = datetime.now().strftime("%y-%m-%d-%H-%M-%S")
    output_path = (name + "-" + timestamp + "." + suffix)
    output_path_html = f"{name}-{timestamp}.html"
    # fig.write_image(output_path, width=800, height=600)
    fig.write_html(output_path_html)


def infer_configs(
    bag_uri: str,
    storage_ids={".db3": ("sqlite3", "cdr", "cdr"), ".mcap": ("mcap", "", "")},
) -> tuple:
    bag_uri_path = Path(bag_uri)
    if os.path.isfile(bag_uri):
        data_file = bag_uri_path
    else:
        data_file = next(p for p in bag_uri_path.glob("*") if p.suffix in storage_ids)
        if data_file.suffix not in storage_ids:
            raise ValueError(f"Unsupported storage id: {data_file.suffix}")
    return storage_ids[data_file.suffix]


def create_reader(input_uri: str) -> SequentialReader:
    """Create a reader object from the given input uri. The input uri could be a directory or a file."""
    storage_id, isf, osf = infer_configs(input_uri)
    storage_options = StorageOptions(
        uri=input_uri,
        storage_id=storage_id,
    )
    converter_options = ConverterOptions(
        input_serialization_format=isf,
        output_serialization_format=osf,
    )

    reader = SequentialReader()
    reader.open(storage_options, converter_options)
    return reader


def sync_topic(data1, data2) -> list:
    sync_data = []
    for idx1 in range(len(data1)):
        data = data2[0]
        for idx2 in range(len(data2)):
            if data1[idx1][0] < data2[idx2][0]:
                break
            data = data2[idx2]
        sync_data.append(data)
    return sync_data


analyze_topic_list = [
    "/localization/kinematic_state",
    "/localization/acceleration",
]


class Analyzer:
    def __init__(self, input_dir, output_dir):
        self.input_bag_dir = input_dir
        self.folder_name = output_dir
        self.window_size = 10
        self.file_name = "motion_analytics.png"

    def _read_bag_data(self):
        reader = create_reader(self.input_bag_dir)
        pose_time_stamp = []
        pose_speed = []
        pose_acceleration = []
        topic_type_list = {}

        for topic_type in reader.get_all_topics_and_types():
            topic_type_list[topic_type.name] = topic_type.type

        while reader.has_next():
            topic_name, msg, stamp = reader.read_next()
            stamp = stamp * 1e-9
            if topic_name in analyze_topic_list:
                data = deserialize_message(msg, get_message(topic_type_list[topic_name]))
                if topic_name == "/localization/kinematic_state":
                    if data.pose.pose.position.x != 0.0 or data.pose.pose.position.y != 0.0:
                        pose_time_stamp.append(
                            [stamp, data.pose.pose.position.x, data.pose.pose.position.y]
                        )
                        pose_speed.append([stamp, data.twist.twist.linear.x])
                elif topic_name == "/localization/acceleration":
                    pose_acceleration.append([stamp, data.accel.accel.linear.x])
        
        return pose_time_stamp, pose_speed, pose_acceleration, topic_type_list

    def _sync_and_filter_data(self, pose_time_stamp, pose_speed, pose_acceleration):
        if pose_speed and pose_time_stamp:
            pose_speed_filter = sync_topic(pose_time_stamp, pose_speed)
        else:
            pose_speed_filter = []

        if pose_acceleration and pose_time_stamp:
            pose_acceleration_filter = sync_topic(pose_time_stamp, pose_acceleration)
        else:
            pose_acceleration_filter = []

        return pose_speed_filter, pose_acceleration_filter
    
    def _create_plots(self, pose_time_stamp, pose_speed_filter, pose_acceleration_filter):
        # データを1/10に間引く
        step = 5
        pose_time_stamp_sampled = pose_time_stamp[::step]
        pose_speed_filter_sampled = pose_speed_filter[::step] if pose_speed_filter else []
        pose_acceleration_filter_sampled = pose_acceleration_filter[::step] if pose_acceleration_filter else []
        
        pose_x = [d[1] for d in pose_time_stamp_sampled]
        pose_y = [d[2] for d in pose_time_stamp_sampled]
        speed_values = [d[1] for d in pose_speed_filter_sampled]
        accel_values = [d[1] for d in pose_acceleration_filter_sampled]

        # x, yの全体範囲を計算
        all_x = [d[1] for d in pose_time_stamp] if pose_time_stamp else [0]
        all_y = [d[2] for d in pose_time_stamp] if pose_time_stamp else [0]
        x_min, x_max = min(all_x), max(all_x)
        y_min, y_max = min(all_y), max(all_y)

        # 単一プロット＋切り替えボタンによる解決策
        fig = go.Figure()
        
        # 速度データのトレース
        if speed_values:
            fig.add_trace(go.Scatter(
                x=pose_x, y=pose_y, mode='markers',
                marker=dict(
                    size=3, 
                    color=speed_values, 
                    colorscale='Jet', 
                    showscale=True,
                    colorbar=dict(
                        title='[m/s]',
                        title_side='top',
                        x=1.02,
                        thickness=15
                    )
                ),
                name='Velocity',
                visible=True
            ))
        
        # 加速度データのトレース
        if accel_values:
            fig.add_trace(go.Scatter(
                x=pose_x, y=pose_y, mode='markers',
                marker=dict(
                    size=3, 
                    color=accel_values, 
                    colorscale='Jet', 
                    showscale=True,
                    colorbar=dict(
                        title='[m/s²]',
                        title_side='top',
                        x=1.02,
                        thickness=15
                    )
                ),
                name='Acceleration',
                visible=False
            ))
        
        # 切り替えボタンを追加
        fig.update_layout(
            updatemenus=[{
                'buttons': [
                    {
                        'label': 'Velocity',
                        'method': 'update',
                        'args': [{'visible': [True, False]}]
                    },
                    {
                        'label': 'Acceleration', 
                        'method': 'update',
                        'args': [{'visible': [False, True]}]
                    }
                ],
                'direction': 'down',
                'showactive': True,
                'x': 0.1,
                'y': 1.15
            }]
        )

        # 軸の設定
        fig.update_xaxes(title_text="x [m]", range=[x_min, x_max])
        fig.update_yaxes(title_text="y [m]", range=[y_min, y_max], scaleanchor="x", scaleratio=1)
        
        # シンプルで確実なレイアウト設定
        fig.update_layout(
            template='plotly_dark',
            font=dict(size=14),
            showlegend=False,
            margin=dict(t=120, b=60, l=60, r=120),  # 右マージンを大きく取りカラーバー用スペース確保
            autosize=True,
            height=600
        )

        return fig

    def plot(self):
        pose_time_stamp, pose_speed, pose_acceleration, _ = self._read_bag_data()
        pose_speed_filter, pose_acceleration_filter = self._sync_and_filter_data(
            pose_time_stamp, pose_speed, pose_acceleration
        )
        
        fig = self._create_plots(pose_time_stamp, pose_speed_filter, pose_acceleration_filter)

        save_and_show_plot(fig, self.folder_name, self.file_name)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--input")
    parser.add_argument("--output", default=datetime.now().strftime("%y-%m-%d-%H-%M-%S"))
    args = parser.parse_args()

    analyzer = Analyzer(args.input, args.output)
    analyzer.plot()


if __name__ == "__main__":
    main()
