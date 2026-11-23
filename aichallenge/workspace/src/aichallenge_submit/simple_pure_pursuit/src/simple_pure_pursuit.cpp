#include "simple_pure_pursuit/simple_pure_pursuit.hpp"

#include <motion_utils/motion_utils.hpp>
#include <tier4_autoware_utils/tier4_autoware_utils.hpp>

#include <tf2/utils.h>

#include <algorithm>
#include <iterator>

namespace simple_pure_pursuit
{

using motion_utils::findNearestIndex;
using tier4_autoware_utils::calcLateralDeviation;
using tier4_autoware_utils::calcYawDeviation;

SimplePurePursuit::SimplePurePursuit()
: Node("simple_pure_pursuit"),
  // initialize parameters
  wheel_base_(declare_parameter<float>("wheel_base", 2.14)),
  lookahead_gain_(declare_parameter<float>("lookahead_gain", 1.0)),
  lookahead_min_distance_(declare_parameter<float>("lookahead_min_distance", 1.0)),
  speed_proportional_gain_(declare_parameter<float>("speed_proportional_gain", 1.0)),
  use_external_target_vel_(declare_parameter<bool>("use_external_target_vel", false)),
  external_target_vel_(declare_parameter<float>("external_target_vel", 0.0)),
  steering_tire_angle_gain_(declare_parameter<float>("steering_tire_angle_gain", 1.0)),
  max_acceleration_(declare_parameter<float>("max_acceleration", 3.0))
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

  cmd.longitudinal.speed = target_longitudinal_vel;
  cmd.longitudinal.acceleration =
    speed_proportional_gain_ * (target_longitudinal_vel - current_longitudinal_vel);
  cmd.longitudinal.acceleration = std::min<double>(cmd.longitudinal.acceleration, max_acceleration_);

  // calc lateral control
  //// calc lookahead distance
  double lookahead_distance = lookahead_gain_ * target_longitudinal_vel + lookahead_min_distance_;
  //// calc center coordinate of rear wheel
  double rear_x = odometry_->pose.pose.position.x -
                  wheel_base_ / 2.0 * std::cos(odometry_->pose.pose.orientation.z);
  double rear_y = odometry_->pose.pose.position.y -
                  wheel_base_ / 2.0 * std::sin(odometry_->pose.pose.orientation.z);
  //// search lookahead point
  const auto & traj_points = trajectory_->points;
  auto is_beyond_lookahead = [&](const TrajectoryPoint & point) {
    return std::hypot(point.pose.position.x - rear_x, point.pose.position.y - rear_y) >=
           lookahead_distance;
  };
  auto lookahead_point_itr = std::find_if(
    traj_points.begin() + closet_traj_point_idx, traj_points.end(), is_beyond_lookahead);

  double lookahead_point_x;
  double lookahead_point_y;
  if (lookahead_point_itr != traj_points.end()) {
    lookahead_point_x = lookahead_point_itr->pose.position.x;
    lookahead_point_y = lookahead_point_itr->pose.position.y;
  } else {
    // No point from closet_traj_point_idx to the end reaches the lookahead
    // distance. For a closed (looped) trajectory -- first and last points
    // within ~2x the point spacing of each other -- wrap the search through
    // the beginning instead of pinning to the terminal point: otherwise the
    // controller steers toward an increasingly nearby endpoint for the
    // whole end-of-lap segment rather than the upcoming part of the loop.
    bool is_closed = false;
    if (traj_points.size() >= 2) {
      const double point_spacing = std::hypot(
        traj_points[1].pose.position.x - traj_points[0].pose.position.x,
        traj_points[1].pose.position.y - traj_points[0].pose.position.y);
      const double end_gap = std::hypot(
        traj_points.back().pose.position.x - traj_points.front().pose.position.x,
        traj_points.back().pose.position.y - traj_points.front().pose.position.y);
      is_closed = end_gap < 1.5 && end_gap < 2.0 * point_spacing;
    }

    auto wrapped_itr = traj_points.end();
    if (is_closed) {
      wrapped_itr = std::find_if(
        traj_points.begin(), traj_points.begin() + closet_traj_point_idx, is_beyond_lookahead);
    }

    if (is_closed && wrapped_itr != traj_points.begin() + closet_traj_point_idx) {
      lookahead_point_x = wrapped_itr->pose.position.x;
      lookahead_point_y = wrapped_itr->pose.position.y;
    } else {
      // Open trajectory (or a closed one with no qualifying point anywhere):
      // clamp to the last point rather than dereferencing end().
      auto last_itr = std::prev(traj_points.end());
      lookahead_point_x = last_itr->pose.position.x;
      lookahead_point_y = last_itr->pose.position.y;
    }
  }

  geometry_msgs::msg::PointStamped lookahead_point_msg;
  lookahead_point_msg.header.stamp = get_clock()->now();
  lookahead_point_msg.header.frame_id = "map";
  lookahead_point_msg.point.x = lookahead_point_x;
  lookahead_point_msg.point.y = lookahead_point_y;
  lookahead_point_msg.point.z = closet_traj_point.pose.position.z;
  pub_lookahead_point_->publish(lookahead_point_msg);

  // calc steering angle for lateral control
  double alpha = std::atan2(lookahead_point_y - rear_y, lookahead_point_x - rear_x) -
                 tf2::getYaw(odometry_->pose.pose.orientation);
  cmd.lateral.steering_tire_angle =
    steering_tire_angle_gain_ * std::atan2(2.0 * wheel_base_ * std::sin(alpha), lookahead_distance);

  pub_cmd_->publish(cmd);
  cmd.lateral.steering_tire_angle /=  steering_tire_angle_gain_;
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
}  // namespace simple_pure_pursuit

int main(int argc, char const * argv[])
{
  rclcpp::init(argc, argv);
  rclcpp::spin(std::make_shared<simple_pure_pursuit::SimplePurePursuit>());
  rclcpp::shutdown();
  return 0;
}
