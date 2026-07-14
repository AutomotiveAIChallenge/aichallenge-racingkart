#include "stuck_recovery_controller/stuck_recovery_controller.hpp"

#include <cmath>
#include <functional>
#include <memory>

namespace stuck_recovery_controller
{
namespace
{

constexpr float kStuckSpeedThreshold = 0.1;
constexpr double kStuckDurationSec = 1.0;
constexpr float kCommandSpeedThreshold = 1.0;
constexpr float kCommandAccelerationThreshold = 0.3;
constexpr float kMovingSpeedThreshold = 0.5;
constexpr double kReverseDurationSec = 4.0;
constexpr double kDriveSettleDurationSec = 0.5;

}  // namespace

StuckRecoveryController::StuckRecoveryController() : Node("stuck_recovery_controller")
{
  control_pub_ = create_publisher<AckermannControlCommand>("/control/command/control_cmd", 1);
  gear_pub_ = create_publisher<GearCommand>("/control/command/gear_cmd", 1);

  nominal_sub_ = create_subscription<AckermannControlCommand>(
    "/control/command/nominal_control_cmd", 1,
    std::bind(&StuckRecoveryController::onNominalCommand, this, std::placeholders::_1));
  velocity_sub_ = create_subscription<VelocityReport>(
    "/vehicle/status/velocity_status", 1,
    [this](const VelocityReport::ConstSharedPtr msg) {
      latest_velocity_ = msg->longitudinal_velocity;
    });
}

void StuckRecoveryController::onNominalCommand(
  const AckermannControlCommand::ConstSharedPtr msg)
{
  const auto now = this->now();
  if (runRecovery(now)) {
    return;
  }
  control_pub_->publish(*msg);
  updateStuckDetection(*msg, now);
}

void StuckRecoveryController::updateStuckDetection(
  const AckermannControlCommand & command, const rclcpp::Time & now)
{
  const float velocity = latest_velocity_;
  // Require movement once to avoid detecting the initial stationary state as stuck.
  if (velocity >= kMovingSpeedThreshold) {
    moving_observed_ = true;
  }

  if (
    !moving_observed_ || command.longitudinal.speed < kCommandSpeedThreshold ||
    command.longitudinal.acceleration < kCommandAccelerationThreshold)
  {
    stuck_start_time_.reset();
    return;
  }

  if (std::abs(velocity) <= kStuckSpeedThreshold) {
    if (!stuck_start_time_.has_value()) {
      stuck_start_time_ = now;
    } else if ((now - stuck_start_time_.value()).seconds() >= kStuckDurationSec) {
      stuck_start_time_.reset();
      recovery_start_time_ = now;
      RCLCPP_INFO(get_logger(), "stuck detected: velocity=%.3f", velocity);
    }
  } else {
    stuck_start_time_.reset();
  }
}

bool StuckRecoveryController::runRecovery(const rclcpp::Time & now)
{
  if (!recovery_start_time_.has_value()) {
    return false;
  }

  const double elapsed = (now - recovery_start_time_.value()).seconds();

  // 1. Reverse for kReverseDurationSec.
  if (elapsed < kReverseDurationSec) {
    publishGear(GearCommand::REVERSE);
    // AWSIM expects positive acceleration with reverse gear and negative target speed.
    publishCommand(-1.0, 1.0);
    return true;
  }

  // 2. Shift to DRIVE and stop for kDriveSettleDurationSec.
  if (elapsed < kReverseDurationSec + kDriveSettleDurationSec) {
    publishGear(GearCommand::DRIVE);
    publishCommand(0.0, 0.0);
    return true;
  }

  // 3. Finish recovery and resume nominal commands.
  recovery_start_time_.reset();
  return false;
}

void StuckRecoveryController::publishCommand(float speed, float acceleration)
{
  const auto stamp = this->now();
  AckermannControlCommand msg;
  msg.stamp = stamp;
  msg.lateral.stamp = stamp;
  msg.lateral.steering_tire_angle = 0.0;
  msg.lateral.steering_tire_rotation_rate = 2.0;
  msg.longitudinal.stamp = stamp;
  msg.longitudinal.speed = speed;
  msg.longitudinal.acceleration = acceleration;
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
