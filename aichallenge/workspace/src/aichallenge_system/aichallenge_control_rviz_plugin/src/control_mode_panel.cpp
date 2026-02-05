#include "aichallenge_control_rviz_plugin/control_mode_panel.hpp"

#include <chrono>
#include <cmath>
#include <limits>
#include <memory>
#include <utility>

#include <QHBoxLayout>
#include <QMetaObject>

#include <rclcpp/node_options.hpp>
#include <rmw/qos_profiles.h>

#include <rviz_common/display_context.hpp>
#include <rviz_common/ros_integration/ros_node_abstraction.hpp>

#include <pluginlib/class_list_macros.hpp>

namespace aichallenge_control_rviz_plugin
{

namespace
{
constexpr double kPi = 3.14159265358979323846;

geometry_msgs::msg::Quaternion createQuaternionFromYaw(const double yaw)
{
  geometry_msgs::msg::Quaternion q;
  q.x = 0.0;
  q.y = 0.0;
  q.z = std::sin(yaw * 0.5);
  q.w = std::cos(yaw * 0.5);
  return q;
}

double squaredDistance2d(const geometry_msgs::msg::Point & a, const geometry_msgs::msg::Point & b)
{
  const double dx = a.x - b.x;
  const double dy = a.y - b.y;
  return dx * dx + dy * dy;
}

bool isFinitePoint2d(const geometry_msgs::msg::Point & p)
{
  return std::isfinite(p.x) && std::isfinite(p.y);
}

bool tryComputeYawFromAdjacentPoints(
  const std::vector<geometry_msgs::msg::Point> & points, const size_t closest_index, double & yaw)
{
  if (points.size() < 2 || closest_index >= points.size()) {
    return false;
  }

  constexpr double kMinSegmentLength2 = 1.0e-6;

  auto try_segment = [&](const geometry_msgs::msg::Point & p0, const geometry_msgs::msg::Point & p1)
    -> bool
    {
      if (!isFinitePoint2d(p0) || !isFinitePoint2d(p1)) {
        return false;
      }
      const double dx = p1.x - p0.x;
      const double dy = p1.y - p0.y;
      const double d2 = dx * dx + dy * dy;
      if (!(d2 > kMinSegmentLength2)) {
        return false;
      }
      yaw = std::atan2(dy, dx);
      return true;
    };

  for (size_t i = closest_index; i + 1 < points.size(); ++i) {
    if (try_segment(points.at(i), points.at(i + 1))) {
      return true;
    }
  }

  for (size_t i = closest_index; i > 0; --i) {
    if (try_segment(points.at(i - 1), points.at(i))) {
      return true;
    }
  }

  return false;
}
}  // namespace

ControlModePanel::ControlModePanel(QWidget * parent)
: rviz_common::Panel(parent),
  topic_name_("/awsim/control_mode_request_topic"),
  gnss_pose_topic_name_("/sensing/gnss/pose_with_covariance"),
  trajectory_topic_name_("/planning/scenario_planning/trajectory"),
  initial_pose_topic_name_("/initialpose"),
  initial_pose_service_name_("/set_initial_pose"),
  topic_label_(new QLabel(this)),
  status_label_(new QLabel(this)),
  send_button_(new QPushButton(tr("Auto Mode Start"), this)),
  stop_button_(new QPushButton(tr("Auto Mode Stop"), this)),
  initial_pose_button_(new QPushButton(tr("Initial Pose Set"), this))
{
  topic_label_->setText(tr("Topic: %1").arg(QString::fromStdString(topic_name_)));
  status_label_->setText(tr("Initial Pose: waiting for GNSS and trajectory"));

  auto * layout = new QVBoxLayout();
  layout->addWidget(topic_label_);
  auto * auto_mode_layout = new QHBoxLayout();
  auto_mode_layout->addWidget(send_button_);
  auto_mode_layout->addWidget(stop_button_);
  layout->addLayout(auto_mode_layout);
  layout->addWidget(initial_pose_button_);
  layout->addWidget(status_label_);
  layout->addStretch(1);
  setLayout(layout);

  connect(send_button_, &QPushButton::clicked, this, &ControlModePanel::sendControlModeRequest);
  connect(stop_button_, &QPushButton::clicked, this, &ControlModePanel::sendControlModeStop);
  connect(initial_pose_button_, &QPushButton::clicked, this, &ControlModePanel::sendInitialPoseSet);
}

ControlModePanel::~ControlModePanel()
{
  if (initial_pose_executor_) {
    initial_pose_executor_->cancel();
  }
  if (initial_pose_spin_thread_.joinable()) {
    initial_pose_spin_thread_.join();
  }
}

void ControlModePanel::onInitialize()
{
  auto context = getDisplayContext();
  if (context) {
    auto node_abstraction = context->getRosNodeAbstraction().lock();
    if (node_abstraction) {
      ros_node_ = node_abstraction->get_raw_node();
    }
  }

  if (!ros_node_) {
    ros_node_ = rclcpp::Node::make_shared("aichallenge_control_mode_panel");
  }

  ensurePublisher();
  ensureInitialPoseWorker();
  ensureInitialPosePublisher();
  ensureInitialPoseService();
  ensureSubscriptions();
}

void ControlModePanel::ensurePublisher()
{
  if (!publisher_ && ros_node_) {
    publisher_ = ros_node_->create_publisher<std_msgs::msg::Bool>(
      topic_name_, rclcpp::QoS(1));
  }
}

void ControlModePanel::ensureInitialPosePublisher()
{
  auto node = initial_pose_node_ ? initial_pose_node_ : ros_node_;
  if (!initial_pose_publisher_ && node) {
    const auto qos = rclcpp::QoS(rclcpp::KeepLast(1)).durability_volatile().reliable();
    initial_pose_publisher_ =
      node->create_publisher<geometry_msgs::msg::PoseWithCovarianceStamped>(initial_pose_topic_name_, qos);
  }
}

void ControlModePanel::ensureInitialPoseService()
{
  ensureInitialPoseWorker();
  if (!initial_pose_service_ && initial_pose_node_) {
    initial_pose_service_ = initial_pose_node_->create_service<std_srvs::srv::Trigger>(
      initial_pose_service_name_,
      [this](
        const std::shared_ptr<std_srvs::srv::Trigger::Request> /*request*/,
        std::shared_ptr<std_srvs::srv::Trigger::Response> response)
      {
        // Make sure subscriptions/publisher are ready before waiting.
        ensureSubscriptions();
        ensureInitialPosePublisher();

        // Wait until GNSS and trajectory are ready, then publish /initialpose.
        // This service is executed on a dedicated MultiThreadedExecutor thread (not the RViz executor),
        // so waiting here won't stall RViz callbacks.
        constexpr int kWaitTimeoutSec = 60;
        const auto deadline =
          std::chrono::steady_clock::now() + std::chrono::seconds(kWaitTimeoutSec);

        while (std::chrono::steady_clock::now() < deadline) {
          {
            std::unique_lock<std::mutex> lock(data_mutex_);
            const bool ready =
              static_cast<bool>(last_gnss_pose_) && last_trajectory_points_.size() >= 2;
            if (ready) {
              break;
            }
            data_cv_.wait_for(lock, std::chrono::milliseconds(200));
          }

          // Trajectory type may not be known at service call time; keep trying to attach the subscription.
          ensureSubscriptions();
        }

        const bool have_gnss = [&]() -> bool {
          std::lock_guard<std::mutex> lock(data_mutex_);
          return static_cast<bool>(last_gnss_pose_);
        }();
        const bool have_traj = [&]() -> bool {
          std::lock_guard<std::mutex> lock(data_mutex_);
          return last_trajectory_points_.size() >= 2;
        }();

        if (!(have_gnss && have_traj)) {

          response->success = false;
          response->message = have_gnss ? "timeout waiting for trajectory" : "timeout waiting for GNSS";
          if (initial_pose_node_) {
            RCLCPP_ERROR(
              initial_pose_node_->get_logger(),
              "Initial pose service timed out (%ds): gnss=%s traj=%s",
              kWaitTimeoutSec, have_gnss ? "ready" : "missing", have_traj ? "ready" : "missing");
          }
          if (status_label_) {
            const QString text = have_gnss ? tr("Initial Pose: timeout waiting for trajectory")
                                           : tr("Initial Pose: timeout waiting for GNSS");
            QMetaObject::invokeMethod(status_label_, "setText", Qt::QueuedConnection, Q_ARG(QString, text));
          }
          return;
        }

        QString status;
        const bool ok = tryPublishInitialPose(status, /*warn_if_not_ready=*/false);
        response->success = ok;
        response->message = status.toStdString();

        if (status_label_) {
          QMetaObject::invokeMethod(status_label_, "setText", Qt::QueuedConnection, Q_ARG(QString, status));
        }
      },
      rmw_qos_profile_services_default,
      initial_pose_service_group_);
  }
}

void ControlModePanel::ensureInitialPoseWorker()
{
  if (initial_pose_node_) {
    return;
  }

  // Run subscriptions/service on a dedicated executor to avoid blocking RViz callbacks.
  rclcpp::NodeOptions options;
  if (ros_node_) {
    options.context(ros_node_->get_node_base_interface()->get_context());
  }
  initial_pose_node_ = std::make_shared<rclcpp::Node>("aichallenge_control_mode_panel_initial_pose", options);
  initial_pose_service_group_ =
    initial_pose_node_->create_callback_group(rclcpp::CallbackGroupType::MutuallyExclusive);
  initial_pose_sub_group_ =
    initial_pose_node_->create_callback_group(rclcpp::CallbackGroupType::Reentrant);
  initial_pose_executor_ = std::make_shared<rclcpp::executors::MultiThreadedExecutor>(
    rclcpp::ExecutorOptions(), 2);
  initial_pose_executor_->add_node(initial_pose_node_);
  initial_pose_spin_thread_ = std::thread([exec = initial_pose_executor_]() { exec->spin(); });

  RCLCPP_INFO(initial_pose_node_->get_logger(), "Initial pose worker started (service=%s).", initial_pose_service_name_.c_str());
}

void ControlModePanel::ensureSubscriptions()
{
  ensureInitialPoseWorker();
  if (!initial_pose_node_) {
    return;
  }

  if (!gnss_pose_sub_) {
    const auto qos = rclcpp::QoS(rclcpp::KeepLast(1)).durability_volatile().reliable();
    rclcpp::SubscriptionOptions options;
    options.callback_group = initial_pose_sub_group_;
    gnss_pose_sub_ = initial_pose_node_->create_subscription<geometry_msgs::msg::PoseWithCovarianceStamped>(
      gnss_pose_topic_name_, qos,
      [this](const geometry_msgs::msg::PoseWithCovarianceStamped::SharedPtr msg)
      {
        std::lock_guard<std::mutex> lock(data_mutex_);
        last_gnss_pose_ = msg;
        data_cv_.notify_all();
      },
      options);
    RCLCPP_INFO(initial_pose_node_->get_logger(), "Subscribed GNSS: %s", gnss_pose_topic_name_.c_str());
  }

  if (!trajectory_typed_sub_) {
    const auto qos = rclcpp::QoS(rclcpp::KeepLast(1)).durability_volatile().best_effort();
    rclcpp::SubscriptionOptions options;
    options.callback_group = initial_pose_sub_group_;
    trajectory_typed_sub_ = initial_pose_node_->create_subscription<autoware_auto_planning_msgs::msg::Trajectory>(
      trajectory_topic_name_, qos,
      [this](const autoware_auto_planning_msgs::msg::Trajectory::SharedPtr msg)
      {
        if (!msg) {
          return;
        }

        std::vector<geometry_msgs::msg::Point> points;
        points.reserve(msg->points.size());
        for (const auto & tp : msg->points) {
          geometry_msgs::msg::Point p;
          p.x = tp.pose.position.x;
          p.y = tp.pose.position.y;
          p.z = 0.0;
          points.push_back(p);
        }

        std::lock_guard<std::mutex> lock(data_mutex_);
        last_trajectory_points_ = std::move(points);
        last_trajectory_frame_id_ = msg->header.frame_id;
        data_cv_.notify_all();
      },
      options);
    RCLCPP_INFO(initial_pose_node_->get_logger(), "Subscribed Trajectory: %s", trajectory_topic_name_.c_str());
  }
}

void ControlModePanel::sendControlModeRequest()
{
  publishControlMode(true);
}

void ControlModePanel::sendControlModeStop()
{
  publishControlMode(false);
}

void ControlModePanel::sendInitialPoseSet()
{
  QString status;
  (void)tryPublishInitialPose(status);
  status_label_->setText(status);
}

bool ControlModePanel::tryPublishInitialPose(QString & status_text, bool warn_if_not_ready)
{
  status_text.clear();

  ensureSubscriptions();
  ensureInitialPosePublisher();

  if (!ros_node_) {
    status_text = tr("Initial Pose: ROS node is not available");
    return false;
  }
  if (!initial_pose_publisher_) {
    status_text = tr("Initial Pose: publisher is not available");
    return false;
  }

  geometry_msgs::msg::PoseWithCovarianceStamped::SharedPtr gnss_pose;
  std::vector<geometry_msgs::msg::Point> trajectory_points;
  std::string trajectory_frame_id;
  {
    std::lock_guard<std::mutex> lock(data_mutex_);
    gnss_pose = last_gnss_pose_;
    trajectory_points = last_trajectory_points_;
    trajectory_frame_id = last_trajectory_frame_id_;
  }

  if (!gnss_pose) {
    status_text = tr("Initial Pose: waiting for GNSS pose");
    if (warn_if_not_ready) {
      RCLCPP_WARN(ros_node_->get_logger(), "Initial pose set requested, but GNSS pose is not received yet.");
    }
    return false;
  }
  if (trajectory_points.empty()) {
    status_text = tr("Initial Pose: waiting for trajectory");
    if (warn_if_not_ready) {
      RCLCPP_WARN(ros_node_->get_logger(), "Initial pose set requested, but trajectory is not received yet.");
    }
    return false;
  }

  if (trajectory_points.size() < 2) {
    status_text = tr("Initial Pose: trajectory has too few points");
    RCLCPP_WARN(
      ros_node_->get_logger(), "Initial pose set requested, but trajectory has too few points: %zu",
      trajectory_points.size());
    return false;
  }

  if (
    !gnss_pose->header.frame_id.empty() && !trajectory_frame_id.empty() &&
    gnss_pose->header.frame_id != trajectory_frame_id)
  {
    RCLCPP_WARN(
      ros_node_->get_logger(),
      "Frame mismatch between GNSS (%s) and trajectory (%s); proceeding without TF transform.",
      gnss_pose->header.frame_id.c_str(), trajectory_frame_id.c_str());
  }

  const auto & gnss_point = gnss_pose->pose.pose.position;
  if (!isFinitePoint2d(gnss_point)) {
    status_text = tr("Initial Pose: GNSS pose is invalid");
    RCLCPP_WARN(ros_node_->get_logger(), "Initial pose set requested, but GNSS pose is invalid (NaN/Inf).");
    return false;
  }

  size_t closest_index = 0;
  double closest_dist2 = std::numeric_limits<double>::infinity();
  bool found = false;
  for (size_t i = 0; i < trajectory_points.size(); ++i) {
    const auto & point = trajectory_points.at(i);
    if (!isFinitePoint2d(point)) {
      continue;
    }
    const double dist2 = squaredDistance2d(point, gnss_point);
    if (dist2 < closest_dist2) {
      closest_dist2 = dist2;
      closest_index = i;
      found = true;
    }
  }

  if (!found) {
    status_text = tr("Initial Pose: trajectory points are invalid");
    RCLCPP_WARN(
      ros_node_->get_logger(),
      "Initial pose set requested, but all trajectory points are invalid (NaN/Inf).");
    return false;
  }

  double yaw = 0.0;
  if (!tryComputeYawFromAdjacentPoints(trajectory_points, closest_index, yaw)) {
    status_text = tr("Initial Pose: cannot compute yaw from trajectory");
    RCLCPP_WARN(
      ros_node_->get_logger(),
      "Initial pose set requested, but cannot compute yaw from adjacent trajectory points.");
    return false;
  }

  auto initial_pose = *gnss_pose;
  initial_pose.header.stamp = ros_node_->now();
  initial_pose.pose.pose.orientation = createQuaternionFromYaw(yaw);
  initial_pose.pose.covariance.at(35) = 0.5;

  initial_pose_publisher_->publish(initial_pose);

  const double yaw_deg = yaw * 180.0 / kPi;
  status_text = tr("Initial Pose: published (yaw %1 deg)").arg(yaw_deg, 0, 'f', 1);
  RCLCPP_INFO(
    ros_node_->get_logger(), "Published /initialpose from GNSS pose with trajectory yaw (%.3f deg).",
    yaw_deg);
  return true;
}

void ControlModePanel::publishControlMode(bool enable)
{
  ensurePublisher();
  if (!publisher_) {
    return;
  }

  std_msgs::msg::Bool msg;
  msg.data = enable;
  publisher_->publish(msg);
}

}  // namespace aichallenge_control_rviz_plugin

PLUGINLIB_EXPORT_CLASS(
  aichallenge_control_rviz_plugin::ControlModePanel,
  rviz_common::Panel)
