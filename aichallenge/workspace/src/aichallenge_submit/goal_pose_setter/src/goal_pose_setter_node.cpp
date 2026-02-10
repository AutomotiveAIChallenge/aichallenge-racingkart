#include "goal_pose_setter_node.hpp"

GoalPosePublisher::GoalPosePublisher() : Node("goal_pose_publisher")
{
    ekf_trigger_client_ = this->create_client<std_srvs::srv::SetBool>("/localization/trigger_node");
    timer_ = rclcpp::create_timer(
        this, get_clock(), std::chrono::milliseconds(300),
        std::bind(&GoalPosePublisher::on_timer, this));
}

void GoalPosePublisher::on_timer()
{
    if (++delay_count_ <= 10) {
        return;
    }

    if (!stop_initializing_pose_) {
        if (ekf_trigger_client_->service_is_ready()) {
            const auto req = std::make_shared<std_srvs::srv::SetBool::Request>();
            req->data = true;
            ekf_trigger_client_->async_send_request(req, [this](rclcpp::Client<std_srvs::srv::SetBool>::SharedFuture future)
            {
                stop_initializing_pose_ = future.get()->success;
                delay_count_ = 0;
                RCLCPP_INFO(this->get_logger(), "Complete localization trigger");
            });
            RCLCPP_INFO(this->get_logger(), "Call localization trigger");
        }
        return;
    }
}

int main(int argc, char const *argv[])
{
    rclcpp::init(argc, argv);
    rclcpp::spin(std::make_shared<GoalPosePublisher>());
    rclcpp::shutdown();
    return 0;
}
