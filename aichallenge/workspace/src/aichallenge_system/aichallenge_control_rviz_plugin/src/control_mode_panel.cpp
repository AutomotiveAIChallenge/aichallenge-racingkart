#include "aichallenge_control_rviz_plugin/control_mode_panel.hpp"

#include <chrono>
#include <cmath>
#include <cstddef>
#include <cstdint>
#include <cstring>
#include <limits>
#include <memory>
#include <new>
#include <stdexcept>
#include <utility>

#include <QHBoxLayout>

#include <rcpputils/shared_library.hpp>

#include <rclcpp/create_generic_subscription.hpp>
#include <rclcpp/serialization.hpp>
#include <rclcpp/serialized_message.hpp>
#include <rclcpp/typesupport_helpers.hpp>

#include <rosidl_runtime_c/message_type_support_struct.h>
#include <rosidl_typesupport_introspection_cpp/field_types.hpp>
#include <rosidl_typesupport_introspection_cpp/message_introspection.hpp>

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

const rosidl_typesupport_introspection_cpp::MessageMembers_s * toMessageMembers(
  const rosidl_message_type_support_t * type_support)
{
  if (!type_support || !type_support->data) {
    return nullptr;
  }
  return static_cast<const rosidl_typesupport_introspection_cpp::MessageMembers_s *>(type_support->data);
}

const rosidl_typesupport_introspection_cpp::MessageMember * findMember(
  const rosidl_typesupport_introspection_cpp::MessageMembers_s * members, const char * name)
{
  if (!members || !members->members_ || !name) {
    return nullptr;
  }

  for (uint32_t i = 0; i < members->member_count_; ++i) {
    const auto & member = members->members_[i];
    if (member.name_ && std::strcmp(member.name_, name) == 0) {
      return &member;
    }
  }

  return nullptr;
}

bool readNumericAsDouble(
  const rosidl_typesupport_introspection_cpp::MessageMember * member, const void * message,
  double & out)
{
  if (!member || !message) {
    return false;
  }
  const auto * ptr =
    reinterpret_cast<const uint8_t *>(message) + static_cast<size_t>(member->offset_);

  switch (member->type_id_) {
    case rosidl_typesupport_introspection_cpp::ROS_TYPE_DOUBLE:
      out = *reinterpret_cast<const double *>(ptr);
      return true;
    case rosidl_typesupport_introspection_cpp::ROS_TYPE_FLOAT:
      out = static_cast<double>(*reinterpret_cast<const float *>(ptr));
      return true;
    default:
      return false;
  }
}

class MessageMemory final
{
public:
  explicit MessageMemory(const rosidl_typesupport_introspection_cpp::MessageMembers_s * members)
  : members_(members), memory_(nullptr)
  {
    if (!members_ || !members_->init_function || !members_->fini_function) {
      throw std::runtime_error("message introspection members are not initialized");
    }
    memory_ = ::operator new(members_->size_of_);
    members_->init_function(memory_, rosidl_runtime_cpp::MessageInitialization::ALL);
  }

  MessageMemory(const MessageMemory &) = delete;
  MessageMemory & operator=(const MessageMemory &) = delete;

  ~MessageMemory()
  {
    if (memory_) {
      members_->fini_function(memory_);
      ::operator delete(memory_);
    }
  }

  void * get() { return memory_; }
  const void * get() const { return memory_; }

private:
  const rosidl_typesupport_introspection_cpp::MessageMembers_s * members_;
  void * memory_;
};

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
  trajectory_topic_type_name_("autoware_auto_planning_msgs/msg/Trajectory"),
  initial_pose_topic_name_("/initialpose"),
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
  ensureInitialPosePublisher();
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
  if (!initial_pose_publisher_ && ros_node_) {
    const auto qos = rclcpp::QoS(rclcpp::KeepLast(1)).durability_volatile().reliable();
    initial_pose_publisher_ =
      ros_node_->create_publisher<geometry_msgs::msg::PoseWithCovarianceStamped>(
        initial_pose_topic_name_, qos);
  }
}

void ControlModePanel::ensureSubscriptions()
{
  if (!ros_node_) {
    return;
  }

  if (!gnss_pose_sub_) {
    const auto qos = rclcpp::QoS(rclcpp::KeepLast(1)).durability_volatile().best_effort();
    gnss_pose_sub_ = ros_node_->create_subscription<geometry_msgs::msg::PoseWithCovarianceStamped>(
      gnss_pose_topic_name_, qos,
      [this](const geometry_msgs::msg::PoseWithCovarianceStamped::SharedPtr msg)
      {
        std::lock_guard<std::mutex> lock(data_mutex_);
        last_gnss_pose_ = msg;
      });
  }

  if (!trajectory_sub_) {
    const auto qos = rclcpp::QoS(rclcpp::KeepLast(1)).durability_volatile().best_effort();
    if (!ensureTrajectoryTypeSupport()) {
      return;
    }

    try {
      trajectory_sub_ = rclcpp::create_generic_subscription(
        ros_node_->get_node_topics_interface(), trajectory_topic_name_, trajectory_topic_type_name_,
        qos,
        [this](std::shared_ptr<rclcpp::SerializedMessage> msg)
        {
          if (!msg) {
            return;
          }

          std::vector<geometry_msgs::msg::Point> points;
          std::string frame_id;
          if (!parseTrajectoryMessage(*msg, points, frame_id)) {
            RCLCPP_WARN_THROTTLE(
              ros_node_->get_logger(), *ros_node_->get_clock(), std::chrono::milliseconds(5000).count(),
              "Failed to parse trajectory message on %s", trajectory_topic_name_.c_str());
            return;
          }

          std::lock_guard<std::mutex> lock(data_mutex_);
          last_trajectory_points_ = std::move(points);
          last_trajectory_frame_id_ = std::move(frame_id);
        });
    } catch (const std::exception & e) {
      RCLCPP_ERROR(
        ros_node_->get_logger(), "Failed to create generic subscription for %s (%s): %s",
        trajectory_topic_name_.c_str(), trajectory_topic_type_name_.c_str(), e.what());
      trajectory_sub_.reset();
    }
  }
}

bool ControlModePanel::ensureTrajectoryTypeSupport()
{
  if (trajectory_members_ && trajectory_ts_cpp_ && trajectory_ts_introspection_) {
    return true;
  }
  if (!ros_node_) {
    return false;
  }

  try {
    trajectory_ts_lib_cpp_ =
      rclcpp::get_typesupport_library(trajectory_topic_type_name_, "rosidl_typesupport_cpp");
    trajectory_ts_cpp_ = rclcpp::get_typesupport_handle(
      trajectory_topic_type_name_, "rosidl_typesupport_cpp", *trajectory_ts_lib_cpp_);

    trajectory_ts_lib_introspection_ = rclcpp::get_typesupport_library(
      trajectory_topic_type_name_, "rosidl_typesupport_introspection_cpp");
    trajectory_ts_introspection_ = rclcpp::get_typesupport_handle(
      trajectory_topic_type_name_, "rosidl_typesupport_introspection_cpp", *trajectory_ts_lib_introspection_);

    trajectory_members_ = toMessageMembers(trajectory_ts_introspection_);
    if (!trajectory_members_) {
      throw std::runtime_error("trajectory introspection data is null");
    }
  } catch (const std::exception & e) {
    RCLCPP_ERROR(
      ros_node_->get_logger(), "Failed to load typesupport for %s: %s",
      trajectory_topic_type_name_.c_str(), e.what());
    trajectory_ts_lib_cpp_.reset();
    trajectory_ts_lib_introspection_.reset();
    trajectory_ts_cpp_ = nullptr;
    trajectory_ts_introspection_ = nullptr;
    trajectory_members_ = nullptr;
    return false;
  }

  return true;
}

bool ControlModePanel::parseTrajectoryMessage(
  const rclcpp::SerializedMessage & serialized, std::vector<geometry_msgs::msg::Point> & points,
  std::string & frame_id)
{
  points.clear();
  frame_id.clear();

  if (!ensureTrajectoryTypeSupport() || !trajectory_members_ || !trajectory_ts_cpp_) {
    return false;
  }

  try {
    MessageMemory message(trajectory_members_);
    {
      rclcpp::SerializationBase serializer(trajectory_ts_cpp_);
      serializer.deserialize_message(&serialized, message.get());
    }

    const auto * header_member = findMember(trajectory_members_, "header");
    if (
      header_member &&
      header_member->type_id_ == rosidl_typesupport_introspection_cpp::ROS_TYPE_MESSAGE)
    {
      const auto * header_members = toMessageMembers(header_member->members_);
      const auto * frame_id_member = findMember(header_members, "frame_id");
      if (
        frame_id_member &&
        frame_id_member->type_id_ == rosidl_typesupport_introspection_cpp::ROS_TYPE_STRING)
      {
        const auto * header_ptr =
          reinterpret_cast<const uint8_t *>(message.get()) + header_member->offset_;
        const auto * frame_ptr = header_ptr + frame_id_member->offset_;
        frame_id = *reinterpret_cast<const std::string *>(frame_ptr);
      }
    }

    const auto * points_member = findMember(trajectory_members_, "points");
    if (
      !points_member ||
      points_member->type_id_ != rosidl_typesupport_introspection_cpp::ROS_TYPE_MESSAGE ||
      !points_member->is_array_ || !points_member->size_function ||
      !points_member->get_const_function)
    {
      return false;
    }

    const auto * points_ptr =
      reinterpret_cast<const uint8_t *>(message.get()) + points_member->offset_;
    const size_t points_size = points_member->size_function(points_ptr);
    points.reserve(points_size);

    const auto * traj_point_members = toMessageMembers(points_member->members_);
    const auto * pose_member = findMember(traj_point_members, "pose");
    if (
      !pose_member ||
      pose_member->type_id_ != rosidl_typesupport_introspection_cpp::ROS_TYPE_MESSAGE)
    {
      return false;
    }
    const auto * pose_members = toMessageMembers(pose_member->members_);
    const auto * position_member = findMember(pose_members, "position");
    if (
      !position_member ||
      position_member->type_id_ != rosidl_typesupport_introspection_cpp::ROS_TYPE_MESSAGE)
    {
      return false;
    }
    const auto * position_members = toMessageMembers(position_member->members_);
    const auto * x_member = findMember(position_members, "x");
    const auto * y_member = findMember(position_members, "y");
    if (!x_member || !y_member) {
      return false;
    }

    for (size_t i = 0; i < points_size; ++i) {
      const auto * traj_point = points_member->get_const_function(points_ptr, i);
      if (!traj_point) {
        continue;
      }

      const auto * pose_ptr =
        reinterpret_cast<const uint8_t *>(traj_point) + static_cast<size_t>(pose_member->offset_);
      const auto * position_ptr = pose_ptr + static_cast<size_t>(position_member->offset_);

      double x = 0.0;
      double y = 0.0;
      if (
        !readNumericAsDouble(x_member, position_ptr, x) ||
        !readNumericAsDouble(y_member, position_ptr, y))
      {
        continue;
      }

      geometry_msgs::msg::Point p;
      p.x = x;
      p.y = y;
      p.z = 0.0;
      points.push_back(p);
    }

    return true;
  } catch (const std::exception & e) {
    if (ros_node_) {
      RCLCPP_DEBUG(ros_node_->get_logger(), "Failed to parse trajectory message: %s", e.what());
    }
    points.clear();
    frame_id.clear();
    return false;
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
  ensureSubscriptions();
  ensureInitialPosePublisher();

  if (!ros_node_) {
    status_label_->setText(tr("Initial Pose: ROS node is not available"));
    return;
  }
  if (!initial_pose_publisher_) {
    status_label_->setText(tr("Initial Pose: publisher is not available"));
    return;
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
    status_label_->setText(tr("Initial Pose: waiting for GNSS pose"));
    RCLCPP_WARN(ros_node_->get_logger(), "Initial pose set requested, but GNSS pose is not received yet.");
    return;
  }
  if (trajectory_points.empty()) {
    status_label_->setText(tr("Initial Pose: waiting for trajectory"));
    RCLCPP_WARN(ros_node_->get_logger(), "Initial pose set requested, but trajectory is not received yet.");
    return;
  }

  if (trajectory_points.size() < 2) {
    status_label_->setText(tr("Initial Pose: trajectory has too few points"));
    RCLCPP_WARN(
      ros_node_->get_logger(), "Initial pose set requested, but trajectory has too few points: %zu",
      trajectory_points.size());
    return;
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
    status_label_->setText(tr("Initial Pose: GNSS pose is invalid"));
    RCLCPP_WARN(ros_node_->get_logger(), "Initial pose set requested, but GNSS pose is invalid (NaN/Inf).");
    return;
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
    status_label_->setText(tr("Initial Pose: trajectory points are invalid"));
    RCLCPP_WARN(
      ros_node_->get_logger(),
      "Initial pose set requested, but all trajectory points are invalid (NaN/Inf).");
    return;
  }

  double yaw = 0.0;
  if (!tryComputeYawFromAdjacentPoints(trajectory_points, closest_index, yaw)) {
    status_label_->setText(tr("Initial Pose: cannot compute yaw from trajectory"));
    RCLCPP_WARN(
      ros_node_->get_logger(),
      "Initial pose set requested, but cannot compute yaw from adjacent trajectory points.");
    return;
  }

  auto initial_pose = *gnss_pose;
  initial_pose.header.stamp = ros_node_->now();
  initial_pose.pose.pose.orientation = createQuaternionFromYaw(yaw);
  initial_pose.pose.covariance.at(35) = 0.5;

  initial_pose_publisher_->publish(initial_pose);

  const double yaw_deg = yaw * 180.0 / kPi;
  status_label_->setText(tr("Initial Pose: published (yaw %1 deg)").arg(yaw_deg, 0, 'f', 1));
  RCLCPP_INFO(
    ros_node_->get_logger(), "Published /initialpose from GNSS pose with trajectory yaw (%.3f deg).",
    yaw_deg);
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
