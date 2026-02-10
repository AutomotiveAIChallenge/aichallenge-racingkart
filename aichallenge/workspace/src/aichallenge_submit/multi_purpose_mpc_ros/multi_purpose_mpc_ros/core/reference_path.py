from typing import List, Optional
import numpy as np
import numpy.ma as ma
import math
import copy
from multi_purpose_mpc_ros.core.map import Map, Obstacle
from skimage.draw import line_aa
import matplotlib.pyplot as plt
from scipy import sparse
import osqp
import itertools

# Colors
DRIVABLE_AREA = '#BDC3C7'
WAYPOINTS = '#D0D3D4'
PATH_CONSTRAINTS = '#F5B041'
OBSTACLE = '#2E4053'


def dist(x1, y1, x2, y2):
    return np.sqrt((x1 - x2)**2 + (y1 - y2)**2)


def calculate_area(vertices):
    """
    Calculate the area of a quadrilateral given its four vertices using the Shoelace formula.
    The vertices should be a list of tuples [(x1, y1), (x2, y2), (x3, y3), (x4, y4)] representing
    the coordinates of the quadrilateral's four vertices in order.

    Args:
        vertices (list): List of tuples with the coordinates of the quadrilateral's vertices.

    Returns:
        float: Area of the quadrilateral.
    """
    if len(vertices) != 4:
        raise ValueError("There must be exactly 4 vertices for a quadrilateral.")

    # Unpack the vertices into x and y coordinates
    x1, y1 = vertices[0]
    x2, y2 = vertices[1]
    x3, y3 = vertices[2]
    x4, y4 = vertices[3]

    # Apply the Shoelace formula
    area = 0.5 * abs(
        x1*y2 + x2*y3 + x3*y4 + x4*y1 -
        (y1*x2 + y2*x3 + y3*x4 + y4*x1)
    )

    return area

def calculate_angle(p1, p2, p3):
    # 傾きの計算
    m1 = (p2[1] - p1[1]) / (p2[0] - p1[0])
    m2 = (p3[1] - p2[1]) / (p3[0] - p2[0])

    # なす角の計算
    tan_theta = (m1 - m2) / (1 + m1 * m2)
    theta = math.atan(tan_theta)
    return theta

def calculate_intersection(p1, p2, p3, p4):
    # 直線1の傾きと切片
    m1 = (p2[1] - p1[1]) / (p2[0] - p1[0])
    b1 = p1[1] - m1 * p1[0]

    # 直線2の傾きと切片
    m2 = (p4[1] - p3[1]) / (p4[0] - p3[0])
    b2 = p3[1] - m2 * p3[0]

    # 傾きが同じ場合、平行なため交点なし
    if m1 == m2:
        return None

    # x座標の計算
    x_intersection = (b2 - b1) / (m1 - m2)

    # y座標の計算
    y_intersection = m1 * x_intersection + b1

    return (x_intersection, y_intersection)

def has_collision_in_line(map, p0, p1):
    p0m = map.w2m(p0[0], p0[1])
    p1m = map.w2m(p1[0], p1[1])
    x_list, y_list, _ = line_aa(p0m[0], p0m[1], p1m[0], p1m[1])

    occupied_indices = map.data[y_list, x_list] == 0
    if np.any(occupied_indices):
        return True
    else:
        return False

############
# Waypoint #
############

class Waypoint:
    def __init__(self, x, y, psi, kappa):
        """
        Waypoint object containing x, y location in global coordinate system,
        orientation of waypoint psi and local curvature kappa. Waypoint further
        contains an associated reference velocity computed by the speed profile
        and a path width specified by upper and lower bounds.
        :param x: x position in global coordinate system | [m]
        :param y: y position in global coordinate system | [m]
        :param psi: orientation of waypoint | [rad]
        :param kappa: local curvature | [1 / m]
        """
        self.x = x
        self.y = y
        self.psi = psi
        self.kappa = kappa

        # Reference velocity at this waypoint according to speed profile
        self.v_ref = None

        # Information about drivable area at waypoint
        # upper and lower bound of drivable area orthogonal to
        # waypoint orientation.
        # Upper bound: free drivable area to the left of center-line in m
        # Lower bound: free drivable area to the right of center-line in m
        self.lb = None
        self.ub = None
        self.lb_sm = None
        self.ub_sm = None
        self.static_border_cells = None
        self.dynamic_border_cells = None

    def __sub__(self, other):
        """
        Overload subtract operator. Difference of two waypoints is equal to
        their euclidean distance.
        :param other: subtrahend
        :return: euclidean distance between two waypoints
        """
        return ((self.x - other.x)**2 + (self.y - other.y)**2)**0.5


##################
# Reference Path #
##################


class BorderCells:
    def __init__(self):
        self.current_wp_id = None
        self.static_upper_bounds = []
        self.static_lower_bounds = []
        self.dynamic_upper_bounds = []
        self.dynamic_lower_bounds = []

class ReferencePath:
    def __init__(self, map, wp_x, wp_y, resolution, smoothing_distance,
                 max_width, circular):
        """
        Reference Path object. Create a reference trajectory from specified
        corner points with given resolution. Smoothing around corners can be
        applied. Waypoints represent center-line of the path with specified
        maximum width to both sides.
        :param map: map object on which path will be placed
        :param wp_x: x coordinates of corner points in global coordinates
        :param wp_y: y coordinates of corner points in global coordinates
        :param resolution: resolution of the path in m/wp
        :param smoothing_distance: number of waypoints used for smoothing the
        path by averaging neighborhood of waypoints
        :param max_width: maximum width of path to both sides in m
        :param circular: True if path circular
        """

        self.org_wp_x = wp_x
        self.org_wp_y = wp_y

        # Precision
        self.eps = 1e-12

        # Map
        self.map = map

        # Resolution of the path
        self.resolution = resolution

        # Look ahead distance for path averaging
        self.smoothing_distance = smoothing_distance

        # Circular flag
        self.circular = circular

        # List of waypoint objects
        self.waypoints = self._construct_path(wp_x, wp_y)

        # Number of waypoints
        self.n_waypoints = len(self.waypoints)
        # print(f"input waypoint: {len(wp_x)}, n_waypoints: {self.n_waypoints}")

        # Length of path
        self.length, self.segment_lengths = self._compute_length()

        # Compute path width (attribute of each waypoint)
        self._compute_width(max_width=max_width)

        self.path_constraints: Optional[List[np.ndarray]] = None
        self.border_cells = BorderCells()

        self.COUNT = 0

    def set_path_constraints(self, upper_bounds: List[float], lower_bounds: List[float], n_rows, n_cols) -> None:
        self.path_constraints = [
            np.array(upper_bounds).reshape(n_rows, n_cols),
            np.array(lower_bounds).reshape(n_rows, n_cols)
        ]

    def set_border_cells(self, dynamic_upper_bounds: List[float], dynamic_lower_bounds: List[float], n_rows, n_cols) -> None:
        self.border_cells.dynamic_upper_bounds = np.array(dynamic_upper_bounds).reshape(n_rows, n_cols, 2)
        self.border_cells.dynamic_lower_bounds = np.array(dynamic_lower_bounds).reshape(n_rows, n_cols, 2)

    def reset_dynamic_constraints(self):
        for wp in self.waypoints:
            wp.dynamic_border_cells = copy.deepcopy(wp.static_border_cells)
            wp.ub_sm = copy.deepcopy(wp.ub)
            wp.lb_sm = copy.deepcopy(wp.lb)

    def set_v_ref(self, v_ref: List[float]) -> None:
        for wp, v in zip(self.waypoints, v_ref):
            wp.v_ref = v

    def _construct_path(self, wp_x, wp_y):
        """
        Construct path from given waypoints.
        :param wp_x: x coordinates of waypoints in global coordinates
        :param wp_y: y coordinates of waypoints in global coordinates
        :return: list of waypoint objects
        """

        if self.circular:
            # insert the first smoothing_distance points to the end of the list
            # FIXME: コースを循環させるときに始点と終点にギャップができないように要素を追加している。しかし、 smoothing_distance に応じて追加要素数を調整する必要があり、マジックナンバーが存在している
            wp_x = wp_x + wp_x[:self.smoothing_distance * 3]
            wp_y = wp_y + wp_y[:self.smoothing_distance * 3]

        # Number of waypoints
        n_wp = [max(1, int(np.sqrt((wp_x[i + 1] - wp_x[i]) ** 2 +
                            (wp_y[i + 1] - wp_y[i]) ** 2) /
                self.resolution)) for i in range(len(wp_x) - 1)]

        # Construct waypoints with specified resolution
        gp_x, gp_y = wp_x[-1], wp_y[-1]
        wp_x = [np.linspace(wp_x[i], wp_x[i+1], n_wp[i], endpoint=False).
                    tolist() for i in range(len(wp_x)-1)]
        wp_x = [wp for segment in wp_x for wp in segment] + [gp_x]
        wp_y = [np.linspace(wp_y[i], wp_y[i + 1], n_wp[i], endpoint=False).
                    tolist() for i in range(len(wp_y) - 1)]
        wp_y = [wp for segment in wp_y for wp in segment] + [gp_y]

        # Smooth path
        wp_xs = []
        wp_ys = []
        for wp_id in range(self.smoothing_distance, len(wp_x) -
                                                    self.smoothing_distance):
            wp_xs.append(np.mean(wp_x[wp_id - self.smoothing_distance:wp_id
                                            + self.smoothing_distance + 1]))
            wp_ys.append(np.mean(wp_y[wp_id - self.smoothing_distance:wp_id
                                            + self.smoothing_distance + 1]))

        # Construct list of waypoint objects
        waypoints = list(zip(wp_xs, wp_ys))
        # print(f"n_wp: {n_wp}, smooth_dist: {self.smoothing_distance}, len(wp_x): {len(wp_x)}, len way: {len(waypoints)}")
        waypoints = self._construct_waypoints(waypoints)

        return waypoints

    def _construct_waypoints(self, waypoint_coordinates):
        """
        Reformulate conventional waypoints (x, y) coordinates into waypoint
        objects containing (x, y, psi, kappa, ub, lb)
        :param waypoint_coordinates: list of (x, y) coordinates of waypoints in
        global coordinates
        :return: list of waypoint objects for entire reference path
        """

        # List containing waypoint objects
        waypoints = []

        # Iterate over all waypoints
        for wp_id in range(len(waypoint_coordinates) - 1):

            # Get start and goal waypoints
            current_wp = np.array(waypoint_coordinates[wp_id])
            next_wp = np.array(waypoint_coordinates[wp_id + 1])

            # Difference vector
            dif_ahead = next_wp - current_wp

            # Angle ahead
            psi = np.arctan2(dif_ahead[1], dif_ahead[0])

            # Distance to next waypoint
            dist_ahead = np.linalg.norm(dif_ahead, 2)

            # Get x and y coordinates of current waypoint
            x, y = current_wp[0], current_wp[1]

            # Compute local curvature at waypoint
            # first waypoint
            if wp_id == 0:
                kappa = 0
            else:
                prev_wp = np.array(waypoint_coordinates[wp_id - 1])
                dif_behind = current_wp - prev_wp
                angle_behind = np.arctan2(dif_behind[1], dif_behind[0])
                angle_dif = np.mod(psi - angle_behind + math.pi, 2 * math.pi) \
                            - math.pi
                kappa = angle_dif / (dist_ahead + self.eps)

            waypoints.append(Waypoint(x, y, psi, kappa))

        return waypoints

    def _compute_length(self):
        """
        Compute length of center-line path as sum of euclidean distance between
        waypoints.
        :return: length of center-line path in m
        """
        segment_lengths = [0.0] + [self.waypoints[wp_id+1] - self.waypoints
                    [wp_id] for wp_id in range(len(self.waypoints)-1)]
        s = sum(segment_lengths)
        return s, segment_lengths

    def _is_obstacle_occupied(self, t_x, t_y):
        for i in range(-1, 2):
            for j in range(-1, 2):
                # Clip the coordinates to stay within map boundaries
                t_xi = np.clip(t_x + i, 0, self.map.width - 1)
                t_yj = np.clip(t_y + j, 0, self.map.height - 1)

                # Check if the cell is occupied
                if self.map.data[t_yj, t_xi] == 0:
                    return True
        # No obstacles detected
        return False

    def _compute_width(self, max_width):
        """
        Compute the width of the path by checking the maximum free space to
        the left and right of the center-line.
        :param max_width: maximum width of the path.
        """

        # Iterate over all waypoints
        for wp_id, wp in enumerate(self.waypoints):
            left_angle = np.mod(wp.psi + math.pi / 2 + math.pi,
                             2 * math.pi) - math.pi
            right_angle = np.mod(wp.psi - math.pi / 2 + math.pi,
                               2 * math.pi) - math.pi

            # Get pixel coordinates of waypoint
            wp_x, wp_y = self.map.w2m(wp.x, wp.y)

            # List containing information for current waypoint
            width_info = [] # [0]: left min_width, [1]: left border_cell, [2]: right_width, [3]: right border_cell
            wp_x_w, wp_y_w = self.map.m2w(wp_x, wp_y)
            # Check width left and right of the center-line
            for i, dir in enumerate(['left', 'right']):
                # Get angle orthogonal to path in current direction
                angle = left_angle if dir == 'left' else right_angle

                # Get border cell to orthogonal vector in map coordinates
                t_x, t_y = self.map.w2m(wp_x_w + max_width * np.cos(angle), wp_y_w
                                        + max_width * np.sin(angle))
                # Compute distance to orthogonal cell on path border
                b_value, b_cell = self._get_min_width(wp_x_w, wp_y_w, wp_x, wp_y, t_x, t_y, max_width)

                # Add information to list for current waypoint
                width_info.append(b_value)
                width_info.append(b_cell)

            # Set waypoint attributes with width to the left and right
            wp.ub = width_info[0]
            wp.lb = -1 * width_info[2]  # minus can be assumed as waypoints
            # represent center-line of the path
            # Set border cells of waypoint
            wp.static_border_cells = (width_info[1], width_info[3])   # (left_border_cell(x,y), right_border_cell(x,y))

        self.reset_dynamic_constraints()

    def _get_min_width(self, wp_x_w, wp_y_w, wp_x, wp_y, t_x, t_y, max_width):
        """
        Compute the minimum distance between the current waypoint and the
        orthogonal cell on the border of the path
        :param wp_x_w: x coordinate of the reference cell in world coordinates
        :param wp_y_w: y coordinate of the reference cell in world coordinates
        :param wp_x: x coordinate of the reference cell in map coordinates
        :param wp_y: y coordinate of the reference cell in map coordinates
        :param t_x: x coordinate of border cell in map coordinates
        :param t_y: y coordinate of border cell in map coordinates
        :param max_width: maximum path width in m
        :return: min_width to border and corresponding cell
        """

        min_width = max_width
        path_x = np.array([])
        path_y = np.array([])

        # Search around the target cell for obstacles
        for i in range(-1, 2):
            for j in range(-1, 2):
                # Clip the coordinates to stay within map boundaries
                t_xi = np.clip(t_x + i, 0, self.map.width - 1)
                t_yj = np.clip(t_y + j, 0, self.map.height - 1)

                # Get the line from the waypoint to the target cell
                x_list, y_list, _ = line_aa(wp_x, wp_y, t_xi, t_yj)
                # Check for occupied cells (obstacles)
                occupied_indices = self.map.data[y_list, x_list] == 0

                if np.any(occupied_indices):
                    # If there are obstacles, find the nearest one
                    obstacle_index = np.argmax(occupied_indices)
                    obstacle_x = x_list[obstacle_index]
                    obstacle_y = y_list[obstacle_index]
                    min_cell = self.map.m2w(obstacle_x, obstacle_y)
                    min_width = np.hypot(wp_x_w - min_cell[0], wp_y_w - min_cell[1])
                    return min_width, min_cell
                else:
                    # If no obstacles are found, add free space coordinates
                    x_list = ma.masked_array(x_list, mask=occupied_indices).compressed()
                    y_list = ma.masked_array(y_list, mask=occupied_indices).compressed()
                    path_x = np.append(path_x, x_list)
                    path_y = np.append(path_y, y_list)

        # If no obstacles are detected, calculate the distance to the farthest free cell
        if path_x.size > 0 and path_y.size > 0:
            min_index = np.argmin(np.hypot(path_x - t_x, path_y - t_y))
            min_cell = self.map.m2w(path_x[min_index], path_y[min_index])
            min_width = np.hypot(wp_x_w - min_cell[0], wp_y_w - min_cell[1])

        return min_width, min_cell

    def compute_speed_profile(self, Constraints):
        """
        Compute a speed profile for the path. Assign a reference velocity
        to each waypoint based on its curvature.
        :param Constraints: constraints on acceleration and velocity
        curvature of the path
        """

        # Set optimization horizon
        N = self.n_waypoints - 1
        if N < 2:
            print("Path too short for speed profile computation!")
            return False

        # Constraints
        a_min = np.ones(N-1) * Constraints['a_min']
        a_max = np.ones(N-1) * Constraints['a_max']
        v_min = np.ones(N) * Constraints['v_min']
        v_max = np.ones(N) * Constraints['v_max']

        # Maximum lateral acceleration
        ay_max = Constraints['ay_max']

        # Inequality Matrix
        D1 = np.zeros((N-1, N))

        # Iterate over horizon
        for i in range(N):

            # Get information about current waypoint
            current_waypoint = self.get_waypoint(i)
            next_waypoint = self.get_waypoint(i+1)
            # distance between waypoints
            li = next_waypoint - current_waypoint
            # curvature of waypoint
            ki = current_waypoint.kappa

            # Fill operator matrix
            # dynamics of acceleration
            if i < N-1:
                D1[i, i:i+2] = np.array([-1/(2*li), 1/(2*li)])

            # Compute dynamic constraint on velocity
            v_max_dyn = np.sqrt(ay_max / (np.abs(ki) + self.eps))
            if v_max_dyn < v_max[i]:
                v_max[i] = v_max_dyn

        # Construct inequality matrix
        D1 = sparse.csc_matrix(D1)
        D2 = sparse.eye(N)
        D = sparse.vstack([D1, D2], format='csc')

        # Get upper and lower bound vectors for inequality constraints
        l = np.hstack([a_min, v_min])
        u = np.hstack([a_max, v_max])

        # Set cost matrices
        P = sparse.eye(N, format='csc')
        q = -1 * v_max

        # Solve optimization problem
        problem = osqp.OSQP()
        problem.setup(P=P, q=q, A=D, l=l, u=u, verbose=False)
        speed_profile = problem.solve().x

        # Assign reference velocity to every waypoint
        for i, wp in enumerate(self.waypoints[:-1]):
            wp.v_ref = speed_profile[i]

        if self.circular:
            self.waypoints[-1].v_ref = self.waypoints[-2].v_ref
        else:
            self.waypoints[-1].v_ref = 0.0

        return True

    def get_waypoint(self, wp_id):
        """
        Get waypoint corresponding to wp_id. Circular indexing supported.
        :param wp_id: unique waypoint ID
        :return: waypoint object
        """

        # Allow circular indexing if circular path
        if wp_id >= self.n_waypoints and self.circular:
            wp_id = np.mod(wp_id, self.n_waypoints)
        # Terminate execution if end of path reached
        elif wp_id >= self.n_waypoints and not self.circular:
            # print('Reached end of path!')
            wp_id = self.n_waypoints - 1
            # exit(1)

        return self.waypoints[wp_id]

    def show(self, ax, display_drivable_area=True):
        """
        Display path object on provided axis.
        :param ax: Matplotlib axis object to plot on
        :param display_drivable_area: If True, display arrows indicating width of drivable area
        """

        # Clear the axis
        ax.cla()

        # Disable ticks
        ax.set_xticks([])
        ax.set_yticks([])

        # Plot map in gray-scale and set extent to match world coordinates
        # canvas = np.ones(self.map.data.shape)
        # canvas = np.flipud(self.map.data)
        canvas = self.map.data

        # print(self.map.origin)
        ax.imshow(canvas, cmap='gray',
                  extent=[self.map.origin[0], self.map.origin[0] +
                          self.map.width * self.map.resolution,
                          self.map.origin[1], self.map.origin[1] +
                          self.map.height * self.map.resolution], vmin=0.0,
                  vmax=1.0)

        # Get x and y coordinates for all waypoints
        wp_x = np.array([wp.x for wp in self.waypoints])
        wp_y = np.array([wp.y for wp in self.waypoints])

        # Get x and y locations of border cells for upper and lower bound
        wp_ub_x = np.array([wp.static_border_cells[0][0] for wp in self.waypoints])
        wp_ub_y = np.array([wp.static_border_cells[0][1] for wp in self.waypoints])
        wp_lb_x = np.array([wp.static_border_cells[1][0] for wp in self.waypoints])
        wp_lb_y = np.array([wp.static_border_cells[1][1] for wp in self.waypoints])

        # Plot waypoints
        colors = [wp.v_ref for wp in self.waypoints]
        ax.scatter(wp_x, wp_y, c=WAYPOINTS, s=10)

        # Plot arrows indicating drivable area
        if display_drivable_area:
            ax.quiver(wp_x, wp_y, wp_ub_x - wp_x, wp_ub_y - wp_y, scale=1,
                      units='xy', width=0.2*self.resolution, color=DRIVABLE_AREA,
                      headwidth=1, headlength=0)
            ax.quiver(wp_x, wp_y, wp_lb_x - wp_x, wp_lb_y - wp_y, scale=1,
                      units='xy', width=0.2*self.resolution, color=DRIVABLE_AREA,
                      headwidth=1, headlength=0)

        # Plot border of path
        bl_x = np.array([wp.static_border_cells[0][0] for wp in self.waypoints] +
                        [self.waypoints[0].static_border_cells[0][0]])
        bl_y = np.array([wp.static_border_cells[0][1] for wp in self.waypoints] +
                        [self.waypoints[0].static_border_cells[0][1]])
        br_x = np.array([wp.static_border_cells[1][0] for wp in self.waypoints] +
                        [self.waypoints[0].static_border_cells[1][0]])
        br_y = np.array([wp.static_border_cells[1][1] for wp in self.waypoints] +
                        [self.waypoints[0].static_border_cells[1][1]])

        # If circular path, connect start and end point
        if self.circular:
            ax.plot(bl_x, bl_y, color='#5E5E5E')
            ax.plot(br_x, br_y, color='#5E5E5E')
        # If not circular, close path at start and end
        else:
            ax.plot(bl_x[:-1], bl_y[:-1], color=OBSTACLE)
            ax.plot(br_x[:-1], br_y[:-1], color=OBSTACLE)
            ax.plot((bl_x[-2], br_x[-2]), (bl_y[-2], br_y[-2]), color=OBSTACLE)
            ax.plot((bl_x[0], br_x[0]), (bl_y[0], br_y[0]), color=OBSTACLE)

        # Plot dynamic path constraints
        # Get x and y locations of border cells for upper and lower bound
        if (self.border_cells.current_wp_id is not None) and \
           (self.border_cells.current_wp_id < len(self.border_cells.dynamic_upper_bounds)):
            dynamic_upper_bounds = self.border_cells.dynamic_upper_bounds[self.border_cells.current_wp_id]
            dynamic_lower_bounds = self.border_cells.dynamic_lower_bounds[self.border_cells.current_wp_id]
            wp_ub_x = dynamic_upper_bounds[:,0]
            wp_ub_y = dynamic_upper_bounds[:,1]
            wp_lb_x = dynamic_lower_bounds[:,0]
            wp_lb_y = dynamic_lower_bounds[:,1]
        else:
            wp_ub_x = np.array([wp.dynamic_border_cells[0][0] for wp in self.waypoints] +
                               [self.waypoints[0].static_border_cells[0][0]])
            wp_ub_y = np.array([wp.dynamic_border_cells[0][1] for wp in self.waypoints] +
                               [self.waypoints[0].static_border_cells[0][1]])
            wp_lb_x = np.array([wp.dynamic_border_cells[1][0] for wp in self.waypoints] +
                               [self.waypoints[0].static_border_cells[1][0]])
            wp_lb_y = np.array([wp.dynamic_border_cells[1][1] for wp in self.waypoints] +
                               [self.waypoints[0].static_border_cells[1][1]])
        ax.plot(wp_ub_x[0:-1], wp_ub_y[0:-1], c=PATH_CONSTRAINTS)
        ax.plot(wp_lb_x[0:-1], wp_lb_y[0:-1], c=PATH_CONSTRAINTS)

        # for seg in self.free_segs:
        #     ax.plot([seg[0][0], seg[1][0]], [seg[0][1], seg[1][1]], "o-", c='blue', markersize=2, linewidth=2)

        # for seg in self.select_free_segs:
        #     ax.plot([seg[0][0], seg[1][0]], [seg[0][1], seg[1][1]], "o-", c='red', markersize=2, linewidth=1)

        # for ub, prev_ub in self.modified_ub:
        #     ax.plot([prev_ub[0], ub[0]], [prev_ub[1], ub[1]], "o-", c='red', markersize=2, linewidth=1)
        # for lb, prev_lb in self.modified_lb:
        #     ax.plot([prev_lb[0], lb[0]], [prev_lb[1], lb[1]], "o-", c='red', markersize=2, linewidth=1)

        # self.cols = np.array(self.cols)
        # print(len(self.cols))
        # for i, cols in enumerate(self.upper_cols):
        #     COLOR = ['red', 'blue', 'green', 'yellow']
        #     ax.plot(cols[0], cols[1], "o-", color=COLOR[i%4], markersize=2, linewidth=1)
        # for i, cols in enumerate(self.lower_cols):
        #     COLOR = ['red', 'blue', 'green', 'yellow']
        #     ax.plot(cols[0], cols[1], "*--", color=COLOR[i%4], markersize=2, linewidth=2)

        # Plot obstacles
        # for obstacle in self.map.obstacles:
        #     obstacle.show(ax=ax)


    def _compute_free_segments(self, wp, min_width):
        """
        Compute free path segments.
        :param wp: waypoint object
        :param min_width: minimum width of valid segment
        :return: segment candidates as list of tuples (ub_cell, lb_cell)
        """

        # Candidate segments
        free_segments = []

        # Get waypoint's border cells in map coordinates
        ub_p = self.map.w2m(wp.static_border_cells[0][0],
                            wp.static_border_cells[0][1])
        lb_p = self.map.w2m(wp.static_border_cells[1][0],
                            wp.static_border_cells[1][1])

        # Compute path from left border cell to right border cell
        x_list, y_list, _ = line_aa(ub_p[0], ub_p[1], lb_p[0], lb_p[1])

        # Initialize upper and lower bound of drivable area to
        # upper bound of path
        ub_o, lb_o = ub_p, ub_p

        # Assume occupied path
        free_cells = False

        # cache to avoid multiple access to self.map.data
        map_data = self.map.data

        # Iterate over path from left border to right border
        for x, y in zip(x_list[1:], y_list[1:]):
            cell_value = map_data[y, x]
            # If cell is free, update lower bound
            if cell_value == 1:
                # Free cell detected
                free_cells = True
                lb_o = (x, y)
            # If cell is occupied or end of path, end segment. Add segment
            # to list of candidates. Then, reset upper and lower bound to
            # current cell.
            if (cell_value == 0 or (x, y) == lb_p) and free_cells:
                # Set lower bound to border cell of segment
                lb_o = (x, y)
                # Transform upper and lower bound cells to world coordinates
                ub_o = self.map.m2w(ub_o[0], ub_o[1])
                lb_o = self.map.m2w(lb_o[0], lb_o[1])
                # If segment larger than threshold, add to candidates
                if ((ub_o[0]-lb_o[0])**2 + (ub_o[1]-lb_o[1])**2) > min_width**2:
                    free_segments.append((ub_o, lb_o))
                # Start new segment
                ub_o = (x, y)
                free_cells = False
            elif cell_value == 0 and not free_cells:
                ub_o = (x, y)
                lb_o = (x, y)

        return free_segments

    def update_simple_path_constraints(self, N, safety_margin):
        upper_bounds = []
        lower_bounds = []
        dynamic_upper_bounds = []
        dynamic_lower_bounds = []

        for wp_id in range(self.n_waypoints-1):
            for n in range(N):
                wp = self.get_waypoint(wp_id+n)

                # Subtract safety margin
                ub_sm = wp.ub - safety_margin
                lb_sm = wp.lb + safety_margin

                # Check feasibility of the path after subtracting safety margin
                if ub_sm < lb_sm:
                    ub_sm = 0.0
                    lb_sm = 0.0

                # wp.ub_sm = ub_sm
                # wp.lb_sm = lb_sm

                # Compute absolute angle of bound cell
                angle_ub = np.mod(math.pi / 2 + wp.psi + math.pi,
                                      2 * math.pi) - math.pi
                angle_lb = np.mod(-math.pi / 2 + wp.psi + math.pi,
                                      2 * math.pi) - math.pi
                # Compute cell on bound for computed distance ub_sm and lb_sm
                ub_sm_ls = wp.x + ub_sm * np.cos(angle_ub), wp.y + ub_sm * np.sin(
                        angle_ub)
                lb_sm_ls = wp.x - lb_sm * np.cos(angle_lb), wp.y - lb_sm * np.sin(
                        angle_lb)

                upper_bounds.append(ub_sm)
                lower_bounds.append(lb_sm)
                dynamic_upper_bounds.append(ub_sm_ls)
                dynamic_lower_bounds.append(lb_sm_ls)

        self.set_path_constraints(
            upper_bounds, lower_bounds, self.n_waypoints - 1, N)
        self.set_border_cells(
            dynamic_upper_bounds, dynamic_lower_bounds, self.n_waypoints - 1, N)

    def update_simple_path_constraints_horizon(self, wp_id, N, safety_margin):
        # container for constraints and border cells
        upper_bounds = []
        lower_bounds = []
        dynamic_upper_bounds = []
        dynamic_lower_bounds = []

        for n in range(N):
            # print(f"wp_id: {wp_id}, N: {N}, n: {n}")
            wp = self.get_waypoint(wp_id+n)

            # Subtract safety margin
            ub_sm = wp.ub - safety_margin
            lb_sm = wp.lb + safety_margin

            # Check feasibility of the path after subtracting safety margin
            if ub_sm < lb_sm:
                ub_sm = 0.0
                lb_sm = 0.0

            # Compute absolute angle of bound cell
            angle_ub = np.mod(math.pi / 2 + wp.psi + math.pi,
                                  2 * math.pi) - math.pi
            angle_lb = np.mod(-math.pi / 2 + wp.psi + math.pi,
                                  2 * math.pi) - math.pi
            # Compute cell on bound for computed distance ub_sm and lb_sm
            ub_sm_ls = wp.x + ub_sm * np.cos(angle_ub), wp.y + ub_sm * np.sin(
                    angle_ub)
            lb_sm_ls = wp.x - lb_sm * np.cos(angle_lb), wp.y - lb_sm * np.sin(
                    angle_lb)

            # Append results
            upper_bounds.append(ub_sm)
            lower_bounds.append(lb_sm)
            dynamic_upper_bounds.append(ub_sm_ls)
            dynamic_lower_bounds.append(lb_sm_ls)

        # Set dynamic bounds for show plot
        self.border_cells.dynamic_upper_bounds[wp_id] = np.array(dynamic_upper_bounds).reshape(N, 2)
        self.border_cells.dynamic_lower_bounds[wp_id] = np.array(dynamic_lower_bounds).reshape(N, 2)

        return np.array(upper_bounds), np.array(lower_bounds)

    def update_path_constraints(self, wp_id, pose, N, model_length, model_width, safety_margin):
        """
        Compute upper and lower bounds of the drivable area orthogonal to
        the given waypoint.
        """

        # min_width = model_width / np.sqrt(2)
        # min_width = model_width
        # min_width = 2.0 * safety_margin
        min_width = model_width
        # min_segment_length = model_width / 4.0
        min_segment_length = 0.1

        # container for constraints and border cells
        ub_hor = []
        lb_hor = []
        border_cells_hor = []
        border_cells_hor_sm = []

        def compute_bound(wp, ls):
            # Check sign of bound
            angle = np.mod(np.arctan2(ls[1] - wp.y, ls[0] - wp.x)
                                  - wp.psi + math.pi, 2 * math.pi) - math.pi
            sign = np.sign(angle)

            # Compute bound
            bound = sign * np.sqrt(
                    (ls[0] - wp.x) ** 2 + (ls[1] - wp.y) ** 2)

            return bound

        def add_constraint(wp, ub_ls, lb_ls):
            # Compute upper and lower bound of largest drivable area
            ub = compute_bound(wp, ub_ls)
            lb = compute_bound(wp, lb_ls)

            segment_length = ub - lb
            segment_length_sm = segment_length - 2.0 * safety_margin

            # Check feasibility of the path
            # segment_lengthから両側のsafety_marginを引いた値がmin_segment_lengthより小さい場合は、
            # border_cellsで囲まれる領域の隙間が狭すぎて障害物回避が困難なため、
            # 回避を諦めてwaypointのstaticなupper boundとlower boundを代わりに使用する
            if segment_length_sm < min_segment_length:
                # print("Infeasible path detected!")
                # print(f"Waypoint: {wp_id}, n: {n}, Upper bound: {ub}")
                # print(f"min_width: {min_width}, safety_margin: {safety_margin}, segment_length: {segment_length}, segment_length_sm: {segment_length_sm}")
                (ub, lb) = (wp.ub, wp.lb)
                # print(f"Updated Upper bound: {wp.ub}, Updated Lower bound: {wp.lb}")

            # Subtract safety margin
            ub_sm = ub - safety_margin
            lb_sm = lb + safety_margin

            if wp.ub_sm < ub_sm:
              ub_sm = wp.ub_sm
            if wp.lb_sm > lb_sm:
              lb_sm = wp.lb_sm

            # Check feasibility of the path after subtracting safety margin
            if ub_sm < lb_sm:
                # 一つ前のifの判定でboundsは正常になっているはずなので、こちらの判定に入る場合は何らかの実装上の異常がある
                print("!!!! Infeasible path detected !!!!")
                ub_sm = 0.0
                lb_sm = 0.0

            # Compute absolute angle of bound cell
            angle_ub = np.mod(math.pi / 2 + wp.psi + math.pi,
                                  2 * math.pi) - math.pi
            angle_lb = np.mod(-math.pi / 2 + wp.psi + math.pi,
                                  2 * math.pi) - math.pi
            # Compute cell on bound for computed distance ub_sm and lb_sm
            ub_sm_ls = wp.x + ub_sm * np.cos(angle_ub), wp.y + ub_sm * np.sin(
                    angle_ub)
            lb_sm_ls = wp.x - lb_sm * np.cos(angle_lb), wp.y - lb_sm * np.sin(
                    angle_lb)
            bound_cells_sm = (ub_sm_ls, lb_sm_ls)
            self.select_free_segs.append([ub_sm_ls, lb_sm_ls])

            # Compute cell on bound for computed distance ub and lb
            ub_ls = wp.x + ub * np.cos(angle_ub), wp.y + ub * np.sin(
                angle_ub)
            lb_ls = wp.x - lb * np.cos(angle_lb), wp.y - lb * np.sin(
                angle_lb)
            bound_cells = (ub_ls, lb_ls)

            # Append results
            ub_hor.append(ub_sm)
            lb_hor.append(lb_sm)
            border_cells_hor.append(list(bound_cells))
            border_cells_hor_sm.append(list(bound_cells_sm))

            # Assign dynamic border cells to waypoints
            wp.dynamic_border_cells = bound_cells_sm
            wp.ub_sm = ub_sm
            wp.lb_sm = lb_sm

        self.rect_points = []
        self.upper_cols = []
        self.lower_cols = []
        self.free_segs = []
        self.select_free_segs = []

        # self.COUNT += 1
        # show = False
        # if self.COUNT == 100:
        #     show = True
        #     self.COUNT = 0

        # compute free segments for each waypoints in horizon
        free_segments_hor = []
        for n in range(N):
            wp = self.get_waypoint(wp_id+n)
            free_segments = self._compute_free_segments(wp, min_width)
            free_segments_hor.append(free_segments)
            self.free_segs.extend(free_segments)

        # Iterate over horizon
        n = 0
        while n < N:

            # get corresponding waypoint
            wp = self.get_waypoint(wp_id+n)

            # Get list of free segments
            free_segments = free_segments_hor[n]

            # Iterate over free segments for current waypoint
            if len(free_segments) >= 2:
                free_segments_indices = [[idx for idx in range(len(free_segments))]]
                for i in range(n+1, n+5):
                    if i >= N:
                        break
                    free_segments = free_segments_hor[i]
                    if len(free_segments) == 0:
                      break
                    else:
                      free_segments_indices.append([idx for idx in range(len(free_segments))])

                free_segments_indices_combinations = itertools.product(*free_segments_indices)
                # if show:
                #     print(f"n :{n}, free_segments_indices: {free_segments_indices}")

                def calculate_combination_total_segment_length(index_combination, ub_pw, lb_pw):
                    total_segment_length = 0.0

                    for i, segment_index in enumerate(index_combination):
                        ub_fs, lb_fs = free_segments_hor[n+i][segment_index]

                        mean_prev = (np.array(ub_pw) + np.array(lb_pw)) / 2.
                        mean_fs = (np.array(ub_fs) + np.array(lb_fs)) / 2.

                        if has_collision_in_line(self.map, mean_prev, mean_fs):
                            self.upper_cols.append([[mean_prev[0], mean_fs[0]], [mean_prev[1], mean_fs[1]]])
                            return -1000000.0 # penalty because has collision!

                        total_segment_length += dist(ub_fs[0], ub_fs[1], lb_fs[0], lb_fs[1])
                        ub_pw = ub_fs
                        lb_pw = lb_fs

                    return total_segment_length

                combination_segment_length = []
                combination_indices = []
                for combination in free_segments_indices_combinations:
                    if n > 0:
                        ub_pw, lb_pw = border_cells_hor[n-1]
                    else:
                        if pose is not None:
                            ub_pw, lb_pw =  [pose[0], pose[1]], [pose[0], pose[1]]
                        else:
                            ub_pw, lb_pw =  [wp.x, wp.y], [wp.x, wp.y]

                    total_segment_length = calculate_combination_total_segment_length(combination, ub_pw, lb_pw)
                    combination_segment_length.append(total_segment_length)
                    combination_indices.append(combination)

                max_area = max(combination_segment_length)
                max_area_index = combination_segment_length.index(max_area)
                max_area_combination_indices = combination_indices[max_area_index]

                # if show:
                #     print(f"max_area_combination_indices: {max_area_combination_indices}")
                #     print(f"n: {n}, combination_segment_length: {combination_segment_length}, combination_indices: {combination_indices}")

                for i in max_area_combination_indices:
                    wp = self.get_waypoint(wp_id+n)
                    ub_ls, lb_ls = free_segments_hor[n][i]
                    add_constraint(wp, ub_ls, lb_ls)
                    n += 1

            # Select free segment in case of only one candidate
            elif len(free_segments) == 1:
                ub_ls, lb_ls = free_segments[0]
                add_constraint(wp, ub_ls, lb_ls)
                n += 1  # increment waypoint index

            # Set waypoint coordinates as bound cells if no feasible
            # segment available
            else:
                print(f"No feasible free segment found! wp_id: {wp_id}, n: {n}")
                ub_ls, lb_ls = (wp.x, wp.y), (wp.x, wp.y)

                # left_angle = np.mod(wp.psi + math.pi / 2 + math.pi,
                #                  2 * math.pi) - math.pi
                # right_angle = np.mod(wp.psi - math.pi / 2 + math.pi,
                #                    2 * math.pi) - math.pi

                # ub_ls = (wp.x + min_width * np.cos(left_angle),
                #          wp.y + min_width * np.sin(left_angle))
                # lb_ls = (wp.x + min_width * np.cos(right_angle),
                #          wp.y + min_width * np.sin(right_angle))

                add_constraint(wp, ub_ls, lb_ls)

                n += 1  # increment waypoint index

        # return np.array(ub_hor), np.array(lb_hor), np.array(border_cells_hor_sm)

        self.modified_ub = []
        self.modified_lb = []

        # safety_marginを考慮したborder_cellsを滑らかにする
        # border_cells_smの連続する点を直線で結び、前後の直線がなす角がしきい値より大きい場合、
        # 間の点を一つ飛ばして直線を引きなおすようにborder_cells_smを更新する
        ANGLE_TH = np.deg2rad(45.0)
        SEARCH_HORIZON = 3 # >=1

        for n in reversed(range(SEARCH_HORIZON, N-SEARCH_HORIZON+1)):
            mid_index = n
            waypoint_mid = self.get_waypoint(wp_id+n)
            wp_mid = (waypoint_mid.x, waypoint_mid.y)
            new_border_cells_hor_sm_mid = [border_cells_hor_sm[mid_index][0], border_cells_hor_sm[mid_index][1]]
            new_bound_sm = [waypoint_mid.ub_sm, waypoint_mid.lb_sm]

            before_indeices = []
            after_indices = []
            for i in range(1, SEARCH_HORIZON+1):
                before_index = mid_index - i
                after_index = mid_index + i
                if before_index >= 0:
                    before_indeices.append(before_index)
                if after_index < N:
                    after_indices.append(after_index)
            # print(f"n: {n}, before_indeices: {before_indeices}, after_indices: {after_indices}")
            border_cell_indices_combinations = list(itertools.product(before_indeices, after_indices))
            # print(f"n: {n}, border_cell_indices_combinations: {border_cell_indices_combinations}")

            def validate_intersection(old_bound, new_bound, new_bound_cell, border_cell_after, bound_sign):
                # boundが安全寄りになっている場合のみ更新を許可
                if bound_sign * new_bound > bound_sign * old_bound:
                    # print(f"n: {n} has invalid bound! old: {old_bound}, new: {new_bound}")
                    return False

                # 更新後のbound cellが障害物に被っていないか確認
                t_x, t_y = self.map.w2m(new_bound_cell[0], new_bound_cell[1])
                if self._is_obstacle_occupied(t_x, t_y):
                    # print(f"n: {n} has collision!")
                    return False
                # if has_collision_in_line(self.map, border_cell_after, new_bound_cell):
                #     # print(f"n: {n} has collision!")
                #     return False

                return True

            for dir in ['upper', 'lower']:
                index = 0 if dir == 'upper' else 1
                bound_sign = 1 if dir == 'upper' else -1
                bound_hor = ub_hor if dir == 'upper' else lb_hor
                bound_mid = waypoint_mid.ub_sm if bound_sign == 1 else waypoint_mid.lb_sm
                border_cell_mid = border_cells_hor_sm[mid_index][index]

                def try_update_border_cell_for_safety(border_cell_before, border_cell_after, is_nearest_pair: bool):
                    angle = calculate_angle(border_cell_before, border_cell_mid, border_cell_after)

                    if np.abs(angle) < ANGLE_TH:
                        return True if is_nearest_pair else False

                    # 前後のborder_cellを結んだ直線と、間のwpとborder_cellを結んだ直線の交点を求める
                    new_border_cell_mid = calculate_intersection(wp_mid, border_cell_mid, border_cell_before, border_cell_after)

                    # 前記直線の交点とwaypoint_midの距離を計算
                    new_bound_mid = compute_bound(waypoint_mid, new_border_cell_mid)

                    # 交点が安全寄りで、干渉なしであれば更新する
                    if not validate_intersection(bound_mid, new_bound_mid, new_border_cell_mid, border_cell_after, bound_sign):
                        return False

                    # self.modified_ub.append([ub0, new_ub1])
                    # self.modified_ub.append([new_ub1, ub2])
                    bound_hor[mid_index] = new_bound_mid
                    new_border_cells_hor_sm_mid[index] = new_border_cell_mid
                    new_bound_sm[index] = new_bound_mid

                    return True

                for i, (before_index, after_index) in enumerate(border_cell_indices_combinations):
                    is_nearest_pair = (i == 0)
                    if try_update_border_cell_for_safety(border_cells_hor_sm[before_index][index], border_cells_hor_sm[after_index][index], is_nearest_pair):
                        break

            # update border cells (border_cells_horは以降使用しないので更新不要)
            border_cells_hor_sm[mid_index] = new_border_cells_hor_sm_mid
            waypoint_mid.dynamic_border_cells = tuple(new_border_cells_hor_sm_mid)
            waypoint_mid.ub_sm = new_bound_sm[0]
            waypoint_mid.lb_sm = new_bound_sm[1]

        return np.array(ub_hor), np.array(lb_hor), np.array(border_cells_hor_sm)


if __name__ == '__main__':

    # Select Track | 'Real_Track' or 'Sim_Track'
    path = 'Sim_Track'

    if path == 'Sim_Track':

        # Load map file
        map = Map(file_path='maps/sim_map.png', origin=[-1, -2], resolution=0.005)

        # Specify waypoints
        wp_x = [-0.75, -0.25, -0.25, 0.25, 0.25, 1.25, 1.25, 0.75, 0.75, 1.25,
                1.25, -0.75, -0.75, -0.25]
        wp_y = [-1.5, -1.5, -0.5, -0.5, -1.5, -1.5, -1, -1, -0.5, -0.5, 0, 0,
                -1.5, -1.5]

        # Specify path resolution
        path_resolution = 0.05  # m / wp

        # Create reference path
        reference_path = ReferencePath(map, wp_x, wp_y, path_resolution,
                     smoothing_distance=5, max_width=0.15,
                                       circular=True)

        # Add obstacles
        obs1 = Obstacle(cx=0.0, cy=0.0, radius=0.05)
        obs2 = Obstacle(cx=-0.8, cy=-0.5, radius=0.08)
        obs3 = Obstacle(cx=-0.7, cy=-1.5, radius=0.05)
        obs4 = Obstacle(cx=-0.3, cy=-1.0, radius=0.08)
        obs5 = Obstacle(cx=0.3, cy=-1.0, radius=0.05)
        obs6 = Obstacle(cx=0.75, cy=-1.5, radius=0.05)
        obs7 = Obstacle(cx=0.7, cy=-0.9, radius=0.07)
        obs8 = Obstacle(cx=1.2, cy=0.0, radius=0.08)
        reference_path.map.add_obstacles([obs1, obs2, obs3, obs4, obs5, obs6, obs7,
                                      obs8])

    elif path == 'Real_Track':

        # Load map file
        map = Map(file_path='maps/real_map.png', origin=(-30.0, -24.0),
                  resolution=0.06)

        # Specify waypoints
        wp_x = [-1.62, -6.04, -6.6, -5.36, -2.0, 5.9,
                11.9, 7.3, 0.0, -1.62]
        wp_y = [3.24, -1.4, -3.0, -5.36, -6.65, 3.5,
                10.9, 14.5, 5.2, 3.24]

        # Specify path resolution
        path_resolution = 0.2  # m / wp

        # Create reference path
        reference_path = ReferencePath(map, wp_x, wp_y, path_resolution,
                                       smoothing_distance=5, max_width=2.0,
                                       circular=True)

        # Add obstacles and bounds to map
        cone1 = Obstacle(-5.9, -2.9, 0.2)
        cone2 = Obstacle(-2.3, -5.9, 0.2)
        cone3 = Obstacle(10.9, 10.7, 0.2)
        cone4 = Obstacle(7.4, 13.5, 0.2)
        table1 = Obstacle(-0.30, -1.75, 0.2)
        table2 = Obstacle(1.55, 1.00, 0.2)
        table3 = Obstacle(4.30, 3.22, 0.2)
        obstacle_list = [cone1, cone2, cone3, cone4, table1, table2, table3]
        map.add_obstacles(obstacle_list)

        bound1 = ((-0.02, -2.72), (1.5, 1.0))
        bound2 = ((4.43, 3.07), (1.5, 1.0))
        bound3 = ((4.43, 3.07), (7.5, 6.93))
        bound4 = ((7.28, 13.37), (-3.32, -0.12))
        boundary_list = [bound1, bound2, bound3, bound4]
        map.add_boundary(boundary_list)

    else:
        reference_path = None
        print('Invalid path!')
        exit(1)

    ub, lb, border_cells = \
        reference_path.update_path_constraints(0, reference_path.n_waypoints,
                                               0.1, 0.01)
    SpeedProfileConstraints = {'a_min': -0.1, 'a_max': 0.5,
                               'v_min': 0, 'v_max': 1.0, 'ay_max': 4.0}
    reference_path.compute_speed_profile(SpeedProfileConstraints)
    # Get x and y locations of border cells for upper and lower bound
    for wp_id in range(reference_path.n_waypoints):
        reference_path.waypoints[wp_id].dynamic_border_cells = border_cells[wp_id]
    reference_path.show()
    plt.show()



