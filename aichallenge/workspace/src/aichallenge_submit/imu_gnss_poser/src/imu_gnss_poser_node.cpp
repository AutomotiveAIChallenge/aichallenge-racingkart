// Copyright 2024 TIER IV, Inc.
// Copyright 2026 Taiki Tanaka
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

#include <cmath>
#include <fstream>
#include <limits>
#include <mutex>
#include <optional>
#include <sstream>
#include <string>
#include <vector>

#include <rclcpp/rclcpp.hpp>

#include <geometry_msgs/msg/pose_with_covariance_stamped.hpp>
#include <sensor_msgs/msg/imu.hpp>
#include <std_srvs/srv/set_bool.hpp>
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

std::vector<Point2D> load_raceline(const std::string & path, rclcpp::Logger logger)
{
  std::vector<Point2D> pts;
  if (path.empty()) {
    return pts;
  }
  std::ifstream ifs(path);
  if (!ifs.is_open()) {
    RCLCPP_WARN(logger, "Cannot open heading CSV: %s (raceline yaw disabled)", path.c_str());
    return pts;
  }
  std::string line;
  std::getline(ifs, line);  // skip header
  while (std::getline(ifs, line)) {
    try {
      std::istringstream ss(line);
      std::string tok;
      std::getline(ss, tok, ',');
      const double x = std::stod(tok);
      std::getline(ss, tok, ',');
      const double y = std::stod(tok);
      if (std::isfinite(x) && std::isfinite(y)) {
        pts.push_back({x, y});
      }
    } catch (const std::exception & e) {
      RCLCPP_WARN(logger, "Skipping invalid CSV line: %s", e.what());
    }
  }
  return pts;
}

size_t find_closest(const std::vector<Point2D> & pts, double qx, double qy)
{
  size_t best = 0;
  double best_d2 = std::numeric_limits<double>::infinity();
  for (size_t i = 0; i < pts.size(); ++i) {
    const double dx = pts[i].x - qx;
    const double dy = pts[i].y - qy;
    const double d2 = dx * dx + dy * dy;
    if (d2 < best_d2) {
      best_d2 = d2;
      best = i;
    }
  }
  return best;
}

std::optional<double> compute_yaw(const std::vector<Point2D> & pts, size_t idx)
{
  constexpr double kMinSegLen2 = 1.0e-6;
  for (size_t i = idx; i + 1 < pts.size(); ++i) {
    const double dx = pts[i + 1].x - pts[i].x;
    const double dy = pts[i + 1].y - pts[i].y;
    if (dx * dx + dy * dy > kMinSegLen2) {
      return std::atan2(dy, dx);
    }
  }
  for (size_t i = idx; i > 0; --i) {
    const double dx = pts[i].x - pts[i - 1].x;
    const double dy = pts[i].y - pts[i - 1].y;
    if (dx * dx + dy * dy > kMinSegLen2) {
      return std::atan2(dy, dx);
    }
  }
  return std::nullopt;
}

}  // namespace

class ImuGnssPoser : public rclcpp::Node
{
public:
  ImuGnssPoser() : Node("imu_gnss_poser")
  {
    // Parameters
    declare_parameter("heading_csv_path", std::string(""));
    declare_parameter("initial_pose_service", std::string("/set_initial_pose"));
    declare_parameter("marker_topic", std::string("/heading_pose_initializer/raceline_markers"));
    declare_parameter("marker_publish_rate", 0.1);
    declare_parameter("arrow_interval", 2);
    declare_parameter("arrow_length", 1.0);

    // GNSS measurement covariance
    declare_parameter("gnss_covariance.good_threshold", 0.1);
    declare_parameter("gnss_covariance.good_value", 0.1);
    declare_parameter("gnss_covariance.moderate_threshold", 0.5);
    declare_parameter("gnss_covariance.moderate_value", 0.25);
    declare_parameter("gnss_covariance.poor_value", 100.0);
    declare_parameter("gnss_covariance.roll", 100000.0);
    declare_parameter("gnss_covariance.pitch", 100000.0);
    declare_parameter("gnss_covariance.yaw", 100000.0);

    // Initial pose covariance (for /set_initial_pose and first initial_pose3d)
    declare_parameter("initial_pose_covariance.x", 0.25);
    declare_parameter("initial_pose_covariance.y", 0.25);
    declare_parameter("initial_pose_covariance.yaw", 0.5);

    arrow_interval_ = get_parameter("arrow_interval").as_int();
    arrow_length_ = get_parameter("arrow_length").as_double();

    gnss_cov_good_thresh_ = get_parameter("gnss_covariance.good_threshold").as_double();
    gnss_cov_good_ = get_parameter("gnss_covariance.good_value").as_double();
    gnss_cov_mod_thresh_ = get_parameter("gnss_covariance.moderate_threshold").as_double();
    gnss_cov_mod_ = get_parameter("gnss_covariance.moderate_value").as_double();
    gnss_cov_poor_ = get_parameter("gnss_covariance.poor_value").as_double();
    gnss_cov_roll_ = get_parameter("gnss_covariance.roll").as_double();
    gnss_cov_pitch_ = get_parameter("gnss_covariance.pitch").as_double();
    gnss_cov_yaw_ = get_parameter("gnss_covariance.yaw").as_double();

    init_cov_x_ = get_parameter("initial_pose_covariance.x").as_double();
    init_cov_y_ = get_parameter("initial_pose_covariance.y").as_double();
    init_cov_yaw_ = get_parameter("initial_pose_covariance.yaw").as_double();

    // Load raceline
    const auto csv_path = get_parameter("heading_csv_path").as_string();
    raceline_ = load_raceline(csv_path, get_logger());
    has_raceline_ = raceline_.size() >= 2;
    if (has_raceline_) {
      RCLCPP_INFO(
        get_logger(), "Loaded %zu heading-reference points from %s",
        raceline_.size(), csv_path.c_str());
    }

    // QoS
    const auto rv_qos = rclcpp::QoS(rclcpp::KeepLast(1)).reliable();
    const auto rt_qos = rclcpp::QoS(rclcpp::KeepLast(1)).reliable().transient_local();

    // Publishers
    pub_pose_ = create_publisher<geometry_msgs::msg::PoseWithCovarianceStamped>(
      "/localization/imu_gnss_poser/pose_with_covariance", rv_qos);
    pub_initial_pose_3d_ = create_publisher<geometry_msgs::msg::PoseWithCovarianceStamped>(
      "/localization/initial_pose3d", rt_qos);

    // Subscriptions
    sub_gnss_ = create_subscription<geometry_msgs::msg::PoseWithCovarianceStamped>(
      "/sensing/gnss/pose_with_covariance", rv_qos,
      std::bind(&ImuGnssPoser::gnss_callback, this, std::placeholders::_1));
    sub_imu_ = create_subscription<sensor_msgs::msg::Imu>(
      "/sensing/imu/imu_raw", rv_qos,
      std::bind(&ImuGnssPoser::imu_callback, this, std::placeholders::_1));

    // EKF trigger client
    ekf_trigger_client_ = create_client<std_srvs::srv::SetBool>("/localization/trigger_node");

    // /set_initial_pose service
    const auto svc_name = get_parameter("initial_pose_service").as_string();
    service_ = create_service<std_srvs::srv::Trigger>(
      svc_name,
      std::bind(
        &ImuGnssPoser::on_set_initial_pose, this,
        std::placeholders::_1, std::placeholders::_2));

    // Raceline markers
    if (has_raceline_) {
      rclcpp::QoS mq(1);
      mq.reliable().transient_local();
      marker_pub_ = create_publisher<visualization_msgs::msg::MarkerArray>(
        get_parameter("marker_topic").as_string(), mq);
      markers_ = build_markers();
      marker_pub_->publish(markers_);
      const double rate = get_parameter("marker_publish_rate").as_double();
      if (rate > 0.0) {
        marker_timer_ = create_wall_timer(
          std::chrono::duration<double>(1.0 / rate),
          [this]() { marker_pub_->publish(markers_); });
      }
    }

    RCLCPP_INFO(
      get_logger(), "imu_gnss_poser ready (raceline=%s, service=%s)",
      has_raceline_ ? "yes" : "no", svc_name.c_str());
  }

private:
  // ── GNSS callback ──────────────────────────────────────────

  void gnss_callback(const geometry_msgs::msg::PoseWithCovarianceStamped::SharedPtr msg)
  {
    adjust_covariance(*msg);
    apply_imu_orientation_fallback(*msg);

    // Publish fused pose for EKF measurement input (GNSS/IMU yaw, not raceline)
    pub_pose_->publish(*msg);

    // Store latest for /set_initial_pose service
    {
      std::lock_guard<std::mutex> lk(gnss_mutex_);
      last_gnss_ = msg;
    }

    // Publish initial_pose3d until EKF is triggered (same msg as pub_pose_)
    if (!ekf_triggered_) {
      pub_initial_pose_3d_->publish(*msg);
      if (!initial_pose_published_) {
        RCLCPP_INFO(get_logger(), "Publishing initial_pose3d");
        initial_pose_published_ = true;
      }
      try_trigger_ekf();
    }
  }

  void adjust_covariance(geometry_msgs::msg::PoseWithCovarianceStamped & msg) const
  {
    auto adj = [this](double v) -> double {
      if (v <= gnss_cov_good_thresh_) return gnss_cov_good_;
      if (v <= gnss_cov_mod_thresh_) return gnss_cov_mod_;
      return gnss_cov_poor_;
    };
    msg.pose.covariance[7 * 0] = adj(msg.pose.covariance[7 * 0]);
    msg.pose.covariance[7 * 1] = adj(msg.pose.covariance[7 * 1]);
    msg.pose.covariance[7 * 2] = adj(msg.pose.covariance[7 * 2]);
    msg.pose.covariance[7 * 3] = gnss_cov_roll_;
    msg.pose.covariance[7 * 4] = gnss_cov_pitch_;
    msg.pose.covariance[7 * 5] = gnss_cov_yaw_;
  }

  bool try_apply_raceline_yaw(geometry_msgs::msg::PoseWithCovarianceStamped & msg) const
  {
    if (!has_raceline_) {
      return false;
    }
    const auto & pos = msg.pose.pose.position;
    if (!std::isfinite(pos.x) || !std::isfinite(pos.y)) {
      return false;
    }
    const auto idx = find_closest(raceline_, pos.x, pos.y);
    const auto yaw = compute_yaw(raceline_, idx);
    if (!yaw.has_value()) {
      return false;
    }
    msg.pose.pose.orientation.x = 0.0;
    msg.pose.pose.orientation.y = 0.0;
    msg.pose.pose.orientation.z = std::sin(*yaw * 0.5);
    msg.pose.pose.orientation.w = std::cos(*yaw * 0.5);
    msg.pose.covariance[7 * 5] = init_cov_yaw_;
    return true;
  }

  void apply_imu_orientation_fallback(geometry_msgs::msg::PoseWithCovarianceStamped & msg) const
  {
    const auto & o = msg.pose.pose.orientation;
    if (std::isnan(o.x) || std::isnan(o.y) || std::isnan(o.z) || std::isnan(o.w) ||
      (o.x == 0 && o.y == 0 && o.z == 0 && o.w == 0))
    {
      msg.pose.pose.orientation = imu_msg_.orientation;
    }
  }

  // ── IMU callback ───────────────────────────────────────────

  void imu_callback(sensor_msgs::msg::Imu::SharedPtr msg)
  {
    imu_msg_ = *msg;
  }

  // ── EKF trigger ────────────────────────────────────────────

  void try_trigger_ekf()
  {
    if (!ekf_trigger_client_->service_is_ready()) {
      return;  // will retry on next GNSS callback
    }
    auto req = std::make_shared<std_srvs::srv::SetBool::Request>();
    req->data = true;
    ekf_trigger_client_->async_send_request(
      req,
      [this](rclcpp::Client<std_srvs::srv::SetBool>::SharedFuture future) {
        const auto resp = future.get();
        RCLCPP_INFO(
          get_logger(), "EKF trigger: success=%s", resp->success ? "true" : "false");
      });
    ekf_triggered_ = true;
    RCLCPP_INFO(get_logger(), "Called EKF trigger");
  }

  // ── /set_initial_pose service ──────────────────────────────

  void on_set_initial_pose(
    const std::shared_ptr<std_srvs::srv::Trigger::Request>,
    std::shared_ptr<std_srvs::srv::Trigger::Response> response)
  {
    if (!has_raceline_) {
      response->success = false;
      response->message = "heading CSV not loaded";
      RCLCPP_ERROR(get_logger(), "%s", response->message.c_str());
      return;
    }

    geometry_msgs::msg::PoseWithCovarianceStamped::SharedPtr gnss;
    {
      std::lock_guard<std::mutex> lk(gnss_mutex_);
      gnss = last_gnss_;
    }
    if (!gnss) {
      response->success = false;
      response->message = "no GNSS data received yet";
      RCLCPP_ERROR(get_logger(), "%s", response->message.c_str());
      return;
    }

    const auto & pos = gnss->pose.pose.position;
    if (!std::isfinite(pos.x) || !std::isfinite(pos.y)) {
      response->success = false;
      response->message = "GNSS position is invalid (NaN/Inf)";
      RCLCPP_ERROR(get_logger(), "%s", response->message.c_str());
      return;
    }

    const auto idx = find_closest(raceline_, pos.x, pos.y);
    const auto yaw = compute_yaw(raceline_, idx);
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
    pose_msg.pose.covariance[7 * 0] = init_cov_x_;
    pose_msg.pose.covariance[7 * 1] = init_cov_y_;
    pose_msg.pose.covariance[7 * 5] = init_cov_yaw_;

    pub_initial_pose_3d_->publish(pose_msg);

    // Call trigger directly without resetting ekf_triggered_,
    // so gnss_callback won't re-publish initial_pose3d continuously.
    if (ekf_trigger_client_->service_is_ready()) {
      auto req = std::make_shared<std_srvs::srv::SetBool::Request>();
      req->data = true;
      ekf_trigger_client_->async_send_request(req);
    }

    const double yaw_deg = *yaw * 180.0 / M_PI;
    char buf[128];
    std::snprintf(buf, sizeof(buf), "published initial pose (yaw %.1f deg)", yaw_deg);
    response->success = true;
    response->message = buf;
    RCLCPP_INFO(get_logger(), "%s", buf);
  }

  // ── Raceline markers ──────────────────────────────────────

  visualization_msgs::msg::MarkerArray build_markers() const
  {
    visualization_msgs::msg::MarkerArray ma;
    if (!has_raceline_) {
      return ma;
    }
    const auto now = this->now();
    int arrow_id = 0;
    for (size_t i = 0; i + 1 < raceline_.size();
      i += static_cast<size_t>(arrow_interval_))
    {
      const auto yaw = compute_yaw(raceline_, i);
      if (!yaw.has_value()) {
        continue;
      }
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

      arrow.scale.x = 0.25;
      arrow.scale.y = 0.3;
      arrow.scale.z = 0.2;
      arrow.color.r = 1.0f;
      arrow.color.g = 1.0f;
      arrow.color.b = 1.0f;
      arrow.color.a = 0.5f;
      ma.markers.push_back(arrow);
    }
    return ma;
  }

  // ── Members ────────────────────────────────────────────────

  // GNSS measurement covariance
  double gnss_cov_good_thresh_{0.1};
  double gnss_cov_good_{0.1};
  double gnss_cov_mod_thresh_{0.5};
  double gnss_cov_mod_{0.25};
  double gnss_cov_poor_{100.0};
  double gnss_cov_roll_{100000.0};
  double gnss_cov_pitch_{100000.0};
  double gnss_cov_yaw_{100000.0};

  // Initial pose covariance
  double init_cov_x_{0.25};
  double init_cov_y_{0.25};
  double init_cov_yaw_{0.5};

  // Raceline
  std::vector<Point2D> raceline_;
  bool has_raceline_{false};
  int64_t arrow_interval_{2};
  double arrow_length_{1.0};

  // State
  bool initial_pose_published_{false};
  bool ekf_triggered_{false};
  std::mutex gnss_mutex_;
  geometry_msgs::msg::PoseWithCovarianceStamped::SharedPtr last_gnss_;
  sensor_msgs::msg::Imu imu_msg_;

  // ROS interfaces
  rclcpp::Publisher<geometry_msgs::msg::PoseWithCovarianceStamped>::SharedPtr pub_pose_;
  rclcpp::Publisher<geometry_msgs::msg::PoseWithCovarianceStamped>::SharedPtr pub_initial_pose_3d_;
  rclcpp::Publisher<visualization_msgs::msg::MarkerArray>::SharedPtr marker_pub_;
  rclcpp::Subscription<geometry_msgs::msg::PoseWithCovarianceStamped>::SharedPtr sub_gnss_;
  rclcpp::Subscription<sensor_msgs::msg::Imu>::SharedPtr sub_imu_;
  rclcpp::Client<std_srvs::srv::SetBool>::SharedPtr ekf_trigger_client_;
  rclcpp::Service<std_srvs::srv::Trigger>::SharedPtr service_;
  rclcpp::TimerBase::SharedPtr marker_timer_;
  visualization_msgs::msg::MarkerArray markers_;
};

int main(int argc, char * argv[])
{
  rclcpp::init(argc, argv);
  rclcpp::spin(std::make_shared<ImuGnssPoser>());
  rclcpp::shutdown();
  return 0;
}
