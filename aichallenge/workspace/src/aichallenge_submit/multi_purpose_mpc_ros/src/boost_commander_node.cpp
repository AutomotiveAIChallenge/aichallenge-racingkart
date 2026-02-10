#include "multi_purpose_mpc_ros/boost_commander.hpp"
#include <rclcpp/executors.hpp>
#include <rclcpp/executors/multi_threaded_executor.hpp>

int main(int argc, char *argv[])
{
  rclcpp::init(argc, argv);
  auto node = rclcpp::Node::make_shared("boost_commander");
  auto command_publisher = std::make_shared<roborovsky::multi_purpose_mpc_ros::BoostCommander>(node);

  rclcpp::executors::MultiThreadedExecutor executor;
  executor.add_node(node);
  std::thread([&executor]() { executor.spin(); }).detach();
  command_publisher->run();

  rclcpp::shutdown();
  return EXIT_SUCCESS;
}
