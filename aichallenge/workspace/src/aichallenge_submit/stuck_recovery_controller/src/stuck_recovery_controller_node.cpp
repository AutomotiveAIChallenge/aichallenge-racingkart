#include <rclcpp/rclcpp.hpp>

#include <autoware_auto_control_msgs/msg/ackermann_control_command.hpp>
#include <autoware_auto_vehicle_msgs/msg/gear_command.hpp>
#include <autoware_auto_vehicle_msgs/msg/gear_report.hpp>
#include <autoware_auto_vehicle_msgs/msg/velocity_report.hpp>
#include <std_msgs/msg/string.hpp>

#include <cmath>
#include <memory>
#include <optional>
#include <string>

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

// Final control mux that backs up when forward throttle cannot move the kart.
// In pass-through states (NORMAL / COOLDOWN) the latest nominal command is
// republished the moment it arrives, so the mux is transparent at full rate and
// safe to keep always running. When no nominal is being published (e.g. a
// different controller is active) the node stays silent and never touches the
// final command topics.
class StuckRecoveryController : public rclcpp::Node
{
public:
  StuckRecoveryController() : Node("stuck_recovery_controller")
  {
    control_pub_ =
      create_publisher<AckermannControlCommand>("/control/command/control_cmd", 1);
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

    // Node-clock timer so state timing follows sim time when use_sim_time is set.
    timer_ = rclcpp::create_timer(
      this, get_clock(), rclcpp::Duration::from_seconds(1.0 / kTimerHz),
      std::bind(&StuckRecoveryController::onTimer, this));

    RCLCPP_INFO(get_logger(), "stuck_recovery_controller started");
  }

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

  double nowSec() { return get_clock()->now().seconds(); }

  static bool isPassThrough(RecoveryState state)
  {
    return state == RecoveryState::NORMAL || state == RecoveryState::COOLDOWN;
  }

  void onNominal(const AckermannControlCommand::SharedPtr msg)
  {
    latest_nominal_ = msg;
    latest_nominal_time_ = nowSec();
    // Transparent, full-rate pass-through while we are not actively recovering.
    if (isPassThrough(state_)) {
      publishNominal();
    }
  }

  void onVelocity(const VelocityReport::SharedPtr msg)
  {
    latest_velocity_ = static_cast<double>(msg->longitudinal_velocity);
    latest_velocity_time_ = nowSec();
  }

  void onGear(const GearReport::SharedPtr msg) { latest_gear_ = msg->report; }

  void onTimer()
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

  void runNormal(double now)
  {
    if (!hasFreshNominal(now)) {
      // No nominal source: stay silent so another controller can own the topic.
      stuck_start_time_.reset();
      return;
    }
    publishGear(GearCommand::DRIVE);  // control command is republished by onNominal()

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

  void runStuckDetected(double now)
  {
    publishGear(GearCommand::DRIVE);
    publishCommand(0.0, 0.0, 0.0);
    if (now - state_enter_time_ >= 0.2) {
      setState(RecoveryState::REVERSING, now);
    }
  }

  void runReversing(double now)
  {
    publishGear(GearCommand::REVERSE);
    // AWSIM expects a positive drive acceleration while the gear and target speed are reverse.
    publishCommand(-std::abs(kReverseSpeed), std::abs(kReverseAccel), 0.0);
    if (now - state_enter_time_ >= kReverseDuration) {
      setState(RecoveryState::DRIVE_SETTLE, now);
    }
  }

  void runDriveSettle(double now)
  {
    publishGear(GearCommand::DRIVE);
    publishCommand(0.0, 0.0, 0.0);
    if (now - state_enter_time_ >= kDriveSettleDuration) {
      setState(RecoveryState::COOLDOWN, now);
    }
  }

  void runCooldown(double now)
  {
    publishGear(GearCommand::DRIVE);  // control command is republished by onNominal()
    if (now - state_enter_time_ >= kCooldownDuration) {
      moving_observed_ = false;
      stuck_start_time_.reset();
      setState(RecoveryState::NORMAL, now);
    }
  }

  void startRecovery(double now)
  {
    ++attempt_count_;
    stuck_start_time_.reset();
    RCLCPP_WARN(
      get_logger(), "stuck under throttle detected: attempt=%d velocity=%.3f", attempt_count_,
      latest_velocity_.value_or(0.0));
    setState(RecoveryState::STUCK_DETECTED, now);
  }

  void setState(RecoveryState state, double now)
  {
    if (state == state_) {
      return;
    }
    RCLCPP_INFO(get_logger(), "state transition: %s -> %s", toString(state_), toString(state));
    state_ = state;
    state_enter_time_ = now;
    publishState();
  }

  bool hasFreshNominal(double now) const
  {
    return latest_nominal_ != nullptr && latest_nominal_time_.has_value() &&
           now - latest_nominal_time_.value() <= kNominalTimeoutSec;
  }

  bool hasFreshVelocity(double now) const
  {
    return latest_velocity_.has_value() && latest_velocity_time_.has_value() &&
           now - latest_velocity_time_.value() <= kVelocityTimeoutSec;
  }

  bool isForwardRequest(const AckermannControlCommand::SharedPtr & cmd) const
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

  void publishNominal()
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

  void publishCommand(double speed, double acceleration, double steer)
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

  void publishGear(uint8_t command)
  {
    GearCommand msg;
    msg.stamp = get_clock()->now();
    msg.command = command;
    gear_pub_->publish(msg);
  }

  void publishState()
  {
    std_msgs::msg::String msg;
    msg.data = toString(state_);
    state_pub_->publish(msg);
  }

  // interfaces
  rclcpp::Publisher<AckermannControlCommand>::SharedPtr control_pub_;
  rclcpp::Publisher<GearCommand>::SharedPtr gear_pub_;
  rclcpp::Publisher<std_msgs::msg::String>::SharedPtr state_pub_;
  rclcpp::Subscription<AckermannControlCommand>::SharedPtr nominal_sub_;
  rclcpp::Subscription<VelocityReport>::SharedPtr velocity_sub_;
  rclcpp::Subscription<GearReport>::SharedPtr gear_sub_;
  rclcpp::TimerBase::SharedPtr timer_;

  // state
  RecoveryState state_{RecoveryState::NORMAL};
  double state_enter_time_{0.0};
  AckermannControlCommand::SharedPtr latest_nominal_;
  std::optional<double> latest_nominal_time_;
  std::optional<double> latest_velocity_;
  std::optional<double> latest_velocity_time_;
  std::optional<uint8_t> latest_gear_;
  bool moving_observed_{false};
  std::optional<double> stuck_start_time_;
  int attempt_count_{0};
};

}  // namespace stuck_recovery_controller

int main(int argc, char ** argv)
{
  rclcpp::init(argc, argv);
  rclcpp::spin(std::make_shared<stuck_recovery_controller::StuckRecoveryController>());
  rclcpp::shutdown();
  return 0;
}
