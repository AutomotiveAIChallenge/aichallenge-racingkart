import time
from collections import defaultdict
from contextlib import contextmanager

import rclpy
import rclpy.node

class ExecutionStats:
    def __init__(self, logger, window_size=10, record_count_threshold=5):
        self.logger = logger

        # Window size (number of periods to keep track of)
        self.window_size = window_size
        # Threshold for how many records before printing stats
        self.record_count_threshold = record_count_threshold
        # List to store timestamps of each execution
        self.timestamps = []
        # List to store time differences (periods) between executions
        self.periods = []
        # Counter to track how many times record() has been called
        self.record_count = 0
        # To store execution times of different labeled operations
        self.exec_times = defaultdict(list)
        # To store start times of different processes when using start/stop
        self.start_times = {}
    
    def record(self):
        """ Called from the periodic execution function. """
        current_time = time.time()
        if self.timestamps:
            # Calculate the difference from the last timestamp and store the period
            period = current_time - self.timestamps[-1]
            self.periods.append(period)
            if len(self.periods) > self.window_size:
                self.periods.pop(0)  # Remove old data
        
        # Store the current timestamp
        self.timestamps.append(current_time)
        if len(self.timestamps) > self.window_size:
            self.timestamps.pop(0)  # Remove old data
        
        # Increment the record count
        self.record_count += 1
        
        # Check if the record count has reached the threshold
        if self.record_count >= self.record_count_threshold:
            self.print_stats()
            self.record_count = 0  # Reset the counter after printing
    
    @contextmanager
    def time_block(self, label):
        """ Context manager to time a specific process with a label. """
        self.start_timer(label)
        yield
        self.stop_timer(label)
    
    def start_timer(self, label):
        """ Start timing a specific process with a label. """
        self.start_times[label] = time.time()
    
    def stop_timer(self, label):
        """ Stop timing a specific process and store the duration. """
        if label in self.start_times:
            exec_time = time.time() - self.start_times.pop(label)
            self.exec_times[label].append(exec_time)
            # Limit the stored execution times to the window size
            if len(self.exec_times[label]) > self.window_size:
                self.exec_times[label].pop(0)
    
    def get_stats(self):
        """ Returns statistical information about the periods. """
        if len(self.periods) == 0:
            return {
                'average_rate': None,
                'min_rate': None,
                'max_rate': None,
                'average_period': None,
                'min_period': None,
                'max_period': None,
            }
        
        # Calculate average, min, and max periods
        average_period = sum(self.periods) / len(self.periods)
        min_period = min(self.periods)
        max_period = max(self.periods)
        
        # Calculate rates based on the periods
        average_rate = 1 / average_period if average_period > 0 else None
        min_rate = 1 / max_period if max_period > 0 else None
        max_rate = 1 / min_period if min_period > 0 else None
        
        return {
            'average_rate': average_rate,
            'min_rate': min_rate,
            'max_rate': max_rate,
            'average_period': average_period,
            'min_period': min_period,
            'max_period': max_period,
        }
    
    def get_exec_time_stats(self, label):
        """ Returns statistical information about execution times for a specific label. """
        times = self.exec_times.get(label, [])
        if len(times) == 0:
            return {
                'average_time': None,
                'min_time': None,
                'max_time': None,
            }
        
        # Calculate average, min, and max execution times
        average_time = sum(times) / len(times)
        min_time = min(times)
        max_time = max(times)
        
        return {
            'average_time': average_time,
            'min_time': min_time,
            'max_time': max_time,
        }
    
    def print_stats(self):
        """ Prints statistical information in a simplified format. """
        stats = self.get_stats()
        # Convert periods from seconds to milliseconds for display
        avg_period_ms = stats['average_period'] * 1000 if stats['average_period'] is not None else 0
        min_period_ms = stats['min_period'] * 1000 if stats['min_period'] is not None else 0
        max_period_ms = stats['max_period'] * 1000 if stats['max_period'] is not None else 0

        # Print execution period stats
        self.logger.info(f"Ave: {stats['average_rate']:.2f} Hz ({avg_period_ms:.2f} ms), "
              f"Min: {stats['min_rate']:.2f} Hz ({max_period_ms:.2f} ms), "
              f"Max: {stats['max_rate']:.2f} Hz ({min_period_ms:.2f} ms)")
        
        # Print execution time stats for each labeled process
        for label, times in self.exec_times.items():
            exec_stats = self.get_exec_time_stats(label)
            avg_time_ms = exec_stats['average_time'] * 1000 if exec_stats['average_time'] is not None else 0
            min_time_ms = exec_stats['min_time'] * 1000 if exec_stats['min_time'] is not None else 0
            max_time_ms = exec_stats['max_time'] * 1000 if exec_stats['max_time'] is not None else 0
            self.logger.info(f"  [{label}] Ave: {avg_time_ms:.2f} ms, Min: {min_time_ms:.2f} ms, Max: {max_time_ms:.2f} ms")

# Usage example:
def main(args=None):
    rclpy.init(args=args)
    node = rclpy.node.Node("execution_stats_example")

    # Create an instance of the stats collection class with a threshold of 5
    stats = ExecutionStats(node.get_logger(), window_size=10, record_count_threshold=5)

    # Inside the periodic execution process, call stats.record()
    for _ in range(15):  # Simulate 15 periodic executions
        time.sleep(0.1)  # 0.1 second period
        stats.record()  # Record the timestamp

        # Simulate timing of specific processes with `with` statement
        with stats.time_block('process_with'):
            time.sleep(0.05)  # Simulate process execution time

        # Simulate timing of specific processes with start/stop method
        stats.start_timer('process_manual')
        time.sleep(0.02)  # Simulate another process execution time
        stats.stop_timer('process_manual')

    rclpy.shutdown()

if __name__ == "__main__":
    main()
