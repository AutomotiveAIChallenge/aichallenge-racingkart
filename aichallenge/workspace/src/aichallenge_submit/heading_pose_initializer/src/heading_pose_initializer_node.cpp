// Copyright 2025 Taiki Tanaka
//
// Licensed under the Apache License, Version 2.0 (the "License");
// you may not use this file except in compliance with the License.
// You may obtain a copy of the License at
//
//     http://www.apache.org/licenses/LICENSE-2.0
//
// Unless required by applicable law or agreed to in writing, software
// distributed under the License is distributed on an "AS IS" BASIS,
// WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
// See the License for the specific language governing permissions and
// limitations under the License.

// Headless /set_initial_pose service node with raceline visualization.
//
// 1. Load a heading-reference CSV at startup.
// 2. Publish heading arrows as RViz markers (TRANSIENT_LOCAL).
// 3. Subscribe to GNSS pose.
// 4. On /set_initial_pose service call, find the closest raceline point,
//    compute yaw, and publish /initialpose.

#include <cmath>
#include <condition_variable>
#include <cstdio>
#include <fstream>
#include <limits>
#include <mutex>
#include <optional>
#include <sstream>
#include <string>
#include <vector>

#include <rclcpp/rclcpp.hpp>

#include <geometry_msgs/msg/point.hpp>
#include <geometry_msgs/msg/pose_with_covariance_stamped.hpp>
#include <std_srvs/srv/trigger.hpp>
#include <visualization_msgs/msg/marker.hpp>
#include <visualization_msgs/msg/marker_array.hpp>

namespace
{

struct Point2D
{
  double x;
  double y;
};

std::vector<Point2D> load_raceline(const std::string & csv_path, rclcpp::Logger logger)
{
  std::vector<Point2D> points;
  std::ifstream ifs(csv_path);
  if (!ifs.is_open()) {
    RCLCPP_ERROR(logger, "Failed to open heading CSV: %s", csv_path.c_str());
    return points;
  }

  std::string line;
  // skip header
  std::getline(ifs, line);

  while (std::getline(ifs, line)) {
    try {
      std::istringstream ss(line);
      std::string token;
      std::getline(ss, token, ',');
      const double x = std::stod(token);
      std::getline(ss, token, ',');
      const double y = std::stod(token);
      if (std::isfinite(x) && std::isfinite(y)) {
        points.push_back({x, y});
      }
    } catch (const std::exception & e) {
      RCLCPP_WARN(logger, "Skipping invalid CSV line: %s", e.what());
    }
  }
  return points;
}

size_t find_closest_index(
  const std::vector<Point2D> & points, const double qx, const double qy)
{
  size_t closest = 0;
  double best_d2 = std::numeric_limits<double>::infinity();
  for (size_t i = 0; i < points.size(); ++i) {
    const double dx = points[i].x - qx;
    const double dy = points[i].y - qy;
    const double d2 = dx * dx + dy * dy;
    if (d2 < best_d2) {
      best_d2 = d2;
      closest = i;
    }
  }
  return closest;
}

std::optional<double> compute_yaw(const std::vector<Point2D> & points, const size_t closest_idx)
{
  constexpr double kMinSegLen2 = 1.0e-6;

  // forward search
  for (size_t i = closest_idx; i + 1 < points.size(); ++i) {
    const double dx = points[i + 1].x - points[i].x;
    const double dy = points[i + 1].y - points[i].y;
    if (dx * dx + dy * dy > kMinSegLen2) {
      return std::atan2(dy, dx);
    }
  }
  // backward search
  for (size_t i = closest_idx; i > 0; --i) {
    const double dx = points[i].x - points[i - 1].x;
    const double dy = points[i].y - points[i - 1].y;
    if (dx * dx + dy * dy > kMinSegLen2) {
      return std::atan2(dy, dx);
    }
  }
  return std::nullopt;
}

}  // namespace

class HeadingPoseInitializerNode : public rclcpp::Node
{
public:
  HeadingPoseInitializerNode() : Node("heading_pose_initializer")
  {
    declare_parameter("heading_csv_path", std::string(""));
    declare_parameter("gnss_pose_topic", std::string("/sensing/gnss/pose_with_covariance"));
    declare_parameter("initial_pose_topic", std::string("/initialpose"));
    declare_parameter("service_name", std::string("/set_initial_pose"));
    declare_parameter("wait_timeout_sec", 120);
    declare_parameter("marker_topic",
      std::string("/heading_pose_initializer/raceline_markers"));
    declare_parameter("marker_publish_rate", 0.1);
    declare_parameter("arrow_interval", 2);
    declare_parameter("arrow_length", 1.0);

    const auto csv_path = get_parameter("heading_csv_path").as_string();
    const auto gnss_topic = get_parameter("gnss_pose_topic").as_string();
    const auto pose_topic = get_parameter("initial_pose_topic").as_string();
    const auto service_name = get_parameter("service_name").as_string();
    const auto marker_topic = get_parameter("marker_topic").as_string();
    const auto marker_rate = get_parameter("marker_publish_rate").as_double();
    arrow_interval_ = get_parameter("arrow_interval").as_int();
    arrow_length_ = get_parameter("arrow_length").as_double();
    wait_timeout_sec_ = static_cast<int>(get_parameter("wait_timeout_sec").as_int());

    raceline_ = load_raceline(csv_path, get_logger());
    RCLCPP_INFO(
      get_logger(), "Loaded %zu heading-reference points from %s",
      raceline_.size(), csv_path.c_str());

    rclcpp::QoS reliable_volatile(1);
    reliable_volatile.reliable();

    gnss_sub_ = create_subscription<geometry_msgs::msg::PoseWithCovarianceStamped>(
      gnss_topic, reliable_volatile,
      [this](const geometry_msgs::msg::PoseWithCovarianceStamped::SharedPtr msg) {
        std::lock_guard<std::mutex> lock(gnss_mutex_);
        last_gnss_ = msg;
        gnss_cv_.notify_all();
      });

    pose_pub_ = create_publisher<geometry_msgs::msg::PoseWithCovarianceStamped>(
      pose_topic, reliable_volatile);

    rclcpp::QoS marker_qos(1);
    marker_qos.reliable().transient_local();
    marker_pub_ = create_publisher<visualization_msgs::msg::MarkerArray>(
      marker_topic, marker_qos);

    service_ = create_service<std_srvs::srv::Trigger>(
      service_name,
      std::bind(
        &HeadingPoseInitializerNode::on_service, this,
        std::placeholders::_1, std::placeholders::_2));

    marker_array_ = build_markers();
    marker_pub_->publish(marker_array_);

    if (marker_rate > 0.0) {
      marker_timer_ = create_wall_timer(
        std::chrono::duration<double>(1.0 / marker_rate),
        [this]() { marker_pub_->publish(marker_array_); });
    }

    RCLCPP_INFO(
      get_logger(),
      "heading_pose_initializer ready: gnss=%s pub=%s srv=%s markers=%s",
      gnss_topic.c_str(), pose_topic.c_str(), service_name.c_str(), marker_topic.c_str());
  }

private:
  visualization_msgs::msg::MarkerArray build_markers()
  {
    visualization_msgs::msg::MarkerArray ma;
    if (raceline_.size() < 2) {
      return ma;
    }

    const auto now = this->now();
    int arrow_id = 0;
    for (size_t i = 0; i + 1 < raceline_.size();
         i += static_cast<size_t>(arrow_interval_)) {
      const auto yaw = compute_yaw(raceline_, i);
      if (!yaw.has_value()) continue;

      visualization_msgs::msg::Marker arrow;
      arrow.header.frame_id = "map";
      arrow.header.stamp = now;
      arrow.ns = "heading_arrows";
      arrow.id = arrow_id++;
      arrow.type = visualization_msgs::msg::Marker::ARROW;
      arrow.action = visualization_msgs::msg::Marker::ADD;

      geometry_msgs::msg::Point start;
      start.x = raceline_[i].x;
      start.y = raceline_[i].y;
      start.z = 0.5;

      geometry_msgs::msg::Point end;
      end.x = raceline_[i].x + arrow_length_ * std::cos(*yaw);
      end.y = raceline_[i].y + arrow_length_ * std::sin(*yaw);
      end.z = 0.5;

      arrow.points.push_back(start);
      arrow.points.push_back(end);

      arrow.scale.x = 0.25;  // shaft diameter
      arrow.scale.y = 0.3;   // head diameter
      arrow.scale.z = 0.2;   // head length
      arrow.color.r = 1.0f;
      arrow.color.g = 1.0f;
      arrow.color.b = 1.0f;
      arrow.color.a = 0.5f;

      ma.markers.push_back(arrow);
    }
    return ma;
  }

  void on_service(
    const std::shared_ptr<std_srvs::srv::Trigger::Request>,
    std::shared_ptr<std_srvs::srv::Trigger::Response> response)
  {
    if (raceline_.size() < 2) {
      response->success = false;
      response->message = "heading CSV not loaded or has fewer than 2 points";
      RCLCPP_ERROR(get_logger(), "%s", response->message.c_str());
      return;
    }

    RCLCPP_INFO(
      get_logger(), "set_initial_pose called, waiting up to %ds for GNSS", wait_timeout_sec_);

    geometry_msgs::msg::PoseWithCovarianceStamped::SharedPtr gnss;
    {
      std::unique_lock<std::mutex> lock(gnss_mutex_);
      gnss_cv_.wait_for(lock, std::chrono::seconds(wait_timeout_sec_), [this]() {
        return last_gnss_ != nullptr;
      });
      gnss = last_gnss_;
    }

    if (!gnss) {
      response->success = false;
      response->message = "timeout waiting for GNSS";
      RCLCPP_ERROR(get_logger(), "%s", response->message.c_str());
      return;
    }

    const auto & pos = gnss->pose.pose.position;
    if (!std::isfinite(pos.x) || !std::isfinite(pos.y)) {
      response->success = false;
      response->message = "GNSS pose is invalid (NaN/Inf)";
      RCLCPP_ERROR(get_logger(), "%s", response->message.c_str());
      return;
    }

    const size_t closest_idx = find_closest_index(raceline_, pos.x, pos.y);
    const auto yaw = compute_yaw(raceline_, closest_idx);
    if (!yaw.has_value()) {
      response->success = false;
      response->message = "cannot compute yaw from heading reference";
      RCLCPP_ERROR(get_logger(), "%s", response->message.c_str());
      return;
    }

    geometry_msgs::msg::PoseWithCovarianceStamped pose_msg;
    pose_msg.header.stamp = this->now();
    pose_msg.header.frame_id = gnss->header.frame_id;
    pose_msg.pose.pose.position = gnss->pose.pose.position;
    pose_msg.pose.pose.orientation.z = std::sin(*yaw * 0.5);
    pose_msg.pose.pose.orientation.w = std::cos(*yaw * 0.5);
    pose_msg.pose.covariance[35] = 0.5;

    pose_pub_->publish(pose_msg);

    const double yaw_deg = *yaw * 180.0 / M_PI;
    char buf[64];
    std::snprintf(buf, sizeof(buf), "published initial pose (yaw %.1f deg)", yaw_deg);
    response->success = true;
    response->message = buf;
    RCLCPP_INFO(get_logger(), "%s", response->message.c_str());
  }

  std::vector<Point2D> raceline_;
  int64_t arrow_interval_{2};
  int wait_timeout_sec_{120};
  double arrow_length_{1.0};

  std::mutex gnss_mutex_;
  std::condition_variable gnss_cv_;
  geometry_msgs::msg::PoseWithCovarianceStamped::SharedPtr last_gnss_;

  rclcpp::Subscription<geometry_msgs::msg::PoseWithCovarianceStamped>::SharedPtr gnss_sub_;
  rclcpp::Publisher<geometry_msgs::msg::PoseWithCovarianceStamped>::SharedPtr pose_pub_;
  rclcpp::Publisher<visualization_msgs::msg::MarkerArray>::SharedPtr marker_pub_;
  rclcpp::Service<std_srvs::srv::Trigger>::SharedPtr service_;
  rclcpp::TimerBase::SharedPtr marker_timer_;
  visualization_msgs::msg::MarkerArray marker_array_;
};

int main(int argc, char ** argv)
{
  rclcpp::init(argc, argv);
  auto node = std::make_shared<HeadingPoseInitializerNode>();
  // MultiThreadedExecutor is required: on_service blocks on gnss_cv_.wait_for(),
  // so the GNSS subscription callback must run on a separate thread.
  rclcpp::executors::MultiThreadedExecutor executor;
  executor.add_node(node);
  executor.spin();
  rclcpp::shutdown();
  return 0;
}
