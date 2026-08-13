#include "racing_kart_sim_adapter/arbiter_core.hpp"

#include <rclcpp/rclcpp.hpp>

#include <autoware_auto_control_msgs/msg/ackermann_control_command.hpp>
#include <autoware_auto_vehicle_msgs/msg/gear_command.hpp>
#include <racing_kart_msgs/msg/vehicle_debug.hpp>
#include <sensor_msgs/msg/joy.hpp>

#include <algorithm>
#include <memory>

namespace racing_kart_sim_adapter
{

// Bridges racing_kart's joy protocol to AWSIM. Subscribes /racing_kart/joy and the real
// control_method's output (remapped away from /control/command/control_cmd by the launch file
// so this node can own that topic), runs it through ArbiterCore, and republishes the arbitrated
// result on the topics AWSIM actually consumes. Ratio<->physical-unit scaling (max_*_ params
// below) is a rough placeholder -- see ../README.md.
class AdapterNode : public rclcpp::Node
{
  using AckermannControlCommand = autoware_auto_control_msgs::msg::AckermannControlCommand;
  using GearCommand = autoware_auto_vehicle_msgs::msg::GearCommand;
  using VehicleDebug = racing_kart_msgs::msg::VehicleDebug;

public:
  AdapterNode()
  : Node("racing_kart_sim_adapter")
  {
    // Matches teleop_manager_node's approach: this stack runs on AWSIM's /clock, and relying on
    // the launch file's use_sim_time param alone has been unreliable here in practice.
    this->set_parameter(rclcpp::Parameter("use_sim_time", true));

    const double joy_delay_threshold = declare_parameter<double>("joy_delay_threshold", 5.0);
    max_accel_mps2_ = declare_parameter<double>("max_accel_mps2", 3.0);
    max_decel_mps2_ = declare_parameter<double>("max_decel_mps2", 3.0);
    max_steer_rad_ = declare_parameter<double>("max_steer_rad", 0.5);

    core_ = std::make_unique<ArbiterCore>(joy_delay_threshold);

    joy_sub_ = create_subscription<sensor_msgs::msg::Joy>(
      "/racing_kart/joy", rclcpp::QoS(1),
      [this](sensor_msgs::msg::Joy::ConstSharedPtr msg) { on_joy(*msg); });

    autoware_cmd_sub_ = create_subscription<AckermannControlCommand>(
      "/ackermann_cmd", rclcpp::QoS(1),
      [this](AckermannControlCommand::ConstSharedPtr msg) { on_autoware_cmd(*msg); });

    autoware_gear_sub_ = create_subscription<GearCommand>(
      "/ackermann_gear_cmd", rclcpp::QoS(1),
      [this](GearCommand::ConstSharedPtr msg) { autoware_gear_command_ = msg->command; });

    control_cmd_pub_ =
      create_publisher<AckermannControlCommand>("/control/command/control_cmd", rclcpp::QoS(1));
    gear_cmd_pub_ = create_publisher<GearCommand>("/control/command/gear_cmd", rclcpp::QoS(1));

    // racing_kart_manager は停止プロトコルでこの emergency を見て、緊急停止が効いたことを
    // 確認してから joy の送信を止める。実車では racing_kart_driver が同じトピックへ出す
    // (racing_kart_driver_node.cpp:50)。出さないと manager 側は emergency を永久に UNKNOWN と
    // 判定し、全操作が塞がれる。
    debug_status_pub_ =
      create_publisher<VehicleDebug>("/racing_kart/debug/status", rclcpp::QoS(1));

    const auto period = rclcpp::Rate(20.0).period();
    timer_ = rclcpp::create_timer(this, get_clock(), period, [this]() { on_timer(); });
  }

private:
  void on_joy(const sensor_msgs::msg::Joy & msg)
  {
    JoyFrame joy;
    joy.buttons.assign(msg.buttons.begin(), msg.buttons.end());
    joy.axes = msg.axes;
    core_->on_joy(joy, now().seconds());
  }

  void on_autoware_cmd(const AckermannControlCommand & msg)
  {
    autoware_cmd_received_ = true;
    latest_autoware_accel_mps2_ = msg.longitudinal.acceleration;
    latest_autoware_steer_rad_ = msg.lateral.steering_tire_angle;
  }

  void on_timer()
  {
    AutowareCommand cmd;
    cmd.available = autoware_cmd_received_;
    cmd.accel_ratio =
      static_cast<float>(std::clamp(latest_autoware_accel_mps2_ / max_accel_mps2_, 0.0, 1.0));
    cmd.brake_ratio =
      static_cast<float>(std::clamp(-latest_autoware_accel_mps2_ / max_decel_mps2_, 0.0, 1.0));
    cmd.steer_ratio =
      static_cast<float>(std::clamp(latest_autoware_steer_rad_ / max_steer_rad_, -1.0, 1.0));
    cmd.gear_command = autoware_gear_command_;

    const auto out = core_->step(now().seconds(), cmd);
    const auto stamp = now();

    AckermannControlCommand control_cmd;
    control_cmd.stamp = stamp;
    control_cmd.lateral.stamp = stamp;
    control_cmd.lateral.steering_tire_angle = static_cast<float>(out.steer_ratio * max_steer_rad_);
    control_cmd.longitudinal.stamp = stamp;
    control_cmd.longitudinal.acceleration = static_cast<float>(
      (out.accel_ratio - out.brake_ratio) * std::max(max_accel_mps2_, max_decel_mps2_));
    control_cmd_pub_->publish(control_cmd);

    GearCommand gear_cmd;
    gear_cmd.stamp = stamp;
    gear_cmd.command = gear_char_to_command(out.gear);
    gear_cmd_pub_->publish(gear_cmd);

    // VCU 固有のフィールド (throttle / brake_torque / steer_position / steer_original) は
    // sim に対応する実体が無いので 0 のままにする。manager が見るのは emergency だけ。
    VehicleDebug debug;
    debug.stamp = stamp;
    debug.accel_ratio = out.accel_ratio;
    debug.brake_ratio = out.brake_ratio;
    debug.steer_ratio = out.steer_ratio;
    debug.emergency = out.is_emergency;
    debug_status_pub_->publish(debug);
  }

  static uint8_t gear_char_to_command(char gear)
  {
    switch (gear) {
      case 'D':
        return GearCommand::DRIVE;
      case 'R':
        return GearCommand::REVERSE;
      default:
        return GearCommand::NEUTRAL;
    }
  }

  std::unique_ptr<ArbiterCore> core_;
  double max_accel_mps2_ = 3.0;
  double max_decel_mps2_ = 3.0;
  double max_steer_rad_ = 0.5;

  bool autoware_cmd_received_ = false;
  double latest_autoware_accel_mps2_ = 0.0;
  double latest_autoware_steer_rad_ = 0.0;
  uint8_t autoware_gear_command_ = GearCommand::NONE;

  rclcpp::Subscription<sensor_msgs::msg::Joy>::SharedPtr joy_sub_;
  rclcpp::Subscription<AckermannControlCommand>::SharedPtr autoware_cmd_sub_;
  rclcpp::Subscription<GearCommand>::SharedPtr autoware_gear_sub_;
  rclcpp::Publisher<AckermannControlCommand>::SharedPtr control_cmd_pub_;
  rclcpp::Publisher<GearCommand>::SharedPtr gear_cmd_pub_;
  rclcpp::Publisher<VehicleDebug>::SharedPtr debug_status_pub_;
  rclcpp::TimerBase::SharedPtr timer_;
};

}  // namespace racing_kart_sim_adapter

int main(int argc, char * argv[])
{
  rclcpp::init(argc, argv);
  rclcpp::spin(std::make_shared<racing_kart_sim_adapter::AdapterNode>());
  rclcpp::shutdown();
  return 0;
}
