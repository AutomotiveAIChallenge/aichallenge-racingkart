#ifndef STUCK_RECOVERY_CONTROLLER_HPP_
#define STUCK_RECOVERY_CONTROLLER_HPP_

#include <rclcpp/rclcpp.hpp>

#include <autoware_auto_control_msgs/msg/ackermann_control_command.hpp>
#include <autoware_auto_vehicle_msgs/msg/gear_command.hpp>
#include <autoware_auto_vehicle_msgs/msg/velocity_report.hpp>

#include <cstdint>
#include <optional>

namespace stuck_recovery_controller
{

using autoware_auto_control_msgs::msg::AckermannControlCommand;
using autoware_auto_vehicle_msgs::msg::GearCommand;
using autoware_auto_vehicle_msgs::msg::VelocityReport;

class StuckRecoveryController : public rclcpp::Node
{
public:
  StuckRecoveryController();

private:
  void onNominalCommand(const AckermannControlCommand::ConstSharedPtr msg);
  void updateStuckDetection(
    const AckermannControlCommand & command, const rclcpp::Time & now);
  bool runRecovery(const rclcpp::Time & now);
  void publishCommand(float speed, float acceleration);
  void publishGear(std::uint8_t command);

  rclcpp::Publisher<AckermannControlCommand>::SharedPtr control_pub_;
  rclcpp::Publisher<GearCommand>::SharedPtr gear_pub_;
  rclcpp::Subscription<AckermannControlCommand>::SharedPtr nominal_sub_;
  rclcpp::Subscription<VelocityReport>::SharedPtr velocity_sub_;

  float latest_velocity_{0.0};
  bool moving_observed_{false};
  std::optional<rclcpp::Time> stuck_start_time_;
  std::optional<rclcpp::Time> recovery_start_time_;
};

}  // namespace stuck_recovery_controller

#endif  // STUCK_RECOVERY_CONTROLLER_HPP_
