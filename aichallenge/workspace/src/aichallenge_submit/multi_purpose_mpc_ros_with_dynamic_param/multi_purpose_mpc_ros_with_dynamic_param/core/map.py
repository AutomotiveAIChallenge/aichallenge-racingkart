import numpy as np
from os import path
import yaml
import matplotlib.pyplot as plt
import matplotlib.image as mpimg
from PIL import Image
from skimage.morphology import remove_small_holes
from skimage.draw import line_aa
import matplotlib.patches as plt_patches

# Colors
OBSTACLE = '#2E4053'


############
# Obstacle #
############

class Obstacle:
    def __init__(self, cx, cy, radius):
        """
        Constructor for a circular obstacle to be placed on a map.
        :param cx: x coordinate of center of obstacle in world coordinates
        :param cy: y coordinate of center of obstacle in world coordinates
        :param radius: radius of circular obstacle in m
        """
        self.cx = cx
        self.cy = cy
        self.radius = radius

    def show(self, ax):
        """
        Display obstacle on the provided axis.
        :param ax: Matplotlib axis object to plot on
        """
        # Draw circle
        circle = plt_patches.Circle(xy=(self.cx, self.cy), radius=self.radius,
                                    color=OBSTACLE, zorder=20)
        ax.add_patch(circle)


#######
# Map #
#######

class Map:
    def __init__(self, map_yaml_path):
        """
        Constructor for map object. Map contains occupancy grid map data of
        environment as well as meta information.
        :param map_yaml_path: path to map yaml
        """

        base_path = path.dirname(map_yaml_path)
        base_name = path.splitext(path.basename(map_yaml_path))[0]
        with open(map_yaml_path, 'r') as f:
            map_data = yaml.safe_load(f)

        # Set binarization threshold
        self.threshold_occupied = map_data['occupied_thresh']

        pgm_file_path = path.join(base_path, map_data['image'])
        image = mpimg.imread(pgm_file_path)
        image_array = np.array(image)

        # file_path = path.join(base_path, map_data['image']).replace('pgm', 'png')
        # image = Image.open(file_path)
        # image_array = np.array(image)

        # Numpy array containing map data
        if image_array.ndim == 3:
            self.data = image_array[:, :, 0]
        elif image_array.ndim == 2:
            self.data = image_array
        else:
            raise ValueError("Unexpected image dimensions")

        # Process raw map image
        self.process_map()

        # Store meta information
        self.height = self.data.shape[0]  # height of the map in px
        self.width = self.data.shape[1]  # width of the map in px
        self.resolution = map_data['resolution']  # resolution of the map in m/px
        self.origin = map_data['origin']  # x and y coordinates of map origin
        # (bottom-left corner) in m

        # Containers for user-specified additional obstacles and boundaries
        self.obstacles = list()
        self.boundaries = list()

        self.data_backup = self.data.copy()

    def w2m(self, x, y):
        """
        World2Map. Transform coordinates from global coordinate system to
        map coordinates.
        :param x: x coordinate in global coordinate system
        :param y: y coordinate in global coordinate system
        :return: discrete x and y coordinates in px
        """
        # dx = int(np.floor((x - self.origin[0]) / self.resolution))
        # dy = int(np.floor((y - self.origin[1]) / self.resolution))

        dx = int((x - self.origin[0]) / self.resolution + 0.5)
        dy = int((self.height - 1) - (y - self.origin[1]) / self.resolution + 0.5)
        dx = np.clip(dx, 0, self.width - 1)
        dy = np.clip(dy, 0, self.height - 1)

        return dx, dy

    def m2w(self, dx, dy):
        """
        Map2World. Transform coordinates from map coordinate system to
        global coordinates.
        :param dx: x coordinate in map coordinate system
        :param dy: y coordinate in map coordinate system
        :return: x and y coordinates of cell center in global coordinate system
        """
        x = int(dx + 0.5) * self.resolution + self.origin[0]
        y = (self.height - 1 - int(dy + 0.5)) * self.resolution + self.origin[1]

        return x, y

    def process_map(self):
        """
        Process raw map image. Binarization and removal of small holes in map.
        """

        # Binarization using specified threshold
        # 1 corresponds to free, 0 to occupied
        self.data = np.where(self.data >= self.threshold_occupied, 1, 0)

        # Remove small holes in map corresponding to spurious measurements
        self.data = remove_small_holes(self.data, area_threshold=5,
                                       connectivity=8).astype(np.int8)

    def reset_map(self):
        self.data = self.data_backup.copy()
        self.obstacles = list()

    def add_obstacles(self, obstacles):
        """
        Add obstacles to the map.
        :param obstacles: list of obstacle objects
        """

        # Extend list of obstacles
        self.obstacles.extend(obstacles)

        # Iterate over list of new obstacles
        for obstacle in obstacles:

            # Compute radius of circular object in pixels
            radius_px = int(np.ceil(obstacle.radius / self.resolution))
            # Get center coordinates of obstacle in map coordinates
            cx_px, cy_px = self.w2m(obstacle.cx, obstacle.cy)

            # Add circular object to map
            y, x = np.ogrid[-radius_px: radius_px, -radius_px: radius_px]
            index = x ** 2 + y ** 2 <= radius_px ** 2
            self.data[cy_px-radius_px:cy_px+radius_px, cx_px-radius_px:
                                                cx_px+radius_px][index] = 0

    def add_boundary(self, boundaries):
        """
        Add boundaries to the map.
        :param boundaries: list of tuples containing coordinates of boundaries'
        start and end points
        """

        # Extend list of boundaries
        self.boundaries.extend(boundaries)

        # Iterate over list of boundaries
        for boundary in boundaries:
            sx = self.w2m(boundary[0][0], boundary[0][1])
            gx = self.w2m(boundary[1][0], boundary[1][1])
            path_x, path_y, _ = line_aa(sx[0], sx[1], gx[0], gx[1])
            for x, y in zip(path_x, path_y):
                self.data[y, x] = 0


if __name__ == '__main__':
    map = Map('maps/real_map.png')
    # map = Map('maps/sim_map.png')
    plt.imshow(np.flipud(map.data), cmap='gray')
    plt.show()
