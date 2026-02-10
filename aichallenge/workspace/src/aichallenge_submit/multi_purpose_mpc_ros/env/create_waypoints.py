import sys
from PIL import Image
from os import path
import matplotlib.pyplot as plt
import matplotlib.image as mpimg
import numpy as np
import yaml
import argparse

# Convert map coordinates to world coordinates
def map_to_world(mx, my, origin, size, resolution):
    pgm_mx = int(mx + 0.5)
    pgm_my = size[1] - 1 - int(my + 0.5)

    wx = pgm_mx * resolution + origin[0]
    wy = pgm_my * resolution + origin[1]

    # nmy = int((size[1] - 1) - (wy - origin[1]) / resolution + 0.5)
    # print(f"my = {my}, pgm_my = {pgm_my}, wy = {wy}, nmy = {nmy}")
    return wx, wy

def world_to_map(wx, wy, origin, size, resolution):
    mx = int((wx - origin[0]) / resolution + 0.5)
    my = int((size[1] - 1) - (wy - origin[1]) / resolution + 0.5)
    return mx, my

# handle input args
parser = argparse.ArgumentParser()
parser.add_argument('--obs', action='store_true')
args = parser.parse_args()

# Load the occupancy grid map
map_yaml_path = f'./occupancy_grid_map.yaml'
base_path = path.dirname(map_yaml_path)
base_name = path.splitext(path.basename(map_yaml_path))[0]

with open(map_yaml_path, 'r') as f:
    map_data = yaml.safe_load(f)

pgm_file_path = path.join(base_path, map_data['image'])
image = mpimg.imread(pgm_file_path)
image_array = np.array(image)
map_data['size'] = [image_array.shape[1], image_array.shape[0]]

# Allow user to select waypoints on the map
print("Please click to select waypoints on the map, and press Enter when done.")

# Function to capture waypoints
wp_x = []
wp_y = []

def onclick(event):
    if event.xdata is not None and event.ydata is not None:
        world_coords = map_to_world(event.xdata, event.ydata, map_data['origin'], map_data['size'], map_data['resolution'])
        map_coords = world_to_map(world_coords[0], world_coords[1], map_data['origin'], map_data['size'], map_data['resolution'])
        print(f"Clicked at x = {event.xdata}, y = {event.ydata}")
        print(f"World coordinates: x = {world_coords[0]}, y = {world_coords[1]}")
        print(f"Map coorinates: x = {map_coords[0]}, y = {map_coords[1]}")

        wp_x.append(world_coords[0])
        wp_y.append(world_coords[1])
        # print(f"wp_x = {wp_x[-1]}, wp_y = {wp_y[-1]}")
        plt.scatter(event.xdata, event.ydata, color='red')
        plt.draw()

fig, ax = plt.subplots()
# ax.imshow(np.flipud(image_array), cmap='gray')
ax.imshow(image_array, cmap='gray')
cid = fig.canvas.mpl_connect('button_press_event', onclick)

# Show the plot to the user for waypoint selection
plt.show()

# ウェイポイントが選択されたか確認
if args.obs:
    obstacles_file_path = path.join(base_path, base_name + '_obstacles.csv')
    with open(obstacles_file_path, 'w') as f:
        f.write("wp_x,wp_y\n")
        for x, y in zip(wp_x, wp_y):
            f.write(f"{x},{y}\n")
else:
    if len(wp_x) < 2:
        print("少なくとも2つのポイントを選択してください。")
    else:
        waypoint_file_path = path.join(base_path, base_name + '_waypoints.csv')
        with open(waypoint_file_path, 'w') as f:
            f.write("wp_x,wp_y\n")
            for x, y in zip(wp_x, wp_y):
                f.write(f"{x},{y}\n")