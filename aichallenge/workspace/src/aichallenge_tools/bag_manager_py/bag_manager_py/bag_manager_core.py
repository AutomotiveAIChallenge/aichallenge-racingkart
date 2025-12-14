#!/usr/bin/env python3
import os
import signal
import subprocess
import datetime
import threading
import logging
from typing import List, Tuple, Optional

class BagRecorderCore:
    """Handles the logic for recording ROS 2 bags via subprocess.

    This class manages the lifecycle of the `ros2 bag record` process, including
    command generation, process spawning, and robust termination handling.
    It is designed to be framework-agnostic to facilitate unit testing.
    """

    def __init__(self, output_dir: str, topics: List[str], all_topics: bool, 
                 storage_id: str, logger: Optional[logging.Logger] = None):
        """Initializes the BagRecorderCore.

        Args:
            output_dir (str): The base directory where bag files will be saved.
            topics (List[str]): A list of specific topics to record.
            all_topics (bool): If True, records all topics, ignoring the `topics` list.
            storage_id (str): The storage format ID (e.g., 'mcap', 'sqlite3').
            logger (Optional[logging.Logger]): A logger instance. If None, a standard
                Python logger is used.
        """
        self.output_dir = output_dir
        self.topics = topics
        self.all_topics = all_topics
        self.storage_id = storage_id
        self.logger = logger or logging.getLogger(__name__)

        self.session_dir = self._setup_session_dir()
        self.recording_process = None
        self.is_recording = False
        self.lock = threading.Lock()

    def _setup_session_dir(self) -> str:
        """Creates a timestamped session directory.

        Returns:
            str: The absolute path to the created session directory.
        """
        now = datetime.datetime.now()
        session_ts = now.strftime('%Y%m%d_%H%M%S')
        path = os.path.join(self.output_dir, session_ts)
        os.makedirs(path, exist_ok=True)
        self.logger.info(f"Session directory created: {path}")
        return path

    def start(self) -> Tuple[bool, str]:
        """Starts the recording process.

        This method generates the recording command and spawns a subprocess.
        It is thread-safe.

        Returns:
            Tuple[bool, str]: A tuple containing a success flag (True/False)
            and a status message.
        """
        with self.lock:
            if self.is_recording:
                self.logger.warning("Attempted to start, but already recording.")
                return False, "Already recording"

            ts = datetime.datetime.now().strftime('%Y%m%d_%H%M%S_%f')
            record_dir = os.path.join(self.session_dir, ts)
            
            cmd = self._build_command(record_dir)
            if not cmd:
                return False, "No topics to record"

            self.logger.info(f"Starting recording: {' '.join(cmd)}")
            
            try:
                self.recording_process = self._spawn_process(cmd)
                self.is_recording = True
                return True, f"Recording started: {record_dir}"
            except Exception as e:
                self.logger.error(f"Failed to start process: {e}")
                return False, str(e)

    def stop(self) -> Tuple[bool, str]:
        """Stops the recording process.

        This method sends a SIGINT to the recording subprocess and waits for
        it to terminate. It is thread-safe.

        Returns:
            Tuple[bool, str]: A tuple containing a success flag (True/False)
            and a status message.
        """
        with self.lock:
            if not self.is_recording:
                self.logger.warning("Attempted to stop, but not recording.")
                return False, "Not recording"

            self.logger.info("Stopping recording...")
            self._stop_process_robustly()
            return True, "Recording stopped"

    def _build_command(self, record_dir: str) -> Optional[List[str]]:
        """Constructs the command line arguments for ros2 bag record.

        Args:
            record_dir (str): The specific directory for the current bag segment.

        Returns:
            Optional[List[str]]: The list of command arguments, or None if
            configuration is invalid (e.g., no topics specified).
        """
        cmd = ['ros2', 'bag', 'record']
        if self.all_topics:
            cmd.append('-a')
        elif self.topics:
            cmd.extend(self.topics)
        else:
            return None
        cmd.extend(['-o', record_dir, '-s', self.storage_id])
        return cmd

    def _spawn_process(self, cmd: List[str]) -> subprocess.Popen:
        """Spawns the subprocess.

        This method is separated to allow mocking during unit tests.

        Args:
            cmd (List[str]): The command to execute.

        Returns:
            subprocess.Popen: The handle to the running process.
        """
        return subprocess.Popen(cmd, preexec_fn=os.setsid)

    def _stop_process_robustly(self):
        """Terminates the recording process robustly.

        Sends SIGINT to the process group to ensure clean termination of
        rosbag writers. Waits up to 10 seconds before giving up (or handling
        force kill logic if extended).
        """
        if self.recording_process and self.recording_process.poll() is None:
            try:
                pgid = os.getpgid(self.recording_process.pid)
                os.killpg(pgid, signal.SIGINT)
                self.recording_process.wait(timeout=10.0)
            except Exception as e:
                self.logger.error(f"Error during stop: {e}")
            finally:
                self.recording_process = None
                self.is_recording = False
        else:
            self.recording_process = None
            self.is_recording = False
