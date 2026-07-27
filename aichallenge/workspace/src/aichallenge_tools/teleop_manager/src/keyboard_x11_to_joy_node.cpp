// Keyboard -> sensor_msgs/Joy bridge (X11 / display-server key state).
//
// Same role as keyboard_to_joy_node, but reads key state from the X server via
// XQueryKeymap instead of /dev/input/event*. Use this over remote desktop
// (VNC/RDP/xrdp), where keystrokes are injected into the X server and never
// reach the kernel evdev layer. Requires DISPLAY + X authority; needs no TTY
// and no `input` group.
//
// Axis/button indices come from the SAME parameters as teleop_manager
// (teleop.param.yaml `/**:`). The MANUAL-mode button is held at all times.
//
// Key bindings:
//   w / s : accelerate forward / reverse   (held -> speed axis +1 / -1)
//   a / d : steer left / right             (held -> steer axis +1 / -1)
//   1 / 2 : gear DRIVE / REVERSE            (pulse on press)
//   b     : turbo boost                     (pulse on press)

#include <algorithm>
#include <chrono>
#include <memory>
#include <vector>

#include "rclcpp/rclcpp.hpp"
#include "sensor_msgs/msg/joy.hpp"

// X11 headers define macros (None, Status, Bool, ...) that corrupt the parsing
// of ROS headers, so include them LAST, after rclcpp/sensor_msgs are parsed.
#include <X11/Xlib.h>
#include <X11/keysym.h>

class KeyboardX11ToJoyNode : public rclcpp::Node
{
public:
  KeyboardX11ToJoyNode()
  : Node("keyboard_x11_to_joy_node")
  {
    // Shared with teleop_manager via teleop.param.yaml (`/**:`).
    speed_axis_index_     = declare_parameter<int>("speed_axis_index", 1);
    steer_axis_index_     = declare_parameter<int>("steer_axis_index", 0);
    joy_button_index_     = declare_parameter<int>("joy_button_index", 2);
    drive_button_index_   = declare_parameter<int>("drive_button_index", 5);
    reverse_button_index_ = declare_parameter<int>("reverse_button_index", 4);
    boost_button_index_   = declare_parameter<int>("boost_button_index", 8);

    publish_hz_ = declare_parameter<double>("publish_hz", 50.0);

    n_axes_ = std::max(speed_axis_index_, steer_axis_index_) + 1;
    n_buttons_ = std::max({joy_button_index_, drive_button_index_,
                           reverse_button_index_, boost_button_index_}) + 1;

    joy_pub_ = create_publisher<sensor_msgs::msg::Joy>("/joy", 10);

    display_ = XOpenDisplay(nullptr);
    if (!display_) {
      RCLCPP_ERROR(get_logger(),
        "Cannot open X display (is DISPLAY/XAUTHORITY set?); keyboard disabled.");
    } else {
      kc_w_ = XKeysymToKeycode(display_, XK_w);
      kc_s_ = XKeysymToKeycode(display_, XK_s);
      kc_a_ = XKeysymToKeycode(display_, XK_a);
      kc_d_ = XKeysymToKeycode(display_, XK_d);
      kc_1_ = XKeysymToKeycode(display_, XK_1);
      kc_2_ = XKeysymToKeycode(display_, XK_2);
      kc_b_ = XKeysymToKeycode(display_, XK_b);
      RCLCPP_INFO(get_logger(),
        "keyboard_x11_to_joy started on display '%s'. "
        "Keys: w/s accel, a/d steer, 1/2 gear D/R, b boost.",
        XDisplayString(display_));
    }

    timer_ = create_wall_timer(
      std::chrono::duration<double>(1.0 / publish_hz_),
      std::bind(&KeyboardX11ToJoyNode::tick, this));
  }

  ~KeyboardX11ToJoyNode() override
  {
    if (display_) XCloseDisplay(display_);
  }

private:
  static bool pressed(const char keys[32], KeyCode kc)
  {
    return kc != 0 && (keys[kc / 8] & (1 << (kc % 8)));
  }

  void tick()
  {
    sensor_msgs::msg::Joy joy;
    joy.header.stamp = now();
    joy.axes.assign(n_axes_, 0.0f);
    joy.buttons.assign(n_buttons_, 0);
    joy.buttons[joy_button_index_] = 1;  // manual mode always engaged

    if (display_) {
      char keys[32];
      XQueryKeymap(display_, keys);

      const bool w = pressed(keys, kc_w_);
      const bool s = pressed(keys, kc_s_);
      const bool a = pressed(keys, kc_a_);
      const bool d = pressed(keys, kc_d_);
      joy.axes[speed_axis_index_] = (w ? 1.0f : 0.0f) + (s ? -1.0f : 0.0f);
      joy.axes[steer_axis_index_] = (a ? 1.0f : 0.0f) + (d ? -1.0f : 0.0f);

      // Gear/boost pulse once on the press edge.
      pulse_on_edge(pressed(keys, kc_1_), prev_1_, drive_button_index_, joy);
      pulse_on_edge(pressed(keys, kc_2_), prev_2_, reverse_button_index_, joy);
      pulse_on_edge(pressed(keys, kc_b_), prev_b_, boost_button_index_, joy);
    }

    joy_pub_->publish(joy);
  }

  void pulse_on_edge(bool now_pressed, bool & prev, int button_index,
                     sensor_msgs::msg::Joy & joy)
  {
    if (now_pressed && !prev && button_index >= 0 && button_index < n_buttons_) {
      joy.buttons[button_index] = 1;
    }
    prev = now_pressed;
  }

  // Params (shared with teleop_manager)
  int speed_axis_index_, steer_axis_index_;
  int joy_button_index_, drive_button_index_, reverse_button_index_, boost_button_index_;
  double publish_hz_;
  int n_axes_, n_buttons_;

  // X11
  Display * display_{nullptr};
  KeyCode kc_w_{0}, kc_s_{0}, kc_a_{0}, kc_d_{0}, kc_1_{0}, kc_2_{0}, kc_b_{0};
  bool prev_1_{false}, prev_2_{false}, prev_b_{false};

  rclcpp::Publisher<sensor_msgs::msg::Joy>::SharedPtr joy_pub_;
  rclcpp::TimerBase::SharedPtr timer_;
};

int main(int argc, char * argv[])
{
  rclcpp::init(argc, argv);
  rclcpp::spin(std::make_shared<KeyboardX11ToJoyNode>());
  rclcpp::shutdown();
  return 0;
}
