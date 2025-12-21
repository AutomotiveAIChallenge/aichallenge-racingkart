#!/usr/bin/env python3
import rclpy
from rclpy.node import Node
from std_msgs.msg import Bool
from std_srvs.srv import Trigger 

from bag_manager_py.bag_manager_core import BagRecorderCore

class RosBagManagerNode(Node):
    """A ROS 2 Node wrapper for the BagRecorderCore.

    This node exposes ROS 2 interfaces (Service, Topic) to control the
    recording logic encapsulated in BagRecorderCore.
    """

    def __init__(self):
        """Initializes the ROS 2 node and the underlying BagRecorderCore."""
        super().__init__('ros2_bag_manager_node')

        self.declare_parameter('output_dir', 'rosbag2_output')
        self.declare_parameter('all_topics', True)
        self.declare_parameter('topics', ['/rosbag2_recorder/trigger'])
        self.declare_parameter('storage_id', 'mcap')
        
        output_dir = self.get_parameter('output_dir').value
        all_topics = self.get_parameter('all_topics').value
        topics = list(self.get_parameter('topics').value)
        storage_id = self.get_parameter('storage_id').value

        self.core = BagRecorderCore(
            output_dir=output_dir,
            topics=topics,
            all_topics=all_topics,
            storage_id=storage_id,
            logger=self.get_logger() 
        )

        self.status_pub = self.create_publisher(Bool, '~/status', 10)
        self.create_subscription(Bool, '/rosbag2_recorder/trigger', self.trigger_cb, 10)
        self.create_service(Trigger, '~/start_recording', self.start_cb)
        self.create_service(Trigger, '~/stop_recording', self.stop_cb)
        
        self.publish_status()

    def publish_status(self):
        """Publishes the current recording status to the '~/status' topic."""
        self.status_pub.publish(Bool(data=self.core.is_recording))

    def trigger_cb(self, msg: Bool):
        """Callback for the trigger topic.

        Args:
            msg (Bool): If data is True, starts recording. If False, stops recording.
        """
        if msg.data:
            self.core.start()
        else:
            self.core.stop()
        self.publish_status()

    def start_cb(self, request: Trigger.Request, response: Trigger.Response) -> Trigger.Response:
        """Callback for the start_recording service.

        Args:
            request (Trigger.Request): The service request (empty).
            response (Trigger.Response): The service response to be populated.

        Returns:
            Trigger.Response: The populated response indicating success or failure.
        """
        success, msg = self.core.start()
        response.success = success
        response.message = msg
        self.publish_status()
        return response

    def stop_cb(self, request: Trigger.Request, response: Trigger.Response) -> Trigger.Response:
        """Callback for the stop_recording service.

        Args:
            request (Trigger.Request): The service request (empty).
            response (Trigger.Response): The service response to be populated.

        Returns:
            Trigger.Response: The populated response indicating success or failure.
        """
        success, msg = self.core.stop()
        response.success = success
        response.message = msg
        self.publish_status()
        return response

    def destroy_node(self):
        """Cleanly destroys the node and ensures recording is stopped."""
        self.core.stop()
        super().destroy_node()

def main(args=None):
    rclpy.init(args=args)
    node = RosBagManagerNode()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        if rclpy.ok():
            node.destroy_node()
            rclpy.shutdown()

if __name__ == '__main__':
    main()
