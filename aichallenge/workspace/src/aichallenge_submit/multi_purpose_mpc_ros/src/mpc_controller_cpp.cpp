#include <algorithm>
#include <array>
#include <cctype>
#include <chrono>
#include <cstdint>
#include <cmath>
#include <fstream>
#include <iomanip>
#include <iostream>
#include <limits>
#include <memory>
#include <numeric>
#include <sstream>
#include <stdexcept>
#include <string>
#include <tuple>
#include <utility>
#include <vector>

#include <Eigen/Dense>
#include <ament_index_cpp/get_package_share_directory.hpp>
#include <autoware_auto_control_msgs/msg/ackermann_control_command.hpp>
#include <geometry_msgs/msg/point.hpp>
#include <geometry_msgs/msg/pose_stamped.hpp>
#include <multi_purpose_mpc_ros_msgs/msg/ackermann_control_boost_command.hpp>
#include <nav_msgs/msg/odometry.hpp>
#include <nav_msgs/msg/path.hpp>
#include <osqp.h>
#include <rcl_interfaces/msg/set_parameters_result.hpp>
#include <rclcpp/rclcpp.hpp>
#include <rclcpp/qos.hpp>
#include <std_msgs/msg/bool.hpp>
#include <std_msgs/msg/empty.hpp>
#include <std_msgs/msg/int32.hpp>
#include <std_msgs/msg/color_rgba.hpp>
#include <std_msgs/msg/float32_multi_array.hpp>
#include <unordered_map>
#include <v2x_msgs/msg/v2_x_vehicle_position_array.hpp>
#include <visualization_msgs/msg/marker.hpp>
#include <visualization_msgs/msg/marker_array.hpp>
#include <yaml-cpp/yaml.h>

namespace
{

using AckermannControlCommand = autoware_auto_control_msgs::msg::AckermannControlCommand;
using AckermannControlBoostCommand = multi_purpose_mpc_ros_msgs::msg::AckermannControlBoostCommand;
using Marker = visualization_msgs::msg::Marker;
using MarkerArray = visualization_msgs::msg::MarkerArray;
using Path = nav_msgs::msg::Path;
using V2XVehiclePositionArray = v2x_msgs::msg::V2XVehiclePositionArray;

constexpr double kPi = 3.14159265358979323846;
constexpr int kNx = 3;
constexpr int kNu = 2;

double wrap_pi(const double value)
{
  double wrapped = std::fmod(value + kPi, 2.0 * kPi);
  if (wrapped < 0.0) {
    wrapped += 2.0 * kPi;
  }
  return wrapped - kPi;
}

double kmh_to_mps(const double kmh) { return kmh / 3.6; }

double yaw_from_quaternion(const geometry_msgs::msg::Quaternion & q)
{
  const double sqx = q.x * q.x;
  const double sqy = q.y * q.y;
  const double sqz = q.z * q.z;
  const double sqw = q.w * q.w;
  const double sarg = -2.0 * (q.x * q.z - q.w * q.y) / (sqx + sqy + sqz + sqw);
  if (sarg <= -0.99999) {
    return -2.0 * std::atan2(q.y, q.x);
  }
  if (sarg >= 0.99999) {
    return 2.0 * std::atan2(q.y, q.x);
  }
  return std::atan2(2.0 * (q.x * q.y + q.w * q.z), sqw + sqx - sqy - sqz);
}

std::vector<std::string> split_csv_line(const std::string & line)
{
  std::vector<std::string> values;
  std::stringstream ss(line);
  std::string value;
  while (std::getline(ss, value, ',')) {
    while (!value.empty() && (value.back() == '\r' || value.back() == '\n' || value.back() == ' ')) {
      value.pop_back();
    }
    while (!value.empty() && value.front() == ' ') {
      value.erase(value.begin());
    }
    values.push_back(value);
  }
  return values;
}

struct Waypoint
{
  double x{};
  double y{};
  double psi{};
  double kappa{};
  double ub{};
  double lb{};
  double v_ref{};
};

struct SpatialState
{
  double e_y{};
  double e_psi{};
  double t{};
};

struct TemporalState
{
  double x{};
  double y{};
  double psi{};
};

struct RefVelSection
{
  int wp_id{};
  double ref_vel_kmh{};
};

enum class AvoidanceLine
{
  Center,
  LeftWall,
  RightWall,
};

struct AvoidanceSectionPolicy
{
  int section_id{};
  double start_s_m{};
  double end_s_m{};
  AvoidanceLine line{AvoidanceLine::Center};
  double wall_margin_m{};
  double blend_length_m{};
  bool has_virtual_block_s{};
  double virtual_block_s_m{};
};

struct AvoidanceConfig
{
  bool enabled{};
  double wall_margin_m{1.0};
  double blend_length_m{4.0};
  double slow_distance_m{3.0};
  double stop_distance_m{1.0};
  double slow_speed_mps{kmh_to_mps(10.0)};
  std::vector<AvoidanceSectionPolicy> sections;
};

struct LanePlannerConfig
{
  bool enabled{};
  double candidate_wall_margin_m{0.4};
  double prediction_horizon_s{3.0};
  double prediction_dt_s{0.2};
  double ego_radius_m{0.75};
  double obstacle_radius_m{0.85};
  double collision_margin_m{0.5};
  double min_hold_time_s{1.5};
  double switch_cooldown_s{1.0};
  double switch_margin_m{0.5};
  double obstacle_timeout_s{1.0};
  double obstacle_v_max_mps{30.0};
  double obstacle_jump_threshold_m{8.0};
  double front_slow_distance_m{3.0};
  double front_stop_distance_m{1.0};
  double front_slow_speed_mps{kmh_to_mps(10.0)};
  bool emergency_override{true};
  AvoidanceLine default_line{AvoidanceLine::Center};
};

enum class LongitudinalSafetyState
{
  Clear,
  Slowdown,
  Brake,
  EmergencyStop,
};

struct LongitudinalSafetyConfig
{
  bool enabled{true};
  double comfortable_decel_mps2{1.6};
  double emergency_decel_mps2{2.5};
  double max_brake_decel_mps2{2.5};
  double latency_margin_s{0.3};
  double distance_margin_m{1.0};
  double hard_stop_distance_m{1.0};
  double min_speed_mps{0.2};
};

struct LongitudinalSafetyDecision
{
  LongitudinalSafetyState state{LongitudinalSafetyState::Clear};
  double front_distance_m{std::numeric_limits<double>::infinity()};
  double warning_distance_m{std::numeric_limits<double>::infinity()};
  double brake_distance_m{std::numeric_limits<double>::infinity()};
  double speed_limit_mps{std::numeric_limits<double>::infinity()};
  double decel_mps2{};
  std::string reason{"clear"};
};

struct V2XObstacleState
{
  std::string vehicle_id;
  double stamp{};
  double x{};
  double y{};
  double vx{};
  double vy{};
};

struct V2XSample
{
  double stamp{};
  double x{};
  double y{};
};

struct LaneCandidate
{
  AvoidanceLine line{AvoidanceLine::Center};
  std::vector<geometry_msgs::msg::Point> points;
  std::vector<double> distances_m;
  double min_obstacle_distance{std::numeric_limits<double>::infinity()};
  double front_block_distance_m{std::numeric_limits<double>::infinity()};
  bool blocked{};
  std::string reason{"safe"};
};

std::string avoidance_line_to_string(const AvoidanceLine line)
{
  if (line == AvoidanceLine::LeftWall) {
    return "left_wall";
  }
  if (line == AvoidanceLine::RightWall) {
    return "right_wall";
  }
  return "center";
}

std::string longitudinal_safety_state_to_string(const LongitudinalSafetyState state)
{
  if (state == LongitudinalSafetyState::Slowdown) {
    return "slowdown";
  }
  if (state == LongitudinalSafetyState::Brake) {
    return "brake";
  }
  if (state == LongitudinalSafetyState::EmergencyStop) {
    return "emergency_stop";
  }
  return "clear";
}

std_msgs::msg::ColorRGBA color_for_line(const AvoidanceLine line, const double alpha = 1.0)
{
  std_msgs::msg::ColorRGBA color;
  color.a = alpha;
  if (line == AvoidanceLine::LeftWall) {
    color.r = 0.55;
    color.g = 0.25;
    color.b = 0.85;
  } else if (line == AvoidanceLine::RightWall) {
    color.r = 0.0;
    color.g = 0.7;
    color.b = 0.55;
  } else {
    color.r = 0.95;
    color.g = 0.95;
    color.b = 0.95;
  }
  return color;
}

class OccupancyMap
{
public:
  explicit OccupancyMap(const std::string & yaml_path)
  {
    const YAML::Node map_yaml = YAML::LoadFile(yaml_path);
    occupied_thresh_ = map_yaml["occupied_thresh"].as<double>();
    resolution_ = map_yaml["resolution"].as<double>();
    origin_x_ = map_yaml["origin"][0].as<double>();
    origin_y_ = map_yaml["origin"][1].as<double>();

    const auto slash = yaml_path.find_last_of('/');
    const std::string base_path = slash == std::string::npos ? std::string(".") : yaml_path.substr(0, slash);
    load_pgm(base_path + "/" + map_yaml["image"].as<std::string>());
  }

  std::pair<int, int> w2m(const double x, const double y) const
  {
    int dx = static_cast<int>((x - origin_x_) / resolution_ + 0.5);
    int dy = static_cast<int>((height_ - 1) - (y - origin_y_) / resolution_ + 0.5);
    dx = std::clamp(dx, 0, width_ - 1);
    dy = std::clamp(dy, 0, height_ - 1);
    return {dx, dy};
  }

  std::pair<double, double> m2w(const int dx, const int dy) const
  {
    const double x = static_cast<int>(dx + 0.5) * resolution_ + origin_x_;
    const double y = (height_ - 1 - static_cast<int>(dy + 0.5)) * resolution_ + origin_y_;
    return {x, y};
  }

  std::pair<double, std::pair<double, double>> get_min_width(
    const double wp_x_w, const double wp_y_w, const int wp_x, const int wp_y, const int target_x,
    const int target_y, const double max_width) const
  {
    double min_width = max_width;
    std::pair<double, double> min_cell = m2w(target_x, target_y);
    std::vector<std::pair<int, int>> free_cells;

    for (int dx = -1; dx <= 1; ++dx) {
      for (int dy = -1; dy <= 1; ++dy) {
        const int tx = std::clamp(target_x + dx, 0, width_ - 1);
        const int ty = std::clamp(target_y + dy, 0, height_ - 1);
        const auto cells = raster_line(wp_x, wp_y, tx, ty);
        for (const auto & cell : cells) {
          if (!is_free(cell.first, cell.second)) {
            min_cell = m2w(cell.first, cell.second);
            min_width = std::hypot(wp_x_w - min_cell.first, wp_y_w - min_cell.second);
            return {min_width, min_cell};
          }
          free_cells.push_back(cell);
        }
      }
    }

    if (!free_cells.empty()) {
      auto best = free_cells.front();
      double best_dist = std::numeric_limits<double>::infinity();
      for (const auto & cell : free_cells) {
        const double dist = std::hypot(
          static_cast<double>(cell.first - target_x), static_cast<double>(cell.second - target_y));
        if (dist < best_dist) {
          best_dist = dist;
          best = cell;
        }
      }
      min_cell = m2w(best.first, best.second);
      min_width = std::hypot(wp_x_w - min_cell.first, wp_y_w - min_cell.second);
    }
    return {min_width, min_cell};
  }

private:
  bool is_free(const int x, const int y) const
  {
    const int cx = std::clamp(x, 0, width_ - 1);
    const int cy = std::clamp(y, 0, height_ - 1);
    return data_.at(cy * width_ + cx) != 0;
  }

  static std::string read_token(std::istream & input)
  {
    std::string token;
    while (input >> token) {
      if (!token.empty() && token.front() == '#') {
        std::string ignored;
        std::getline(input, ignored);
        continue;
      }
      return token;
    }
    throw std::runtime_error("unexpected EOF while reading PGM");
  }

  void load_pgm(const std::string & pgm_path)
  {
    std::ifstream file(pgm_path, std::ios::binary);
    if (!file) {
      throw std::runtime_error("failed to open PGM: " + pgm_path);
    }
    const std::string magic = read_token(file);
    if (magic != "P5" && magic != "P2") {
      throw std::runtime_error("unsupported PGM format: " + magic);
    }
    width_ = std::stoi(read_token(file));
    height_ = std::stoi(read_token(file));
    const int max_value = std::stoi(read_token(file));
    data_.assign(width_ * height_, 0);

    if (magic == "P5") {
      file.get();
      std::vector<unsigned char> raw(width_ * height_);
      file.read(reinterpret_cast<char *>(raw.data()), static_cast<std::streamsize>(raw.size()));
      for (size_t idx = 0; idx < raw.size(); ++idx) {
        const double value = static_cast<double>(raw.at(idx)) / static_cast<double>(max_value);
        data_.at(idx) = value >= occupied_thresh_ ? 1 : 0;
      }
      return;
    }

    for (int idx = 0; idx < width_ * height_; ++idx) {
      const double value = std::stod(read_token(file)) / static_cast<double>(max_value);
      data_.at(idx) = value >= occupied_thresh_ ? 1 : 0;
    }
  }

  static std::vector<std::pair<int, int>> raster_line(int x0, int y0, const int x1, const int y1)
  {
    std::vector<std::pair<int, int>> cells;
    const int dx = std::abs(x1 - x0);
    const int sx = x0 < x1 ? 1 : -1;
    const int dy = -std::abs(y1 - y0);
    const int sy = y0 < y1 ? 1 : -1;
    int err = dx + dy;
    while (true) {
      cells.emplace_back(x0, y0);
      if (x0 == x1 && y0 == y1) {
        break;
      }
      const int e2 = 2 * err;
      if (e2 >= dy) {
        err += dy;
        x0 += sx;
      }
      if (e2 <= dx) {
        err += dx;
        y0 += sy;
      }
    }
    return cells;
  }

  int width_{};
  int height_{};
  double resolution_{};
  double origin_x_{};
  double origin_y_{};
  double occupied_thresh_{};
  std::vector<std::uint8_t> data_;
};

class ReferencePath
{
public:
  ReferencePath(
    const OccupancyMap * map, const std::vector<double> & input_x, const std::vector<double> & input_y,
    const double resolution, const int smoothing_distance, const double max_width, const bool circular)
  : resolution_(resolution),
    smoothing_distance_(smoothing_distance),
    max_width_(max_width),
    circular_(circular)
  {
    construct_path(input_x, input_y);
    compute_width(map);
    set_v_ref(std::vector<double>(waypoints_.size(), 0.0));
    compute_segment_lengths();
    update_simple_path_constraints(20, 0.0);
  }

  const Waypoint & get_waypoint(const int index) const
  {
    if (circular_) {
      const int size = static_cast<int>(waypoints_.size());
      int wrapped = index % size;
      if (wrapped < 0) {
        wrapped += size;
      }
      return waypoints_.at(wrapped);
    }
    const int clipped = std::clamp(index, 0, static_cast<int>(waypoints_.size()) - 1);
    return waypoints_.at(clipped);
  }

  double segment_length(const int index) const
  {
    const int clipped = std::clamp(index, 0, static_cast<int>(segment_lengths_.size()) - 1);
    return segment_lengths_.at(clipped);
  }

  int size() const { return static_cast<int>(waypoints_.size()); }

  const std::vector<Waypoint> & waypoints() const { return waypoints_; }

  const std::vector<double> & cumulative_lengths() const { return cumulative_lengths_; }

  double total_length() const
  {
    if (cumulative_lengths_.empty()) {
      return 0.0;
    }
    return cumulative_lengths_.back();
  }

  double s_at_index(const int index) const
  {
    if (cumulative_lengths_.empty()) {
      return 0.0;
    }
    if (!circular_) {
      const int clipped = std::clamp(index, 0, static_cast<int>(cumulative_lengths_.size()) - 1);
      return cumulative_lengths_.at(clipped);
    }

    const int size = static_cast<int>(cumulative_lengths_.size());
    int wrapped = index % size;
    int laps = index / size;
    if (wrapped < 0) {
      wrapped += size;
      --laps;
    }
    return static_cast<double>(laps) * total_length() + cumulative_lengths_.at(wrapped);
  }

  std::pair<std::vector<double>, std::vector<double>> get_path_constraints(
    const int ref_wp_id, const int horizon, const double safety_margin) const
  {
    (void)safety_margin;
    std::vector<double> ub;
    std::vector<double> lb;
    ub.reserve(horizon);
    lb.reserve(horizon);
    const int rows = std::max(1, size() - 1);
    const int row = ((ref_wp_id % rows) + rows) % rows;
    for (int n = 0; n < horizon; ++n) {
      const double raw_ub = path_ub_.at(row).at(n);
      const double raw_lb = path_lb_.at(row).at(n);
      if (raw_ub < raw_lb) {
        ub.push_back(0.0);
        lb.push_back(0.0);
      } else {
        ub.push_back(raw_ub);
        lb.push_back(raw_lb);
      }
    }
    return {ub, lb};
  }

  void set_v_ref(const std::vector<double> & v_ref)
  {
    for (size_t i = 0; i < waypoints_.size(); ++i) {
      waypoints_.at(i).v_ref = v_ref.at(std::min(i, v_ref.size() - 1));
    }
  }

  void compute_speed_profile(
    const double a_min, const double a_max, const double v_min, const double v_max, const double ay_max)
  {
    const int horizon = size() - 1;
    if (horizon < 2) {
      return;
    }

    std::vector<double> v_max_dyn(horizon, v_max);
    std::vector<double> segment_lengths(horizon, 0.0);
    for (int i = 0; i < horizon; ++i) {
      const auto & current = get_waypoint(i);
      const auto & next = get_waypoint(i + 1);
      segment_lengths.at(i) = std::hypot(next.x - current.x, next.y - current.y);
      const double kappa = std::abs(current.kappa);
      const double v_dyn = std::sqrt(ay_max / (kappa + 1.0e-12));
      if (v_dyn < v_max_dyn.at(i)) {
        v_max_dyn.at(i) = v_dyn;
      }
    }

    std::vector<c_float> p_x(horizon, 1.0);
    std::vector<c_int> p_i(horizon, 0);
    std::vector<c_int> p_p(horizon + 1, 0);
    std::vector<c_float> q(horizon, 0.0);
    for (int i = 0; i < horizon; ++i) {
      p_i.at(i) = i;
      p_p.at(i) = i;
      q.at(i) = -v_max_dyn.at(i);
    }
    p_p.at(horizon) = horizon;

    std::vector<std::tuple<int, int, double>> d_triplets;
    d_triplets.reserve(3 * horizon - 2);
    for (int i = 0; i < horizon - 1; ++i) {
      const double coeff = 1.0 / (2.0 * segment_lengths.at(i));
      d_triplets.emplace_back(i, i, -coeff);
      d_triplets.emplace_back(i, i + 1, coeff);
    }
    for (int i = 0; i < horizon; ++i) {
      d_triplets.emplace_back((horizon - 1) + i, i, 1.0);
    }
    std::stable_sort(d_triplets.begin(), d_triplets.end(), [](const auto & lhs, const auto & rhs) {
      if (std::get<1>(lhs) == std::get<1>(rhs)) {
        return std::get<0>(lhs) < std::get<0>(rhs);
      }
      return std::get<1>(lhs) < std::get<1>(rhs);
    });

    const int n_constraints = (horizon - 1) + horizon;
    std::vector<c_float> a_x;
    std::vector<c_int> a_i;
    std::vector<c_int> a_p(horizon + 1, 0);
    a_x.reserve(d_triplets.size());
    a_i.reserve(d_triplets.size());
    int current_col = 0;
    for (const auto & [row, col, value] : d_triplets) {
      while (current_col < col) {
        a_p.at(current_col + 1) = static_cast<c_int>(a_x.size());
        ++current_col;
      }
      a_i.push_back(static_cast<c_int>(row));
      a_x.push_back(static_cast<c_float>(value));
    }
    while (current_col < horizon) {
      a_p.at(current_col + 1) = static_cast<c_int>(a_x.size());
      ++current_col;
    }

    std::vector<c_float> lower(n_constraints, 0.0);
    std::vector<c_float> upper(n_constraints, 0.0);
    for (int i = 0; i < horizon - 1; ++i) {
      lower.at(i) = a_min;
      upper.at(i) = a_max;
    }
    for (int i = 0; i < horizon; ++i) {
      lower.at((horizon - 1) + i) = v_min;
      upper.at((horizon - 1) + i) = v_max_dyn.at(i);
    }

    csc * p_mat = csc_matrix(horizon, horizon, horizon, p_x.data(), p_i.data(), p_p.data());
    csc * a_mat = csc_matrix(
      n_constraints, horizon, static_cast<c_int>(a_x.size()), a_x.data(), a_i.data(), a_p.data());
    OSQPData data{};
    data.n = horizon;
    data.m = n_constraints;
    data.P = p_mat;
    data.q = q.data();
    data.A = a_mat;
    data.l = lower.data();
    data.u = upper.data();
    OSQPSettings settings{};
    osqp_set_default_settings(&settings);
    settings.verbose = false;

    OSQPWorkspace * work = nullptr;
    const c_int setup_status = osqp_setup(&work, &data, &settings);
    if (setup_status == 0 && work != nullptr && osqp_solve(work) == 0 && work->solution != nullptr &&
      work->solution->x != nullptr)
    {
      for (int i = 0; i < horizon; ++i) {
        waypoints_.at(i).v_ref = work->solution->x[i];
      }
      waypoints_.back().v_ref = circular_ ? waypoints_.at(waypoints_.size() - 2).v_ref : 0.0;
    } else {
      std::vector<double> fallback(waypoints_.size(), v_max);
      set_v_ref(fallback);
    }
    if (work != nullptr) {
      osqp_cleanup(work);
    }
    c_free(p_mat);
    c_free(a_mat);
  }

  void update_simple_path_constraints(const int horizon, const double safety_margin)
  {
    const int rows = std::max(1, size() - 1);
    path_ub_.assign(rows, std::vector<double>(horizon, 0.0));
    path_lb_.assign(rows, std::vector<double>(horizon, 0.0));
    for (int wp_id = 0; wp_id < rows; ++wp_id) {
      for (int n = 0; n < horizon; ++n) {
        const auto & wp = get_waypoint(wp_id + n);
        double ub = wp.ub - safety_margin;
        double lb = wp.lb + safety_margin;
        if (ub < lb) {
          ub = 0.0;
          lb = 0.0;
        }
        path_ub_.at(wp_id).at(n) = ub;
        path_lb_.at(wp_id).at(n) = lb;
      }
    }
  }

  bool load_width_constraints_csv(const std::string & csv_path)
  {
    std::ifstream file(csv_path);
    if (!file) {
      return false;
    }
    std::string header;
    std::getline(file, header);
    const auto columns = split_csv_line(header);
    const auto find_index = [&](const std::string & name) {
      const auto it = std::find(columns.begin(), columns.end(), name);
      if (it == columns.end()) {
        throw std::runtime_error("missing constraints CSV column: " + name);
      }
      return static_cast<int>(std::distance(columns.begin(), it));
    };
    const int wp_idx = find_index("wp_id");
    const int ub_idx = find_index("ub");
    const int lb_idx = find_index("lb");
    std::string line;
    int loaded = 0;
    while (std::getline(file, line)) {
      if (line.empty()) {
        continue;
      }
      const auto values = split_csv_line(line);
      const int wp_id = std::stoi(values.at(wp_idx));
      if (wp_id < 0 || wp_id >= static_cast<int>(waypoints_.size())) {
        continue;
      }
      waypoints_.at(wp_id).ub = std::stod(values.at(ub_idx));
      waypoints_.at(wp_id).lb = std::stod(values.at(lb_idx));
      ++loaded;
    }
    return loaded == static_cast<int>(waypoints_.size());
  }

private:
  void construct_path(std::vector<double> wp_x, std::vector<double> wp_y)
  {
    if (circular_) {
      const int append_count = std::min<int>(static_cast<int>(wp_x.size()), smoothing_distance_ * 3);
      for (int i = 0; i < append_count; ++i) {
        wp_x.push_back(wp_x.at(i));
        wp_y.push_back(wp_y.at(i));
      }
    }

    std::vector<double> dense_x;
    std::vector<double> dense_y;
    for (size_t i = 0; i + 1 < wp_x.size(); ++i) {
      const double dx = wp_x.at(i + 1) - wp_x.at(i);
      const double dy = wp_y.at(i + 1) - wp_y.at(i);
      const int n_wp = std::max(1, static_cast<int>(std::hypot(dx, dy) / resolution_));
      for (int j = 0; j < n_wp; ++j) {
        const double ratio = static_cast<double>(j) / static_cast<double>(n_wp);
        dense_x.push_back(wp_x.at(i) + ratio * dx);
        dense_y.push_back(wp_y.at(i) + ratio * dy);
      }
    }
    dense_x.push_back(wp_x.back());
    dense_y.push_back(wp_y.back());

    std::vector<std::pair<double, double>> smoothed;
    for (int i = smoothing_distance_; i < static_cast<int>(dense_x.size()) - smoothing_distance_; ++i) {
      double sx = 0.0;
      double sy = 0.0;
      for (int j = i - smoothing_distance_; j <= i + smoothing_distance_; ++j) {
        sx += dense_x.at(j);
        sy += dense_y.at(j);
      }
      const double denom = static_cast<double>(2 * smoothing_distance_ + 1);
      smoothed.emplace_back(sx / denom, sy / denom);
    }

    waypoints_.clear();
    for (size_t i = 0; i + 1 < smoothed.size(); ++i) {
      const auto current = smoothed.at(i);
      const auto next = smoothed.at(i + 1);
      const double dx = next.first - current.first;
      const double dy = next.second - current.second;
      const double psi = std::atan2(dy, dx);
      const double dist_ahead = std::hypot(dx, dy);
      double kappa = 0.0;
      if (i > 0) {
        const auto prev = smoothed.at(i - 1);
        const double angle_behind = std::atan2(current.second - prev.second, current.first - prev.first);
        kappa = wrap_pi(psi - angle_behind) / (dist_ahead + 1.0e-12);
      }
      waypoints_.push_back(Waypoint{current.first, current.second, psi, kappa, max_width_, -max_width_, 0.0});
    }
  }

  void compute_width(const OccupancyMap * map)
  {
    if (map == nullptr) {
      return;
    }
    for (auto & wp : waypoints_) {
      const double left_angle = wrap_pi(wp.psi + kPi / 2.0);
      const double right_angle = wrap_pi(wp.psi - kPi / 2.0);
      const auto [wp_mx, wp_my] = map->w2m(wp.x, wp.y);
      const auto [wp_x_w, wp_y_w] = map->m2w(wp_mx, wp_my);

      const auto [left_tx, left_ty] =
        map->w2m(wp_x_w + max_width_ * std::cos(left_angle), wp_y_w + max_width_ * std::sin(left_angle));
      const auto [right_tx, right_ty] =
        map->w2m(wp_x_w + max_width_ * std::cos(right_angle), wp_y_w + max_width_ * std::sin(right_angle));

      const auto [left_width, left_cell] =
        map->get_min_width(wp_x_w, wp_y_w, wp_mx, wp_my, left_tx, left_ty, max_width_);
      const auto [right_width, right_cell] =
        map->get_min_width(wp_x_w, wp_y_w, wp_mx, wp_my, right_tx, right_ty, max_width_);
      (void)left_cell;
      (void)right_cell;
      wp.ub = left_width;
      wp.lb = -right_width;
    }
  }

  void compute_segment_lengths()
  {
    segment_lengths_.assign(waypoints_.size(), 0.0);
    cumulative_lengths_.assign(waypoints_.size(), 0.0);
    for (size_t i = 1; i < waypoints_.size(); ++i) {
      segment_lengths_.at(i) = std::hypot(
        waypoints_.at(i).x - waypoints_.at(i - 1).x, waypoints_.at(i).y - waypoints_.at(i - 1).y);
      cumulative_lengths_.at(i) = cumulative_lengths_.at(i - 1) + segment_lengths_.at(i);
    }
  }

  double resolution_{};
  int smoothing_distance_{};
  double max_width_{};
  bool circular_{};
  std::vector<Waypoint> waypoints_;
  std::vector<double> segment_lengths_;
  std::vector<double> cumulative_lengths_;
  std::vector<std::vector<double>> path_ub_;
  std::vector<std::vector<double>> path_lb_;
};

class BicycleModel
{
public:
  BicycleModel(ReferencePath * reference_path, const double length, const double width, const double ts)
  : reference_path_(reference_path),
    length_(length),
    width_(width),
    safety_margin_(width / std::sqrt(2.0)),
    ts_(ts)
  {
    current_waypoint_ = &reference_path_->get_waypoint(wp_id_);
    temporal_state_ = s2t(*current_waypoint_, spatial_state_);
  }

  ReferencePath & reference_path() { return *reference_path_; }
  const ReferencePath & reference_path() const { return *reference_path_; }
  double length() const { return length_; }
  double width() const { return width_; }
  double safety_margin() const { return safety_margin_; }
  double ts() const { return ts_; }
  double s() const { return s_; }
  int wp_id() const { return wp_id_; }
  void set_wp_id(const int wp_id) { wp_id_ = wp_id; }
  const SpatialState & spatial_state() const { return spatial_state_; }
  const TemporalState & temporal_state() const { return temporal_state_; }

  void update_states(const double x, const double y, const double psi)
  {
    temporal_state_ = TemporalState{x, y, psi};
    wp_id_ = get_closest_waypoint(x, y);
    s_ = get_s_at_waypoint(wp_id_);
    current_waypoint_ = &reference_path_->get_waypoint(wp_id_);
  }

  void update_current_waypoint()
  {
    const auto & length_cum = reference_path_->cumulative_lengths();
    const auto it = std::upper_bound(length_cum.begin(), length_cum.end(), s_);
    int next_wp_id = static_cast<int>(std::distance(length_cum.begin(), it));
    if (next_wp_id >= static_cast<int>(length_cum.size())) {
      wp_id_ = static_cast<int>(length_cum.size()) - 1;
      current_waypoint_ = &reference_path_->get_waypoint(wp_id_);
      return;
    }
    const int prev_wp_id = next_wp_id - 1;
    const double s_next = length_cum.at(next_wp_id);
    const double s_prev = length_cum.at(std::max(0, prev_wp_id));
    if (std::abs(s_ - s_next) < std::abs(s_ - s_prev)) {
      wp_id_ = next_wp_id;
    } else {
      wp_id_ = std::max(0, prev_wp_id);
    }
    current_waypoint_ = &reference_path_->get_waypoint(wp_id_);
  }

  SpatialState t2s(const Waypoint & reference_waypoint, const TemporalState & state) const
  {
    const double e_y = std::cos(reference_waypoint.psi) * (state.y - reference_waypoint.y) -
                       std::sin(reference_waypoint.psi) * (state.x - reference_waypoint.x);
    const double e_psi = wrap_pi(state.psi - reference_waypoint.psi);
    return SpatialState{e_y, e_psi, 0.0};
  }

  TemporalState s2t(const Waypoint & reference_waypoint, const SpatialState & state) const
  {
    const double x = reference_waypoint.x - state.e_y * std::sin(reference_waypoint.psi);
    const double y = reference_waypoint.y + state.e_y * std::cos(reference_waypoint.psi);
    const double psi = reference_waypoint.psi + state.e_psi;
    return TemporalState{x, y, psi};
  }

  void update_spatial_state()
  {
    spatial_state_ = t2s(*current_waypoint_, temporal_state_);
  }

  void drive(const double v, const double delta)
  {
    temporal_state_.x += v * std::cos(temporal_state_.psi) * ts_;
    temporal_state_.y += v * std::sin(temporal_state_.psi) * ts_;
    temporal_state_.psi += v / length_ * std::tan(delta) * ts_;
    const double s_dot = v * std::cos(spatial_state_.e_psi) /
                         (1.0 - spatial_state_.e_y * current_waypoint_->kappa);
    s_ += s_dot * ts_;
  }

  std::tuple<Eigen::Vector3d, Eigen::Matrix3d, Eigen::Matrix<double, 3, 2>> linearize(
    const double v_ref, const double kappa_ref, const double delta_s) const
  {
    Eigen::Vector3d f;
    Eigen::Matrix3d a = Eigen::Matrix3d::Zero();
    Eigen::Matrix<double, 3, 2> b = Eigen::Matrix<double, 3, 2>::Zero();

    a.row(0) << 1.0, delta_s, 0.0;
    a.row(1) << -kappa_ref * kappa_ref * delta_s, 1.0, 0.0;
    b.row(0) << 0.0, 0.0;
    b.row(1) << 0.0, delta_s;
    if (v_ref == 0.0) {
      a.row(2) << 0.0, 0.0, 1.0;
      b.row(2) << 0.0, 0.0;
      f << 0.0, 0.0, 0.0;
    } else {
      a.row(2) << -kappa_ref / v_ref * delta_s, 0.0, 1.0;
      b.row(2) << -1.0 / (v_ref * v_ref) * delta_s, 0.0;
      f << 0.0, 0.0, 1.0 / v_ref * delta_s;
    }
    return {f, a, b};
  }

private:
  int get_closest_waypoint(const double x, const double y) const
  {
    int closest = 0;
    double best = std::numeric_limits<double>::infinity();
    for (int i = 0; i < reference_path_->size(); ++i) {
      const auto & wp = reference_path_->get_waypoint(i);
      const double dist = std::hypot(wp.x - x, wp.y - y);
      if (dist < best) {
        best = dist;
        closest = i;
      }
    }
    return closest;
  }

  double get_s_at_waypoint(const int wp_id) const
  {
    const auto & length_cum = reference_path_->cumulative_lengths();
    return length_cum.at(std::clamp(wp_id, 0, static_cast<int>(length_cum.size()) - 1));
  }

  ReferencePath * reference_path_{};
  double length_{};
  double width_{};
  double safety_margin_{};
  double ts_{};
  double s_{};
  int wp_id_{};
  SpatialState spatial_state_;
  TemporalState temporal_state_;
  const Waypoint * current_waypoint_{};
};

struct SparseCsc
{
  int rows{};
  int cols{};
  std::vector<c_float> x;
  std::vector<c_int> i;
  std::vector<c_int> p;
};

SparseCsc build_csc_from_triplets(
  const int rows, const int cols, std::vector<std::tuple<int, int, double>> triplets)
{
  std::stable_sort(triplets.begin(), triplets.end(), [](const auto & lhs, const auto & rhs) {
    if (std::get<1>(lhs) == std::get<1>(rhs)) {
      return std::get<0>(lhs) < std::get<0>(rhs);
    }
    return std::get<1>(lhs) < std::get<1>(rhs);
  });

  SparseCsc csc;
  csc.rows = rows;
  csc.cols = cols;
  csc.p.assign(cols + 1, 0);
  csc.x.reserve(triplets.size());
  csc.i.reserve(triplets.size());
  int current_col = 0;
  for (const auto & [row, col, value] : triplets) {
    while (current_col < col) {
      csc.p.at(current_col + 1) = static_cast<c_int>(csc.x.size());
      ++current_col;
    }
    csc.i.push_back(static_cast<c_int>(row));
    csc.x.push_back(static_cast<c_float>(value));
  }
  while (current_col < cols) {
    csc.p.at(current_col + 1) = static_cast<c_int>(csc.x.size());
    ++current_col;
  }
  return csc;
}

class OsqpWorkspace
{
public:
  ~OsqpWorkspace() { cleanup(); }

  void setup(const SparseCsc & p, const std::vector<double> & q, const SparseCsc & a,
    const std::vector<double> & l, const std::vector<double> & u)
  {
    cleanup();
    p_x_ = p.x;
    p_i_ = p.i;
    p_p_ = p.p;
    a_x_ = a.x;
    a_i_ = a.i;
    a_p_ = a.p;
    q_ = to_c_float(q);
    l_ = to_c_float(l);
    u_ = to_c_float(u);

    p_mat_ = csc_matrix(p.rows, p.cols, static_cast<c_int>(p_x_.size()), p_x_.data(), p_i_.data(), p_p_.data());
    a_mat_ = csc_matrix(a.rows, a.cols, static_cast<c_int>(a_x_.size()), a_x_.data(), a_i_.data(), a_p_.data());

    data_.n = p.cols;
    data_.m = a.rows;
    data_.P = p_mat_;
    data_.q = q_.data();
    data_.A = a_mat_;
    data_.l = l_.data();
    data_.u = u_.data();

    osqp_set_default_settings(&settings_);
    settings_.verbose = false;
    settings_.warm_start = true;
    settings_.eps_abs = 1.0e-8;
    settings_.eps_rel = 1.0e-8;

    OSQPWorkspace * raw_work = nullptr;
    const c_int status = osqp_setup(&raw_work, &data_, &settings_);
    if (status != 0 || raw_work == nullptr) {
      throw std::runtime_error("osqp_setup failed");
    }
    work_ = raw_work;
    p_nnz_ = static_cast<int>(p_x_.size());
    a_nnz_ = static_cast<int>(a_x_.size());
    initialized_ = true;
  }

  bool update(const std::vector<double> & q, const SparseCsc & a, const std::vector<double> & l,
    const std::vector<double> & u)
  {
    if (!initialized_ || static_cast<int>(a.x.size()) != a_nnz_) {
      return false;
    }
    q_ = to_c_float(q);
    l_ = to_c_float(l);
    u_ = to_c_float(u);
    a_x_ = a.x;

    const c_int q_status = osqp_update_lin_cost(work_, q_.data());
    const c_int b_status = osqp_update_bounds(work_, l_.data(), u_.data());
    const c_int a_status = osqp_update_A(work_, a_x_.data(), OSQP_NULL, static_cast<c_int>(a_x_.size()));
    return q_status == 0 && b_status == 0 && a_status == 0;
  }

  std::vector<double> solve()
  {
    if (!initialized_) {
      throw std::runtime_error("OSQP workspace is not initialized");
    }
    const c_int status = osqp_solve(work_);
    const std::string solver_status =
      work_->info != nullptr && work_->info->status != nullptr ? std::string(work_->info->status) : "unknown";
    if (status != 0 || work_->solution == nullptr || work_->solution->x == nullptr || work_->info == nullptr) {
      throw std::runtime_error("osqp_solve failed: " + solver_status);
    }
    if (solver_status.find("infeasible") != std::string::npos) {
      throw std::runtime_error("osqp_solve failed: " + solver_status);
    }
    std::vector<double> solution(data_.n);
    for (int idx = 0; idx < data_.n; ++idx) {
      solution.at(idx) = work_->solution->x[idx];
    }
    return solution;
  }

  bool initialized() const { return initialized_; }
  int p_nnz() const { return p_nnz_; }
  int a_nnz() const { return a_nnz_; }

private:
  static std::vector<c_float> to_c_float(const std::vector<double> & source)
  {
    std::vector<c_float> result(source.size());
    std::transform(source.begin(), source.end(), result.begin(), [](double v) { return static_cast<c_float>(v); });
    return result;
  }

  void cleanup()
  {
    if (work_ != nullptr) {
      osqp_cleanup(work_);
      work_ = nullptr;
    }
    if (p_mat_ != nullptr) {
      c_free(p_mat_);
      p_mat_ = nullptr;
    }
    if (a_mat_ != nullptr) {
      c_free(a_mat_);
      a_mat_ = nullptr;
    }
    initialized_ = false;
  }

  OSQPWorkspace * work_{};
  OSQPData data_{};
  OSQPSettings settings_{};
  csc * p_mat_{};
  csc * a_mat_{};
  std::vector<c_float> p_x_;
  std::vector<c_int> p_i_;
  std::vector<c_int> p_p_;
  std::vector<c_float> a_x_;
  std::vector<c_int> a_i_;
  std::vector<c_int> a_p_;
  std::vector<c_float> q_;
  std::vector<c_float> l_;
  std::vector<c_float> u_;
  bool initialized_{};
  int p_nnz_{};
  int a_nnz_{};
};

struct MpcProblem
{
  SparseCsc p;
  SparseCsc a;
  std::vector<double> q;
  std::vector<double> l;
  std::vector<double> u;
};

AvoidanceLine parse_avoidance_line(const std::string & line)
{
  if (line == "center") {
    return AvoidanceLine::Center;
  }
  if (line == "left_wall") {
    return AvoidanceLine::LeftWall;
  }
  if (line == "right_wall") {
    return AvoidanceLine::RightWall;
  }
  throw std::runtime_error("unknown avoidance line: " + line);
}

AvoidanceConfig load_avoidance_config(const YAML::Node & config)
{
  AvoidanceConfig avoidance;
  const YAML::Node node = config["avoidance"];
  if (!node) {
    return avoidance;
  }

  avoidance.enabled = node["enabled"].as<bool>(false);
  avoidance.wall_margin_m = node["wall_margin_m"].as<double>(avoidance.wall_margin_m);
  avoidance.blend_length_m = node["blend_length_m"].as<double>(avoidance.blend_length_m);
  avoidance.slow_distance_m = node["slow_distance_m"].as<double>(avoidance.slow_distance_m);
  avoidance.stop_distance_m = node["stop_distance_m"].as<double>(avoidance.stop_distance_m);
  avoidance.slow_speed_mps = kmh_to_mps(node["slow_speed_kmh"].as<double>(10.0));

  const YAML::Node sections = node["sections"];
  if (!sections) {
    return avoidance;
  }
  for (const auto & item : sections) {
    AvoidanceSectionPolicy policy;
    policy.section_id = item["section_id"].as<int>();
    policy.start_s_m = item["start_s_m"].as<double>();
    policy.end_s_m = item["end_s_m"].as<double>();
    policy.line = parse_avoidance_line(item["line"].as<std::string>("center"));
    policy.wall_margin_m = item["wall_margin_m"].as<double>(avoidance.wall_margin_m);
    policy.blend_length_m = item["blend_length_m"].as<double>(avoidance.blend_length_m);
    if (item["virtual_block_s_m"] && item["virtual_block_s_m"].IsScalar()) {
      policy.has_virtual_block_s = true;
      policy.virtual_block_s_m = item["virtual_block_s_m"].as<double>();
    }
    avoidance.sections.push_back(policy);
  }
  std::sort(avoidance.sections.begin(), avoidance.sections.end(), [](const auto & lhs, const auto & rhs) {
    return lhs.start_s_m < rhs.start_s_m;
  });
  return avoidance;
}

LanePlannerConfig load_lane_planner_config(const YAML::Node & config)
{
  LanePlannerConfig planner;
  const YAML::Node node = config["avoidance_planner"];
  if (!node) {
    return planner;
  }

  planner.enabled = node["enabled"].as<bool>(false);
  planner.candidate_wall_margin_m =
    node["candidate_wall_margin_m"].as<double>(planner.candidate_wall_margin_m);
  planner.prediction_horizon_s = node["prediction_horizon_s"].as<double>(planner.prediction_horizon_s);
  planner.prediction_dt_s = node["prediction_dt_s"].as<double>(planner.prediction_dt_s);
  planner.ego_radius_m = node["ego_radius_m"].as<double>(planner.ego_radius_m);
  planner.obstacle_radius_m = node["obstacle_radius_m"].as<double>(planner.obstacle_radius_m);
  planner.collision_margin_m = node["collision_margin_m"].as<double>(planner.collision_margin_m);
  planner.min_hold_time_s = node["min_hold_time_s"].as<double>(planner.min_hold_time_s);
  planner.switch_cooldown_s = node["switch_cooldown_s"].as<double>(planner.switch_cooldown_s);
  planner.switch_margin_m = node["switch_margin_m"].as<double>(planner.switch_margin_m);
  planner.obstacle_timeout_s = node["obstacle_timeout_s"].as<double>(planner.obstacle_timeout_s);
  planner.obstacle_v_max_mps = node["obstacle_v_max_mps"].as<double>(planner.obstacle_v_max_mps);
  planner.obstacle_jump_threshold_m =
    node["obstacle_jump_threshold_m"].as<double>(planner.obstacle_jump_threshold_m);
  planner.front_slow_distance_m = node["front_slow_distance_m"].as<double>(
    config["avoidance"]["slow_distance_m"].as<double>(planner.front_slow_distance_m));
  planner.front_stop_distance_m = node["front_stop_distance_m"].as<double>(
    config["avoidance"]["stop_distance_m"].as<double>(planner.front_stop_distance_m));
  planner.front_slow_speed_mps = kmh_to_mps(
    node["front_slow_speed_kmh"].as<double>(config["avoidance"]["slow_speed_kmh"].as<double>(10.0)));
  planner.emergency_override = node["emergency_override"].as<bool>(planner.emergency_override);
  planner.default_line = parse_avoidance_line(node["default_line"].as<std::string>("center"));
  return planner;
}

LongitudinalSafetyConfig load_longitudinal_safety_config(const YAML::Node & config)
{
  LongitudinalSafetyConfig safety;
  const YAML::Node node = config["longitudinal_safety"];
  if (!node) {
    return safety;
  }

  safety.enabled = node["enabled"].as<bool>(safety.enabled);
  safety.comfortable_decel_mps2 =
    node["comfortable_decel_mps2"].as<double>(safety.comfortable_decel_mps2);
  safety.emergency_decel_mps2 = node["emergency_decel_mps2"].as<double>(safety.emergency_decel_mps2);
  safety.max_brake_decel_mps2 = node["max_brake_decel_mps2"].as<double>(safety.max_brake_decel_mps2);
  safety.latency_margin_s = node["latency_margin_s"].as<double>(safety.latency_margin_s);
  safety.distance_margin_m = node["distance_margin_m"].as<double>(safety.distance_margin_m);
  safety.hard_stop_distance_m = node["hard_stop_distance_m"].as<double>(safety.hard_stop_distance_m);
  safety.min_speed_mps = node["min_speed_mps"].as<double>(safety.min_speed_mps);
  return safety;
}

LongitudinalSafetyDecision evaluate_longitudinal_safety_impl(
  const LongitudinalSafetyConfig & config, const double actual_v, const double front_distance_m)
{
  LongitudinalSafetyDecision decision;
  decision.front_distance_m = front_distance_m;
  if (!config.enabled || !std::isfinite(front_distance_m)) {
    return decision;
  }

  const double speed = std::max(0.0, std::abs(actual_v));
  const double comfortable_decel = std::max(0.1, config.comfortable_decel_mps2);
  const double emergency_decel = std::max(comfortable_decel, config.emergency_decel_mps2);
  const double max_brake_decel = std::max(emergency_decel, config.max_brake_decel_mps2);
  const double latency_distance = speed * std::max(0.0, config.latency_margin_s);
  const double margin = std::max(0.0, config.distance_margin_m);
  const auto stopping_distance = [&](const double decel) {
    return speed * speed / (2.0 * decel) + latency_distance + margin;
  };
  decision.warning_distance_m = stopping_distance(comfortable_decel);
  decision.brake_distance_m = stopping_distance(emergency_decel);

  const auto speed_limit_for_distance = [&](const double decel) {
    const double usable_distance = std::max(0.0, front_distance_m - latency_distance - margin);
    return std::sqrt(2.0 * decel * usable_distance);
  };

  if (front_distance_m <= config.hard_stop_distance_m) {
    decision.state = LongitudinalSafetyState::EmergencyStop;
    decision.speed_limit_mps = 0.0;
    decision.decel_mps2 = max_brake_decel;
    decision.reason = "hard_stop_distance";
  } else if (front_distance_m <= decision.brake_distance_m) {
    decision.state = LongitudinalSafetyState::Brake;
    decision.speed_limit_mps = speed_limit_for_distance(emergency_decel);
    decision.decel_mps2 = emergency_decel;
    decision.reason = "brake_distance";
  } else if (front_distance_m <= decision.warning_distance_m) {
    decision.state = LongitudinalSafetyState::Slowdown;
    decision.speed_limit_mps = speed_limit_for_distance(comfortable_decel);
    decision.decel_mps2 = comfortable_decel;
    decision.reason = "warning_distance";
  }

  if (decision.state != LongitudinalSafetyState::Clear && decision.speed_limit_mps < config.min_speed_mps) {
    decision.speed_limit_mps = 0.0;
  }
  return decision;
}

double lateral_target_for_line(
  const AvoidanceLine line, const double wall_margin_m, const double lb, const double ub)
{
  const double center = (lb + ub) / 2.0;
  const double margin = std::max(0.0, wall_margin_m);
  double target = center;
  if (line == AvoidanceLine::LeftWall) {
    target = ub - margin;
  } else if (line == AvoidanceLine::RightWall) {
    target = lb + margin;
  }
  return std::clamp(target, lb, ub);
}

class MpcSolver
{
public:
  MpcSolver(BicycleModel * model, const YAML::Node & config)
  : model_(model)
  {
    const auto mpc = config["mpc"];
    n_ = mpc["N"].as<int>();
    q_diag_ = mpc["Q"].as<std::vector<double>>();
    r_diag_ = mpc["R"].as<std::vector<double>>();
    qn_diag_ = mpc["QN"].as<std::vector<double>>();
    v_max_ = kmh_to_mps(mpc["v_max"].as<double>());
    a_min_ = mpc["a_min"].as<double>();
    a_max_ = mpc["a_max"].as<double>();
    ay_max_ = mpc["ay_max"].as<double>();
    delta_max_ = mpc["delta_max_deg"].as<double>() * kPi / 180.0;
    steering_rate_max_ = mpc["steer_rate_max"].as<double>() / mpc["steering_tire_angle_gain_var"].as<double>();
    wp_id_offset_ = mpc["wp_id_offset"].as<int>();
    use_max_kappa_pred_ = mpc["use_max_kappa_pred"].as<bool>();
    avoidance_config_ = load_avoidance_config(config);
    current_control_.assign(kNu * n_, 0.0);
  }

  void update_v_max(const double v_max) { v_max_ = v_max; }
  void update_ay_max(const double ay_max) { ay_max_ = ay_max; }
  void update_wp_id_offset(const int wp_id_offset) { wp_id_offset_ = wp_id_offset; }
  void update_q(const int index, const double value) { q_diag_.at(index) = value; }
  void update_r(const int index, const double value) { r_diag_.at(index) = value; }
  void update_qn(const int index, const double value) { qn_diag_.at(index) = value; }
  void update_avoidance_config(const AvoidanceConfig & config) { avoidance_config_ = config; }
  void update_selected_avoidance_line(const AvoidanceLine line, const bool enabled)
  {
    selected_avoidance_line_ = line;
    has_selected_avoidance_line_ = enabled;
  }
  void update_planner_speed_limit(const double speed_limit_mps, const bool enabled)
  {
    planner_speed_limit_mps_ = speed_limit_mps;
    has_planner_speed_limit_ = enabled;
  }

  MpcProblem debug_problem()
  {
    model_->update_current_waypoint();
    model_->update_spatial_state();
    return init_problem(n_, model_->safety_margin());
  }

  std::vector<double> debug_solve(const MpcProblem & problem)
  {
    OsqpWorkspace workspace;
    workspace.setup(problem.p, problem.q, problem.a, problem.l, problem.u);
    return workspace.solve();
  }

  std::pair<std::array<double, 2>, double> get_control()
  {
    model_->update_current_waypoint();
    const int horizon = n_;
    model_->update_spatial_state();
    std::vector<double> solution;
    std::vector<double> control_signals;
    try {
      MpcProblem problem = init_problem(horizon, model_->safety_margin());
      workspace_.setup(problem.p, problem.q, problem.a, problem.l, problem.u);
      solution = workspace_.solve();
      control_signals.assign(solution.end() - horizon * kNu, solution.end());

      bool all_use_control_signals = true;
      for (int i = 1; i < static_cast<int>(control_signals.size()); i += 2) {
        if (control_signals.at(i) == 0.0) {
          all_use_control_signals = false;
          break;
        }
      }

      if (!all_use_control_signals) {
        for (int i = 1; i < 6; ++i) {
          const double relaxed_safety_margin = model_->safety_margin() * ((5.0 - static_cast<double>(i)) / 5.0);
          problem = init_problem(horizon, relaxed_safety_margin);
          workspace_.setup(problem.p, problem.q, problem.a, problem.l, problem.u);
          solution = workspace_.solve();
          control_signals.assign(solution.end() - horizon * kNu, solution.end());

          all_use_control_signals = true;
          for (int j = 1; j < static_cast<int>(control_signals.size()); j += 2) {
            if (control_signals.at(j) == 0.0) {
              all_use_control_signals = false;
              break;
            }
          }
          if (infeasibility_counter_ == 0 && all_use_control_signals) {
            break;
          }
        }
      }
    } catch (const std::exception & error) {
      if (infeasibility_counter_ % 40 == 0) {
        std::cerr << "C++ MPC solve failed: " << error.what() << std::endl;
      }
      const int id = kNu * (infeasibility_counter_ + 1);
      ++infeasibility_counter_;
      if (id + 2 < static_cast<int>(current_control_.size())) {
        return {{{current_control_.at(id), current_control_.at(id + 1)}}, std::abs(current_control_.at(id + 1))};
      }
      return {{{0.0, 0.0}}, 0.0};
    }

    for (int i = 1; i < static_cast<int>(control_signals.size()); i += 2) {
      control_signals.at(i) = std::atan(control_signals.at(i) * model_->length());
    }

    double delta = control_signals.at(1);
    const double max_delta_change = steering_rate_max_ * model_->ts();
    delta = std::clamp(delta, previous_steering_ - max_delta_change, previous_steering_ + max_delta_change);
    previous_steering_ = delta;

    current_control_ = control_signals;
    update_prediction(solution, horizon);

    double max_delta = 0.0;
    const int max_index = static_cast<int>(control_signals.size()) / 3 * 2;
    for (int i = 1; i < max_index; i += 2) {
      max_delta = std::max(max_delta, std::abs(control_signals.at(i)));
    }
    infeasibility_counter_ = 0;
    return {{{control_signals.at(0), delta}}, max_delta};
  }

  const std::vector<double> & prediction_x() const { return prediction_x_; }
  const std::vector<double> & prediction_y() const { return prediction_y_; }

private:
  double normalize_path_s(const double path_s) const
  {
    const double total = model_->reference_path().total_length();
    if (total <= 0.0) {
      return path_s;
    }
    double normalized = std::fmod(path_s, total);
    if (normalized < 0.0) {
      normalized += total;
    }
    return normalized;
  }

  const AvoidanceSectionPolicy * policy_at_s(const double path_s) const
  {
    if (!avoidance_config_.enabled) {
      return nullptr;
    }
    const double local_s = normalize_path_s(path_s);
    for (const auto & policy : avoidance_config_.sections) {
      if (policy.start_s_m <= local_s && local_s <= policy.end_s_m) {
        return &policy;
      }
    }
    return nullptr;
  }

  const AvoidanceSectionPolicy * next_policy_after_s(const double path_s) const
  {
    if (!avoidance_config_.enabled || avoidance_config_.sections.empty()) {
      return nullptr;
    }
    const double local_s = normalize_path_s(path_s);
    for (const auto & policy : avoidance_config_.sections) {
      if (local_s < policy.start_s_m) {
        return &policy;
      }
    }

    return nullptr;
  }

  double distance_to_policy_start(const AvoidanceSectionPolicy & policy, const double path_s) const
  {
    const double local_s = normalize_path_s(path_s);
    if (local_s <= policy.start_s_m) {
      return policy.start_s_m - local_s;
    }
    const double total = model_->reference_path().total_length();
    if (total <= 0.0) {
      return std::numeric_limits<double>::infinity();
    }
    return total - local_s + policy.start_s_m;
  }

  double lateral_target_for_line(
    const AvoidanceLine line, const double wall_margin_m, const double lb, const double ub) const
  {
    return ::lateral_target_for_line(line, wall_margin_m, lb, ub);
  }

  AvoidanceLine effective_line_for_policy(const AvoidanceSectionPolicy & policy) const
  {
    return has_selected_avoidance_line_ ? selected_avoidance_line_ : policy.line;
  }

  double lateral_target_for_policy(
    const AvoidanceSectionPolicy * policy, const double path_s, const double lb, const double ub) const
  {
    const double center = (lb + ub) / 2.0;
    if (!avoidance_config_.enabled) {
      return center;
    }

    if (policy == nullptr) {
      const AvoidanceSectionPolicy * next_policy = next_policy_after_s(path_s);
      if (next_policy == nullptr || next_policy->blend_length_m <= 0.0) {
        return center;
      }

      const double distance_to_start = distance_to_policy_start(*next_policy, path_s);
      if (distance_to_start > next_policy->blend_length_m) {
        return center;
      }

      const double next_target = lateral_target_for_line(
        effective_line_for_policy(*next_policy), next_policy->wall_margin_m, lb, ub);
      const double alpha =
        std::clamp(1.0 - distance_to_start / next_policy->blend_length_m, 0.0, 1.0);
      return center + (next_target - center) * alpha;
    }

    const double current_target =
      lateral_target_for_line(effective_line_for_policy(*policy), policy->wall_margin_m, lb, ub);
    if (policy->blend_length_m <= 0.0) {
      return current_target;
    }

    const double local_s = normalize_path_s(path_s);
    const double distance_to_end = policy->end_s_m - local_s;
    if (distance_to_end > policy->blend_length_m) {
      return current_target;
    }

    const AvoidanceSectionPolicy * next_policy = next_policy_after_s(path_s);
    const bool next_is_near =
      next_policy != nullptr && (next_policy->start_s_m - policy->end_s_m) <= policy->blend_length_m;
    const double end_target = next_is_near
                                ? lateral_target_for_line(
                                    effective_line_for_policy(*next_policy), next_policy->wall_margin_m, lb, ub)
                                : center;
    const double alpha = std::clamp(distance_to_end / policy->blend_length_m, 0.0, 1.0);
    return end_target + (current_target - end_target) * alpha;
  }

  double speed_limit_for_policy(const AvoidanceSectionPolicy * policy, const double path_s) const
  {
    if (policy == nullptr || !policy->has_virtual_block_s) {
      return v_max_;
    }
    const double local_s = normalize_path_s(path_s);
    const double distance_to_block = policy->virtual_block_s_m - local_s;
    if (distance_to_block < 0.0) {
      return v_max_;
    }
    if (distance_to_block <= avoidance_config_.stop_distance_m) {
      return 0.0;
    }
    if (distance_to_block <= avoidance_config_.slow_distance_m) {
      return avoidance_config_.slow_speed_mps;
    }
    return v_max_;
  }

  MpcProblem init_problem(const int horizon, const double safety_margin)
  {
    const int nx_n = kNx * (horizon + 1);
    const int nu_n = kNu * horizon;
    const int nvar = nx_n + nu_n;
    const int n_rate = horizon - 1;
    const int n_constraints = nx_n + nvar + n_rate;

    std::vector<double> xr(nx_n, 0.0);
    std::vector<double> ur(nu_n, 0.0);
    std::vector<double> uq(horizon * kNx, 0.0);
    std::vector<double> xmin_dyn(nx_n, -std::numeric_limits<double>::infinity());
    std::vector<double> xmax_dyn(nx_n, std::numeric_limits<double>::infinity());
    std::vector<double> umax_dyn(nu_n, 0.0);
    std::vector<double> umin_dyn(nu_n, 0.0);

    const double curvature_min = -std::tan(delta_max_) / model_->length();
    const double curvature_max = std::tan(delta_max_) / model_->length();
    for (int n = 0; n < horizon; ++n) {
      umin_dyn.at(kNu * n) = 0.0;
      umin_dyn.at(kNu * n + 1) = curvature_min;
      umax_dyn.at(kNu * n) = v_max_;
      umax_dyn.at(kNu * n + 1) = curvature_max;
    }

    std::vector<double> kappa_pred(horizon, 0.0);
    for (int n = 0; n < horizon; ++n) {
      const int control_index = std::min<int>(3 + kNu * n, static_cast<int>(current_control_.size()) - 1);
      kappa_pred.at(n) = std::tan(current_control_.at(control_index)) / model_->length();
    }

    const int delayed_wp_id = model_->wp_id() + wp_id_offset_;
    model_->set_wp_id(delayed_wp_id);

    std::vector<std::tuple<int, int, double>> a_triplets;
    a_triplets.reserve(nx_n + horizon * 8 + nvar + n_rate * 2);
    for (int index = 0; index < nx_n; ++index) {
      a_triplets.emplace_back(index, index, -1.0);
    }

    for (int n = 0; n < horizon; ++n) {
      const auto & current_wp = model_->reference_path().get_waypoint(model_->wp_id() + n);
      const auto & next_wp = model_->reference_path().get_waypoint(model_->wp_id() + n + 1);
      const double path_s = model_->reference_path().s_at_index(model_->wp_id() + n);
      const AvoidanceSectionPolicy * policy = policy_at_s(path_s);
      double policy_speed_limit = speed_limit_for_policy(policy, path_s);
      if (has_planner_speed_limit_) {
        policy_speed_limit = std::min(policy_speed_limit, planner_speed_limit_mps_);
      }
      const double delta_s = std::hypot(next_wp.x - current_wp.x, next_wp.y - current_wp.y);
      const double kappa_ref = current_wp.kappa;
      const double v_ref = std::clamp(current_wp.v_ref, 0.0, std::min(v_max_, policy_speed_limit));
      const auto [f, a_lin, b_lin] = model_->linearize(v_ref, kappa_ref, delta_s);

      const int row = (n + 1) * kNx;
      const int state_col = n * kNx;
      const int input_col = nx_n + n * kNu;
      for (const auto & rc : {std::pair<int, int>{0, 0}, {0, 1}, {1, 0}, {1, 1}, {2, 0}, {2, 2}}) {
        a_triplets.emplace_back(row + rc.first, state_col + rc.second, a_lin(rc.first, rc.second));
      }
      a_triplets.emplace_back(row + 1, input_col + 1, b_lin(1, 1));
      a_triplets.emplace_back(row + 2, input_col + 0, b_lin(2, 0));

      ur.at(n * kNu) = v_ref;
      ur.at(n * kNu + 1) = kappa_ref;
      const Eigen::Vector2d ref_input(v_ref, kappa_ref);
      const Eigen::Vector3d uq_block = b_lin * ref_input - f;
      for (int r = 0; r < kNx; ++r) {
        uq.at(n * kNx + r) = uq_block(r);
      }

      double max_kappa_pred = std::abs(kappa_pred.at(n));
      if (use_max_kappa_pred_) {
        for (int j = n; j < horizon; ++j) {
          max_kappa_pred = std::max(max_kappa_pred, std::abs(kappa_pred.at(j)));
        }
      }
      const double vmax_dyn = std::sqrt(ay_max_ / (max_kappa_pred + 1.0e-12));
      umax_dyn.at(kNu * n) = std::min({vmax_dyn, policy_speed_limit, umax_dyn.at(kNu * n)});
    }

    const auto [ub, lb] = model_->reference_path().get_path_constraints(model_->wp_id() + 1, horizon, safety_margin);
    xmin_dyn.at(0) = model_->spatial_state().e_y;
    xmax_dyn.at(0) = model_->spatial_state().e_y;
    for (int n = 0; n < horizon; ++n) {
      const double path_s = model_->reference_path().s_at_index(model_->wp_id() + 1 + n);
      const AvoidanceSectionPolicy * policy = policy_at_s(path_s);
      xmin_dyn.at(kNx + n * kNx) = lb.at(n);
      xmax_dyn.at(kNx + n * kNx) = ub.at(n);
      xr.at(kNx + n * kNx) = lateral_target_for_policy(policy, path_s, lb.at(n), ub.at(n));
    }

    const int ineq_row = nx_n;
    for (int index = 0; index < nvar; ++index) {
      a_triplets.emplace_back(ineq_row + index, index, 1.0);
    }
    const int rate_row = nx_n + nvar;
    for (int n = 0; n < n_rate; ++n) {
      a_triplets.emplace_back(rate_row + n, nx_n + kNu * n + 1, -1.0);
      a_triplets.emplace_back(rate_row + n, nx_n + kNu * (n + 1) + 1, 1.0);
    }

    std::vector<double> lower(n_constraints, 0.0);
    std::vector<double> upper(n_constraints, 0.0);
    lower.at(0) = upper.at(0) = -model_->spatial_state().e_y;
    lower.at(1) = upper.at(1) = -model_->spatial_state().e_psi;
    lower.at(2) = upper.at(2) = -model_->spatial_state().t;
    for (int idx = 0; idx < static_cast<int>(uq.size()); ++idx) {
      lower.at(kNx + idx) = uq.at(idx);
      upper.at(kNx + idx) = uq.at(idx);
    }
    for (int idx = 0; idx < nx_n; ++idx) {
      lower.at(nx_n + idx) = xmin_dyn.at(idx);
      upper.at(nx_n + idx) = xmax_dyn.at(idx);
    }
    for (int idx = 0; idx < nu_n; ++idx) {
      lower.at(nx_n + nx_n + idx) = umin_dyn.at(idx);
      upper.at(nx_n + nx_n + idx) = umax_dyn.at(idx);
    }
    const double max_delta_change = steering_rate_max_ * model_->ts();
    for (int idx = 0; idx < n_rate; ++idx) {
      lower.at(nx_n + nvar + idx) = -max_delta_change;
      upper.at(nx_n + nvar + idx) = max_delta_change;
    }

    std::vector<std::tuple<int, int, double>> p_triplets;
    p_triplets.reserve(nvar);
    std::vector<double> q(nvar, 0.0);
    for (int n = 0; n < horizon; ++n) {
      for (int r = 0; r < kNx; ++r) {
        const int idx = n * kNx + r;
        if (q_diag_.at(r) != 0.0) {
          p_triplets.emplace_back(idx, idx, q_diag_.at(r));
        }
        q.at(idx) = -q_diag_.at(r) * xr.at(idx);
      }
    }
    for (int r = 0; r < kNx; ++r) {
      const int idx = horizon * kNx + r;
      if (qn_diag_.at(r) != 0.0) {
        p_triplets.emplace_back(idx, idx, qn_diag_.at(r));
      }
      q.at(idx) = -qn_diag_.at(r) * xr.at(idx);
    }
    for (int n = 0; n < horizon; ++n) {
      for (int r = 0; r < kNu; ++r) {
        const int idx = nx_n + n * kNu + r;
        if (r_diag_.at(r) != 0.0) {
          p_triplets.emplace_back(idx, idx, r_diag_.at(r));
        }
        q.at(idx) = -r_diag_.at(r) * ur.at(n * kNu + r);
      }
    }

    return MpcProblem{
      build_csc_from_triplets(nvar, nvar, std::move(p_triplets)),
      build_csc_from_triplets(n_constraints, nvar, std::move(a_triplets)), q, lower, upper};
  }

  void update_prediction(const std::vector<double> & solution, const int horizon)
  {
    prediction_x_.clear();
    prediction_y_.clear();
    for (int n = 2; n < horizon; ++n) {
      const auto & associated_wp = model_->reference_path().get_waypoint(model_->wp_id() + n);
      SpatialState state{
        solution.at(n * kNx), solution.at(n * kNx + 1), solution.at(n * kNx + 2)};
      const auto temporal = model_->s2t(associated_wp, state);
      prediction_x_.push_back(temporal.x);
      prediction_y_.push_back(temporal.y);
    }
  }

  BicycleModel * model_{};
  int n_{};
  std::vector<double> q_diag_;
  std::vector<double> r_diag_;
  std::vector<double> qn_diag_;
  double v_max_{};
  double a_min_{};
  double a_max_{};
  double ay_max_{};
  double delta_max_{};
  double steering_rate_max_{};
  int wp_id_offset_{};
  bool use_max_kappa_pred_{};
  AvoidanceConfig avoidance_config_;
  AvoidanceLine selected_avoidance_line_{AvoidanceLine::Center};
  bool has_selected_avoidance_line_{};
  double planner_speed_limit_mps_{std::numeric_limits<double>::infinity()};
  bool has_planner_speed_limit_{};
  double previous_steering_{};
  int infeasibility_counter_{};
  std::vector<double> current_control_;
  std::vector<double> prediction_x_;
  std::vector<double> prediction_y_;
  OsqpWorkspace workspace_;
};

std::pair<std::vector<double>, std::vector<double>> load_reference_csv(const std::string & path)
{
  std::ifstream file(path);
  if (!file) {
    throw std::runtime_error("failed to open reference path: " + path);
  }
  std::string header;
  std::getline(file, header);
  const auto columns = split_csv_line(header);
  const auto find_index = [&](const std::string & name) {
    const auto it = std::find(columns.begin(), columns.end(), name);
    if (it == columns.end()) {
      throw std::runtime_error("missing CSV column: " + name);
    }
    return static_cast<int>(std::distance(columns.begin(), it));
  };
  const int x_idx = find_index("x_m");
  const int y_idx = find_index("y_m");
  std::vector<double> x;
  std::vector<double> y;
  std::string line;
  while (std::getline(file, line)) {
    if (line.empty()) {
      continue;
    }
    const auto values = split_csv_line(line);
    x.push_back(std::stod(values.at(x_idx)));
    y.push_back(std::stod(values.at(y_idx)));
  }
  return {x, y};
}

std::vector<RefVelSection> load_ref_vel_sections(const std::string & path)
{
  std::vector<RefVelSection> sections;
  if (path.empty()) {
    return sections;
  }
  const YAML::Node root = YAML::LoadFile(path);
  if (!root["ref_vel_configulator"]) {
    return sections;
  }
  for (const auto & item : root["ref_vel_configulator"]) {
    const auto node = item.second;
    sections.push_back(RefVelSection{node["wp_id"].as<int>(), node["ref_vel"].as<double>()});
  }
  std::sort(sections.begin(), sections.end(), [](const auto & lhs, const auto & rhs) {
    return lhs.wp_id < rhs.wp_id;
  });
  return sections;
}

double get_ref_vel_kmh(const std::vector<RefVelSection> & sections, const int current_wp_id)
{
  if (sections.empty()) {
    return 0.0;
  }
  for (size_t i = 0; i < sections.size(); ++i) {
    const int start = sections.at(i).wp_id;
    const int end = sections.at((i + 1) % sections.size()).wp_id;
    if (start <= end) {
      if (start <= current_wp_id && current_wp_id < end) {
        return sections.at(i).ref_vel_kmh;
      }
    } else if (current_wp_id >= start || current_wp_id < end) {
      return sections.at(i).ref_vel_kmh;
    }
  }
  return sections.front().ref_vel_kmh;
}

std::string constraints_cache_path_for_reference_csv(const std::string & reference_csv_path)
{
  const auto dot = reference_csv_path.find_last_of('.');
  if (dot == std::string::npos) {
    return reference_csv_path + "_constraints.csv";
  }
  return reference_csv_path.substr(0, dot) + "_constraints" + reference_csv_path.substr(dot);
}

template <typename T>
void write_vector(std::ofstream & out, const std::string & name, const std::vector<T> & values)
{
  out << name;
  for (const auto & value : values) {
    out << "," << std::setprecision(17) << value;
  }
  out << "\n";
}

int run_qp_dump(const std::string & output_path, const int dump_wp_id)
{
  const std::string package_path = ament_index_cpp::get_package_share_directory("multi_purpose_mpc_ros") + "/";
  const YAML::Node config = YAML::LoadFile(package_path + "config/config.yaml");
  const auto ref_cfg = config["reference_path"];
  const std::string reference_csv_path = package_path + ref_cfg["csv_path"].as<std::string>();
  const auto [wp_x, wp_y] = load_reference_csv(reference_csv_path);
  auto occupancy_map = std::make_unique<OccupancyMap>(package_path + config["map"]["yaml_path"].as<std::string>());
  auto reference_path = std::make_unique<ReferencePath>(
    occupancy_map.get(), wp_x, wp_y, ref_cfg["resolution"].as<double>(), ref_cfg["smoothing_distance"].as<int>(),
    ref_cfg["max_width"].as<double>(), ref_cfg["circular"].as<bool>());
  reference_path->load_width_constraints_csv(constraints_cache_path_for_reference_csv(reference_csv_path));

  const auto mpc_cfg = config["mpc"];
  reference_path->compute_speed_profile(
    mpc_cfg["a_min"].as<double>(), mpc_cfg["a_max"].as<double>(), 0.0,
    kmh_to_mps(mpc_cfg["v_max"].as<double>()), mpc_cfg["ay_max"].as<double>());
  reference_path->update_simple_path_constraints(
    mpc_cfg["N"].as<int>(), config["bicycle_model"]["width"].as<double>() / std::sqrt(2.0));

  BicycleModel model(
    reference_path.get(), config["bicycle_model"]["length"].as<double>(), config["bicycle_model"]["width"].as<double>(),
    1.0 / mpc_cfg["control_rate"].as<double>());
  const auto & initial_wp = reference_path->get_waypoint(dump_wp_id);
  model.update_states(initial_wp.x, initial_wp.y, initial_wp.psi);
  MpcSolver solver(&model, config);
  const auto problem = solver.debug_problem();
  const auto solution = solver.debug_solve(problem);
  const int horizon = mpc_cfg["N"].as<int>();
  std::vector<double> raw_control(solution.end() - horizon * kNu, solution.end());
  std::vector<double> steer_control = raw_control;
  for (int i = 1; i < static_cast<int>(steer_control.size()); i += 2) {
    steer_control.at(i) = std::atan(steer_control.at(i) * model.length());
  }

  std::ofstream out(output_path);
  if (!out) {
    std::cerr << "failed to open dump path: " << output_path << std::endl;
    return 1;
  }
  out << "meta,nx," << kNx << ",nu," << kNu << ",wp_id," << dump_wp_id << "\n";
  out << "P_shape," << problem.p.rows << "," << problem.p.cols << "," << problem.p.x.size() << "\n";
  write_vector(out, "P_x", problem.p.x);
  write_vector(out, "P_i", problem.p.i);
  write_vector(out, "P_p", problem.p.p);
  out << "A_shape," << problem.a.rows << "," << problem.a.cols << "," << problem.a.x.size() << "\n";
  write_vector(out, "A_x", problem.a.x);
  write_vector(out, "A_i", problem.a.i);
  write_vector(out, "A_p", problem.a.p);
  write_vector(out, "q", problem.q);
  write_vector(out, "l", problem.l);
  write_vector(out, "u", problem.u);
  write_vector(out, "solution", solution);
  write_vector(out, "raw_control", raw_control);
  write_vector(out, "steer_control", steer_control);
  return 0;
}

int run_sequence_dump(const std::string & output_path, const int start_wp_id, const int steps)
{
  const std::string package_path = ament_index_cpp::get_package_share_directory("multi_purpose_mpc_ros") + "/";
  const YAML::Node config = YAML::LoadFile(package_path + "config/config.yaml");
  const auto ref_cfg = config["reference_path"];
  const std::string reference_csv_path = package_path + ref_cfg["csv_path"].as<std::string>();
  const auto [wp_x, wp_y] = load_reference_csv(reference_csv_path);
  auto occupancy_map = std::make_unique<OccupancyMap>(package_path + config["map"]["yaml_path"].as<std::string>());
  auto reference_path = std::make_unique<ReferencePath>(
    occupancy_map.get(), wp_x, wp_y, ref_cfg["resolution"].as<double>(), ref_cfg["smoothing_distance"].as<int>(),
    ref_cfg["max_width"].as<double>(), ref_cfg["circular"].as<bool>());
  reference_path->load_width_constraints_csv(constraints_cache_path_for_reference_csv(reference_csv_path));

  const auto mpc_cfg = config["mpc"];
  reference_path->compute_speed_profile(
    mpc_cfg["a_min"].as<double>(), mpc_cfg["a_max"].as<double>(), 0.0,
    kmh_to_mps(mpc_cfg["v_max"].as<double>()), mpc_cfg["ay_max"].as<double>());
  reference_path->update_simple_path_constraints(
    mpc_cfg["N"].as<int>(), config["bicycle_model"]["width"].as<double>() / std::sqrt(2.0));

  BicycleModel model(
    reference_path.get(), config["bicycle_model"]["length"].as<double>(), config["bicycle_model"]["width"].as<double>(),
    1.0 / mpc_cfg["control_rate"].as<double>());
  const auto & initial_wp = reference_path->get_waypoint(start_wp_id);
  model.update_states(initial_wp.x, initial_wp.y, initial_wp.psi);
  MpcSolver solver(&model, config);

  std::ofstream out(output_path);
  if (!out) {
    std::cerr << "failed to open sequence dump path: " << output_path << std::endl;
    return 1;
  }
  out << "step,wp_id,x,y,psi,v_cmd,delta_cmd,max_delta\n";
  for (int step = 0; step < steps; ++step) {
    const auto [control, max_delta] = solver.get_control();
    const auto state = model.temporal_state();
    out << step << "," << model.wp_id() << "," << std::setprecision(17) << state.x << "," << state.y << ","
        << state.psi << "," << control[0] << "," << control[1] << "," << max_delta << "\n";
    model.drive(control[0], control[1]);
  }
  return 0;
}

int run_sequence_benchmark(const int start_wp_id, const int steps)
{
  using Clock = std::chrono::steady_clock;
  const auto total_start = Clock::now();
  const std::string package_path = ament_index_cpp::get_package_share_directory("multi_purpose_mpc_ros") + "/";
  const YAML::Node config = YAML::LoadFile(package_path + "config/config.yaml");
  const auto ref_cfg = config["reference_path"];
  const std::string reference_csv_path = package_path + ref_cfg["csv_path"].as<std::string>();
  const auto [wp_x, wp_y] = load_reference_csv(reference_csv_path);
  auto occupancy_map = std::make_unique<OccupancyMap>(package_path + config["map"]["yaml_path"].as<std::string>());
  auto reference_path = std::make_unique<ReferencePath>(
    occupancy_map.get(), wp_x, wp_y, ref_cfg["resolution"].as<double>(), ref_cfg["smoothing_distance"].as<int>(),
    ref_cfg["max_width"].as<double>(), ref_cfg["circular"].as<bool>());
  reference_path->load_width_constraints_csv(constraints_cache_path_for_reference_csv(reference_csv_path));

  const auto mpc_cfg = config["mpc"];
  reference_path->compute_speed_profile(
    mpc_cfg["a_min"].as<double>(), mpc_cfg["a_max"].as<double>(), 0.0,
    kmh_to_mps(mpc_cfg["v_max"].as<double>()), mpc_cfg["ay_max"].as<double>());
  reference_path->update_simple_path_constraints(
    mpc_cfg["N"].as<int>(), config["bicycle_model"]["width"].as<double>() / std::sqrt(2.0));

  BicycleModel model(
    reference_path.get(), config["bicycle_model"]["length"].as<double>(), config["bicycle_model"]["width"].as<double>(),
    1.0 / mpc_cfg["control_rate"].as<double>());
  const auto & initial_wp = reference_path->get_waypoint(start_wp_id);
  model.update_states(initial_wp.x, initial_wp.y, initial_wp.psi);
  MpcSolver solver(&model, config);
  const auto setup_end = Clock::now();

  std::vector<double> samples_ms;
  samples_ms.reserve(steps);
  const auto loop_start = Clock::now();
  for (int step = 0; step < steps; ++step) {
    const auto step_start = Clock::now();
    const auto [control, max_delta] = solver.get_control();
    (void)max_delta;
    samples_ms.push_back(std::chrono::duration<double, std::milli>(Clock::now() - step_start).count());
    model.drive(control[0], control[1]);
  }
  const auto loop_end = Clock::now();

  auto sorted_samples = samples_ms;
  std::sort(sorted_samples.begin(), sorted_samples.end());
  const double sum = std::accumulate(samples_ms.begin(), samples_ms.end(), 0.0);
  const double mean = sum / static_cast<double>(samples_ms.size());
  const size_t p95_index = std::min(
    sorted_samples.size() - 1, static_cast<size_t>(std::ceil(static_cast<double>(sorted_samples.size()) * 0.95)) - 1);
  const double setup_ms = std::chrono::duration<double, std::milli>(setup_end - total_start).count();
  const double loop_ms = std::chrono::duration<double, std::milli>(loop_end - loop_start).count();
  std::cout << "cpp_mpc_load_test"
            << " steps=" << steps << " wp_id=" << start_wp_id << " setup_ms=" << std::setprecision(3) << std::fixed
            << setup_ms << " loop_total_ms=" << loop_ms << " mean_ms=" << mean
            << " p95_ms=" << sorted_samples.at(p95_index) << " max_ms=" << sorted_samples.back()
            << " rate_hz=" << static_cast<double>(steps) / (loop_ms / 1000.0) << std::endl;
  return 0;
}

int run_safety_dump(const double speed_kmh, const double front_distance_m)
{
  const std::string package_path = ament_index_cpp::get_package_share_directory("multi_purpose_mpc_ros") + "/";
  const YAML::Node config = YAML::LoadFile(package_path + "config/config.yaml");
  const LongitudinalSafetyConfig safety_config = load_longitudinal_safety_config(config);
  const LongitudinalSafetyDecision decision =
    evaluate_longitudinal_safety_impl(safety_config, kmh_to_mps(speed_kmh), front_distance_m);

  std::cout << "state," << longitudinal_safety_state_to_string(decision.state) << "\n";
  std::cout << "speed_limit_mps," << std::setprecision(17) << decision.speed_limit_mps << "\n";
  std::cout << "decel_mps2," << std::setprecision(17) << decision.decel_mps2 << "\n";
  std::cout << "front_distance_m," << std::setprecision(17) << decision.front_distance_m << "\n";
  std::cout << "warning_distance_m," << std::setprecision(17) << decision.warning_distance_m << "\n";
  std::cout << "brake_distance_m," << std::setprecision(17) << decision.brake_distance_m << "\n";
  std::cout << "reason," << decision.reason << "\n";
  return 0;
}

class MpcControllerCpp : public rclcpp::Node
{
public:
  MpcControllerCpp()
  : Node("mpc_controller_cpp")
  {
    declare_parameter("config_path", std::string("config/config.yaml"));
    declare_parameter("ref_vel_config_path", std::string("config/ref_vel.yaml"));
    declare_parameter("use_boost_acceleration", false);
    declare_parameter("use_obstacle_avoidance", false);
    declare_parameter("use_stats", false);

    package_path_ = ament_index_cpp::get_package_share_directory("multi_purpose_mpc_ros") + "/";
    config_path_ = resolve_pkg_path(get_parameter("config_path").as_string());
    ref_vel_config_path_ = resolve_pkg_path(get_parameter("ref_vel_config_path").as_string());
    use_boost_acceleration_ = get_parameter("use_boost_acceleration").as_bool();
    config_ = YAML::LoadFile(config_path_);
    lane_planner_config_ = load_lane_planner_config(config_);
    longitudinal_safety_config_ = load_longitudinal_safety_config(config_);
    selected_lane_ = lane_planner_config_.default_line;
    ref_vel_sections_ = load_ref_vel_sections(ref_vel_config_path_);

    const auto ref_cfg = config_["reference_path"];
    const std::string reference_csv_path = resolve_pkg_path(ref_cfg["csv_path"].as<std::string>());
    const auto [wp_x, wp_y] = load_reference_csv(reference_csv_path);
    occupancy_map_ = std::make_unique<OccupancyMap>(resolve_pkg_path(config_["map"]["yaml_path"].as<std::string>()));
    reference_path_ = std::make_unique<ReferencePath>(
      occupancy_map_.get(), wp_x, wp_y, ref_cfg["resolution"].as<double>(),
      ref_cfg["smoothing_distance"].as<int>(), ref_cfg["max_width"].as<double>(), ref_cfg["circular"].as<bool>());
    reference_path_->load_width_constraints_csv(constraints_cache_path_for_reference_csv(reference_csv_path));

    const auto mpc_cfg = config_["mpc"];
    const double initial_v_max_kmh = use_boost_acceleration_ ? 40.0 : mpc_cfg["v_max"].as<double>();
    reference_path_->compute_speed_profile(
      mpc_cfg["a_min"].as<double>(), mpc_cfg["a_max"].as<double>(), 0.0,
      kmh_to_mps(initial_v_max_kmh), mpc_cfg["ay_max"].as<double>());
    reference_path_->update_simple_path_constraints(
      mpc_cfg["N"].as<int>(), config_["bicycle_model"]["width"].as<double>() / std::sqrt(2.0));

    model_ = std::make_unique<BicycleModel>(
      reference_path_.get(), config_["bicycle_model"]["length"].as<double>(),
      config_["bicycle_model"]["width"].as<double>(), 1.0 / mpc_cfg["control_rate"].as<double>());
    mpc_ = std::make_unique<MpcSolver>(model_.get(), config_);
    if (use_boost_acceleration_) {
      mpc_->update_v_max(kmh_to_mps(40.0));
    }

    if (use_boost_acceleration_) {
      boost_command_pub_ = create_publisher<AckermannControlBoostCommand>("/boost_commander/command", 1);
    } else {
      command_pub_ = create_publisher<AckermannControlCommand>("/control/command/control_cmd", 1);
      command_raw_pub_ = create_publisher<AckermannControlCommand>("/control/command/control_cmd_raw", 1);
    }
    prediction_pub_ = create_publisher<MarkerArray>("/mpc/prediction", 1);
    prediction_path_pub_ = create_publisher<Path>("/mpc/predicted_path", 1);
    prediction_dummy_pub_ = create_publisher<MarkerArray>(
      "/planning/scenario_planning/lane_driving/motion_planning/obstacle_stop_planner/virtual_wall", 1);
    ref_path_pub_ = create_publisher<MarkerArray>("/mpc/ref_path", rclcpp::QoS(1).transient_local());
    ref_path_dummy_pub_ = create_publisher<MarkerArray>(
      "/planning/scenario_planning/lane_driving/behavior_planning/behavior_path_planner/debug/bound",
      rclcpp::QoS(1).transient_local());
    avoidance_candidates_pub_ = create_publisher<MarkerArray>("/mpc/avoidance_candidates", 1);
    selected_avoidance_pub_ = create_publisher<MarkerArray>("/mpc/selected_avoidance_lane", 1);
    avoidance_debug_pub_ = create_publisher<MarkerArray>("/mpc/avoidance_debug", 1);

    odom_sub_ = create_subscription<nav_msgs::msg::Odometry>(
      "/localization/kinematic_state", 1, [this](const nav_msgs::msg::Odometry::SharedPtr msg) { odom_ = msg; });
    control_mode_sub_ = create_subscription<std_msgs::msg::Bool>(
      "control/control_mode_request_topic", 1, [this](const std_msgs::msg::Bool::SharedPtr msg) {
        if (msg->data && !enable_control_) {
          RCLCPP_INFO(get_logger(), "Control mode request received");
          enable_control_ = true;
        }
      });
    stop_request_sub_ = create_subscription<std_msgs::msg::Empty>(
      "/control/mpc/stop_request", 1, [this](const std_msgs::msg::Empty::SharedPtr) {
        if (enable_control_) {
          RCLCPP_WARN(get_logger(), "Stop request received");
          enable_control_ = false;
        }
      });
    v2x_sub_ = create_subscription<V2XVehiclePositionArray>(
      "/v2x/vehicle_positions", 1, [this](const V2XVehiclePositionArray::SharedPtr msg) { update_v2x(*msg); });
    if (get_parameter("use_sim_time").as_bool()) {
      awsim_status_sub_ = create_subscription<std_msgs::msg::Float32MultiArray>(
        "/awsim/status", 1, [this](const std_msgs::msg::Float32MultiArray::SharedPtr msg) {
          if (msg->data.size() > 2) {
            current_laps_ = static_cast<int>(msg->data.at(1));
            last_lap_time_ = msg->data.at(2);
          }
        });
      condition_sub_ = create_subscription<std_msgs::msg::Int32>(
        "/aichallenge/pitstop/condition", 1, [this](const std_msgs::msg::Int32::SharedPtr msg) {
          last_condition_ = msg->data;
        });
    }

    publish_ref_path_marker();
    setup_parameter_callback();
    const double rate = mpc_cfg["control_rate"].as<double>();
    timer_ = create_wall_timer(std::chrono::duration<double>(1.0 / rate), [this]() { control(); });
    last_time_ = now();
    RCLCPP_INFO(get_logger(), "C++ MPC controller started");
  }

private:
  std::string resolve_pkg_path(const std::string & path) const
  {
    if (path.empty() || path.front() == '/') {
      return path;
    }
    return package_path_ + path;
  }

  static double stamp_to_seconds(const builtin_interfaces::msg::Time & stamp)
  {
    return static_cast<double>(stamp.sec) + static_cast<double>(stamp.nanosec) * 1.0e-9;
  }

  int waypoint_index_at_distance(const int start_index, const double distance_m) const
  {
    int index = start_index;
    double traveled = 0.0;
    const int max_steps = std::max(1, reference_path_->size());
    for (int step = 0; step < max_steps && traveled < distance_m; ++step) {
      traveled += std::max(1.0e-3, reference_path_->segment_length(index + 1));
      ++index;
    }
    return index;
  }

  geometry_msgs::msg::Point point_for_line(
    const int waypoint_index, const AvoidanceLine line, const double wall_margin_m) const
  {
    const auto & waypoint = reference_path_->get_waypoint(waypoint_index);
    const auto [ub, lb] = reference_path_->get_path_constraints(waypoint_index, 1, model_->safety_margin());
    const double e_y = ::lateral_target_for_line(line, wall_margin_m, lb.front(), ub.front());

    geometry_msgs::msg::Point point;
    point.x = waypoint.x - e_y * std::sin(waypoint.psi);
    point.y = waypoint.y + e_y * std::cos(waypoint.psi);
    point.z = 0.2;
    return point;
  }

  LaneCandidate build_lane_candidate(
    const AvoidanceLine line, const int start_waypoint, const double actual_v) const
  {
    LaneCandidate candidate;
    candidate.line = line;
    const double dt = std::max(0.05, lane_planner_config_.prediction_dt_s);
    const int sample_count =
      std::max(2, static_cast<int>(std::ceil(lane_planner_config_.prediction_horizon_s / dt)) + 1);
    const double speed = std::max(1.0, std::abs(actual_v));
    candidate.points.reserve(sample_count);
    candidate.distances_m.reserve(sample_count);
    for (int sample = 0; sample < sample_count; ++sample) {
      const double distance = speed * dt * static_cast<double>(sample);
      const int waypoint_index = waypoint_index_at_distance(start_waypoint, distance);
      candidate.points.push_back(point_for_line(waypoint_index, line, lane_planner_config_.candidate_wall_margin_m));
      candidate.distances_m.push_back(distance);
    }
    return candidate;
  }

  std::vector<V2XObstacleState> active_obstacles(const double now_sec) const
  {
    std::vector<V2XObstacleState> active;
    for (const auto & item : v2x_obstacles_) {
      if (now_sec - item.second.stamp <= lane_planner_config_.obstacle_timeout_s) {
        active.push_back(item.second);
      }
    }
    return active;
  }

  void evaluate_candidate_collision(LaneCandidate & candidate, const std::vector<V2XObstacleState> & obstacles) const
  {
    candidate.min_obstacle_distance = std::numeric_limits<double>::infinity();
    candidate.front_block_distance_m = std::numeric_limits<double>::infinity();
    candidate.blocked = false;
    candidate.reason = "safe";

    if (obstacles.empty()) {
      return;
    }

    const double dt = std::max(0.05, lane_planner_config_.prediction_dt_s);
    const double collision_distance =
      lane_planner_config_.ego_radius_m + lane_planner_config_.obstacle_radius_m +
      lane_planner_config_.collision_margin_m;
    const auto & ego = model_->temporal_state();
    const double dir_x = std::cos(ego.psi);
    const double dir_y = std::sin(ego.psi);
    for (size_t index = 0; index < candidate.points.size(); ++index) {
      const double t = dt * static_cast<double>(index);
      const auto & point = candidate.points.at(index);
      for (const auto & obstacle : obstacles) {
        const double ox = obstacle.x + obstacle.vx * t;
        const double oy = obstacle.y + obstacle.vy * t;
        const double forward_m = (ox - ego.x) * dir_x + (oy - ego.y) * dir_y;
        if (forward_m <= 0.0) {
          continue;
        }
        const double distance = std::hypot(point.x - ox, point.y - oy);
        candidate.min_obstacle_distance = std::min(candidate.min_obstacle_distance, distance);
        if (distance <= collision_distance) {
          candidate.blocked = true;
          candidate.front_block_distance_m = std::min(candidate.front_block_distance_m, forward_m);
          candidate.reason = "blocked:" + obstacle.vehicle_id;
        }
      }
    }
  }

  const LaneCandidate * find_candidate(
    const std::vector<LaneCandidate> & candidates, const AvoidanceLine line) const
  {
    for (const auto & candidate : candidates) {
      if (candidate.line == line) {
        return &candidate;
      }
    }
    return nullptr;
  }

  AvoidanceLine choose_best_safe_candidate(const std::vector<LaneCandidate> & candidates) const
  {
    const LaneCandidate * default_candidate = find_candidate(candidates, lane_planner_config_.default_line);
    if (default_candidate != nullptr && !default_candidate->blocked) {
      return default_candidate->line;
    }

    const LaneCandidate * best = nullptr;
    for (const auto & candidate : candidates) {
      if (candidate.blocked) {
        continue;
      }
      if (best == nullptr || candidate.min_obstacle_distance > best->min_obstacle_distance) {
        best = &candidate;
      }
    }
    if (best != nullptr) {
      return best->line;
    }

    return std::max_element(candidates.begin(), candidates.end(), [](const auto & lhs, const auto & rhs) {
      return lhs.min_obstacle_distance < rhs.min_obstacle_distance;
    })->line;
  }

  AvoidanceLine select_lane(const std::vector<LaneCandidate> & candidates, const rclcpp::Time & stamp)
  {
    if (candidates.empty()) {
      return lane_planner_config_.default_line;
    }

    const double now_sec = stamp.seconds();
    if (!has_lane_selection_) {
      has_lane_selection_ = true;
      selected_lane_ = choose_best_safe_candidate(candidates);
      selected_lane_since_sec_ = now_sec;
      last_lane_switch_sec_ = now_sec;
      lane_selection_reason_ = "initial";
      return selected_lane_;
    }

    const LaneCandidate * current = find_candidate(candidates, selected_lane_);
    const bool current_blocked = current != nullptr && current->blocked;
    const bool hold_elapsed = now_sec - selected_lane_since_sec_ >= lane_planner_config_.min_hold_time_s;
    const bool cooldown_elapsed = now_sec - last_lane_switch_sec_ >= lane_planner_config_.switch_cooldown_s;
    const bool emergency_switch = lane_planner_config_.emergency_override && current_blocked;
    if (!emergency_switch && (!hold_elapsed || !cooldown_elapsed)) {
      lane_selection_reason_ = "hold";
      return selected_lane_;
    }

    const AvoidanceLine desired = choose_best_safe_candidate(candidates);
    const LaneCandidate * desired_candidate = find_candidate(candidates, desired);
    const bool current_safe = current != nullptr && !current->blocked;
    bool should_switch = desired != selected_lane_;
    if (should_switch && current_safe && desired_candidate != nullptr) {
      should_switch =
        desired_candidate->min_obstacle_distance >
        current->min_obstacle_distance + lane_planner_config_.switch_margin_m;
    }

    if (emergency_switch || should_switch) {
      selected_lane_ = desired;
      selected_lane_since_sec_ = now_sec;
      last_lane_switch_sec_ = now_sec;
      lane_selection_reason_ = emergency_switch ? "emergency" : "safer";
    } else {
      lane_selection_reason_ = current_safe ? "keep_current" : "no_better_lane";
    }
    return selected_lane_;
  }

  double front_speed_limit_for_selected_lane(const std::vector<LaneCandidate> & candidates) const
  {
    const LaneCandidate * selected_candidate = find_candidate(candidates, selected_lane_);
    if (selected_candidate == nullptr || !std::isfinite(selected_candidate->front_block_distance_m)) {
      return std::numeric_limits<double>::infinity();
    }

    // This intentionally uses forward path distance along the selected lane.
    // Obstacles near the vehicle but outside the selected forward corridor do not trigger braking.
    const double distance = selected_candidate->front_block_distance_m;
    if (distance <= lane_planner_config_.front_stop_distance_m) {
      return 0.0;
    }
    if (distance <= lane_planner_config_.front_slow_distance_m) {
      return lane_planner_config_.front_slow_speed_mps;
    }
    return std::numeric_limits<double>::infinity();
  }

  double front_block_distance_for_selected_lane(const std::vector<LaneCandidate> & candidates) const
  {
    const LaneCandidate * selected_candidate = find_candidate(candidates, selected_lane_);
    if (selected_candidate == nullptr) {
      return std::numeric_limits<double>::infinity();
    }
    return selected_candidate->front_block_distance_m;
  }

  LongitudinalSafetyDecision evaluate_longitudinal_safety(
    const double actual_v, const double front_distance_m) const
  {
    return evaluate_longitudinal_safety_impl(longitudinal_safety_config_, actual_v, front_distance_m);
  }

  void apply_longitudinal_safety(
    const LongitudinalSafetyDecision & decision, const double actual_v, std::array<double, 2> & command,
    double & acc, bool & boost_enabled) const
  {
    if (decision.state == LongitudinalSafetyState::Clear) {
      return;
    }

    boost_enabled = false;
    if (std::isfinite(decision.speed_limit_mps)) {
      command[0] = std::min(command[0], decision.speed_limit_mps);
    }
    if (decision.state == LongitudinalSafetyState::EmergencyStop) {
      command[0] = 0.0;
      acc = -std::max(0.1, longitudinal_safety_config_.max_brake_decel_mps2);
      return;
    }
    if (actual_v > command[0] + 0.1) {
      acc = std::min(acc, -std::max(0.1, decision.decel_mps2));
    }
  }

  void update_lane_planner(const rclcpp::Time & stamp, const double actual_v)
  {
    if (!lane_planner_config_.enabled) {
      mpc_->update_selected_avoidance_line(lane_planner_config_.default_line, false);
      mpc_->update_planner_speed_limit(std::numeric_limits<double>::infinity(), false);
      active_planner_speed_limit_mps_ = std::numeric_limits<double>::infinity();
      active_front_block_distance_m_ = std::numeric_limits<double>::infinity();
      last_lane_candidates_.clear();
      return;
    }

    const int start_waypoint = model_->wp_id();
    last_lane_candidates_.clear();
    for (const auto line : {AvoidanceLine::Center, AvoidanceLine::LeftWall, AvoidanceLine::RightWall}) {
      last_lane_candidates_.push_back(build_lane_candidate(line, start_waypoint, actual_v));
    }

    const auto obstacles = active_obstacles(stamp.seconds());
    for (auto & candidate : last_lane_candidates_) {
      evaluate_candidate_collision(candidate, obstacles);
    }
    last_active_obstacles_ = obstacles;

    const AvoidanceLine selected = select_lane(last_lane_candidates_, stamp);
    mpc_->update_selected_avoidance_line(selected, true);
    const double speed_limit = front_speed_limit_for_selected_lane(last_lane_candidates_);
    mpc_->update_planner_speed_limit(speed_limit, std::isfinite(speed_limit));
    active_planner_speed_limit_mps_ = speed_limit;
    active_front_block_distance_m_ = front_block_distance_for_selected_lane(last_lane_candidates_);
  }

  void update_v2x(const V2XVehiclePositionArray & msg)
  {
    const double receive_stamp = now().seconds();
    for (const auto & vehicle : msg.vehicles) {
      double measurement_stamp = stamp_to_seconds(vehicle.header.stamp);
      if (measurement_stamp <= 0.0) {
        measurement_stamp = receive_stamp;
      }
      const double x = vehicle.position.x;
      const double y = vehicle.position.y;

      auto sample_it = v2x_samples_.find(vehicle.vehicle_id);
      double vx = 0.0;
      double vy = 0.0;
      if (sample_it != v2x_samples_.end()) {
        const auto & previous = sample_it->second;
        const double jump = std::hypot(x - previous.x, y - previous.y);
        const double dt = measurement_stamp - previous.stamp;
        if (jump <= lane_planner_config_.obstacle_jump_threshold_m && dt > 1.0e-3) {
          vx = (x - previous.x) / dt;
          vy = (y - previous.y) / dt;
          if (std::hypot(vx, vy) > lane_planner_config_.obstacle_v_max_mps) {
            vx = 0.0;
            vy = 0.0;
          }
        }
      }
      v2x_samples_[vehicle.vehicle_id] = V2XSample{measurement_stamp, x, y};
      v2x_obstacles_[vehicle.vehicle_id] = V2XObstacleState{vehicle.vehicle_id, receive_stamp, x, y, vx, vy};
    }
  }

  void setup_parameter_callback()
  {
    auto mpc_cfg = config_["mpc"];
    declare_parameter("v_max", mpc_cfg["v_max"].as<double>());
    declare_parameter("steering_tire_angle_gain_var", mpc_cfg["steering_tire_angle_gain_var"].as<double>());
    declare_parameter("Q0", mpc_cfg["Q"][0].as<double>());
    declare_parameter("Q1", mpc_cfg["Q"][1].as<double>());
    declare_parameter("Q2", mpc_cfg["Q"][2].as<double>());
    declare_parameter("R0", mpc_cfg["R"][0].as<double>());
    declare_parameter("R1", mpc_cfg["R"][1].as<double>());
    declare_parameter("QN0", mpc_cfg["QN"][0].as<double>());
    declare_parameter("QN1", mpc_cfg["QN"][1].as<double>());
    declare_parameter("QN2", mpc_cfg["QN"][2].as<double>());
    declare_parameter("ay_max", mpc_cfg["ay_max"].as<double>());
    declare_parameter("accel_low_pass_gain", mpc_cfg["accel_low_pass_gain"].as<double>());
    declare_parameter("steer_low_pass_gain", mpc_cfg["steer_low_pass_gain"].as<double>());
    declare_parameter("wp_id_offset", mpc_cfg["wp_id_offset"].as<int>());

    parameter_callback_handle_ = add_on_set_parameters_callback(
      [this](const std::vector<rclcpp::Parameter> & parameters) {
        rcl_interfaces::msg::SetParametersResult result;
        result.successful = true;
        for (const auto & parameter : parameters) {
          const auto & name = parameter.get_name();
          if (name == "v_max" && parameter.get_type() == rclcpp::ParameterType::PARAMETER_DOUBLE) {
            const double v_max_kmh = parameter.as_double();
            config_["mpc"]["v_max"] = v_max_kmh;
            const double v_max = kmh_to_mps(v_max_kmh);
            mpc_->update_v_max(v_max);
            reference_path_->set_v_ref(std::vector<double>(reference_path_->size(), v_max));
          } else if (
            name == "steering_tire_angle_gain_var" &&
            parameter.get_type() == rclcpp::ParameterType::PARAMETER_DOUBLE)
          {
            config_["mpc"]["steering_tire_angle_gain_var"] = parameter.as_double();
          } else if (name == "ay_max" && parameter.get_type() == rclcpp::ParameterType::PARAMETER_DOUBLE) {
            config_["mpc"]["ay_max"] = parameter.as_double();
            mpc_->update_ay_max(parameter.as_double());
          } else if (
            name == "accel_low_pass_gain" && parameter.get_type() == rclcpp::ParameterType::PARAMETER_DOUBLE)
          {
            config_["mpc"]["accel_low_pass_gain"] = parameter.as_double();
          } else if (
            name == "steer_low_pass_gain" && parameter.get_type() == rclcpp::ParameterType::PARAMETER_DOUBLE)
          {
            config_["mpc"]["steer_low_pass_gain"] = parameter.as_double();
          } else if (name == "wp_id_offset" && parameter.get_type() == rclcpp::ParameterType::PARAMETER_INTEGER) {
            config_["mpc"]["wp_id_offset"] = parameter.as_int();
            mpc_->update_wp_id_offset(static_cast<int>(parameter.as_int()));
          } else if (name.size() == 2 && name[0] == 'Q' && std::isdigit(name[1])) {
            const int index = name[1] - '0';
            config_["mpc"]["Q"][index] = parameter.as_double();
            mpc_->update_q(index, parameter.as_double());
          } else if (name.size() == 2 && name[0] == 'R' && std::isdigit(name[1])) {
            const int index = name[1] - '0';
            config_["mpc"]["R"][index] = parameter.as_double();
            mpc_->update_r(index, parameter.as_double());
          } else if (name.size() == 3 && name.substr(0, 2) == "QN" && std::isdigit(name[2])) {
            const int index = name[2] - '0';
            config_["mpc"]["QN"][index] = parameter.as_double();
            mpc_->update_qn(index, parameter.as_double());
          }
        }
        return result;
      });
  }

  void control()
  {
    if (!odom_) {
      return;
    }
    const auto current_time = now();
    const double dt = std::max(1.0e-3, (current_time - last_time_).seconds());
    last_time_ = current_time;

    const auto & pose = odom_->pose.pose;
    const double yaw = yaw_from_quaternion(pose.orientation);
    const double actual_v = odom_->twist.twist.linear.x;
    model_->update_states(pose.position.x, pose.position.y, yaw);
    update_lane_planner(current_time, actual_v);

    auto [u, max_delta] = mpc_->get_control();
    longitudinal_safety_decision_ =
      evaluate_longitudinal_safety(actual_v, active_front_block_distance_m_);
    if (std::isfinite(longitudinal_safety_decision_.speed_limit_mps)) {
      u[0] = std::min(u[0], longitudinal_safety_decision_.speed_limit_mps);
    }
    if (std::isfinite(active_planner_speed_limit_mps_)) {
      u[0] = std::min(u[0], active_planner_speed_limit_mps_);
    }

    if (!ref_vel_sections_.empty()) {
      const double ref_vel_mps = std::min(
        kmh_to_mps(get_ref_vel_kmh(ref_vel_sections_, model_->wp_id())),
        kmh_to_mps(config_["mpc"]["v_max"].as<double>()));
      mpc_->update_v_max(ref_vel_mps);
      reference_path_->set_v_ref(std::vector<double>(reference_path_->size(), ref_vel_mps));
    }

    if (!enable_control_) {
      const double last_v_cmd = last_u_[0];
      if (last_v_cmd < 0.5) {
        u[0] = 0.0;
      } else {
        u[0] = std::clamp(
          last_v_cmd + config_["mpc"]["a_min"].as<double>() * dt, 0.0,
          kmh_to_mps(config_["mpc"]["v_max"].as<double>()));
      }
    }

    double acc = 0.0;
    bool boost_enabled = false;
    if (use_boost_acceleration_) {
      const auto deg2rad = [](const double deg) { return deg * kPi / 180.0; };
      if (u[0] < actual_v - 0.2 || u[0] < 0.5) {
        boost_enabled = false;
        acc = std::clamp(
          kp_ * (u[0] - actual_v), config_["mpc"]["a_min"].as<double>(), config_["mpc"]["a_max"].as<double>());
      } else if (
        std::abs(actual_v) > kmh_to_mps(44.0) ||
        (std::abs(actual_v) > kmh_to_mps(38.0) && std::abs(max_delta) > deg2rad(12.0)))
      {
        boost_enabled = false;
        acc = config_["mpc"]["a_min"].as<double>() / 3.0 * 2.0;
      } else if (std::abs(actual_v) > kmh_to_mps(41.0) || std::abs(u[1]) > deg2rad(10.0)) {
        boost_enabled = false;
        acc = config_["mpc"]["a_max"].as<double>();
      } else {
        boost_enabled = true;
        acc = 500.0;
      }
    } else {
      acc = std::clamp(
        kp_ * (u[0] - actual_v), config_["mpc"]["a_min"].as<double>(), config_["mpc"]["a_max"].as<double>());
    }
    acc = last_acc_ + (acc - last_acc_) * config_["mpc"]["accel_low_pass_gain"].as<double>();
    apply_longitudinal_safety(longitudinal_safety_decision_, actual_v, u, acc, boost_enabled);
    u[1] = last_u_[1] + (u[1] - last_u_[1]) * config_["mpc"]["steer_low_pass_gain"].as<double>();

    last_acc_ = acc;
    last_u_ = u;
    model_->drive(actual_v, u[1]);
    publish_command(current_time, u, acc, boost_enabled);

    ++loop_;
    const int pred_period = std::max(1, static_cast<int>(config_["mpc"]["control_rate"].as<double>() / 4.0));
    if (loop_ % pred_period == 0) {
      publish_prediction_marker();
      publish_avoidance_markers(current_time);
    }
  }

  void publish_command(
    const rclcpp::Time & stamp, const std::array<double, 2> & u, const double acc, const bool boost_enabled)
  {
    AckermannControlCommand raw;
    raw.stamp = stamp;
    raw.lateral.stamp = stamp;
    raw.lateral.steering_tire_angle = u[1];
    raw.lateral.steering_tire_rotation_rate = 2.0;
    raw.longitudinal.stamp = stamp;
    raw.longitudinal.speed = u[0];
    raw.longitudinal.acceleration = acc;
    if (use_boost_acceleration_) {
      AckermannControlBoostCommand boost;
      boost.command = raw;
      boost.boost_mode = boost_enabled;
      boost_command_pub_->publish(boost);
      return;
    }

    command_raw_pub_->publish(raw);

    auto cmd = raw;
    cmd.lateral.steering_tire_angle *= config_["mpc"]["steering_tire_angle_gain_var"].as<double>();
    command_pub_->publish(cmd);
  }

  void publish_prediction_marker()
  {
    MarkerArray array;
    Path path;
    path.header.frame_id = "map";
    path.header.stamp = now();
    for (size_t i = 0; i < mpc_->prediction_x().size(); ++i) {
      Marker marker;
      marker.header.frame_id = "map";
      marker.header.stamp = path.header.stamp;
      marker.ns = "mpc_pred";
      marker.id = static_cast<int>(i);
      marker.type = Marker::SPHERE;
      marker.action = Marker::ADD;
      marker.pose.position.x = mpc_->prediction_x().at(i);
      marker.pose.position.y = mpc_->prediction_y().at(i);
      marker.pose.position.z = 0.0;
      marker.scale.x = 0.5;
      marker.scale.y = 0.5;
      marker.scale.z = 0.5;
      marker.color.r = 0.0;
      marker.color.g = 156.0 / 255.0;
      marker.color.b = 209.0 / 255.0;
      marker.color.a = 1.0;
      array.markers.push_back(marker);

      geometry_msgs::msg::PoseStamped pose;
      pose.header = path.header;
      pose.pose.position.x = mpc_->prediction_x().at(i);
      pose.pose.position.y = mpc_->prediction_y().at(i);
      pose.pose.position.z = 0.0;
      pose.pose.orientation.w = 1.0;
      path.poses.push_back(pose);
    }
    prediction_pub_->publish(array);
    prediction_path_pub_->publish(path);
    prediction_dummy_pub_->publish(array);
  }

  Marker make_delete_all_marker(const std::string & ns, const rclcpp::Time & stamp) const
  {
    Marker marker;
    marker.header.frame_id = "map";
    marker.header.stamp = stamp;
    marker.ns = ns;
    marker.id = 0;
    marker.action = Marker::DELETEALL;
    return marker;
  }

  void publish_avoidance_markers(const rclcpp::Time & stamp)
  {
    MarkerArray candidates_array;
    candidates_array.markers.push_back(make_delete_all_marker("avoidance_candidates", stamp));
    int marker_id = 1;
    for (const auto & candidate : last_lane_candidates_) {
      Marker marker;
      marker.header.frame_id = "map";
      marker.header.stamp = stamp;
      marker.ns = "avoidance_candidates";
      marker.id = marker_id++;
      marker.type = Marker::LINE_STRIP;
      marker.action = Marker::ADD;
      marker.scale.x = candidate.line == selected_lane_ ? 0.28 : 0.12;
      marker.color = color_for_line(candidate.line, candidate.blocked ? 0.25 : 0.9);
      marker.points = candidate.points;
      candidates_array.markers.push_back(marker);
    }
    avoidance_candidates_pub_->publish(candidates_array);

    MarkerArray selected_array;
    selected_array.markers.push_back(make_delete_all_marker("selected_avoidance_lane", stamp));
    const LaneCandidate * selected_candidate = find_candidate(last_lane_candidates_, selected_lane_);
    if (selected_candidate != nullptr) {
      Marker selected;
      selected.header.frame_id = "map";
      selected.header.stamp = stamp;
      selected.ns = "selected_avoidance_lane";
      selected.id = 1;
      selected.type = Marker::LINE_STRIP;
      selected.action = Marker::ADD;
      selected.scale.x = 0.42;
      selected.color.r = 1.0;
      selected.color.g = 0.0;
      selected.color.b = 1.0;
      selected.color.a = 0.95;
      selected.points = selected_candidate->points;
      selected_array.markers.push_back(selected);
    }
    selected_avoidance_pub_->publish(selected_array);

    MarkerArray debug_array;
    debug_array.markers.push_back(make_delete_all_marker("avoidance_debug", stamp));
    marker_id = 1;
    for (const auto & candidate : last_lane_candidates_) {
      if (candidate.points.empty()) {
        continue;
      }
      Marker text;
      text.header.frame_id = "map";
      text.header.stamp = stamp;
      text.ns = "avoidance_debug";
      text.id = marker_id++;
      text.type = Marker::TEXT_VIEW_FACING;
      text.action = Marker::ADD;
      text.pose.position = candidate.points.at(candidate.points.size() / 2);
      text.pose.position.z = 1.2;
      text.scale.z = 0.7;
      text.color = color_for_line(candidate.line, 1.0);
      const std::string distance_text = std::isfinite(candidate.min_obstacle_distance)
                                          ? std::to_string(candidate.min_obstacle_distance).substr(0, 4) + "m"
                                          : "inf";
      const std::string front_text = std::isfinite(candidate.front_block_distance_m)
                                       ? " front " +
                                           std::to_string(candidate.front_block_distance_m).substr(0, 4) + "m"
                                       : "";
      text.text = avoidance_line_to_string(candidate.line) + "\n" +
                  (candidate.blocked ? "blocked" : "safe") + " " + distance_text + front_text;
      debug_array.markers.push_back(text);
    }

    if (selected_candidate != nullptr && !selected_candidate->points.empty()) {
      Marker text;
      text.header.frame_id = "map";
      text.header.stamp = stamp;
      text.ns = "avoidance_debug";
      text.id = marker_id++;
      text.type = Marker::TEXT_VIEW_FACING;
      text.action = Marker::ADD;
      text.pose.position = selected_candidate->points.front();
      text.pose.position.z = 1.8;
      text.scale.z = 0.8;
      text.color.r = 1.0;
      text.color.g = 0.0;
      text.color.b = 1.0;
      text.color.a = 1.0;
      const double selected_speed_limit = front_speed_limit_for_selected_lane(last_lane_candidates_);
      const std::string speed_text = std::isfinite(selected_speed_limit)
                                       ? "\nspeed_limit: " +
                                           std::to_string(selected_speed_limit * 3.6).substr(0, 4) + "km/h"
                                       : "";
      const std::string safety_text =
        "\nsafety: " + longitudinal_safety_state_to_string(longitudinal_safety_decision_.state) +
        "\nfront: " + (std::isfinite(longitudinal_safety_decision_.front_distance_m)
                         ? std::to_string(longitudinal_safety_decision_.front_distance_m).substr(0, 4) + "m"
                         : "inf") +
        "\nbrake_d: " + (std::isfinite(longitudinal_safety_decision_.brake_distance_m)
                           ? std::to_string(longitudinal_safety_decision_.brake_distance_m).substr(0, 4) + "m"
                           : "inf");
      text.text = "selected: " + avoidance_line_to_string(selected_lane_) + "\nreason: " + lane_selection_reason_ +
                  speed_text + safety_text;
      debug_array.markers.push_back(text);
    }

    for (const auto & obstacle : last_active_obstacles_) {
      Marker sphere;
      sphere.header.frame_id = "map";
      sphere.header.stamp = stamp;
      sphere.ns = "avoidance_debug";
      sphere.id = marker_id++;
      sphere.type = Marker::SPHERE;
      sphere.action = Marker::ADD;
      sphere.pose.position.x = obstacle.x;
      sphere.pose.position.y = obstacle.y;
      sphere.pose.position.z = 0.8;
      sphere.scale.x = lane_planner_config_.obstacle_radius_m * 2.0;
      sphere.scale.y = lane_planner_config_.obstacle_radius_m * 2.0;
      sphere.scale.z = 0.4;
      sphere.color.r = 1.0;
      sphere.color.g = 0.2;
      sphere.color.b = 0.0;
      sphere.color.a = 0.7;
      debug_array.markers.push_back(sphere);
    }
    avoidance_debug_pub_->publish(debug_array);
  }

  void publish_ref_path_marker()
  {
    MarkerArray array;
    for (int i = 0; i + 1 < reference_path_->size(); ++i) {
      const auto & start_wp = reference_path_->get_waypoint(i);
      const auto & end_wp = reference_path_->get_waypoint(i + 1);
      Marker marker;
      marker.header.frame_id = "map";
      marker.header.stamp = now();
      marker.ns = "ref_path";
      marker.id = i;
      marker.type = Marker::LINE_STRIP;
      marker.action = Marker::ADD;
      marker.scale.x = 0.2;
      marker.color.b = 1.0;
      marker.color.a = 0.7;
      geometry_msgs::msg::Point start;
      start.x = start_wp.x;
      start.y = start_wp.y;
      geometry_msgs::msg::Point end;
      end.x = end_wp.x;
      end.y = end_wp.y;
      marker.points.push_back(start);
      marker.points.push_back(end);
      array.markers.push_back(marker);
    }
    ref_path_pub_->publish(array);
    ref_path_dummy_pub_->publish(array);
  }

  std::string package_path_;
  std::string config_path_;
  std::string ref_vel_config_path_;
  YAML::Node config_;
  LanePlannerConfig lane_planner_config_;
  LongitudinalSafetyConfig longitudinal_safety_config_;
  std::unique_ptr<OccupancyMap> occupancy_map_;
  std::unique_ptr<ReferencePath> reference_path_;
  std::unique_ptr<BicycleModel> model_;
  std::unique_ptr<MpcSolver> mpc_;
  std::vector<RefVelSection> ref_vel_sections_;
  std::unordered_map<std::string, V2XSample> v2x_samples_;
  std::unordered_map<std::string, V2XObstacleState> v2x_obstacles_;
  std::vector<LaneCandidate> last_lane_candidates_;
  std::vector<V2XObstacleState> last_active_obstacles_;
  nav_msgs::msg::Odometry::SharedPtr odom_;
  bool use_boost_acceleration_{};
  bool enable_control_{true};
  AvoidanceLine selected_lane_{AvoidanceLine::Center};
  bool has_lane_selection_{};
  double active_planner_speed_limit_mps_{std::numeric_limits<double>::infinity()};
  double active_front_block_distance_m_{std::numeric_limits<double>::infinity()};
  LongitudinalSafetyDecision longitudinal_safety_decision_;
  double selected_lane_since_sec_{};
  double last_lane_switch_sec_{};
  std::string lane_selection_reason_{"disabled"};
  double last_acc_{};
  std::array<double, 2> last_u_{0.0, 0.0};
  rclcpp::Time last_time_;
  int loop_{};
  int current_laps_{};
  double last_lap_time_{};
  int last_condition_{};
  const double kp_{100.0};

  rclcpp::Publisher<AckermannControlCommand>::SharedPtr command_pub_;
  rclcpp::Publisher<AckermannControlCommand>::SharedPtr command_raw_pub_;
  rclcpp::Publisher<AckermannControlBoostCommand>::SharedPtr boost_command_pub_;
  rclcpp::Publisher<MarkerArray>::SharedPtr prediction_pub_;
  rclcpp::Publisher<Path>::SharedPtr prediction_path_pub_;
  rclcpp::Publisher<MarkerArray>::SharedPtr prediction_dummy_pub_;
  rclcpp::Publisher<MarkerArray>::SharedPtr ref_path_pub_;
  rclcpp::Publisher<MarkerArray>::SharedPtr ref_path_dummy_pub_;
  rclcpp::Publisher<MarkerArray>::SharedPtr avoidance_candidates_pub_;
  rclcpp::Publisher<MarkerArray>::SharedPtr selected_avoidance_pub_;
  rclcpp::Publisher<MarkerArray>::SharedPtr avoidance_debug_pub_;
  rclcpp::Subscription<nav_msgs::msg::Odometry>::SharedPtr odom_sub_;
  rclcpp::Subscription<V2XVehiclePositionArray>::SharedPtr v2x_sub_;
  rclcpp::Subscription<std_msgs::msg::Bool>::SharedPtr control_mode_sub_;
  rclcpp::Subscription<std_msgs::msg::Empty>::SharedPtr stop_request_sub_;
  rclcpp::Subscription<std_msgs::msg::Float32MultiArray>::SharedPtr awsim_status_sub_;
  rclcpp::Subscription<std_msgs::msg::Int32>::SharedPtr condition_sub_;
  rclcpp::node_interfaces::OnSetParametersCallbackHandle::SharedPtr parameter_callback_handle_;
  rclcpp::TimerBase::SharedPtr timer_;
};

}  // namespace

int main(int argc, char ** argv)
{
  int dump_wp_id = 0;
  int sequence_steps = 20;
  double safety_speed_kmh = 20.0;
  double safety_distance_m = std::numeric_limits<double>::infinity();
  for (int i = 1; i + 1 < argc; ++i) {
    if (std::string(argv[i]) == "--dump_wp_id") {
      dump_wp_id = std::stoi(argv[i + 1]);
    }
    if (std::string(argv[i]) == "--sequence_steps") {
      sequence_steps = std::stoi(argv[i + 1]);
    }
    if (std::string(argv[i]) == "--safety_speed_kmh") {
      safety_speed_kmh = std::stod(argv[i + 1]);
    }
    if (std::string(argv[i]) == "--safety_distance_m") {
      safety_distance_m = std::stod(argv[i + 1]);
    }
  }
  for (int i = 1; i + 1 < argc; ++i) {
    if (std::string(argv[i]) == "--dump_qp") {
      return run_qp_dump(argv[i + 1], dump_wp_id);
    }
    if (std::string(argv[i]) == "--dump_sequence") {
      return run_sequence_dump(argv[i + 1], dump_wp_id, sequence_steps);
    }
    if (std::string(argv[i]) == "--benchmark_sequence") {
      return run_sequence_benchmark(dump_wp_id, sequence_steps);
    }
  }
  for (int i = 1; i < argc; ++i) {
    if (std::string(argv[i]) == "--dump_safety") {
      return run_safety_dump(safety_speed_kmh, safety_distance_m);
    }
  }
  rclcpp::init(argc, argv);
  rclcpp::spin(std::make_shared<MpcControllerCpp>());
  rclcpp::shutdown();
  return 0;
}
