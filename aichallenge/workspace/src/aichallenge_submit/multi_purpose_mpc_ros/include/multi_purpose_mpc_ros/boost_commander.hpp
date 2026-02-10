#ifndef ROBOROVSKY__MULTI_PURPOSE_MPC_ROS__COMMAND_PUBLISHER_HPP_
#define ROBOROVSKY__MULTI_PURPOSE_MPC_ROS__COMMAND_PUBLISHER_HPP_

#include <multi_purpose_mpc_ros_msgs/msg/ackermann_control_boost_command.hpp>

// ROS 2
#include <rclcpp/rclcpp.hpp>

// Autoware
#include <autoware_auto_control_msgs/msg/ackermann_control_command.hpp>


namespace roborovsky::multi_purpose_mpc_ros
{

class BoostCommander
{
public:
    // Alias
    using AckermannControlCommand = autoware_auto_control_msgs::msg::AckermannControlCommand;
    using AckermannControlBoostCommand = multi_purpose_mpc_ros_msgs::msg::AckermannControlBoostCommand;

public:
    explicit BoostCommander(rclcpp::Node::SharedPtr node);
    void run();

private:
    rclcpp::Node::SharedPtr node_;
    rclcpp::Publisher<AckermannControlCommand>::SharedPtr command_publisher_;
    rclcpp::Subscription<AckermannControlBoostCommand>::SharedPtr command_subscriber_;
    AckermannControlBoostCommand command_;
    bool command_received_ = false;

    void commandCallback(const AckermannControlBoostCommand::SharedPtr msg);

};


} // namespace roborovsky::multi_purpose_mpc_ros

#endif