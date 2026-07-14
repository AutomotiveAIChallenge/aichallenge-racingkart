#include "stuck_recovery_controller/stuck_recovery_controller.hpp"

#include <cmath>
#include <functional>
#include <memory>

namespace stuck_recovery_controller
{
namespace
{

constexpr double kStuckSpeedThreshold = 0.2;
constexpr double kStuckDurationSec = 1.0;
constexpr double kCommandSpeedThreshold = 1.0;
constexpr double kMovingSpeedThreshold = 0.5;
constexpr double kReverseSpeed = 1.0;
constexpr double kReverseAccel = 1.0;
constexpr double kGearChangeDelaySec = 0.2;
constexpr double kReverseDurationSec = 4.0;
constexpr double kDriveSettleDurationSec = 0.5;
constexpr double kVelocityTimeoutSec = 0.5;

const char * toString(RecoveryState state)
{
  switch (state) {
    case RecoveryState::NORMAL:
      return "NORMAL";
    case RecoveryState::STUCK_DETECTED:
      return "STUCK_DETECTED";
    case RecoveryState::REVERSING:
      return "REVERSING";
    case RecoveryState::DRIVE_SETTLE:
      return "DRIVE_SETTLE";
  }
  return "UNKNOWN";
}

}  // namespace

StuckRecoveryController::StuckRecoveryController() : Node("stuck_recovery_controller")
{
  state_enter_time_ = this->now();

  control_pub_ = create_publisher<AckermannControlCommand>("/control/command/control_cmd", 1);
  gear_pub_ = create_publisher<GearCommand>("/control/command/gear_cmd", 1);

  nominal_sub_ = create_subscription<AckermannControlCommand>(
    "/control/command/nominal_control_cmd", 1,
    std::bind(&StuckRecoveryController::onNominal, this, std::placeholders::_1));
  velocity_sub_ = create_subscription<VelocityReport>(
    "/vehicle/status/velocity_status", 1,
    std::bind(&StuckRecoveryController::onVelocity, this, std::placeholders::_1));

  RCLCPP_INFO(get_logger(), "stuck_recovery_controller started");
}

void StuckRecoveryController::onNominal(const AckermannControlCommand::ConstSharedPtr msg)
{
  const auto now = this->now();

  switch (state_) {
    case RecoveryState::NORMAL:
      runNormal(msg, now);
      break;
    case RecoveryState::STUCK_DETECTED:
      runStuckDetected(now);
      break;
    case RecoveryState::REVERSING:
      runReversing(now);
      break;
    case RecoveryState::DRIVE_SETTLE:
      runDriveSettle(now);
      break;
  }
}

void StuckRecoveryController::onVelocity(const VelocityReport::ConstSharedPtr msg)
{
  latest_velocity_ = static_cast<double>(msg->longitudinal_velocity);
  latest_velocity_time_ = this->now();
}

void StuckRecoveryController::runNormal(
  const AckermannControlCommand::ConstSharedPtr & msg, const rclcpp::Time & now)
{
  control_pub_->publish(*msg);
  publishGear(GearCommand::DRIVE);

  if (!hasFreshVelocity(now)) {
    stuck_start_time_.reset();
    return;
  }

  const double velocity = latest_velocity_.value();
  if (velocity >= kMovingSpeedThreshold) {
    moving_observed_ = true;
  }

  const double nominal_speed = msg->longitudinal.speed;
  const bool forward_requested =
    std::isfinite(nominal_speed) && nominal_speed >= kCommandSpeedThreshold;
  if (!moving_observed_ || !forward_requested) {
    stuck_start_time_.reset();
    return;
  }

  if (std::abs(velocity) <= kStuckSpeedThreshold) {
    if (!stuck_start_time_.has_value()) {
      stuck_start_time_ = now;
    } else if ((now - stuck_start_time_.value()).seconds() >= kStuckDurationSec) {
      startRecovery(now);
    }
  } else {
    stuck_start_time_.reset();
  }
}

void StuckRecoveryController::runStuckDetected(const rclcpp::Time & now)
{
  publishGear(GearCommand::DRIVE);
  publishCommand(0.0, 0.0);
  if ((now - state_enter_time_).seconds() >= kGearChangeDelaySec) {
    setState(RecoveryState::REVERSING, now);
  }
}

void StuckRecoveryController::runReversing(const rclcpp::Time & now)
{
  publishGear(GearCommand::REVERSE);
  publishCommand(-kReverseSpeed, kReverseAccel);
  if ((now - state_enter_time_).seconds() >= kReverseDurationSec) {
    setState(RecoveryState::DRIVE_SETTLE, now);
  }
}

void StuckRecoveryController::runDriveSettle(const rclcpp::Time & now)
{
  publishGear(GearCommand::DRIVE);
  publishCommand(0.0, 0.0);
  if ((now - state_enter_time_).seconds() >= kDriveSettleDurationSec) {
    moving_observed_ = false;
    stuck_start_time_.reset();
    setState(RecoveryState::NORMAL, now);
  }
}

void StuckRecoveryController::startRecovery(const rclcpp::Time & now)
{
  stuck_start_time_.reset();
  RCLCPP_WARN(
    get_logger(), "stuck under throttle detected: velocity=%.3f",
    latest_velocity_.value_or(0.0));
  setState(RecoveryState::STUCK_DETECTED, now);
}

void StuckRecoveryController::setState(RecoveryState state, const rclcpp::Time & now)
{
  if (state == state_) {
    return;
  }
  RCLCPP_INFO(get_logger(), "state transition: %s -> %s", toString(state_), toString(state));
  state_ = state;
  state_enter_time_ = now;
}

bool StuckRecoveryController::hasFreshVelocity(const rclcpp::Time & now) const
{
  return latest_velocity_.has_value() && latest_velocity_time_.has_value() &&
         (now - latest_velocity_time_.value()).seconds() <= kVelocityTimeoutSec;
}

void StuckRecoveryController::publishCommand(double speed, double acceleration)
{
  const auto stamp = this->now();
  AckermannControlCommand msg;
  msg.stamp = stamp;
  msg.lateral.stamp = stamp;
  msg.lateral.steering_tire_angle = 0.0F;
  msg.lateral.steering_tire_rotation_rate = 2.0F;
  msg.longitudinal.stamp = stamp;
  msg.longitudinal.speed = static_cast<float>(speed);
  msg.longitudinal.acceleration = static_cast<float>(acceleration);
  control_pub_->publish(msg);
}

void StuckRecoveryController::publishGear(std::uint8_t command)
{
  GearCommand msg;
  msg.stamp = this->now();
  msg.command = command;
  gear_pub_->publish(msg);
}

}  // namespace stuck_recovery_controller

int main(int argc, char ** argv)
{
  rclcpp::init(argc, argv);
  rclcpp::spin(std::make_shared<stuck_recovery_controller::StuckRecoveryController>());
  rclcpp::shutdown();
  return 0;
}
