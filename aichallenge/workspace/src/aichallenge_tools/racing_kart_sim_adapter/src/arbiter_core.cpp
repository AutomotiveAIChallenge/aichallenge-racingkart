#include "racing_kart_sim_adapter/arbiter_core.hpp"
#include "racing_kart_sim_adapter/keybind.hpp"

#include <algorithm>

// Ports racing_kart_driver_node.cpp's on_joy()/on_timer() (racing_kart_interface repo) into a
// ROS-independent state machine. Line numbers in comments refer to that file; keep this in sync
// by hand if it changes. See ../README.md for the deliberate differences from the original.

namespace racing_kart_sim_adapter
{

namespace
{
using GearCommand = autoware_auto_vehicle_msgs::msg::GearCommand;
namespace Joy = Keybind::Joystick;
}  // namespace

ArbiterCore::ArbiterCore(double joy_delay_threshold_sec)
: joy_delay_threshold_sec_(joy_delay_threshold_sec)
{
}

void ArbiterCore::on_joy(const JoyFrame & joy, double received_time_sec)
{
  input_ = joy;
  last_received_time_joy_ = received_time_sec;
}

ArbiterOutput ArbiterCore::stop_output() const
{
  ArbiterOutput out;
  out.mode = mode_;
  out.is_emergency = is_emergency_;
  out.accel_ratio = 0.0f;
  out.brake_ratio = 1.0f;
  out.steer_ratio = 0.0f;
  out.gear = 'N';
  return out;
}

ArbiterOutput ArbiterCore::step(double now_sec, const AutowareCommand & autoware_cmd)
{
  // on_timer():191-199 -- stale joy forces emergency and drops the input.
  if (last_received_time_joy_.has_value()) {
    const double joy_delay = now_sec - *last_received_time_joy_;
    if (joy_delay_threshold_sec_ < joy_delay) {
      is_emergency_ = true;
      input_ = JoyFrame{};
    }
  }

  // on_timer():184-189,216-224
  const bool joystick_available =
    input_.buttons.size() == Joy::NumButtons && input_.axes.size() == Joy::NumAxes;
  if (!joystick_available) {
    ArbiterOutput out = stop_output();
    out.joystick_available = false;
    return out;
  }

  // on_timer():227-238
  if (input_.buttons[Joy::ButtonLB]) is_emergency_ = true;
  if (input_.buttons[Joy::ButtonRB]) is_emergency_ = true;
  if (input_.buttons[Joy::ButtonStart]) is_emergency_ = true;
  if (input_.buttons[Joy::ButtonBack]) is_emergency_ = true;

  // on_timer():241-249 -- no button pressed keeps the previous mode_ (not a one-shot).
  if (is_emergency_) {
    mode_ = ControlMode::kManual;
  } else if (input_.buttons[Joy::ButtonA]) {
    mode_ = ControlMode::kManual;
  } else if (input_.buttons[Joy::ButtonX]) {
    mode_ = ControlMode::kAutonomousSteerOnly;
  } else if (input_.buttons[Joy::ButtonY]) {
    mode_ = ControlMode::kAutonomous;
  }

  // on_timer():251-271
  if (mode_ == ControlMode::kAutonomous) {
    if (autoware_cmd.gear_command == GearCommand::NEUTRAL) {
      gear_request_ = 'N';
    } else if (autoware_cmd.gear_command == GearCommand::DRIVE) {
      gear_request_ = 'D';
    } else if (autoware_cmd.gear_command == GearCommand::REVERSE) {
      gear_request_ = 'R';
    } else {
      gear_request_ = 'D';
    }
  } else {
    if (input_.axes[Joy::DpadHorizontal] == +1.0f) {
      gear_request_ = 'N';
    } else if (input_.axes[Joy::DpadVertical] == +1.0f) {
      gear_request_ = 'D';
    } else if (input_.axes[Joy::DpadVertical] == -1.0f) {
      gear_request_ = 'R';
    }
  }

  // on_timer():277-281 -- checked after mode_ is decided, so clearing emergency here can still
  // produce a live (non-stop) output on this same tick.
  const bool lsb = input_.buttons[Joy::ButtonLeftStick];
  const bool rsb = input_.buttons[Joy::ButtonRightStick];
  if (lsb && rsb) {
    is_emergency_ = false;
  }

  // on_timer():283-290
  if (is_emergency_) {
    ArbiterOutput out = stop_output();
    out.joystick_available = true;
    return out;
  }

  // publish_vcu_command():343-363 / publish_brake_command():365-383 -- accel and brake switch to
  // the Autoware-sourced ratio only in full kAutonomous (AUTONOMOUS_STEER_ONLY keeps both on the
  // joystick). "available == false" is a deliberate safe-zero deviation from the upstream
  // unconditional std::optional dereference; see ../README.md.
  float accel = std::clamp((1.0f - input_.axes[Keybind::Accel]) / 2.0f, 0.0f, 1.0f);
  float brake = std::clamp((1.0f - input_.axes[Keybind::Brake]) / 2.0f, 0.0f, 1.0f);
  if (mode_ == ControlMode::kAutonomous) {
    accel = autoware_cmd.available ? std::clamp(autoware_cmd.accel_ratio, 0.0f, 1.0f) : 0.0f;
    brake = autoware_cmd.available ? std::clamp(autoware_cmd.brake_ratio, 0.0f, 1.0f) : 0.0f;
  }

  // publish_steer_command():385-408 -- steer switches to Autoware in kAutonomous AND
  // kAutonomousSteerOnly (unlike accel/brake above), with a narrower clamp range.
  float steer = std::clamp(input_.axes[Keybind::Steer], -0.9f, 0.9f);
  if (mode_ == ControlMode::kAutonomous || mode_ == ControlMode::kAutonomousSteerOnly) {
    steer = autoware_cmd.available ? std::clamp(autoware_cmd.steer_ratio, -0.8f, 0.8f) : 0.0f;
  }

  ArbiterOutput out;
  out.mode = mode_;
  out.is_emergency = false;
  out.joystick_available = true;
  out.accel_ratio = accel;
  out.brake_ratio = brake;
  out.steer_ratio = steer;
  out.gear = gear_request_;
  return out;
}

}  // namespace racing_kart_sim_adapter
