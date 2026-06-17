#!/usr/bin/env python3

import argparse
import json
import math
import os
import sys
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from rclpy.serialization import deserialize_message
from rosbag2_py import ConverterOptions, SequentialReader, StorageOptions
from rosidl_runtime_py.utilities import get_message


Sample = Tuple[float, float]
CommandSample = Tuple[float, float, float]
GearSample = Tuple[float, int]
StateSample = Tuple[float, str]


TOPIC_CONTROL = "/control/command/control_cmd"
TOPIC_NOMINAL_CONTROL = "/control/command/nominal_control_cmd"
TOPIC_CONDITION = "/aichallenge/pitstop/condition"
TOPIC_GEAR_CMD = "/control/command/gear_cmd"
TOPIC_GEAR_STATUS = "/vehicle/status/gear_status"
TOPIC_KINEMATIC = "/localization/kinematic_state"
TOPIC_SUPERVISOR_STATE = "/recovery_supervisor/state"
TOPIC_VELOCITY = "/vehicle/status/velocity_status"
GEAR_DRIVE = 2
GEAR_REVERSE = 20


def infer_storage_config(bag_uri: str) -> Tuple[str, str, str]:
    storage_ids = {
        ".db3": ("sqlite3", "cdr", "cdr"),
        ".mcap": ("mcap", "", ""),
    }

    def storage_suffix(path: Path) -> Optional[str]:
        if path.name.endswith(".mcap.zstd"):
            raise ValueError(
                "file-compressed .mcap.zstd bags are not supported by this analyzer; "
                "record recovery bags without rosbag file compression"
            )
        return path.suffix

    bag_path = Path(bag_uri)
    data_file = bag_path if bag_path.is_file() else None
    if data_file is None:
        for candidate in bag_path.glob("*"):
            if storage_suffix(candidate) in storage_ids:
                data_file = candidate
                break
    if data_file is None or storage_suffix(data_file) not in storage_ids:
        raise ValueError(f"unsupported or empty rosbag uri: {bag_uri}")
    return storage_ids[storage_suffix(data_file)]


def create_reader(bag_uri: str) -> SequentialReader:
    storage_id, input_format, output_format = infer_storage_config(bag_uri)
    reader = SequentialReader()
    reader.open(
        StorageOptions(uri=bag_uri, storage_id=storage_id),
        ConverterOptions(
            input_serialization_format=input_format,
            output_serialization_format=output_format,
        ),
    )
    return reader


def has_continuous_window(samples: Iterable[Sample], start_time: float, duration: float, predicate) -> bool:
    window_start: Optional[float] = None
    for stamp, value in samples:
        if stamp < start_time:
            continue
        if predicate(value):
            if window_start is None:
                window_start = stamp
            if stamp - window_start >= duration:
                return True
        else:
            window_start = None
    return False


def first_time(samples: Iterable[Sample], predicate) -> Optional[float]:
    for stamp, value in samples:
        if predicate(value):
            return stamp
    return None


def first_state_time(samples: Iterable[StateSample], predicate) -> Optional[float]:
    for stamp, value in samples:
        if predicate(value):
            return stamp
    return None


def latest_gear_at(gears: List[GearSample], stamp: float, timeout_sec: float) -> Optional[int]:
    latest: Optional[GearSample] = None
    for gear_stamp, gear in gears:
        if gear_stamp > stamp:
            break
        latest = (gear_stamp, gear)
    if latest is None:
        return None
    return latest[1] if stamp - latest[0] <= timeout_sec else None


def has_commanded_low_velocity_window(
    commands: List[CommandSample],
    gears: List[GearSample],
    velocities: List[Sample],
    start_time: float,
    duration: float,
    command_timeout_sec: float,
    command_predicate,
    velocity_predicate,
) -> Tuple[bool, Optional[float]]:
    if not commands or not velocities:
        return False, None

    command_index = -1
    gear_index = -1
    latest_command: Optional[CommandSample] = None
    latest_gear: Optional[GearSample] = None
    window_start: Optional[float] = None

    for stamp, velocity in velocities:
        if stamp < start_time:
            continue

        while command_index + 1 < len(commands) and commands[command_index + 1][0] <= stamp:
            command_index += 1
            latest_command = commands[command_index]

        while gear_index + 1 < len(gears) and gears[gear_index + 1][0] <= stamp:
            gear_index += 1
            latest_gear = gears[gear_index]

        latest_gear_value = latest_gear[1] if latest_gear is not None else None
        active_command = (
            latest_command is not None
            and stamp - latest_command[0] <= command_timeout_sec
            and command_predicate(latest_command, latest_gear_value)
        )
        if active_command and velocity_predicate(velocity):
            if window_start is None:
                window_start = stamp
            if stamp - window_start >= duration:
                return True, window_start
        else:
            window_start = None

    return False, None


def read_bag(bag_uri: str, collision_delta: int) -> Dict[str, Any]:
    reader = create_reader(bag_uri)
    topic_types = {topic.name: topic.type for topic in reader.get_all_topics_and_types()}

    commands: List[CommandSample] = []
    nominal_commands: List[CommandSample] = []
    gear_commands: List[GearSample] = []
    gear_reports: List[GearSample] = []
    velocity_reports: List[Sample] = []
    kinematic_velocities: List[Sample] = []
    conditions: List[Sample] = []
    supervisor_states: List[StateSample] = []
    collision_times: List[float] = []
    last_condition: Optional[int] = None

    while reader.has_next():
        topic_name, serialized, stamp_ns = reader.read_next()
        if topic_name not in (
            TOPIC_CONTROL,
            TOPIC_NOMINAL_CONTROL,
            TOPIC_CONDITION,
            TOPIC_GEAR_CMD,
            TOPIC_GEAR_STATUS,
            TOPIC_KINEMATIC,
            TOPIC_SUPERVISOR_STATE,
            TOPIC_VELOCITY,
        ):
            continue
        msg_type = topic_types.get(topic_name)
        if msg_type is None:
            continue
        msg = deserialize_message(serialized, get_message(msg_type))
        stamp = stamp_ns * 1e-9

        if topic_name == TOPIC_CONTROL:
            commands.append((
                stamp,
                float(msg.longitudinal.speed),
                float(msg.longitudinal.acceleration),
            ))
        elif topic_name == TOPIC_NOMINAL_CONTROL:
            nominal_commands.append((
                stamp,
                float(msg.longitudinal.speed),
                float(msg.longitudinal.acceleration),
            ))
        elif topic_name == TOPIC_GEAR_CMD:
            gear_commands.append((stamp, int(msg.command)))
        elif topic_name == TOPIC_GEAR_STATUS:
            gear_reports.append((stamp, int(msg.report)))
        elif topic_name == TOPIC_VELOCITY:
            velocity_reports.append((stamp, float(msg.longitudinal_velocity)))
        elif topic_name == TOPIC_KINEMATIC:
            kinematic_velocities.append((stamp, float(msg.twist.twist.linear.x)))
        elif topic_name == TOPIC_CONDITION:
            value = int(msg.data)
            conditions.append((stamp, float(value)))
            if last_condition is not None and value - last_condition >= collision_delta:
                collision_times.append(stamp)
            last_condition = value
        elif topic_name == TOPIC_SUPERVISOR_STATE:
            supervisor_states.append((stamp, str(msg.data)))

    velocities = velocity_reports if velocity_reports else kinematic_velocities
    return {
        "commands": commands,
        "nominal_commands": nominal_commands,
        "gears": gear_commands if gear_commands else gear_reports,
        "velocities": velocities,
        "conditions": conditions,
        "collision_times": collision_times,
        "supervisor_states": supervisor_states,
        "topics": sorted(topic_types.keys()),
    }


def evaluate(data: Dict[str, Any], args: argparse.Namespace) -> Dict[str, Any]:
    commands: List[CommandSample] = data["commands"]
    nominal_commands: List[CommandSample] = data["nominal_commands"]
    detection_commands = nominal_commands if nominal_commands else commands
    gears: List[GearSample] = data["gears"]
    velocities: List[Sample] = data["velocities"]
    collision_times: List[float] = data["collision_times"]
    supervisor_states: List[StateSample] = data["supervisor_states"]

    first_collision = collision_times[0] if collision_times else None
    min_velocity = min((v for _, v in velocities), default=None)
    max_velocity = max((v for _, v in velocities), default=None)
    negative_command_time = first_time(
        ((stamp, speed) for stamp, speed, _ in commands),
        lambda v: v <= args.negative_command_speed,
    )
    reverse_velocity_time = first_time(velocities, lambda v: v <= args.reverse_speed)
    finite_command_speeds = [speed for _, speed, _ in commands if math.isfinite(speed)]
    finite_command_accelerations = [
        acceleration for _, _, acceleration in commands if math.isfinite(acceleration)
    ]
    negative_command_count = sum(1 for v in finite_command_speeds if v <= args.negative_command_speed)
    positive_command_count = sum(1 for v in finite_command_speeds if v >= args.positive_command_speed)
    positive_acceleration_count = sum(
        1 for v in finite_command_accelerations if v >= args.positive_acceleration
    )
    forward_throttle_command_count = sum(
        1
        for stamp, speed, acceleration in detection_commands
        if (
            (math.isfinite(speed) and speed >= args.positive_command_speed)
            or (
                math.isfinite(acceleration)
                and acceleration >= args.positive_acceleration
                and latest_gear_at(gears, stamp, args.command_timeout_sec) in (None, GEAR_DRIVE)
            )
        )
    )
    first_moving_time = first_time(velocities, lambda v: v >= args.moving_speed)

    stopped_after_collision = False
    stopped_after_motion = False
    stuck_under_throttle = False
    stuck_under_throttle_time: Optional[float] = None
    resumed_forward_after_collision = False
    resumed_forward_after_stuck = False
    reverse_after_collision = False
    reverse_after_stuck = False
    reverse_gear_after_stuck_time: Optional[float] = None
    drive_gear_after_reverse_time: Optional[float] = None
    supervisor_released_after_reverse_time: Optional[float] = None
    forward_command_after_recovery_time: Optional[float] = None
    if first_collision is not None:
        stopped_after_collision = has_continuous_window(
            velocities,
            first_collision,
            args.stopped_duration_sec,
            lambda v: abs(v) <= args.stopped_speed,
        )
        reverse_after_collision = first_time(
            ((t, v) for t, v in velocities if t >= first_collision),
            lambda v: v <= args.reverse_speed,
        ) is not None
        resumed_forward_after_collision = has_continuous_window(
            velocities,
            first_collision,
            args.forward_duration_sec,
            lambda v: v >= args.forward_speed,
        )

    if first_moving_time is not None:
        stopped_after_motion = has_continuous_window(
            velocities,
            first_moving_time,
            args.stopped_duration_sec,
            lambda v: abs(v) <= args.stopped_speed,
        )
        stuck_under_throttle, stuck_under_throttle_time = has_commanded_low_velocity_window(
            detection_commands,
            gears,
            velocities,
            first_moving_time,
            args.stopped_duration_sec,
            args.command_timeout_sec,
            lambda command, gear: (
                (math.isfinite(command[1]) and command[1] >= args.positive_command_speed)
                or (
                    math.isfinite(command[2])
                    and command[2] >= args.positive_acceleration
                    and gear in (None, GEAR_DRIVE)
                )
            ),
            lambda velocity: abs(velocity) <= args.stopped_speed,
        )

    if stuck_under_throttle_time is not None:
        reverse_after_stuck = first_time(
            ((t, v) for t, v in velocities if t >= stuck_under_throttle_time),
            lambda v: v <= args.reverse_speed,
        ) is not None
        reverse_gear_after_stuck_time = first_time(
            ((t, float(gear)) for t, gear in gears if t >= stuck_under_throttle_time),
            lambda gear: int(gear) == GEAR_REVERSE,
        )
        if reverse_gear_after_stuck_time is not None:
            drive_gear_after_reverse_time = first_time(
                ((t, float(gear)) for t, gear in gears if t >= reverse_gear_after_stuck_time),
                lambda gear: int(gear) == GEAR_DRIVE,
            )
            supervisor_released_after_reverse_time = first_state_time(
                ((t, state) for t, state in supervisor_states if t >= reverse_gear_after_stuck_time),
                lambda state: state in ("NORMAL", "COOLDOWN"),
            )
        if drive_gear_after_reverse_time is not None:
            forward_command_after_recovery_time = first_time(
                ((t, speed) for t, speed, _ in commands if t >= drive_gear_after_reverse_time),
                lambda speed: math.isfinite(speed) and speed >= args.positive_command_speed,
            )
        resumed_forward_after_stuck = has_continuous_window(
            velocities,
            stuck_under_throttle_time,
            args.forward_duration_sec,
            lambda v: v >= args.forward_speed,
        )

    metrics = {
        "command_count": len(commands),
        "nominal_command_count": len(nominal_commands),
        "finite_command_count": len(finite_command_speeds),
        "negative_command_count": negative_command_count,
        "positive_command_count": positive_command_count,
        "positive_acceleration_count": positive_acceleration_count,
        "forward_throttle_command_count": forward_throttle_command_count,
        "command_min_speed": min(finite_command_speeds, default=None),
        "command_max_speed": max(finite_command_speeds, default=None),
        "command_min_acceleration": min(finite_command_accelerations, default=None),
        "command_max_acceleration": max(finite_command_accelerations, default=None),
        "velocity_count": len(velocities),
        "gear_count": len(gears),
        "supervisor_state_count": len(supervisor_states),
        "supervisor_states": sorted({state for _, state in supervisor_states}),
        "condition_count": len(data["conditions"]),
        "collision_count": len(collision_times),
        "first_collision_time": first_collision,
        "negative_command_time": negative_command_time,
        "first_moving_time": first_moving_time,
        "reverse_velocity_time": reverse_velocity_time,
        "stuck_under_throttle_time": stuck_under_throttle_time,
        "reverse_gear_after_stuck_time": reverse_gear_after_stuck_time,
        "drive_gear_after_reverse_time": drive_gear_after_reverse_time,
        "supervisor_released_after_reverse_time": supervisor_released_after_reverse_time,
        "forward_command_after_recovery_time": forward_command_after_recovery_time,
        "min_velocity": min_velocity,
        "max_velocity": max_velocity,
        "stopped_after_collision": stopped_after_collision,
        "stopped_after_motion": stopped_after_motion,
        "stuck_under_throttle": stuck_under_throttle,
        "reverse_after_collision": reverse_after_collision,
        "reverse_after_stuck": reverse_after_stuck,
        "resumed_forward_after_collision": resumed_forward_after_collision,
        "resumed_forward_after_stuck": resumed_forward_after_stuck,
    }

    if args.expect in ("stuck_under_throttle", "collision_stuck"):
        passed = stuck_under_throttle
        reason = (
            "stuck under throttle observed"
            if passed
            else "stuck under throttle not observed"
        )
    elif args.expect == "reverse_capable":
        passed = negative_command_time is not None and reverse_velocity_time is not None
        reason = "reverse command and negative velocity observed" if passed else "reverse command or negative velocity missing"
    elif args.expect == "recovered":
        passed = (
            stuck_under_throttle_time is not None
            and reverse_gear_after_stuck_time is not None
            and reverse_after_stuck
            and drive_gear_after_reverse_time is not None
            and supervisor_released_after_reverse_time is not None
            and forward_command_after_recovery_time is not None
        )
        reason = "stuck, reverse gear, negative velocity, drive return, and pass-through observed" if passed else "recovery sequence incomplete"
    else:
        passed = False
        reason = f"unsupported expectation: {args.expect}"

    return {
        "expect": args.expect,
        "pass": passed,
        "reason": reason,
        "metrics": metrics,
    }


def default_output_path(bag_uri: str) -> Path:
    bag_path = Path(bag_uri)
    if bag_path.is_file():
        return bag_path.parent / "recovery-result.json"
    return bag_path.parent / "recovery-result.json"


def parse_args(argv: List[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Analyze recovery development rosbags.")
    parser.add_argument("bag", help="rosbag directory or storage file")
    parser.add_argument(
        "--expect",
        choices=("stuck_under_throttle", "collision_stuck", "reverse_capable", "recovered"),
        default="stuck_under_throttle",
        help="expected scenario to evaluate",
    )
    parser.add_argument("--output", help="output JSON path")
    parser.add_argument("--collision-delta", type=int, default=30)
    parser.add_argument("--stopped-speed", type=float, default=0.2)
    parser.add_argument("--stopped-duration-sec", type=float, default=1.0)
    parser.add_argument("--command-timeout-sec", type=float, default=0.3)
    parser.add_argument("--negative-command-speed", type=float, default=-0.1)
    parser.add_argument("--positive-command-speed", type=float, default=0.5)
    parser.add_argument("--positive-acceleration", type=float, default=0.1)
    parser.add_argument("--moving-speed", type=float, default=0.5)
    parser.add_argument("--reverse-speed", type=float, default=-0.1)
    parser.add_argument("--forward-speed", type=float, default=1.0)
    parser.add_argument("--forward-duration-sec", type=float, default=2.0)
    return parser.parse_args(argv)


def main(argv: List[str] = None) -> int:
    args = parse_args(argv if argv is not None else sys.argv[1:])
    data = read_bag(args.bag, args.collision_delta)
    result = evaluate(data, args)

    output_path = Path(args.output) if args.output else default_output_path(args.bag)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result["pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
