#include "multi_purpose_mpc_ros/boost_commander.hpp"

namespace roborovsky::multi_purpose_mpc_ros
{

// Public methods

BoostCommander::BoostCommander(rclcpp::Node::SharedPtr node)
: node_(node)
{
  command_.boost_mode = false;
  command_.command.lateral.steering_tire_angle = 0.0;
  command_.command.longitudinal.speed = 0.0;
  command_.command.longitudinal.acceleration = 0.0;

  command_publisher_ = node_->create_publisher<AckermannControlCommand>("/control/command/control_cmd", 10);
  command_subscriber_ = node_->create_subscription<AckermannControlBoostCommand>(
    "~/command",
    10,
    std::bind(&BoostCommander::commandCallback, this, std::placeholders::_1)
  );
}

void BoostCommander::run()
{
  rclcpp::Rate high_rate(1700);
  rclcpp::Rate low_rate(50);

  while (rclcpp::ok())
  {
    if (!command_received_)
    {
      low_rate.sleep();
      continue;
    }

    command_publisher_->publish(command_.command);
    if (command_.boost_mode)
    {
      high_rate.sleep();
    }
    else
    {
      low_rate.sleep();
    }
  }

}

// Private methods

void BoostCommander::commandCallback(const AckermannControlBoostCommand::SharedPtr msg)
{
  command_received_ = true;
  command_ = *msg;
}



} // namespace roborovsky::multi_purpose_mpc_ros
