from typing import List
import random

# Multi_Purpose_MPC
from multi_purpose_mpc_ros.core.map import Map, Obstacle

class ObstacleManager:
    def __init__(self, map: Map, obstacles: List[Obstacle]):
        self.map = map
        self.obstacles = obstacles

        self.current_obstacles = list()
        self.obs_idx = 0
        self.queue_size = 10

    def push_all_obstacles(self):
        self.current_obstacles = self.obstacles

    def push_next_obstacle(self):
        # Remove the oldest obstacle if the queue is full
        if len(self.current_obstacles) > self.queue_size:
          del self.current_obstacles[0]

        # Add the next obstacle to the queue
        self.current_obstacles.append(self.obstacles[self.obs_idx])
        self.obs_idx += 1
        if self.obs_idx >= len(self.obstacles):
          self.obs_idx = 0

    def push_next_obstacle_random(self):
        # Remove the oldest obstacle if the queue is full
        if len(self.current_obstacles) >= self.queue_size:
            del self.current_obstacles[0]

        # Choose a random obstacle from obstacles that is not already in current_obstacles
        available_obstacles = [obs for obs in self.obstacles if obs not in self.current_obstacles]
        if available_obstacles:
            next_obstacle = random.choice(available_obstacles)
            self.current_obstacles.append(next_obstacle)

    def update_map(self):
        # Reset the map and add current obstacles
        self.map.reset_map()
        self.map.add_obstacles(self.current_obstacles)
