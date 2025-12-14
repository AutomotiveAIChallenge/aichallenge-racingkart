#ifndef TELEOP_MANAGER_NODE_HPP_
#define TELEOP_MANAGER_NODE_HPP_

#include <chrono>
#include <memory>

#include "rclcpp/rclcpp.hpp"
#include "sensor_msgs/msg/joy.hpp"
#include "autoware_auto_control_msgs/msg/ackermann_control_command.hpp"
#include "std_msgs/msg/bool.hpp"
#include "std_msgs/msg/empty.hpp"
#include "geometry_msgs/msg/pose_with_covariance_stamped.hpp"
#include "std_msgs/msg/float32_multi_array.hpp"

class TeleopManagerNode : public rclcpp::Node
{
public:
  TeleopManagerNode();

private:
  bool check_button_press(bool curr, bool &prev_flag);

  // Callbacks
  void status_callback(const std_msgs::msg::Float32MultiArray::SharedPtr msg);
  void joy_callback(const sensor_msgs::msg::Joy::SharedPtr msg);
  void ack_callback(const autoware_auto_control_msgs::msg::AckermannControlCommand::SharedPtr msg);
  void timer_callback();

  // --- Member Variables ---
  rclcpp::Subscription<sensor_msgs::msg::Joy>::SharedPtr           joy_sub_;
  rclcpp::Subscription<autoware_auto_control_msgs::msg::AckermannControlCommand>::SharedPtr ack_sub_;
  rclcpp::Subscription<std_msgs::msg::Float32MultiArray>::SharedPtr status_sub_;
  rclcpp::Publisher<autoware_auto_control_msgs::msg::AckermannControlCommand>::SharedPtr drive_pub_;
  rclcpp::Publisher<std_msgs::msg::Bool>::SharedPtr                trigger_pub_;
  rclcpp::Publisher<std_msgs::msg::Bool>::SharedPtr                awsim_trigger_pub_;
  rclcpp::Publisher<std_msgs::msg::Empty>::SharedPtr               reset_publisher_;
  rclcpp::Publisher<geometry_msgs::msg::PoseWithCovarianceStamped>::SharedPtr initialpose_publisher_;
  rclcpp::TimerBase::SharedPtr                                     timer_;
  geometry_msgs::msg::PoseWithCovarianceStamped reset_pose_msg_;

  // Parameters
  double speed_scale_, steer_scale_;
  int joy_button_index_, ack_button_index_;
  int start_button_index_, stop_button_index_;
  int awsim_button_index_;
  int reset_button_index_;
  int dpad_lr_axis_index_;
  int dpad_ud_axis_index_;
  double timer_hz_;
  double joy_timeout_sec_;

  // State
  bool joy_active_, ack_active_;
  double joy_speed_, joy_steer_;
  float current_lap_;
  autoware_auto_control_msgs::msg::AckermannControlCommand last_autonomy_msg_;
  bool ack_received_{false};
  rclcpp::Time last_joy_msg_time_;

  // Debounce flags
  bool prev_start_pressed_, prev_stop_pressed_;
  bool prev_awsim_button_pressed_;
  bool prev_reset_button_pressed_;
  bool prev_steer_scale_inc_pressed_;
  bool prev_steer_scale_dec_pressed_;
  bool prev_speed_scale_inc_pressed_;
  bool prev_speed_scale_dec_pressed_;
};

#endif  // TELEOP_MANAGER_NODE_HPP_
