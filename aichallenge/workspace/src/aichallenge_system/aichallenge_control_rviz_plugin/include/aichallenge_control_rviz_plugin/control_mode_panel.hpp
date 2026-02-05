#ifndef AICHALLENGE_CONTROL_RVIZ_PLUGIN__CONTROL_MODE_PANEL_HPP
#define AICHALLENGE_CONTROL_RVIZ_PLUGIN__CONTROL_MODE_PANEL_HPP

#include <chrono>
#include <condition_variable>
#include <memory>
#include <mutex>
#include <string>
#include <thread>
#include <vector>

#include <QLabel>
#include <QPushButton>
#include <QVBoxLayout>

#include <autoware_auto_planning_msgs/msg/trajectory.hpp>
#include <geometry_msgs/msg/pose_with_covariance_stamped.hpp>
#include <rclcpp/rclcpp.hpp>
#include <rclcpp/callback_group.hpp>
#include <rclcpp/executors/multi_threaded_executor.hpp>
#include <rviz_common/panel.hpp>
#include <std_msgs/msg/bool.hpp>
#include <std_srvs/srv/trigger.hpp>

namespace aichallenge_control_rviz_plugin
{

class ControlModePanel : public rviz_common::Panel
{
  Q_OBJECT

public:
  explicit ControlModePanel(QWidget * parent = nullptr);
  ~ControlModePanel() override;

protected:
  void onInitialize() override;

private Q_SLOTS:
  void sendControlModeRequest();
  void sendControlModeStop();
  void sendInitialPoseSet();

private:
  void publishControlMode(bool enable);
  void ensureInitialPoseWorker();
  void ensurePublisher();
  void ensureInitialPosePublisher();
  void ensureInitialPoseService();
  void ensureSubscriptions();
  bool tryPublishInitialPose(QString & status_text, bool warn_if_not_ready = true);

  rclcpp::Node::SharedPtr ros_node_;
  rclcpp::Publisher<std_msgs::msg::Bool>::SharedPtr publisher_;

  rclcpp::Publisher<geometry_msgs::msg::PoseWithCovarianceStamped>::SharedPtr
    initial_pose_publisher_;
  rclcpp::Service<std_srvs::srv::Trigger>::SharedPtr initial_pose_service_;
  rclcpp::Subscription<geometry_msgs::msg::PoseWithCovarianceStamped>::SharedPtr gnss_pose_sub_;
  rclcpp::Subscription<autoware_auto_planning_msgs::msg::Trajectory>::SharedPtr trajectory_typed_sub_;

  std::string topic_name_;
  std::string gnss_pose_topic_name_;
  std::string trajectory_topic_name_;
  std::string initial_pose_topic_name_;
  std::string initial_pose_service_name_;
  QLabel * topic_label_;
  QLabel * status_label_;
  QPushButton * send_button_;
  QPushButton * stop_button_;
  QPushButton * initial_pose_button_;

  rclcpp::Node::SharedPtr initial_pose_node_;
  std::shared_ptr<rclcpp::executors::MultiThreadedExecutor> initial_pose_executor_;
  std::thread initial_pose_spin_thread_;
  rclcpp::CallbackGroup::SharedPtr initial_pose_service_group_;
  rclcpp::CallbackGroup::SharedPtr initial_pose_sub_group_;

  std::mutex data_mutex_;
  std::condition_variable data_cv_;
  geometry_msgs::msg::PoseWithCovarianceStamped::SharedPtr last_gnss_pose_;
  std::vector<geometry_msgs::msg::Point> last_trajectory_points_;
  std::string last_trajectory_frame_id_;
};

}  // namespace aichallenge_control_rviz_plugin

#endif  // AICHALLENGE_CONTROL_RVIZ_PLUGIN__CONTROL_MODE_PANEL_HPP
