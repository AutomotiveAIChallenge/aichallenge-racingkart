// Copyright 2023 Tier IV, Inc. All rights reserved.
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

#include <rclcpp/rclcpp.hpp>
#include <autoware_auto_planning_msgs/msg/trajectory.hpp>
#include <geometry_msgs/msg/pose.hpp>
#include <geometry_msgs/msg/quaternion.hpp>
#include <algorithm>
#include <cmath>
#include <filesystem>
#include <fstream>
#include <limits>
#include <string>
#include <vector>
#include <sstream>

using Trajectory = autoware_auto_planning_msgs::msg::Trajectory;
using TrajectoryPoint = autoware_auto_planning_msgs::msg::TrajectoryPoint;

class CSVToTrajectory : public rclcpp::Node
{
public:
  CSVToTrajectory() : Node("csv_to_trajectory_node")
  {
    const auto rb_qos = rclcpp::QoS(rclcpp::KeepLast(1)).durability_volatile().best_effort();
    pub_ = this->create_publisher<Trajectory>("trajectory", rb_qos);
    set_parameter_callback_handle_ = this->add_on_set_parameters_callback(
      std::bind(&CSVToTrajectory::on_parameter_event, this, std::placeholders::_1));


    declare_parameter("csv_path", "");
    z_= declare_parameter<float>("z");
    enable_resampling_ = declare_parameter<bool>("enable_resampling", true);
    resample_interval_m_ = declare_parameter<double>("resample_interval_m", 0.5);
    enable_velocity_profile_ = declare_parameter<bool>("enable_velocity_profile", true);
    velocity_profile_max_velocity_mps_ =
      declare_parameter<double>("velocity_profile_max_velocity_mps", 8.3);
    velocity_profile_min_velocity_mps_ =
      declare_parameter<double>("velocity_profile_min_velocity_mps", 1.2);
    velocity_profile_max_lateral_accel_mps2_ =
      declare_parameter<double>("velocity_profile_max_lateral_accel_mps2", 3.0);
    velocity_profile_max_accel_mps2_ =
      declare_parameter<double>("velocity_profile_max_accel_mps2", 5.0);
    velocity_profile_max_decel_mps2_ =
      declare_parameter<double>("velocity_profile_max_decel_mps2", 2.5);
    velocity_profile_use_csv_velocity_limit_ =
      declare_parameter<bool>("velocity_profile_use_csv_velocity_limit", false);
    std::string csv_path = get_parameter("csv_path").as_string();
    current_csv_path_ = csv_path;
    
    if (csv_path.empty()) {
      RCLCPP_ERROR(get_logger(), "CSV path is not specified");
      return;
    }
    
    if (!loadCSVTrajectory(csv_path)) {
      RCLCPP_ERROR(get_logger(), "Failed to load CSV file: %s", csv_path.c_str());
      return;
    }
    
    RCLCPP_INFO(get_logger(), "Loaded trajectory from CSV with %zu points", csv_trajectory_.points.size());

    timer_ = rclcpp::create_timer(
      this, get_clock(), std::chrono::seconds(1),
      std::bind(&CSVToTrajectory::publish_trajectory, this));

  }

private:
  bool loadCSVTrajectory(const std::string & csv_path)
  {
    std::ifstream file(csv_path);
    if (!file.is_open()) {
      return false;
    }
    
    std::string line;
    std::getline(file, line);
    
    csv_trajectory_.header.stamp = this->now();
    csv_trajectory_.header.frame_id = "map";

    csv_trajectory_.points.clear();
    
    while (std::getline(file, line)) {
      std::stringstream ss(line);
      std::string token;
      std::vector<double> values;
      
      while (std::getline(ss, token, ',')) {
        values.push_back(std::stod(token));
      }
      
      if (values.size() != 8) {
        RCLCPP_WARN(get_logger(), "Invalid CSV line format, expected 8 values");
        continue;
      }
      
      TrajectoryPoint point;
      point.pose.position.x = values[0];
      point.pose.position.y = values[1];
      point.pose.position.z = z_;

      point.pose.orientation.x = values[3];
      point.pose.orientation.y = values[4];
      point.pose.orientation.z = values[5];
      point.pose.orientation.w = values[6];
      
      point.longitudinal_velocity_mps = values[7];
      
      point.lateral_velocity_mps = 0.0;
      point.acceleration_mps2 = 0.0;
      point.heading_rate_rps = 0.0;
      
      csv_trajectory_.points.push_back(point);
    }

    const auto raw_points = csv_trajectory_.points.size();
    if (enable_resampling_) {
      csv_trajectory_ = resampleTrajectory(csv_trajectory_, resample_interval_m_);
      RCLCPP_INFO(
        get_logger(), "Resampled trajectory from %zu to %zu points at %.2f m spacing",
        raw_points, csv_trajectory_.points.size(), resample_interval_m_);
    }

    if (enable_velocity_profile_) {
      applyVelocityProfile(&csv_trajectory_);
    }

    return !csv_trajectory_.points.empty();
  }

  Trajectory resampleTrajectory(const Trajectory & input, const double interval_m) const
  {
    if (input.points.size() < 2 || interval_m <= 0.0 || !std::isfinite(interval_m)) {
      return input;
    }

    std::vector<TrajectoryPoint> source;
    source.reserve(input.points.size());
    source.push_back(input.points.front());
    for (size_t i = 1; i < input.points.size(); ++i) {
      const auto & prev = source.back().pose.position;
      const auto & curr = input.points.at(i).pose.position;
      const auto distance = std::hypot(curr.x - prev.x, curr.y - prev.y);
      if (distance > std::numeric_limits<double>::epsilon()) {
        source.push_back(input.points.at(i));
      }
    }

    if (source.size() < 2) {
      return input;
    }

    std::vector<double> cumulative_distance(source.size(), 0.0);
    for (size_t i = 1; i < source.size(); ++i) {
      const auto & prev = source.at(i - 1).pose.position;
      const auto & curr = source.at(i).pose.position;
      cumulative_distance.at(i) =
        cumulative_distance.at(i - 1) + std::hypot(curr.x - prev.x, curr.y - prev.y);
    }

    const double total_length = cumulative_distance.back();
    if (total_length <= interval_m) {
      return input;
    }

    Trajectory output;
    output.header = input.header;
    output.points.reserve(static_cast<size_t>(std::ceil(total_length / interval_m)) + 1);

    size_t segment_index = 0;
    for (double target_s = 0.0; target_s < total_length; target_s += interval_m) {
      while (
        segment_index + 1 < cumulative_distance.size() &&
        cumulative_distance.at(segment_index + 1) < target_s) {
        ++segment_index;
      }
      output.points.push_back(interpolatePoint(source, cumulative_distance, segment_index, target_s));
    }
    output.points.push_back(source.back());

    return output;
  }

  TrajectoryPoint interpolatePoint(
    const std::vector<TrajectoryPoint> & source, const std::vector<double> & cumulative_distance,
    const size_t segment_index, const double target_s) const
  {
    const auto next_index = std::min(segment_index + 1, source.size() - 1);
    const auto & from = source.at(segment_index);
    const auto & to = source.at(next_index);
    const double segment_length =
      std::max(cumulative_distance.at(next_index) - cumulative_distance.at(segment_index), 1.0e-6);
    const double ratio =
      std::clamp((target_s - cumulative_distance.at(segment_index)) / segment_length, 0.0, 1.0);

    TrajectoryPoint point = from;
    point.pose.position.x =
      from.pose.position.x + ratio * (to.pose.position.x - from.pose.position.x);
    point.pose.position.y =
      from.pose.position.y + ratio * (to.pose.position.y - from.pose.position.y);
    point.pose.position.z = z_;

    const double yaw = std::atan2(
      to.pose.position.y - from.pose.position.y, to.pose.position.x - from.pose.position.x);
    point.pose.orientation.x = 0.0;
    point.pose.orientation.y = 0.0;
    point.pose.orientation.z = std::sin(yaw * 0.5);
    point.pose.orientation.w = std::cos(yaw * 0.5);

    point.longitudinal_velocity_mps =
      from.longitudinal_velocity_mps +
      ratio * (to.longitudinal_velocity_mps - from.longitudinal_velocity_mps);
    point.lateral_velocity_mps = 0.0;
    point.acceleration_mps2 = 0.0;
    point.heading_rate_rps = 0.0;
    return point;
  }

  void applyVelocityProfile(Trajectory * trajectory) const
  {
    if (trajectory->points.size() < 3) {
      return;
    }

    const std::vector<TrajectoryPoint> points(trajectory->points.begin(), trajectory->points.end());
    const auto cumulative_distance = calculateCumulativeDistance(points);
    std::vector<double> velocity_limits(points.size(), velocity_profile_max_velocity_mps_);

    for (size_t i = 0; i < points.size(); ++i) {
      const double csv_velocity =
        std::max(0.0, static_cast<double>(points.at(i).longitudinal_velocity_mps));
      const double source_velocity =
        velocity_profile_use_csv_velocity_limit_ ? csv_velocity : velocity_profile_max_velocity_mps_;
      const double curvature = std::abs(calculateCurvature(points, i));
      double lateral_velocity_limit = velocity_profile_max_velocity_mps_;
      if (curvature > 1.0e-4) {
        lateral_velocity_limit =
          std::sqrt(std::max(0.0, velocity_profile_max_lateral_accel_mps2_) / curvature);
      }

      double velocity_limit =
        std::min({source_velocity, velocity_profile_max_velocity_mps_, lateral_velocity_limit});
      if (velocity_limit > velocity_profile_min_velocity_mps_) {
        velocity_limit = std::max(velocity_limit, velocity_profile_min_velocity_mps_);
      }
      velocity_limits.at(i) = std::max(0.0, velocity_limit);
    }

    for (size_t reverse_i = velocity_limits.size() - 1; reverse_i > 0; --reverse_i) {
      const size_t i = reverse_i - 1;
      const double ds = std::max(cumulative_distance.at(i + 1) - cumulative_distance.at(i), 1.0e-6);
      const double reachable_velocity = std::sqrt(
        velocity_limits.at(i + 1) * velocity_limits.at(i + 1) +
        2.0 * std::max(0.0, velocity_profile_max_decel_mps2_) * ds);
      velocity_limits.at(i) = std::min(velocity_limits.at(i), reachable_velocity);
    }

    for (size_t i = 1; i < velocity_limits.size(); ++i) {
      const double ds = std::max(cumulative_distance.at(i) - cumulative_distance.at(i - 1), 1.0e-6);
      const double reachable_velocity = std::sqrt(
        velocity_limits.at(i - 1) * velocity_limits.at(i - 1) +
        2.0 * std::max(0.0, velocity_profile_max_accel_mps2_) * ds);
      velocity_limits.at(i) = std::min(velocity_limits.at(i), reachable_velocity);
    }

    double min_velocity = std::numeric_limits<double>::max();
    double max_velocity = 0.0;
    for (size_t i = 0; i < points.size(); ++i) {
      trajectory->points.at(i).longitudinal_velocity_mps = velocity_limits.at(i);
      min_velocity = std::min(min_velocity, velocity_limits.at(i));
      max_velocity = std::max(max_velocity, velocity_limits.at(i));
    }

    RCLCPP_INFO(
      get_logger(),
      "Applied velocity profile: min=%.2f m/s max=%.2f m/s, a_lat<=%.2f m/s^2, accel<=%.2f m/s^2, decel<=%.2f m/s^2",
      min_velocity, max_velocity, velocity_profile_max_lateral_accel_mps2_,
      velocity_profile_max_accel_mps2_, velocity_profile_max_decel_mps2_);
  }

  std::vector<double> calculateCumulativeDistance(const std::vector<TrajectoryPoint> & points) const
  {
    std::vector<double> cumulative_distance(points.size(), 0.0);
    for (size_t i = 1; i < points.size(); ++i) {
      const auto & prev = points.at(i - 1).pose.position;
      const auto & curr = points.at(i).pose.position;
      cumulative_distance.at(i) =
        cumulative_distance.at(i - 1) + std::hypot(curr.x - prev.x, curr.y - prev.y);
    }
    return cumulative_distance;
  }

  double calculateCurvature(const std::vector<TrajectoryPoint> & points, const size_t index) const
  {
    if (points.size() < 3) {
      return 0.0;
    }

    const size_t prev_index = index == 0 ? 0 : index - 1;
    const size_t next_index = std::min(index + 1, points.size() - 1);
    if (prev_index == index || next_index == index) {
      return 0.0;
    }

    const auto & p0 = points.at(prev_index).pose.position;
    const auto & p1 = points.at(index).pose.position;
    const auto & p2 = points.at(next_index).pose.position;
    const double a = std::hypot(p1.x - p0.x, p1.y - p0.y);
    const double b = std::hypot(p2.x - p1.x, p2.y - p1.y);
    const double c = std::hypot(p2.x - p0.x, p2.y - p0.y);
    const double denominator = a * b * c;
    if (denominator < 1.0e-6) {
      return 0.0;
    }

    const double cross = (p1.x - p0.x) * (p2.y - p0.y) - (p1.y - p0.y) * (p2.x - p0.x);
    return 2.0 * cross / denominator;
  }
  
  void publish_trajectory()
  {
    if (csv_trajectory_.points.empty()) {
      RCLCPP_WARN(get_logger(), "No trajectory points to publish");
      return;
    }
    
    csv_trajectory_.header.stamp = this->now();
    pub_->publish(csv_trajectory_);
    RCLCPP_INFO_THROTTLE(get_logger(),*get_clock(), 60000 /*ms*/, "Published trajectory with %zu points", csv_trajectory_.points.size());
  }

  rcl_interfaces::msg::SetParametersResult on_parameter_event(
    const std::vector<rclcpp::Parameter> & parameters)
  {
    rcl_interfaces::msg::SetParametersResult result;
    result.successful = true;
    result.reason = "";

    for (const auto & param : parameters) {
      if (param.get_name() == "csv_path") {
        if (param.get_type() == rclcpp::ParameterType::PARAMETER_STRING) {
          std::string new_csv_path = param.as_string();
          // new_csv_pathがFileSystemのパスであることを確認
          if (!std::filesystem::exists(new_csv_path)) {
            RCLCPP_ERROR(get_logger(), "File does not exist: '%s'", new_csv_path.c_str());
            result.successful = false;
            result.reason = "File does not exist.";
            continue;
          }

          if (new_csv_path != current_csv_path_) {
            RCLCPP_INFO(get_logger(), "csv_path parameter changed from '%s' to '%s'", 
                        current_csv_path_.c_str(), new_csv_path.c_str());
            
            // 新しいCSVファイルの読み込みを試みる
            if (loadCSVTrajectory(new_csv_path)) {
              current_csv_path_ = new_csv_path;
              RCLCPP_INFO(get_logger(), "Successfully loaded new trajectory from CSV: %s with %zu points", 
                          current_csv_path_.c_str(), csv_trajectory_.points.size());
            } else {
              RCLCPP_ERROR(get_logger(), "Failed to load new CSV file: %s. Keeping old trajectory.", new_csv_path.c_str());
              result.successful = false;
              result.reason = "Failed to load new CSV file.";
            }
          }
        } else {
          RCLCPP_WARN(get_logger(), "Parameter 'csv_path' received with wrong type. Expected string.");
          result.successful = false;
          result.reason = "Invalid type for csv_path parameter.";
        }
      } else if (param.get_name() == "z") {
        if (param.get_type() == rclcpp::ParameterType::PARAMETER_DOUBLE || param.get_type() == rclcpp::ParameterType::PARAMETER_INTEGER) {
          z_ = static_cast<float>(param.as_double());
          RCLCPP_INFO(get_logger(), "z parameter changed to %f", z_);
        } else {
          RCLCPP_WARN(get_logger(), "Parameter 'z' received with wrong type. Expected float/double.");
          result.successful = false;
          result.reason = "Invalid type for z parameter.";
        }
      } else if (param.get_name() == "enable_resampling") {
        if (param.get_type() == rclcpp::ParameterType::PARAMETER_BOOL) {
          enable_resampling_ = param.as_bool();
          if (!current_csv_path_.empty()) {
            loadCSVTrajectory(current_csv_path_);
          }
        } else {
          RCLCPP_WARN(get_logger(), "Parameter 'enable_resampling' received with wrong type.");
          result.successful = false;
          result.reason = "Invalid type for enable_resampling parameter.";
        }
      } else if (param.get_name() == "resample_interval_m") {
        if (param.get_type() == rclcpp::ParameterType::PARAMETER_DOUBLE) {
          resample_interval_m_ = param.as_double();
          if (!current_csv_path_.empty()) {
            loadCSVTrajectory(current_csv_path_);
          }
        } else {
          RCLCPP_WARN(get_logger(), "Parameter 'resample_interval_m' received with wrong type.");
          result.successful = false;
          result.reason = "Invalid type for resample_interval_m parameter.";
        }
      } else if (param.get_name() == "enable_velocity_profile") {
        if (param.get_type() == rclcpp::ParameterType::PARAMETER_BOOL) {
          enable_velocity_profile_ = param.as_bool();
          if (!current_csv_path_.empty()) {
            loadCSVTrajectory(current_csv_path_);
          }
        } else {
          RCLCPP_WARN(get_logger(), "Parameter 'enable_velocity_profile' received with wrong type.");
          result.successful = false;
          result.reason = "Invalid type for enable_velocity_profile parameter.";
        }
      } else if (param.get_name() == "velocity_profile_use_csv_velocity_limit") {
        if (param.get_type() == rclcpp::ParameterType::PARAMETER_BOOL) {
          velocity_profile_use_csv_velocity_limit_ = param.as_bool();
          if (!current_csv_path_.empty()) {
            loadCSVTrajectory(current_csv_path_);
          }
        } else {
          RCLCPP_WARN(
            get_logger(), "Parameter 'velocity_profile_use_csv_velocity_limit' received with wrong type.");
          result.successful = false;
          result.reason = "Invalid type for velocity_profile_use_csv_velocity_limit parameter.";
        }
      } else if (
        param.get_name() == "velocity_profile_max_velocity_mps" ||
        param.get_name() == "velocity_profile_min_velocity_mps" ||
        param.get_name() == "velocity_profile_max_lateral_accel_mps2" ||
        param.get_name() == "velocity_profile_max_accel_mps2" ||
        param.get_name() == "velocity_profile_max_decel_mps2") {
        if (param.get_type() == rclcpp::ParameterType::PARAMETER_DOUBLE) {
          updateVelocityProfileParameter(param);
          if (!current_csv_path_.empty()) {
            loadCSVTrajectory(current_csv_path_);
          }
        } else {
          RCLCPP_WARN(get_logger(), "Velocity profile parameter received with wrong type.");
          result.successful = false;
          result.reason = "Invalid type for velocity profile parameter.";
        }
      }
    }
    return result;
  }

  void updateVelocityProfileParameter(const rclcpp::Parameter & param)
  {
    if (param.get_name() == "velocity_profile_max_velocity_mps") {
      velocity_profile_max_velocity_mps_ = param.as_double();
    } else if (param.get_name() == "velocity_profile_min_velocity_mps") {
      velocity_profile_min_velocity_mps_ = param.as_double();
    } else if (param.get_name() == "velocity_profile_max_lateral_accel_mps2") {
      velocity_profile_max_lateral_accel_mps2_ = param.as_double();
    } else if (param.get_name() == "velocity_profile_max_accel_mps2") {
      velocity_profile_max_accel_mps2_ = param.as_double();
    } else if (param.get_name() == "velocity_profile_max_decel_mps2") {
      velocity_profile_max_decel_mps2_ = param.as_double();
    }
  }
  
  rclcpp::Publisher<Trajectory>::SharedPtr pub_;
  rclcpp::TimerBase::SharedPtr timer_;
  Trajectory csv_trajectory_;
  float z_;
  bool enable_resampling_;
  double resample_interval_m_;
  bool enable_velocity_profile_;
  double velocity_profile_max_velocity_mps_;
  double velocity_profile_min_velocity_mps_;
  double velocity_profile_max_lateral_accel_mps2_;
  double velocity_profile_max_accel_mps2_;
  double velocity_profile_max_decel_mps2_;
  bool velocity_profile_use_csv_velocity_limit_;
  std::string current_csv_path_;
  OnSetParametersCallbackHandle::SharedPtr set_parameter_callback_handle_;
};

int main(int argc, char ** argv)
{
  rclcpp::init(argc, argv);
  auto node = std::make_shared<CSVToTrajectory>();
  rclcpp::spin(node);
  rclcpp::shutdown();
  return 0;
}
