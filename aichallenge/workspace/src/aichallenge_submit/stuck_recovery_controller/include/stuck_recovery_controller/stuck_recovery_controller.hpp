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

enum class RecoveryState
{
  NORMAL,
  STUCK_DETECTED,
  REVERSING,
  DRIVE_SETTLE,
};

class StuckRecoveryController : public rclcpp::Node
{
public:
  StuckRecoveryController();

private:
  double nowSec();
  void onNominal(const AckermannControlCommand::SharedPtr msg);
  void onVelocity(const VelocityReport::SharedPtr msg);
  void runNormal(const AckermannControlCommand::SharedPtr & msg, double now);
  void runStuckDetected(double now);
  void runReversing(double now);
  void runDriveSettle(double now);
  void startRecovery(double now);
  void setState(RecoveryState state, double now);
  bool hasFreshVelocity(double now) const;
  void publishCommand(double speed, double acceleration);
  void publishGear(std::uint8_t command);

  rclcpp::Publisher<AckermannControlCommand>::SharedPtr control_pub_;
  rclcpp::Publisher<GearCommand>::SharedPtr gear_pub_;
  rclcpp::Subscription<AckermannControlCommand>::SharedPtr nominal_sub_;
  rclcpp::Subscription<VelocityReport>::SharedPtr velocity_sub_;

  RecoveryState state_{RecoveryState::NORMAL};
  double state_enter_time_{0.0};
  std::optional<double> latest_velocity_;
  std::optional<double> latest_velocity_time_;
  bool moving_observed_{false};
  std::optional<double> stuck_start_time_;
};

}  // namespace stuck_recovery_controller

#endif  // STUCK_RECOVERY_CONTROLLER_HPP_
