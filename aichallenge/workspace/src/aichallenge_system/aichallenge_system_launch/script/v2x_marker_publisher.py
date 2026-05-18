#!/usr/bin/env python3
import rclpy
import rclpy.node
from builtin_interfaces.msg import Duration
from std_msgs.msg import ColorRGBA
from visualization_msgs.msg import Marker, MarkerArray
from v2x_msgs.msg import V2XVehiclePositionArray


VEHICLE_COLORS = {
    "d1": (0.2, 0.4, 1.0),
    "d2": (1.0, 0.9, 0.2),
    "d3": (0.2, 1.0, 0.2),
    "d4": (1.0, 0.2, 0.2),
}
DEFAULT_COLOR = (1.0, 1.0, 1.0)
SPHERE_DIAMETER = 1.5
ALPHA = 0.9
LIFETIME_SEC = 1


class V2XMarkerPublisherNode(rclpy.node.Node):
    def __init__(self):
        super().__init__("v2x_marker_publisher")
        self.sub = self.create_subscription(
            V2XVehiclePositionArray, "/v2x/vehicle_positions", self.callback, 1)
        self.pub = self.create_publisher(
            MarkerArray, "/v2x/vehicle_positions/markers", 1)

    def callback(self, msg: V2XVehiclePositionArray) -> None:
        markers = MarkerArray()

        clear = Marker()
        clear.action = Marker.DELETEALL
        markers.markers.append(clear)

        for index, vehicle in enumerate(msg.vehicles):
            markers.markers.append(self._build_marker(msg, vehicle, index))

        self.pub.publish(markers)

    def _build_marker(self, array_msg, vehicle, index: int) -> Marker:
        marker = Marker()
        marker.header.frame_id = vehicle.header.frame_id or array_msg.header.frame_id or "map"
        marker.header.stamp = vehicle.header.stamp
        marker.ns = "v2x_vehicles"
        marker.id = index
        marker.type = Marker.SPHERE
        marker.action = Marker.ADD
        marker.pose.position.x = vehicle.position.x
        marker.pose.position.y = vehicle.position.y
        marker.pose.position.z = vehicle.position.z
        marker.pose.orientation.w = 1.0
        marker.scale.x = SPHERE_DIAMETER
        marker.scale.y = SPHERE_DIAMETER
        marker.scale.z = SPHERE_DIAMETER
        r, g, b = VEHICLE_COLORS.get(vehicle.vehicle_id, DEFAULT_COLOR)
        marker.color = ColorRGBA(r=r, g=g, b=b, a=ALPHA)
        marker.lifetime = Duration(sec=LIFETIME_SEC, nanosec=0)
        return marker


def main(args=None):
    rclpy.init(args=args)
    rclpy.spin(V2XMarkerPublisherNode())
    rclpy.shutdown()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
