#ifndef STUCK_RECOVERY_CONTROLLER_HPP_
#define STUCK_RECOVERY_CONTROLLER_HPP_

#include <rclcpp/rclcpp.hpp>

#include <autoware_auto_control_msgs/msg/ackermann_control_command.hpp>
#include <autoware_auto_vehicle_msgs/msg/gear_command.hpp>
#include <autoware_auto_vehicle_msgs/msg/gear_report.hpp>
#include <autoware_auto_vehicle_msgs/msg/velocity_report.hpp>
#include <std_msgs/msg/string.hpp>

#include <cstdint>
#include <optional>

namespace stuck_recovery_controller
{

using autoware_auto_control_msgs::msg::AckermannControlCommand;
using autoware_auto_vehicle_msgs::msg::GearCommand;
using autoware_auto_vehicle_msgs::msg::GearReport;
using autoware_auto_vehicle_msgs::msg::VelocityReport;

enum class RecoveryState
{
  NORMAL,
  STUCK_DETECTED,
  REVERSING,
  DRIVE_SETTLE,
  COOLDOWN,
};

class StuckRecoveryController : public rclcpp::Node
{
public:
  StuckRecoveryController();

private:
  static constexpr double kStuckSpeedThreshold = 0.2;
  static constexpr double kStuckDuration = 1.0;
  static constexpr double kCommandSpeedThreshold = 1.0;
  static constexpr double kCommandAccelThreshold = 0.3;
  static constexpr double kMovingSpeedThreshold = 0.5;
  static constexpr double kReverseSpeed = 1.0;
  static constexpr double kReverseAccel = 1.0;
  static constexpr double kReverseDuration = 4.0;
  static constexpr double kDriveSettleDuration = 0.5;
  static constexpr double kCooldownDuration = 3.0;
  static constexpr double kNominalTimeoutSec = 0.5;
  static constexpr double kVelocityTimeoutSec = 0.5;
  static constexpr double kTimerHz = 20.0;

  double nowSec();
  static bool isPassThrough(RecoveryState state);
  void onNominal(const AckermannControlCommand::SharedPtr msg);
  void onVelocity(const VelocityReport::SharedPtr msg);
  void onGear(const GearReport::SharedPtr msg);
  void onTimer();
  void runNormal(double now);
  void runStuckDetected(double now);
  void runReversing(double now);
  void runDriveSettle(double now);
  void runCooldown(double now);
  void startRecovery(double now);
  void setState(RecoveryState state, double now);
  bool hasFreshNominal(double now) const;
  bool hasFreshVelocity(double now) const;
  bool isForwardRequest(const AckermannControlCommand::SharedPtr & cmd) const;
  void publishNominal();
  void publishCommand(double speed, double acceleration, double steer);
  void publishGear(std::uint8_t command);
  void publishState();

  rclcpp::Publisher<AckermannControlCommand>::SharedPtr control_pub_;
  rclcpp::Publisher<GearCommand>::SharedPtr gear_pub_;
  rclcpp::Publisher<std_msgs::msg::String>::SharedPtr state_pub_;
  rclcpp::Subscription<AckermannControlCommand>::SharedPtr nominal_sub_;
  rclcpp::Subscription<VelocityReport>::SharedPtr velocity_sub_;
  rclcpp::Subscription<GearReport>::SharedPtr gear_sub_;
  rclcpp::TimerBase::SharedPtr timer_;

  RecoveryState state_{RecoveryState::NORMAL};
  double state_enter_time_{0.0};
  AckermannControlCommand::SharedPtr latest_nominal_;
  std::optional<double> latest_nominal_time_;
  std::optional<double> latest_velocity_;
  std::optional<double> latest_velocity_time_;
  std::optional<std::uint8_t> latest_gear_;
  bool moving_observed_{false};
  std::optional<double> stuck_start_time_;
  int attempt_count_{0};
};

}  // namespace stuck_recovery_controller

#endif  // STUCK_RECOVERY_CONTROLLER_HPP_
