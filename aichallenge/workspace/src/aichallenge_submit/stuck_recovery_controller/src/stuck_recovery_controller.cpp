#include "stuck_recovery_controller/stuck_recovery_controller.hpp"

#include <cmath>
#include <functional>
#include <memory>

namespace stuck_recovery_controller
{

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
    case RecoveryState::COOLDOWN:
      return "COOLDOWN";
  }
  return "UNKNOWN";
}

StuckRecoveryController::StuckRecoveryController() : Node("stuck_recovery_controller")
{
  control_pub_ = create_publisher<AckermannControlCommand>("/control/command/control_cmd", 1);
  gear_pub_ = create_publisher<GearCommand>("/control/command/gear_cmd", 1);
  state_pub_ = create_publisher<std_msgs::msg::String>("/stuck_recovery_controller/state", 1);

  nominal_sub_ = create_subscription<AckermannControlCommand>(
    "/control/command/nominal_control_cmd", 1,
    std::bind(&StuckRecoveryController::onNominal, this, std::placeholders::_1));
  velocity_sub_ = create_subscription<VelocityReport>(
    "/vehicle/status/velocity_status", 1,
    std::bind(&StuckRecoveryController::onVelocity, this, std::placeholders::_1));
  gear_sub_ = create_subscription<GearReport>(
    "/vehicle/status/gear_status", 1,
    std::bind(&StuckRecoveryController::onGear, this, std::placeholders::_1));

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

bool StuckRecoveryController::isPassThrough(RecoveryState state)
{
  return state == RecoveryState::NORMAL || state == RecoveryState::COOLDOWN;
}

void StuckRecoveryController::onNominal(const AckermannControlCommand::SharedPtr msg)
{
  latest_nominal_ = msg;
  latest_nominal_time_ = nowSec();
  if (isPassThrough(state_)) {
    publishNominal();
  }
}

void StuckRecoveryController::onVelocity(const VelocityReport::SharedPtr msg)
{
  latest_velocity_ = static_cast<double>(msg->longitudinal_velocity);
  latest_velocity_time_ = nowSec();
}

void StuckRecoveryController::onGear(const GearReport::SharedPtr msg)
{
  latest_gear_ = msg->report;
}

void StuckRecoveryController::onTimer()
{
  const double now = nowSec();
  publishState();

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
    case RecoveryState::COOLDOWN:
      runCooldown(now);
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

  if (!hasFreshVelocity(now) || !latest_velocity_.has_value()) {
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
    setState(RecoveryState::COOLDOWN, now);
  }
}

void StuckRecoveryController::runCooldown(double now)
{
  publishGear(GearCommand::DRIVE);
  if (now - state_enter_time_ >= kCooldownDuration) {
    moving_observed_ = false;
    stuck_start_time_.reset();
    setState(RecoveryState::NORMAL, now);
  }
}

void StuckRecoveryController::startRecovery(double now)
{
  ++attempt_count_;
  stuck_start_time_.reset();
  RCLCPP_WARN(
    get_logger(), "stuck under throttle detected: attempt=%d velocity=%.3f", attempt_count_,
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
  publishState();
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
  const double acceleration = cmd->longitudinal.acceleration;
  const bool speed_forward = std::isfinite(speed) && speed >= kCommandSpeedThreshold;
  const bool gear_allows_accel =
    !latest_gear_.has_value() || latest_gear_.value() == GearReport::DRIVE;
  const bool accel_forward =
    std::isfinite(acceleration) && acceleration >= kCommandAccelThreshold && gear_allows_accel;
  return speed_forward || accel_forward;
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

void StuckRecoveryController::publishState()
{
  std_msgs::msg::String msg;
  msg.data = toString(state_);
  state_pub_->publish(msg);
}

}  // namespace stuck_recovery_controller

int main(int argc, char ** argv)
{
  rclcpp::init(argc, argv);
  rclcpp::spin(std::make_shared<stuck_recovery_controller::StuckRecoveryController>());
  rclcpp::shutdown();
  return 0;
}
