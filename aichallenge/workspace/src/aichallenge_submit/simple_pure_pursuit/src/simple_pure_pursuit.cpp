#include "simple_pure_pursuit/simple_pure_pursuit.hpp"

#include <motion_utils/motion_utils.hpp>
#include <tier4_autoware_utils/tier4_autoware_utils.hpp>

#include <tf2/utils.h>

#include <algorithm>
#include <array>
#include <limits>

namespace simple_pure_pursuit
{

using motion_utils::findNearestIndex;
using tier4_autoware_utils::calcLateralDeviation;
using tier4_autoware_utils::calcYawDeviation;

SimplePurePursuit::SimplePurePursuit()
: Node("simple_pure_pursuit"),
  // initialize parameters
  wheel_base_(declare_parameter<float>("wheel_base", 1.087)),
  lookahead_gain_(declare_parameter<float>("lookahead_gain", 1.0)),
  lookahead_min_distance_(declare_parameter<float>("lookahead_min_distance", 1.0)),
  speed_proportional_gain_(declare_parameter<float>("speed_proportional_gain", 1.0)),
  use_external_target_vel_(declare_parameter<bool>("use_external_target_vel", false)),
  external_target_vel_(declare_parameter<float>("external_target_vel", 0.0)),
  steering_tire_angle_gain_(declare_parameter<float>("steering_tire_angle_gain", 1.0)),
  steering_tire_angle_offset_(declare_parameter<float>("steering_tire_angle_offset", 0.0)),
  steering_tire_rotation_rate_(declare_parameter<float>("steering_tire_rotation_rate", 2.0)),
  max_steering_tire_angle_(declare_parameter<float>("max_steering_tire_angle", 0.64)),
  max_linear_vel_(declare_parameter<float>("max_linear_vel", 8.3)),
  min_linear_vel_(declare_parameter<float>("min_linear_vel", 0.0)),
  max_angular_vel_(declare_parameter<float>("max_angular_vel", 2.5)),
  min_angular_vel_(declare_parameter<float>("min_angular_vel", -2.5)),
  max_linear_accel_(declare_parameter<float>("max_linear_accel", 4.0)),
  max_linear_decel_(declare_parameter<float>("max_linear_decel", -6.0)),
  max_angular_accel_(declare_parameter<float>("max_angular_accel", 5.0)),
  max_angular_decel_(declare_parameter<float>("max_angular_decel", -5.0)),
  control_period_(declare_parameter<float>("control_period", 0.01)),
  use_dynamic_window_(declare_parameter<bool>("use_dynamic_window", true)),
  use_racing_dynamic_window_(declare_parameter<bool>("use_racing_dynamic_window", true)),
  use_curvature_speed_control_(declare_parameter<bool>("use_curvature_speed_control", true)),
  max_lateral_accel_(declare_parameter<float>("max_lateral_accel", 4.0)),
  braking_decel_(declare_parameter<float>("braking_decel", 2.5)),
  speed_preview_distance_(declare_parameter<float>("speed_preview_distance", 18.0)),
  min_curve_velocity_(declare_parameter<float>("min_curve_velocity", 2.0)),
  tracking_error_velocity_(declare_parameter<float>("tracking_error_velocity", 1.0)),
  lateral_error_brake_threshold_(declare_parameter<float>("lateral_error_brake_threshold", 1.0)),
  yaw_error_brake_threshold_(declare_parameter<float>("yaw_error_brake_threshold", 0.55))
{
  pub_cmd_ = create_publisher<AckermannControlCommand>("output/control_cmd", 1);
  pub_raw_cmd_ = create_publisher<AckermannControlCommand>("output/raw_control_cmd", 1);
  pub_lookahead_point_ = create_publisher<PointStamped>("/control/debug/lookahead_point", 1);

  const auto bv_qos = rclcpp::QoS(rclcpp::KeepLast(1)).durability_volatile().best_effort();
  sub_kinematics_ = create_subscription<Odometry>(
    "input/kinematics", bv_qos, [this](const Odometry::SharedPtr msg) { odometry_ = msg; });
  sub_trajectory_ = create_subscription<Trajectory>(
    "input/trajectory", bv_qos, [this](const Trajectory::SharedPtr msg) { trajectory_ = msg; });

  using namespace std::literals::chrono_literals;
  timer_ = create_wall_timer(10ms, std::bind(&SimplePurePursuit::onTimer, this));
}

AckermannControlCommand zeroAckermannControlCommand(rclcpp::Time stamp)
{
  AckermannControlCommand cmd;
  cmd.stamp = stamp;
  cmd.longitudinal.stamp = stamp;
  cmd.longitudinal.speed = 0.0;
  cmd.longitudinal.acceleration = 0.0;
  cmd.lateral.stamp = stamp;
  cmd.lateral.steering_tire_angle = 0.0;
  cmd.lateral.steering_tire_rotation_rate = 0.0;
  return cmd;
}

void SimplePurePursuit::onTimer()
{
  // check data
  if (!subscribeMessageAvailable()) {
    return;
  }

  size_t closet_traj_point_idx =
    findNearestIndex(trajectory_->points, odometry_->pose.pose.position);

  // publish zero command
  AckermannControlCommand cmd = zeroAckermannControlCommand(get_clock()->now());

  // get closest trajectory point from current position
  TrajectoryPoint closet_traj_point = trajectory_->points.at(closet_traj_point_idx);

  // calc longitudinal speed and acceleration
  double target_longitudinal_vel =
    use_external_target_vel_ ? external_target_vel_ : closet_traj_point.longitudinal_velocity_mps;
  double current_longitudinal_vel = odometry_->twist.twist.linear.x;
  const double current_yaw = tf2::getYaw(odometry_->pose.pose.orientation);
  target_longitudinal_vel = applyTrackingErrorSpeedLimit(
    target_longitudinal_vel, closet_traj_point, current_yaw);
  target_longitudinal_vel = applyCurvatureSpeedLimit(
    target_longitudinal_vel, closet_traj_point_idx, current_longitudinal_vel);

  // calc lateral control
  //// calc lookahead distance
  double lookahead_distance = lookahead_gain_ * target_longitudinal_vel + lookahead_min_distance_;
  //// calc center coordinate of rear wheel
  double rear_x = odometry_->pose.pose.position.x - wheel_base_ / 2.0 * std::cos(current_yaw);
  double rear_y = odometry_->pose.pose.position.y - wheel_base_ / 2.0 * std::sin(current_yaw);
  //// search lookahead point
  auto lookahead_point_itr = std::find_if(
    trajectory_->points.begin() + closet_traj_point_idx, trajectory_->points.end(),
    [&](const TrajectoryPoint & point) {
      return std::hypot(point.pose.position.x - rear_x, point.pose.position.y - rear_y) >=
             lookahead_distance;
    });
  if (lookahead_point_itr == trajectory_->points.end()) {
    lookahead_point_itr = std::prev(trajectory_->points.end());
  }
  double lookahead_point_x = lookahead_point_itr->pose.position.x;
  double lookahead_point_y = lookahead_point_itr->pose.position.y;

  geometry_msgs::msg::PointStamped lookahead_point_msg;
  lookahead_point_msg.header.stamp = get_clock()->now();
  lookahead_point_msg.header.frame_id = "map";
  lookahead_point_msg.point.x = lookahead_point_x;
  lookahead_point_msg.point.y = lookahead_point_y;
  lookahead_point_msg.point.z = closet_traj_point.pose.position.z;
  pub_lookahead_point_->publish(lookahead_point_msg);

  // calc steering angle for lateral control
  double alpha = std::atan2(lookahead_point_y - rear_y, lookahead_point_x - rear_x) - current_yaw;
  double curvature = 2.0 * std::sin(alpha) / std::max(lookahead_distance, 1.0e-3);
  double target_angular_vel = curvature * target_longitudinal_vel;

  if (use_dynamic_window_) {
    std::tie(target_longitudinal_vel, target_angular_vel) = computeDynamicWindowVelocities(
      odometry_->twist.twist, target_longitudinal_vel, curvature, 1.0);
  }

  cmd.longitudinal.speed = target_longitudinal_vel;
  const double target_acceleration =
    speed_proportional_gain_ * (target_longitudinal_vel - current_longitudinal_vel) /
    std::max(control_period_, 1.0e-3);
  cmd.longitudinal.acceleration =
    std::clamp(target_acceleration, max_linear_decel_, max_linear_accel_);

  const double steering = std::abs(target_longitudinal_vel) > 1.0e-3
                            ? std::atan2(wheel_base_ * target_angular_vel, target_longitudinal_vel)
                            : 0.0;
  const double target_steering_tire_angle = std::clamp(
    steering_tire_angle_gain_ * steering + steering_tire_angle_offset_, -max_steering_tire_angle_,
    max_steering_tire_angle_);
  cmd.lateral.steering_tire_angle = applySteeringRateLimit(target_steering_tire_angle);
  cmd.lateral.steering_tire_rotation_rate = steering_tire_rotation_rate_;

  pub_cmd_->publish(cmd);
  cmd.lateral.steering_tire_angle = steering;
  pub_raw_cmd_->publish(cmd);
}

bool SimplePurePursuit::subscribeMessageAvailable()
{
  if (!odometry_) {
    RCLCPP_INFO_THROTTLE(get_logger(), *get_clock(), 1000 /*ms*/, "odometry is not available");
    return false;
  }
  if (!trajectory_) {
    RCLCPP_INFO_THROTTLE(get_logger(), *get_clock(), 1000 /*ms*/, "trajectory is not available");
    return false;
  }
  if (trajectory_->points.empty()) {
      RCLCPP_INFO_THROTTLE(get_logger(), *get_clock(), 1000 /*ms*/,  "trajectory points is empty");
      return false;
    }
  return true;
}

double SimplePurePursuit::applySteeringRateLimit(const double target_steering_tire_angle)
{
  if (!has_previous_steering_tire_angle_) {
    has_previous_steering_tire_angle_ = true;
    previous_steering_tire_angle_ = 0.0;
  }

  const double max_delta =
    std::max(0.0, steering_tire_rotation_rate_) * std::max(control_period_, 1.0e-3);
  const double limited_steering_tire_angle = std::clamp(
    target_steering_tire_angle, previous_steering_tire_angle_ - max_delta,
    previous_steering_tire_angle_ + max_delta);
  previous_steering_tire_angle_ = limited_steering_tire_angle;
  return limited_steering_tire_angle;
}

double SimplePurePursuit::applyTrackingErrorSpeedLimit(
  const double target_velocity, const TrajectoryPoint & closest_point, const double current_yaw) const
{
  const double lateral_error =
    std::abs(calcLateralDeviation(closest_point.pose, odometry_->pose.pose.position));
  double yaw_error = current_yaw - tf2::getYaw(closest_point.pose.orientation);
  while (yaw_error > M_PI) {
    yaw_error -= 2.0 * M_PI;
  }
  while (yaw_error < -M_PI) {
    yaw_error += 2.0 * M_PI;
  }
  yaw_error = std::abs(yaw_error);

  if (lateral_error > lateral_error_brake_threshold_ || yaw_error > yaw_error_brake_threshold_) {
    return std::min(target_velocity, tracking_error_velocity_);
  }
  return target_velocity;
}

double SimplePurePursuit::applyCurvatureSpeedLimit(
  const double target_velocity, const size_t closest_index, const double current_velocity) const
{
  if (!use_curvature_speed_control_ || trajectory_->points.size() < 3) {
    return target_velocity;
  }

  double limited_velocity = std::min(target_velocity, max_linear_vel_);
  double distance = 0.0;
  size_t previous_index = closest_index;

  for (size_t index = closest_index; index < trajectory_->points.size(); ++index) {
    if (index != closest_index) {
      const auto & prev = trajectory_->points.at(previous_index).pose.position;
      const auto & curr = trajectory_->points.at(index).pose.position;
      distance += std::hypot(curr.x - prev.x, curr.y - prev.y);
      previous_index = index;
    }
    if (distance > speed_preview_distance_) {
      break;
    }

    const double curvature = std::abs(curvatureAt(index));
    if (curvature < 1.0e-4) {
      continue;
    }

    const double curve_velocity = std::max(
      min_curve_velocity_, std::sqrt(std::max(max_lateral_accel_, 0.0) / curvature));
    const double reachable_velocity = std::sqrt(
      curve_velocity * curve_velocity + 2.0 * std::max(braking_decel_, 0.0) * distance);
    limited_velocity = std::min(limited_velocity, reachable_velocity);
  }

  // If the kart is already above the preview-limited speed, request braking now.
  if (current_velocity > limited_velocity) {
    return limited_velocity;
  }
  return std::min(target_velocity, limited_velocity);
}

double SimplePurePursuit::curvatureAt(const size_t index) const
{
  if (!trajectory_ || trajectory_->points.size() < 3) {
    return 0.0;
  }

  const size_t prev_index = index == 0 ? 0 : index - 1;
  const size_t next_index = std::min(index + 1, trajectory_->points.size() - 1);
  if (prev_index == index || next_index == index) {
    return 0.0;
  }

  const auto & p0 = trajectory_->points.at(prev_index).pose.position;
  const auto & p1 = trajectory_->points.at(index).pose.position;
  const auto & p2 = trajectory_->points.at(next_index).pose.position;

  const double a = std::hypot(p1.x - p0.x, p1.y - p0.y);
  const double b = std::hypot(p2.x - p1.x, p2.y - p1.y);
  const double c = std::hypot(p2.x - p0.x, p2.y - p0.y);
  const double denominator = a * b * c;
  if (denominator < 1.0e-6) {
    return 0.0;
  }

  const double cross =
    (p1.x - p0.x) * (p2.y - p0.y) - (p1.y - p0.y) * (p2.x - p0.x);
  return 2.0 * cross / denominator;
}

SimplePurePursuit::DynamicWindowBounds SimplePurePursuit::computeDynamicWindow(
  const Twist & current_speed) const
{
  DynamicWindowBounds dynamic_window;
  constexpr double eps = 1.0e-3;

  auto compute_window = [&](const double current_vel, const double max_vel, const double min_vel,
                            const double max_accel, const double max_decel) {
      double candidate_max_vel = 0.0;
      double candidate_min_vel = 0.0;

      if (current_vel > eps) {
        candidate_max_vel = current_vel + max_accel * control_period_;
        candidate_min_vel = current_vel + max_decel * control_period_;
      } else if (current_vel < -eps) {
        candidate_max_vel = current_vel - max_decel * control_period_;
        candidate_min_vel = current_vel - max_accel * control_period_;
      } else {
        candidate_max_vel = current_vel + max_accel * control_period_;
        candidate_min_vel = current_vel - max_accel * control_period_;
      }

      return std::make_tuple(
        std::min(candidate_max_vel, max_vel), std::max(candidate_min_vel, min_vel));
    };

  std::tie(dynamic_window.max_linear_vel, dynamic_window.min_linear_vel) = compute_window(
    current_speed.linear.x, max_linear_vel_, min_linear_vel_, max_linear_accel_, max_linear_decel_);
  std::tie(dynamic_window.max_angular_vel, dynamic_window.min_angular_vel) = compute_window(
    current_speed.angular.z, max_angular_vel_, min_angular_vel_, max_angular_accel_,
    max_angular_decel_);

  return dynamic_window;
}

void SimplePurePursuit::applyRegulationToDynamicWindow(
  const double regulated_linear_vel, DynamicWindowBounds & dynamic_window) const
{
  const double regulated_min = std::min(0.0, regulated_linear_vel);
  const double regulated_max = std::max(0.0, regulated_linear_vel);

  dynamic_window.min_linear_vel = std::max(dynamic_window.min_linear_vel, regulated_min);
  dynamic_window.max_linear_vel = std::min(dynamic_window.max_linear_vel, regulated_max);

  if (dynamic_window.min_linear_vel > dynamic_window.max_linear_vel) {
    if (dynamic_window.min_linear_vel > regulated_max) {
      dynamic_window.max_linear_vel = dynamic_window.min_linear_vel;
    } else {
      dynamic_window.min_linear_vel = dynamic_window.max_linear_vel;
    }
  }
}

std::tuple<double, double> SimplePurePursuit::computeOptimalVelocityWithinDynamicWindow(
  const DynamicWindowBounds & dynamic_window, const double curvature, const double sign) const
{
  if (std::abs(curvature) < 1.0e-3) {
    const double linear_vel =
      sign >= 0.0 ? dynamic_window.max_linear_vel : dynamic_window.min_linear_vel;
    double angular_vel = 0.0;
    if (!(dynamic_window.min_angular_vel <= 0.0 && 0.0 <= dynamic_window.max_angular_vel)) {
      angular_vel = std::abs(dynamic_window.min_angular_vel) <=
                      std::abs(dynamic_window.max_angular_vel)
                      ? dynamic_window.min_angular_vel
                      : dynamic_window.max_angular_vel;
    }
    return std::make_tuple(linear_vel, angular_vel);
  }

  const std::array<std::pair<double, double>, 4> candidates = {{
    {dynamic_window.min_linear_vel, curvature * dynamic_window.min_linear_vel},
    {dynamic_window.max_linear_vel, curvature * dynamic_window.max_linear_vel},
    {dynamic_window.min_angular_vel / curvature, dynamic_window.min_angular_vel},
    {dynamic_window.max_angular_vel / curvature, dynamic_window.max_angular_vel},
  }};

  double best_linear_vel = -std::numeric_limits<double>::max() * sign;
  double best_angular_vel = 0.0;
  bool has_intersection = false;

  for (const auto & candidate : candidates) {
    const double linear_vel = candidate.first;
    const double angular_vel = candidate.second;
    if (
      linear_vel >= dynamic_window.min_linear_vel && linear_vel <= dynamic_window.max_linear_vel &&
      angular_vel >= dynamic_window.min_angular_vel && angular_vel <= dynamic_window.max_angular_vel)
    {
      if (!has_intersection || linear_vel * sign > best_linear_vel * sign) {
        best_linear_vel = linear_vel;
        best_angular_vel = angular_vel;
        has_intersection = true;
      }
    }
  }

  if (has_intersection) {
    return std::make_tuple(best_linear_vel, best_angular_vel);
  }

  if (use_racing_dynamic_window_) {
    const double linear_vel =
      sign >= 0.0 ? dynamic_window.max_linear_vel : dynamic_window.min_linear_vel;
    const double angular_vel = std::clamp(
      curvature * linear_vel, dynamic_window.min_angular_vel, dynamic_window.max_angular_vel);
    return std::make_tuple(linear_vel, angular_vel);
  }

  const std::array<std::array<double, 2>, 4> corners = {{
    {dynamic_window.min_linear_vel, dynamic_window.min_angular_vel},
    {dynamic_window.min_linear_vel, dynamic_window.max_angular_vel},
    {dynamic_window.max_linear_vel, dynamic_window.min_angular_vel},
    {dynamic_window.max_linear_vel, dynamic_window.max_angular_vel},
  }};
  const double denom = std::sqrt(curvature * curvature + 1.0);
  double closest_dist = std::numeric_limits<double>::max();
  best_linear_vel = -std::numeric_limits<double>::max() * sign;
  best_angular_vel = 0.0;

  for (const auto & corner : corners) {
    const double dist = std::abs(curvature * corner[0] - corner[1]) / denom;
    if (
      dist < closest_dist ||
      (std::abs(dist - closest_dist) <= 1.0e-3 && corner[0] * sign > best_linear_vel * sign))
    {
      closest_dist = dist;
      best_linear_vel = corner[0];
      best_angular_vel = corner[1];
    }
  }

  return std::make_tuple(best_linear_vel, best_angular_vel);
}

std::tuple<double, double> SimplePurePursuit::computeDynamicWindowVelocities(
  const Twist & current_speed, const double regulated_linear_vel, const double curvature,
  const double sign) const
{
  auto dynamic_window = computeDynamicWindow(current_speed);
  applyRegulationToDynamicWindow(regulated_linear_vel, dynamic_window);
  return computeOptimalVelocityWithinDynamicWindow(dynamic_window, curvature, sign);
}
}  // namespace simple_pure_pursuit

int main(int argc, char const * argv[])
{
  rclcpp::init(argc, argv);
  rclcpp::spin(std::make_shared<simple_pure_pursuit::SimplePurePursuit>());
  rclcpp::shutdown();
  return 0;
}
