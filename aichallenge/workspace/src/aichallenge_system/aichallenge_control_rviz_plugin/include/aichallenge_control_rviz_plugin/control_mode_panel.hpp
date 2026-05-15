#ifndef AICHALLENGE_CONTROL_RVIZ_PLUGIN__CONTROL_MODE_PANEL_HPP
#define AICHALLENGE_CONTROL_RVIZ_PLUGIN__CONTROL_MODE_PANEL_HPP

#include <memory>
#include <string>
#include <thread>

#include <QLabel>
#include <QPushButton>
#include <QVBoxLayout>

#include <rclcpp/rclcpp.hpp>
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
  void ensurePublisher();
  void ensureInitialPoseWorker();
  void ensureInitialPoseServiceClient();

  rclcpp::Node::SharedPtr ros_node_;
  rclcpp::Publisher<std_msgs::msg::Bool>::SharedPtr publisher_;

  rclcpp::Client<std_srvs::srv::Trigger>::SharedPtr initial_pose_service_client_;

  std::string topic_name_;
  std::string initial_pose_service_name_;
  QLabel * topic_label_;
  QLabel * status_label_;
  QPushButton * send_button_;
  QPushButton * stop_button_;
  QPushButton * initial_pose_button_;

  rclcpp::Node::SharedPtr initial_pose_node_;
  std::shared_ptr<rclcpp::executors::MultiThreadedExecutor> initial_pose_executor_;
  std::thread initial_pose_spin_thread_;
};

}  // namespace aichallenge_control_rviz_plugin

#endif  // AICHALLENGE_CONTROL_RVIZ_PLUGIN__CONTROL_MODE_PANEL_HPP
