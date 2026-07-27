// Keyboard -> sensor_msgs/Joy bridge (evdev / OS-level key events).
//
// Publishes /joy so teleop_manager_node can be driven from a keyboard instead
// of a joystick. Axis/button indices are read from the SAME parameters as
// teleop_manager (teleop.param.yaml uses the `/**:` wildcard), so they stay in
// sync. The MANUAL-mode button (joy_button_index) is held down at all times.
//
// Input is read from a Linux input device (/dev/input/event*): real OS-level
// key press/release (EV_KEY), so simultaneous holds work and no TTY/X11 is
// needed (only the `input` group). Pick the device with the `device_path`
// parameter, or leave it empty to auto-detect the first device that reports the
// W/A/S/D keys.
//
// Key bindings:
//   w / s : accelerate forward / reverse   (held -> speed axis +1 / -1)
//   a / d : steer left / right             (held -> steer axis +1 / -1)
//   1 / 2 : gear DRIVE / REVERSE            (pulse on press)
//   b     : turbo boost                     (pulse on press)

#include <dirent.h>
#include <fcntl.h>
#include <linux/input.h>
#include <sys/ioctl.h>
#include <unistd.h>

#include <algorithm>
#include <chrono>
#include <cstring>
#include <memory>
#include <string>
#include <vector>

#include "rclcpp/rclcpp.hpp"
#include "sensor_msgs/msg/joy.hpp"

namespace
{
bool key_supported(int fd, int code)
{
  unsigned long bits[(KEY_MAX / (8 * sizeof(unsigned long))) + 1];
  std::memset(bits, 0, sizeof(bits));
  if (ioctl(fd, EVIOCGBIT(EV_KEY, sizeof(bits)), bits) < 0) return false;
  return (bits[code / (8 * sizeof(unsigned long))] >>
          (code % (8 * sizeof(unsigned long)))) & 1UL;
}
}  // namespace

class KeyboardToJoyNode : public rclcpp::Node
{
public:
  KeyboardToJoyNode()
  : Node("keyboard_to_joy_node")
  {
    // Shared with teleop_manager via teleop.param.yaml (`/**:`).
    speed_axis_index_     = declare_parameter<int>("speed_axis_index", 1);
    steer_axis_index_     = declare_parameter<int>("steer_axis_index", 0);
    joy_button_index_     = declare_parameter<int>("joy_button_index", 2);
    drive_button_index_   = declare_parameter<int>("drive_button_index", 5);
    reverse_button_index_ = declare_parameter<int>("reverse_button_index", 4);
    boost_button_index_   = declare_parameter<int>("boost_button_index", 8);

    publish_hz_  = declare_parameter<double>("publish_hz", 50.0);
    device_path_ = declare_parameter<std::string>("device_path", "");

    n_axes_ = std::max(speed_axis_index_, steer_axis_index_) + 1;
    n_buttons_ = std::max({joy_button_index_, drive_button_index_,
                           reverse_button_index_, boost_button_index_}) + 1;

    joy_pub_ = create_publisher<sensor_msgs::msg::Joy>("/joy", 10);

    open_device();

    timer_ = create_wall_timer(
      std::chrono::duration<double>(1.0 / publish_hz_),
      std::bind(&KeyboardToJoyNode::tick, this));

    RCLCPP_INFO(get_logger(),
      "keyboard_to_joy started (manual mode always held). "
      "Keys: w/s accel, a/d steer, 1/2 gear D/R, b boost.");
  }

  ~KeyboardToJoyNode() override
  {
    if (fd_ >= 0) close(fd_);
  }

private:
  void open_device()
  {
    if (!device_path_.empty()) {
      fd_ = open(device_path_.c_str(), O_RDONLY | O_NONBLOCK);
      if (fd_ < 0) {
        RCLCPP_ERROR(get_logger(), "Failed to open device_path '%s'; keyboard disabled.",
                     device_path_.c_str());
      } else {
        RCLCPP_INFO(get_logger(), "Using keyboard device: %s", device_path_.c_str());
      }
      return;
    }

    // Auto-detect: first /dev/input/event* that reports W/A/S/D keys.
    DIR * dir = opendir("/dev/input");
    if (!dir) {
      RCLCPP_ERROR(get_logger(), "Cannot open /dev/input; keyboard disabled.");
      return;
    }
    struct dirent * ent;
    while ((ent = readdir(dir)) != nullptr) {
      if (std::strncmp(ent->d_name, "event", 5) != 0) continue;
      const std::string path = std::string("/dev/input/") + ent->d_name;
      int fd = open(path.c_str(), O_RDONLY | O_NONBLOCK);
      if (fd < 0) continue;
      if (key_supported(fd, KEY_W) && key_supported(fd, KEY_A) &&
          key_supported(fd, KEY_S) && key_supported(fd, KEY_D)) {
        fd_ = fd;
        RCLCPP_INFO(get_logger(), "Auto-detected keyboard device: %s", path.c_str());
        break;
      }
      close(fd);
    }
    closedir(dir);
    if (fd_ < 0) {
      RCLCPP_ERROR(get_logger(),
        "No keyboard device found under /dev/input; set the 'device_path' param.");
    }
  }

  void tick()
  {
    read_events();

    sensor_msgs::msg::Joy joy;
    joy.header.stamp = now();
    joy.axes.assign(n_axes_, 0.0f);
    joy.buttons.assign(n_buttons_, 0);

    // Held W/A/S/D map straight to axis values (opposite keys cancel).
    joy.axes[speed_axis_index_] = (w_ ? 1.0f : 0.0f) + (s_ ? -1.0f : 0.0f);
    joy.axes[steer_axis_index_] = (a_ ? 1.0f : 0.0f) + (d_ ? -1.0f : 0.0f);

    // Manual mode is always engaged.
    joy.buttons[joy_button_index_] = 1;

    // One-shot gear/boost pulses asserted for a single message.
    for (int idx : pulse_buttons_) {
      if (idx >= 0 && idx < n_buttons_) joy.buttons[idx] = 1;
    }
    pulse_buttons_.clear();

    joy_pub_->publish(joy);
  }

  void read_events()
  {
    if (fd_ < 0) return;
    input_event ev;
    while (read(fd_, &ev, sizeof(ev)) == sizeof(ev)) {
      if (ev.type != EV_KEY) continue;
      const bool down = (ev.value != 0);  // 1=press, 2=repeat
      switch (ev.code) {
        case KEY_W: w_ = down; break;
        case KEY_S: s_ = down; break;
        case KEY_A: a_ = down; break;
        case KEY_D: d_ = down; break;
        case KEY_1: if (ev.value == 1) pulse_buttons_.push_back(drive_button_index_); break;
        case KEY_2: if (ev.value == 1) pulse_buttons_.push_back(reverse_button_index_); break;
        case KEY_B: if (ev.value == 1) pulse_buttons_.push_back(boost_button_index_); break;
        default: break;
      }
    }
  }

  // Params (shared with teleop_manager)
  int speed_axis_index_, steer_axis_index_;
  int joy_button_index_, drive_button_index_, reverse_button_index_, boost_button_index_;
  double publish_hz_;
  std::string device_path_;
  int n_axes_, n_buttons_;

  // State
  bool w_{false}, a_{false}, s_{false}, d_{false};
  std::vector<int> pulse_buttons_;

  rclcpp::Publisher<sensor_msgs::msg::Joy>::SharedPtr joy_pub_;
  rclcpp::TimerBase::SharedPtr timer_;
  int fd_{-1};
};

int main(int argc, char * argv[])
{
  rclcpp::init(argc, argv);
  rclcpp::spin(std::make_shared<KeyboardToJoyNode>());
  rclcpp::shutdown();
  return 0;
}
