#include "aichallenge_control_rviz_plugin/control_mode_panel.hpp"

#include <chrono>
#include <memory>
#include <string>

#include <QHBoxLayout>
#include <QMetaObject>

#include <rclcpp/node_options.hpp>

#include <rviz_common/display_context.hpp>
#include <rviz_common/ros_integration/ros_node_abstraction.hpp>

#include <pluginlib/class_list_macros.hpp>

namespace aichallenge_control_rviz_plugin
{

ControlModePanel::ControlModePanel(QWidget * parent)
: rviz_common::Panel(parent),
  topic_name_("/awsim/control_mode_request_topic"),
  initial_pose_service_name_("/set_initial_pose"),
  topic_label_(new QLabel(this)),
  status_label_(new QLabel(this)),
  send_button_(new QPushButton(tr("Auto Mode Start"), this)),
  stop_button_(new QPushButton(tr("Auto Mode Stop"), this)),
  initial_pose_button_(new QPushButton(tr("Initial Pose Set"), this))
{
  topic_label_->setText(tr("Topic: %1").arg(QString::fromStdString(topic_name_)));
  status_label_->setText(tr("Ready"));

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
  // Safety: cancel() signals the executor to stop, join() blocks until the
  // executor thread exits (completing any in-flight callback). After join(),
  // no more callbacks can run. Any Qt::QueuedConnection events already posted
  // are cleaned up by QObject::~QObject() (removePostedEvents) when the child
  // widgets are destroyed, so no use-after-free can occur.
  if (initial_pose_executor_) {
    initial_pose_executor_->cancel();
  }
  if (initial_pose_spin_thread_.joinable()) {
    initial_pose_spin_thread_.join();
  }
  initial_pose_service_client_.reset();
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
  ensureInitialPoseServiceClient();
}

void ControlModePanel::ensurePublisher()
{
  if (!publisher_ && ros_node_) {
    publisher_ = ros_node_->create_publisher<std_msgs::msg::Bool>(
      topic_name_, rclcpp::QoS(1));
  }
}

void ControlModePanel::ensureInitialPoseWorker()
{
  if (initial_pose_node_) {
    return;
  }

  rclcpp::NodeOptions options;
  if (ros_node_) {
    options.context(ros_node_->get_node_base_interface()->get_context());
  }
  initial_pose_node_ = std::make_shared<rclcpp::Node>(
    "aichallenge_control_mode_panel_initial_pose", options);
  initial_pose_executor_ = std::make_shared<rclcpp::executors::MultiThreadedExecutor>(
    rclcpp::ExecutorOptions(), 1);
  initial_pose_executor_->add_node(initial_pose_node_);
  initial_pose_spin_thread_ = std::thread([exec = initial_pose_executor_]() { exec->spin(); });
}

void ControlModePanel::ensureInitialPoseServiceClient()
{
  ensureInitialPoseWorker();
  if (!initial_pose_service_client_ && initial_pose_node_) {
    initial_pose_service_client_ = initial_pose_node_->create_client<std_srvs::srv::Trigger>(
      initial_pose_service_name_);
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
  ensureInitialPoseServiceClient();
  if (!initial_pose_service_client_) {
    status_label_->setText(tr("Initial Pose: service client not available"));
    return;
  }

  if (!initial_pose_service_client_->wait_for_service(std::chrono::seconds(0))) {
    status_label_->setText(tr("Initial Pose: service not available"));
    return;
  }

  status_label_->setText(tr("Initial Pose: calling service..."));
  initial_pose_button_->setEnabled(false);

  auto request = std::make_shared<std_srvs::srv::Trigger::Request>();
  initial_pose_service_client_->async_send_request(
    request,
    [this](rclcpp::Client<std_srvs::srv::Trigger>::SharedFuture future) {
      try {
        auto response = future.get();
        const QString status = response->success
          ? QString::fromStdString("Initial Pose: " + response->message)
          : QString::fromStdString("Initial Pose: FAILED - " + response->message);
        QMetaObject::invokeMethod(
          status_label_, "setText", Qt::QueuedConnection, Q_ARG(QString, status));
      } catch (const std::exception & e) {
        const QString status =
          QString("Initial Pose: service call failed - %1").arg(e.what());
        QMetaObject::invokeMethod(
          status_label_, "setText", Qt::QueuedConnection, Q_ARG(QString, status));
      }
      QMetaObject::invokeMethod(
        initial_pose_button_, "setEnabled", Qt::QueuedConnection, Q_ARG(bool, true));
    });
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
