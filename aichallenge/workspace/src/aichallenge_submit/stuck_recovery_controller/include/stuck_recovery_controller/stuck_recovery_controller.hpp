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
  void onNominal(const AckermannControlCommand::ConstSharedPtr msg);
  void onVelocity(const VelocityReport::ConstSharedPtr msg);
  void runNormal(const AckermannControlCommand::ConstSharedPtr & msg, const rclcpp::Time & now);
  void runStuckDetected(const rclcpp::Time & now);
  void runReversing(const rclcpp::Time & now);
  void runDriveSettle(const rclcpp::Time & now);
  void startRecovery(const rclcpp::Time & now);
  void setState(RecoveryState state, const rclcpp::Time & now);
  bool hasFreshVelocity(const rclcpp::Time & now) const;
  void publishCommand(double speed, double acceleration);
  void publishGear(std::uint8_t command);

  rclcpp::Publisher<AckermannControlCommand>::SharedPtr control_pub_;
  rclcpp::Publisher<GearCommand>::SharedPtr gear_pub_;
  rclcpp::Subscription<AckermannControlCommand>::SharedPtr nominal_sub_;
  rclcpp::Subscription<VelocityReport>::SharedPtr velocity_sub_;

  RecoveryState state_{RecoveryState::NORMAL};
  rclcpp::Time state_enter_time_;
  std::optional<double> latest_velocity_;
  std::optional<rclcpp::Time> latest_velocity_time_;
  bool moving_observed_{false};
  std::optional<rclcpp::Time> stuck_start_time_;
};

}  // namespace stuck_recovery_controller

#endif  // STUCK_RECOVERY_CONTROLLER_HPP_
