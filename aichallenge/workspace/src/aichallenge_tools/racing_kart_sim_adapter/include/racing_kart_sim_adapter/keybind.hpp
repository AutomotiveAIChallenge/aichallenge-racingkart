#ifndef RACING_KART_SIM_ADAPTER__KEYBIND_HPP_
#define RACING_KART_SIM_ADAPTER__KEYBIND_HPP_

#include <cstddef>

// Duplicated from racing_kart_interface's src/racing_kart_driver/src/keybind/{joystick,mapping}.hpp
// (separate repo, not a build dependency here). Keep in sync by hand if the real driver changes.
namespace racing_kart_sim_adapter::Keybind::Joystick
{

constexpr size_t NumButtons = 11;
constexpr size_t NumAxes = 8;

constexpr int LeftStickHorizontal = 0;
constexpr int LeftStickVertical = 1;
constexpr int LeftTrigger = 2;
constexpr int RightStickHorizontal = 3;
constexpr int RightStickVertical = 4;
constexpr int RightTrigger = 5;
constexpr int DpadHorizontal = 6;
constexpr int DpadVertical = 7;

constexpr int ButtonA = 0;
constexpr int ButtonB = 1;
constexpr int ButtonX = 2;
constexpr int ButtonY = 3;
constexpr int ButtonLB = 4;
constexpr int ButtonRB = 5;
constexpr int ButtonStart = 6;
constexpr int ButtonBack = 7;
constexpr int ButtonLeftStick = 9;
constexpr int ButtonRightStick = 10;

}  // namespace racing_kart_sim_adapter::Keybind::Joystick

namespace racing_kart_sim_adapter::Keybind
{

// Note: Accel/Brake should be [+1.0 to -1.0], neutral (untouched trigger) is +1.0.
constexpr int Accel = Joystick::RightTrigger;
constexpr int Brake = Joystick::LeftTrigger;
constexpr int Steer = Joystick::LeftStickHorizontal;

}  // namespace racing_kart_sim_adapter::Keybind

#endif  // RACING_KART_SIM_ADAPTER__KEYBIND_HPP_
