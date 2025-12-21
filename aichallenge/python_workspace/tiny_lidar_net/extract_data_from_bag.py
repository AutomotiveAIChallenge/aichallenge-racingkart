import argparse
import logging
import multiprocessing
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Tuple, List

import numpy as np
from rosbags.highlevel import AnyReader


@dataclass
class ExtractionConfig:
    """Configuration parameters for data extraction."""
    control_topic: str
    scan_topic: str
    control_msg_type: str = 'autoware_auto_control_msgs/msg/AckermannControlCommand'
    scan_msg_type: str = 'sensor_msgs/msg/LaserScan'
    max_scan_range: float = 30.0


def worker_init(debug_mode: bool) -> None:
    """
    Initializes the logging configuration for worker processes.

    Args:
        debug_mode: If True, sets logging level to DEBUG.
    """
    level = logging.DEBUG if debug_mode else logging.INFO
    logging.basicConfig(
        level=level,
        format='[%(levelname)s] [PID:%(process)d] %(message)s',
        force=True
    )


def setup_logger(debug: bool = False) -> logging.Logger:
    """
    Sets up the main process logger.

    Args:
        debug: If True, enables debug logging.

    Returns:
        Configured Logger instance.
    """
    level = logging.DEBUG if debug else logging.INFO
    logging.basicConfig(
        level=level,
        format='[%(levelname)s] [PID:%(process)d] %(message)s',
        handlers=[logging.StreamHandler()]
    )
    return logging.getLogger(__name__)


def clean_scan_array(scan_array: np.ndarray, max_range: float) -> np.ndarray:
    """
    Sanitizes LiDAR scan data by handling non-finite values.

    Operations:
        - NaN -> 0.0
        - Positive Inf -> max_range
        - Negative Inf -> 0.0
        - Values > max_range -> clipped to max_range

    Args:
        scan_array: Raw input array from LaserScan message.
        max_range: The maximum valid range distance.

    Returns:
        A float32 numpy array with cleaned values.
    """
    if not isinstance(scan_array, np.ndarray):
        scan_array = np.array(scan_array, dtype=np.float32)

    # Replace NaN with 0.0 and positive infinity with max_range
    cleaned = np.nan_to_num(scan_array, nan=0.0, posinf=max_range, neginf=0.0)
    
    # Clip values to ensure they fall within the valid range [0.0, max_range]
    cleaned = np.clip(cleaned, 0.0, max_range)
    
    return cleaned.astype(np.float32)


def synchronize_data(src_times: np.ndarray, target_times: np.ndarray) -> Tuple[np.ndarray, np.ndarray]:
    """
    Synchronizes two time series using Nearest Neighbor search.
    Optimized with np.searchsorted for O(N log M) complexity.

    Args:
        src_times: Timestamps of the source data (e.g., Scan times).
        target_times: Reference timestamps to match against (e.g., Control times).

    Returns:
        A tuple containing:
            - indices: Indices of target_times that are closest to src_times.
            - deltas: Absolute time differences between matched timestamps.
    """
    if len(target_times) == 0:
        return np.array([]), np.array([])
        
    # Find insertion points for source times in target times
    idx_sorted = np.searchsorted(target_times, src_times)
    
    # Clip indices to stay within valid bounds
    idx_sorted = np.clip(idx_sorted, 0, len(target_times) - 1)
    prev_idx = np.clip(idx_sorted - 1, 0, len(target_times) - 1)
    
    # Calculate time differences for current and previous indices
    time_diff_curr = np.abs(target_times[idx_sorted] - src_times)
    time_diff_prev = np.abs(target_times[prev_idx] - src_times)
    
    # Select the index with the smaller time difference
    use_prev = time_diff_prev < time_diff_curr
    final_indices = np.where(use_prev, prev_idx, idx_sorted)
    final_deltas = np.where(use_prev, time_diff_prev, time_diff_curr)
    
    return final_indices, final_deltas


def process_bag(
    bag_path: Path, 
    output_root: Path, 
    config: ExtractionConfig, 
    debug: bool = False
) -> None:
    """
    Worker function to process a single ROS bag file.
    Reads, cleans, synchronizes, and saves the data.
    """
    logger = logging.getLogger(__name__)
    bag_name = bag_path.name
    out_dir = output_root / bag_name
    out_dir.mkdir(parents=True, exist_ok=True)

    t_start_total = time.perf_counter()

    cmd_data: List[List[float]] = []
    cmd_times: List[int] = []
    scan_data: List[np.ndarray] = []
    scan_times: List[int] = []

    # --- 1. Read Bag File ---
    t_start_read = time.perf_counter()
    try:
        with AnyReader([bag_path]) as reader:
            target_topics = [config.control_topic, config.scan_topic]
            connections = [c for c in reader.connections if c.topic in target_topics]
            
            if not connections:
                if debug: logger.warning(f"{bag_name}: No relevant topics found.")
                return

            for conn, timestamp, raw in reader.messages(connections=connections):
                try:
                    msg = reader.deserialize(raw, conn.msgtype)
                    
                    # Extract Control Command
                    if conn.topic == config.control_topic:
                        if conn.msgtype == config.control_msg_type:
                            accel = msg.longitudinal.acceleration
                            steer = msg.lateral.steering_tire_angle
                            cmd_data.append([steer, accel])
                            cmd_times.append(timestamp)
                    
                    # Extract LiDAR Scan
                    elif conn.topic == config.scan_topic:
                        if conn.msgtype == config.scan_msg_type:
                            ranges = np.array(msg.ranges, dtype=np.float32)
                            scan_vec = clean_scan_array(ranges, config.max_scan_range)
                            scan_data.append(scan_vec)
                            scan_times.append(timestamp)
                except Exception:
                    continue
    except Exception as e:
        logger.error(f"Failed to read {bag_name}: {e}")
        return

    t_end_read = time.perf_counter()

    if not cmd_data or not scan_data:
        if debug: logger.warning(f"Skipping {bag_name}: Insufficient data.")
        return

    # Convert lists to NumPy arrays for efficient processing
    np_cmd_data = np.array(cmd_data, dtype=np.float32)
    np_cmd_times = np.array(cmd_times, dtype=np.int64)
    np_scan_data = np.array(scan_data, dtype=np.float32)
    np_scan_times = np.array(scan_times, dtype=np.int64)

    # --- 2. Synchronize Data ---
    t_start_sync = time.perf_counter()
    
    # searchsorted requires the target array to be sorted
    sort_idx = np.argsort(np_cmd_times)
    np_cmd_times = np_cmd_times[sort_idx]
    np_cmd_data = np_cmd_data[sort_idx]

    indices, deltas = synchronize_data(np_scan_times, np_cmd_times)

    synced_cmds = np_cmd_data[indices]
    synced_steers = synced_cmds[:, 0]
    synced_accels = synced_cmds[:, 1]
    
    t_end_sync = time.perf_counter()

    # --- 3. Save Results ---
    t_start_save = time.perf_counter()
    
    np.save(out_dir / 'scans.npy', np_scan_data)
    np.save(out_dir / 'steers.npy', synced_steers)
    np.save(out_dir / 'accelerations.npy', synced_accels)
    
    # Save delta times only when debugging to save disk space/IO
    if debug:
        delta_seconds = deltas / 1e9
        np.save(out_dir / 'delta_times.npy', delta_seconds)
    
    t_end_save = time.perf_counter()
    duration_total = t_end_save - t_start_total

    # Log successful processing
    logger.info(f"Saved {bag_name}: {len(np_scan_data)} samples (Total: {duration_total:.2f}s)")

    if debug:
        duration_read = t_end_read - t_start_read
        duration_sync = t_end_sync - t_start_sync
        duration_save = t_end_save - t_start_save
        delta_seconds = deltas / 1e9
        
        logger.debug(
            f"  [Performance {bag_name}]\n"
            f"    - Read : {duration_read:.4f}s\n"
            f"    - Sync : {duration_sync:.4f}s\n"
            f"    - Save : {duration_save:.4f}s\n"
            f"  [Sync Stats]\n"
            f"    - Δt Mean: {delta_seconds.mean():.6f}s\n"
            f"    - Δt Max : {delta_seconds.max():.6f}s"
        )


def main():
    parser = argparse.ArgumentParser(
        description='Extract and synchronize scan and control data from ROS 2 bags.',
        formatter_class=argparse.ArgumentDefaultsHelpFormatter
    )
    
    # Input/Output arguments
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument('--bags-dir', type=Path, help='Path to directory containing rosbag folders (recursive search).')
    group.add_argument('--seq-dirs', type=Path, nargs='+', help='List of specific sequence directories to process.')
    parser.add_argument('--outdir', type=Path, required=True, help='Root directory for output files.')
    
    # Topic configuration
    parser.add_argument('--control-topic', type=str, default='/awsim/control_cmd', help='Topic name for control commands.')
    parser.add_argument('--scan-topic', type=str, default='/sensing/lidar/scan', help='Topic name for LiDAR scans.')
    
    # Performance arguments
    default_workers = min(os.cpu_count() or 1, 8)
    parser.add_argument('--workers', type=int, default=default_workers, help='Number of parallel workers.')
    parser.add_argument('--debug', action='store_true', help='Enable detailed performance and debug logging.')

    args = parser.parse_args()
    setup_logger(args.debug)
    logger = logging.getLogger(__name__)

    # --- Discovery Phase ---
    bag_dirs = []
    if args.bags_dir:
        p = args.bags_dir.expanduser().resolve()
        # Find directories containing metadata.yaml
        bag_dirs = [x.parent for x in p.rglob("metadata.yaml")]
        # Handle case where bags-dir itself is a bag
        if not bag_dirs and (p / "metadata.yaml").exists():
            bag_dirs = [p]
    elif args.seq_dirs:
        for p in args.seq_dirs:
            p = p.expanduser().resolve()
            if (p / "metadata.yaml").exists():
                bag_dirs.append(p)
    
    bag_dirs = sorted(list(set(bag_dirs)))
    if not bag_dirs:
        logger.error("No valid ROS 2 bag directories found.")
        return

    # Intelligent worker sizing: don't create more workers than tasks
    num_workers = min(max(1, args.workers), len(bag_dirs))
    logger.info(f"Found {len(bag_dirs)} bags. Starting processing with {num_workers} workers.")

    # --- Processing Phase ---
    config = ExtractionConfig(control_topic=args.control_topic, scan_topic=args.scan_topic)
    tasks = [(p, args.outdir, config, args.debug) for p in bag_dirs]

    start_time = time.time()
    
    # Use 'spawn' method for compatibility with various environments (especially if CUDA is involved later)
    with multiprocessing.Pool(processes=num_workers, initializer=worker_init, initargs=(args.debug,)) as pool:
        pool.starmap(process_bag, tasks)
        
    logger.info(f"All processing finished in {time.time() - start_time:.2f} seconds.")


if __name__ == '__main__':
    # Ensure multiprocessing works correctly on all platforms
    try:
        multiprocessing.set_start_method('spawn', force=True)
    except RuntimeError:
        pass
    main()
