#ifndef AICHALLENGE_CONTROL_RVIZ_PLUGIN__CONTROL_MODE_PANEL_HPP
#define AICHALLENGE_CONTROL_RVIZ_PLUGIN__CONTROL_MODE_PANEL_HPP

#include <memory>
#include <mutex>
#include <string>
#include <vector>

#include <QLabel>
#include <QPushButton>
#include <QVBoxLayout>

#include <geometry_msgs/msg/pose_with_covariance_stamped.hpp>
#include <rclcpp/rclcpp.hpp>
#include <rviz_common/panel.hpp>
#include <std_msgs/msg/bool.hpp>
#include <std_srvs/srv/trigger.hpp>

struct rosidl_message_type_support_t;

namespace rclcpp
{
class GenericSubscription;
class SerializedMessage;
}  // namespace rclcpp

namespace rcpputils
{
class SharedLibrary;
}  // namespace rcpputils

namespace rosidl_typesupport_introspection_cpp
{
struct MessageMembers_s;
}  // namespace rosidl_typesupport_introspection_cpp

namespace aichallenge_control_rviz_plugin
{

class ControlModePanel : public rviz_common::Panel
{
  Q_OBJECT

public:
  explicit ControlModePanel(QWidget * parent = nullptr);

protected:
  void onInitialize() override;

private Q_SLOTS:
  void sendControlModeRequest();
  void sendControlModeStop();
  void sendInitialPoseSet();

private:
  void publishControlMode(bool enable);
  void ensurePublisher();
  void ensureInitialPosePublisher();
  void ensureInitialPoseService();
  void ensureSubscriptions();
  bool ensureTrajectoryTypeSupport();
  bool parseTrajectoryMessage(
    const rclcpp::SerializedMessage & serialized, std::vector<geometry_msgs::msg::Point> & points,
    std::string & frame_id);
  bool tryPublishInitialPose(QString & status_text);

  rclcpp::Node::SharedPtr ros_node_;
  rclcpp::Publisher<std_msgs::msg::Bool>::SharedPtr publisher_;

  rclcpp::Publisher<geometry_msgs::msg::PoseWithCovarianceStamped>::SharedPtr
    initial_pose_publisher_;
  rclcpp::Service<std_srvs::srv::Trigger>::SharedPtr initial_pose_service_;
  rclcpp::Subscription<geometry_msgs::msg::PoseWithCovarianceStamped>::SharedPtr gnss_pose_sub_;
  std::shared_ptr<rclcpp::GenericSubscription> trajectory_sub_;

  std::shared_ptr<rcpputils::SharedLibrary> trajectory_ts_lib_cpp_;
  std::shared_ptr<rcpputils::SharedLibrary> trajectory_ts_lib_introspection_;
  const rosidl_message_type_support_t * trajectory_ts_cpp_{nullptr};
  const rosidl_message_type_support_t * trajectory_ts_introspection_{nullptr};
  const rosidl_typesupport_introspection_cpp::MessageMembers_s * trajectory_members_{nullptr};

  std::string topic_name_;
  std::string gnss_pose_topic_name_;
  std::string trajectory_topic_name_;
  std::string trajectory_topic_type_name_;
  std::string initial_pose_topic_name_;
  std::string initial_pose_service_name_;
  QLabel * topic_label_;
  QLabel * status_label_;
  QPushButton * send_button_;
  QPushButton * stop_button_;
  QPushButton * initial_pose_button_;

  std::mutex data_mutex_;
  geometry_msgs::msg::PoseWithCovarianceStamped::SharedPtr last_gnss_pose_;
  std::vector<geometry_msgs::msg::Point> last_trajectory_points_;
  std::string last_trajectory_frame_id_;
};

}  // namespace aichallenge_control_rviz_plugin

#endif  // AICHALLENGE_CONTROL_RVIZ_PLUGIN__CONTROL_MODE_PANEL_HPP
