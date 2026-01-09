#ifndef AICHALLENGE_CONTROL_RVIZ_PLUGIN__CONTROL_MODE_PANEL_HPP
#define AICHALLENGE_CONTROL_RVIZ_PLUGIN__CONTROL_MODE_PANEL_HPP

#include <memory>
#include <string>

#include <QLabel>
#include <QPushButton>
#include <QVBoxLayout>

#include <rclcpp/rclcpp.hpp>
#include <rviz_common/panel.hpp>
#include <std_msgs/msg/bool.hpp>

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

private:
  void publishControlMode(bool enable);
  void ensurePublisher();

  rclcpp::Node::SharedPtr ros_node_;
  rclcpp::Publisher<std_msgs::msg::Bool>::SharedPtr publisher_;
  std::string topic_name_;
  QLabel * topic_label_;
  QPushButton * send_button_;
  QPushButton * stop_button_;
};

}  // namespace aichallenge_control_rviz_plugin

#endif  // AICHALLENGE_CONTROL_RVIZ_PLUGIN__CONTROL_MODE_PANEL_HPP
