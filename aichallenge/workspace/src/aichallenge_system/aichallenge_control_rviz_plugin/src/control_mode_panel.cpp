#include "aichallenge_control_rviz_plugin/control_mode_panel.hpp"

#include <memory>

#include <rviz_common/display_context.hpp>
#include <rviz_common/ros_integration/ros_node_abstraction.hpp>

#include <pluginlib/class_list_macros.hpp>

namespace aichallenge_control_rviz_plugin
{

ControlModePanel::ControlModePanel(QWidget * parent)
: rviz_common::Panel(parent),
  topic_name_("/awsim/control_mode_request_topic"),
  topic_label_(new QLabel(this)),
  send_button_(new QPushButton(tr("Auto Mode Start"), this)),
  stop_button_(new QPushButton(tr("Auto Mode Stop"), this))
{
  topic_label_->setText(tr("Topic: %1").arg(QString::fromStdString(topic_name_)));

  auto * layout = new QVBoxLayout();
  layout->addWidget(topic_label_);
  layout->addWidget(send_button_);
  layout->addWidget(stop_button_);
  layout->addStretch(1);
  setLayout(layout);

  connect(send_button_, &QPushButton::clicked, this, &ControlModePanel::sendControlModeRequest);
  connect(stop_button_, &QPushButton::clicked, this, &ControlModePanel::sendControlModeStop);
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
}

void ControlModePanel::ensurePublisher()
{
  if (!publisher_ && ros_node_) {
    publisher_ = ros_node_->create_publisher<std_msgs::msg::Bool>(
      topic_name_, rclcpp::QoS(1));
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
