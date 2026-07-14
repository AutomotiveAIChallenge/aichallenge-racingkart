#include "stuck_recovery_controller/stuck_recovery_controller.hpp"

#include <cmath>
#include <functional>
#include <memory>

namespace stuck_recovery_controller
{
namespace
{

constexpr double kStuckSpeedThreshold = 0.2;
constexpr double kStuckDuration = 1.0;
constexpr double kCommandSpeedThreshold = 1.0;
constexpr double kMovingSpeedThreshold = 0.5;
constexpr double kReverseSpeed = 1.0;
constexpr double kReverseAccel = 1.0;
constexpr double kReverseDuration = 4.0;
constexpr double kDriveSettleDuration = 0.5;
constexpr double kNominalTimeoutSec = 0.5;
constexpr double kVelocityTimeoutSec = 0.5;
constexpr double kTimerHz = 20.0;

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
  control_pub_ = create_publisher<AckermannControlCommand>("/control/command/control_cmd", 1);
  gear_pub_ = create_publisher<GearCommand>("/control/command/gear_cmd", 1);

  nominal_sub_ = create_subscription<AckermannControlCommand>(
    "/control/command/nominal_control_cmd", 1,
    std::bind(&StuckRecoveryController::onNominal, this, std::placeholders::_1));
  velocity_sub_ = create_subscription<VelocityReport>(
    "/vehicle/status/velocity_status", 1,
    std::bind(&StuckRecoveryController::onVelocity, this, std::placeholders::_1));

  state_enter_time_ = nowSec();

  timer_ = rclcpp::create_timer(
    this, get_clock(), rclcpp::Duration::from_seconds(1.0 / kTimerHz),
    std::bind(&StuckRecoveryController::onTimer, this));

  RCLCPP_INFO(get_logger(), "stuck_recovery_controller started");
}

double StuckRecoveryController::nowSec()
{
  return get_clock()->now().seconds();
}

void StuckRecoveryController::onNominal(const AckermannControlCommand::SharedPtr msg)
{
  latest_nominal_ = msg;
  latest_nominal_time_ = nowSec();
  if (state_ == RecoveryState::NORMAL) {
    publishNominal();
  }
}

void StuckRecoveryController::onVelocity(const VelocityReport::SharedPtr msg)
{
  latest_velocity_ = static_cast<double>(msg->longitudinal_velocity);
  latest_velocity_time_ = nowSec();
}

void StuckRecoveryController::onTimer()
{
  const double now = nowSec();

  switch (state_) {
    case RecoveryState::NORMAL:
      runNormal(now);
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

void StuckRecoveryController::runNormal(double now)
{
  if (!hasFreshNominal(now)) {
    stuck_start_time_.reset();
    return;
  }
  publishGear(GearCommand::DRIVE);

  if (!hasFreshVelocity(now)) {
    stuck_start_time_.reset();
    return;
  }

  const double velocity = latest_velocity_.value();
  if (velocity >= kMovingSpeedThreshold) {
    moving_observed_ = true;
  }

  if (!moving_observed_ || !isForwardRequest(latest_nominal_)) {
    stuck_start_time_.reset();
    return;
  }

  if (std::abs(velocity) <= kStuckSpeedThreshold) {
    if (!stuck_start_time_.has_value()) {
      stuck_start_time_ = now;
    } else if (now - stuck_start_time_.value() >= kStuckDuration) {
      startRecovery(now);
    }
  } else {
    stuck_start_time_.reset();
  }
}

void StuckRecoveryController::runStuckDetected(double now)
{
  publishGear(GearCommand::DRIVE);
  publishCommand(0.0, 0.0, 0.0);
  if (now - state_enter_time_ >= 0.2) {
    setState(RecoveryState::REVERSING, now);
  }
}

void StuckRecoveryController::runReversing(double now)
{
  publishGear(GearCommand::REVERSE);
  publishCommand(-std::abs(kReverseSpeed), std::abs(kReverseAccel), 0.0);
  if (now - state_enter_time_ >= kReverseDuration) {
    setState(RecoveryState::DRIVE_SETTLE, now);
  }
}

void StuckRecoveryController::runDriveSettle(double now)
{
  publishGear(GearCommand::DRIVE);
  publishCommand(0.0, 0.0, 0.0);
  if (now - state_enter_time_ >= kDriveSettleDuration) {
    moving_observed_ = false;
    stuck_start_time_.reset();
    setState(RecoveryState::NORMAL, now);
  }
}

void StuckRecoveryController::startRecovery(double now)
{
  stuck_start_time_.reset();
  RCLCPP_WARN(
    get_logger(), "stuck under throttle detected: velocity=%.3f",
    latest_velocity_.value_or(0.0));
  setState(RecoveryState::STUCK_DETECTED, now);
}

void StuckRecoveryController::setState(RecoveryState state, double now)
{
  if (state == state_) {
    return;
  }
  RCLCPP_INFO(get_logger(), "state transition: %s -> %s", toString(state_), toString(state));
  state_ = state;
  state_enter_time_ = now;
}

bool StuckRecoveryController::hasFreshNominal(double now) const
{
  return latest_nominal_ != nullptr && latest_nominal_time_.has_value() &&
         now - latest_nominal_time_.value() <= kNominalTimeoutSec;
}

bool StuckRecoveryController::hasFreshVelocity(double now) const
{
  return latest_velocity_.has_value() && latest_velocity_time_.has_value() &&
         now - latest_velocity_time_.value() <= kVelocityTimeoutSec;
}

bool StuckRecoveryController::isForwardRequest(
  const AckermannControlCommand::SharedPtr & cmd) const
{
  if (cmd == nullptr) {
    return false;
  }
  const double speed = cmd->longitudinal.speed;
  return std::isfinite(speed) && speed >= kCommandSpeedThreshold;
}

void StuckRecoveryController::publishNominal()
{
  if (latest_nominal_ == nullptr) {
    publishCommand(0.0, 0.0, 0.0);
    return;
  }
  AckermannControlCommand msg = *latest_nominal_;
  const auto stamp = get_clock()->now();
  msg.stamp = stamp;
  msg.lateral.stamp = stamp;
  msg.longitudinal.stamp = stamp;
  control_pub_->publish(msg);
}

void StuckRecoveryController::publishCommand(double speed, double acceleration, double steer)
{
  const auto stamp = get_clock()->now();
  AckermannControlCommand msg;
  msg.stamp = stamp;
  msg.lateral.stamp = stamp;
  msg.lateral.steering_tire_angle = static_cast<float>(steer);
  msg.lateral.steering_tire_rotation_rate = 2.0F;
  msg.longitudinal.stamp = stamp;
  msg.longitudinal.speed = static_cast<float>(speed);
  msg.longitudinal.acceleration = static_cast<float>(acceleration);
  control_pub_->publish(msg);
}

void StuckRecoveryController::publishGear(std::uint8_t command)
{
  GearCommand msg;
  msg.stamp = get_clock()->now();
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
