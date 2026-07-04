#ifndef SIMPLE_PURE_PURSUIT_HPP_
#define SIMPLE_PURE_PURSUIT_HPP_

#include <autoware_auto_control_msgs/msg/ackermann_control_command.hpp>
#include <autoware_auto_planning_msgs/msg/trajectory.hpp>
#include <autoware_auto_planning_msgs/msg/trajectory_point.hpp>
#include <geometry_msgs/msg/pose.hpp>
#include <geometry_msgs/msg/twist.hpp>
#include <geometry_msgs/msg/point_stamped.hpp>
#include <nav_msgs/msg/odometry.hpp>
#include <optional>
#include <rclcpp/rclcpp.hpp>
#include <tuple>

namespace simple_pure_pursuit {

using autoware_auto_control_msgs::msg::AckermannControlCommand;
using autoware_auto_planning_msgs::msg::Trajectory;
using autoware_auto_planning_msgs::msg::TrajectoryPoint;
using geometry_msgs::msg::Pose;
using geometry_msgs::msg::PointStamped;
using geometry_msgs::msg::Twist;
using nav_msgs::msg::Odometry;

class SimplePurePursuit : public rclcpp::Node {
 public:
  explicit SimplePurePursuit();
  
  // subscribers
  rclcpp::Subscription<Odometry>::SharedPtr sub_kinematics_;
  rclcpp::Subscription<Trajectory>::SharedPtr sub_trajectory_;
  
  // publishers
  rclcpp::Publisher<AckermannControlCommand>::SharedPtr pub_cmd_;
  rclcpp::Publisher<AckermannControlCommand>::SharedPtr pub_raw_cmd_;
  rclcpp::Publisher<PointStamped>::SharedPtr pub_lookahead_point_;  

  // timer
  rclcpp::TimerBase::SharedPtr timer_;

  // updated by subscribers
  Trajectory::SharedPtr trajectory_;
  Odometry::SharedPtr odometry_;
  bool has_previous_steering_tire_angle_{false};
  double previous_steering_tire_angle_{0.0};



  // pure pursuit parameters
  const double wheel_base_;
  const double lookahead_gain_;
  const double lookahead_min_distance_;
  const double speed_proportional_gain_;
  const bool use_external_target_vel_;
  const double external_target_vel_;
  const double steering_tire_angle_gain_;
  const double steering_tire_angle_offset_;
  const double steering_tire_rotation_rate_;
  const double max_steering_tire_angle_;
  const double max_linear_vel_;
  const double min_linear_vel_;
  const double max_angular_vel_;
  const double min_angular_vel_;
  const double max_linear_accel_;
  const double max_linear_decel_;
  const double max_angular_accel_;
  const double max_angular_decel_;
  const double control_period_;
  const bool use_dynamic_window_;
  const bool use_racing_dynamic_window_;
  const bool use_curvature_speed_control_;
  const double max_lateral_accel_;
  const double braking_decel_;
  const double speed_preview_distance_;
  const double min_curve_velocity_;
  const double tracking_error_velocity_;
  const double lateral_error_brake_threshold_;
  const double yaw_error_brake_threshold_;

  struct DynamicWindowBounds
  {
    double max_linear_vel;
    double min_linear_vel;
    double max_angular_vel;
    double min_angular_vel;
  };

 private:
  void onTimer();
  bool subscribeMessageAvailable();
  double applySteeringRateLimit(const double target_steering_tire_angle);
  double applyTrackingErrorSpeedLimit(
    const double target_velocity, const TrajectoryPoint & closest_point,
    const double current_yaw) const;
  double applyCurvatureSpeedLimit(
    const double target_velocity, const size_t closest_index, const double current_velocity) const;
  double curvatureAt(const size_t index) const;
  DynamicWindowBounds computeDynamicWindow(const Twist & current_speed) const;
  void applyRegulationToDynamicWindow(
    const double regulated_linear_vel, DynamicWindowBounds & dynamic_window) const;
  std::tuple<double, double> computeOptimalVelocityWithinDynamicWindow(
    const DynamicWindowBounds & dynamic_window, const double curvature, const double sign) const;
  std::tuple<double, double> computeDynamicWindowVelocities(
    const Twist & current_speed, const double regulated_linear_vel, const double curvature,
    const double sign) const;
};

}  // namespace simple_pure_pursuit

#endif  // SIMPLE_PURE_PURSUIT_HPP_
