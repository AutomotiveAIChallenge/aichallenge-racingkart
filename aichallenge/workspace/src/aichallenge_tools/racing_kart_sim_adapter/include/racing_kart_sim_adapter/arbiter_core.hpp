#ifndef RACING_KART_SIM_ADAPTER__ARBITER_CORE_HPP_
#define RACING_KART_SIM_ADAPTER__ARBITER_CORE_HPP_

#include <autoware_auto_vehicle_msgs/msg/gear_command.hpp>

#include <cstdint>
#include <optional>
#include <vector>

namespace racing_kart_sim_adapter
{

enum class ControlMode
{
  kManual,
  kAutonomousSteerOnly,
  kAutonomous,
};

// Plain snapshot of a sensor_msgs/Joy frame. Kept ROS-message-free so this header has no rclcpp
// dependency and can be unit tested without a ROS graph.
struct JoyFrame
{
  std::vector<uint8_t> buttons;
  std::vector<float> axes;
};

// What the real Autoware control stack currently wants, already normalized to the same ratio
// space the joystick axes use. Ratio normalization (AckermannControlCommand <-> ratio) is a
// node-level concern (physical limits are sim-vehicle-specific tuning), not part of this core.
struct AutowareCommand
{
  // False until the adapter node has ever received a control_cmd/gear_cmd from Autoware this
  // session. racing_kart_driver_node dereferences its optional unconditionally in AUTONOMOUS
  // (a latent bug there); this core deliberately treats "unavailable" as a safe zero command
  // instead of reproducing that undefined behavior.
  bool available = false;
  float accel_ratio = 0.0f;  // [0, 1]
  float brake_ratio = 0.0f;  // [0, 1]
  float steer_ratio = 0.0f;  // [-1, 1]
  uint8_t gear_command = autoware_auto_vehicle_msgs::msg::GearCommand::NEUTRAL;
};

struct ArbiterOutput
{
  ControlMode mode = ControlMode::kManual;
  bool is_emergency = true;
  // False when the last received joy frame doesn't have the expected 11 buttons / 8 axes (or
  // none has ever arrived). Mirrors racing_kart_driver_node's is_joystick_available().
  bool joystick_available = false;
  float accel_ratio = 0.0f;  // [0, 1]
  float brake_ratio = 1.0f;  // [0, 1], stop default is full brake
  float steer_ratio = 0.0f;  // [-1, 1]
  char gear = 'N';           // 'N' / 'D' / 'R'
};

// Reimplements racing_kart_driver_node.cpp's on_joy()/on_timer() joystick arbitration state
// machine (mode switching, emergency latch, axis interpretation), retargeted to output
// normalized ratios instead of VCU-specific hardware commands. See
// racing_kart_interface/src/racing_kart_driver/src/racing_kart_driver_node.cpp for the source
// of truth this is meant to stay behaviorally identical to.
class ArbiterCore
{
public:
  explicit ArbiterCore(double joy_delay_threshold_sec = 5.0);

  // Call on every received joy message. received_time_sec must be monotonically non-decreasing.
  void on_joy(const JoyFrame & joy, double received_time_sec);

  // Call once per control tick (mirrors on_timer(), nominally 20 Hz). now_sec must use the same
  // clock/epoch as the received_time_sec values passed to on_joy().
  ArbiterOutput step(double now_sec, const AutowareCommand & autoware_cmd);

private:
  ArbiterOutput stop_output() const;

  double joy_delay_threshold_sec_;
  JoyFrame input_;
  std::optional<double> last_received_time_joy_;
  bool is_emergency_ = true;
  ControlMode mode_ = ControlMode::kManual;
  char gear_request_ = 'D';
};

}  // namespace racing_kart_sim_adapter

#endif  // RACING_KART_SIM_ADAPTER__ARBITER_CORE_HPP_
