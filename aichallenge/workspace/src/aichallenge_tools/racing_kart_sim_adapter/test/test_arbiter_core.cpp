#include "racing_kart_sim_adapter/arbiter_core.hpp"
#include "racing_kart_sim_adapter/keybind.hpp"

#include <autoware_auto_vehicle_msgs/msg/gear_command.hpp>

#include <gtest/gtest.h>

namespace racing_kart_sim_adapter
{
namespace
{

using GearCommand = autoware_auto_vehicle_msgs::msg::GearCommand;
namespace Joy = Keybind::Joystick;

// 11 buttons all released, 8 axes at rest (triggers at their untouched +1.0, per
// racing_kart_driver_node.cpp: accel/brake ratio is clamp((1-axis)/2, 0, 1), so +1.0 -> 0.0).
JoyFrame NeutralJoy()
{
  JoyFrame joy;
  joy.buttons.assign(Joy::NumButtons, 0);
  joy.axes.assign(Joy::NumAxes, 0.0f);
  joy.axes[Keybind::Accel] = 1.0f;
  joy.axes[Keybind::Brake] = 1.0f;
  return joy;
}

class ArbiterCoreTest : public ::testing::Test
{
protected:
  ArbiterCore core_{5.0};
  double t_ = 0.0;

  ArbiterOutput Tick(const JoyFrame & joy, const AutowareCommand & autoware_cmd = {})
  {
    core_.on_joy(joy, t_);
    return core_.step(t_, autoware_cmd);
  }

  // Neutral joy with the emergency-clear chord held; clears is_emergency_ but keeps the vehicle
  // in kManual for this same tick (mode is decided before the clear check runs).
  ArbiterOutput Unlock()
  {
    JoyFrame joy = NeutralJoy();
    joy.buttons[Joy::ButtonLeftStick] = 1;
    joy.buttons[Joy::ButtonRightStick] = 1;
    return Tick(joy);
  }
};

TEST_F(ArbiterCoreTest, InitialState_IsEmergencyAndStopped)
{
  const auto out = core_.step(t_, {});
  EXPECT_TRUE(out.is_emergency);
  EXPECT_FALSE(out.joystick_available);
  EXPECT_EQ(out.mode, ControlMode::kManual);
  EXPECT_FLOAT_EQ(out.accel_ratio, 0.0f);
  EXPECT_FLOAT_EQ(out.brake_ratio, 1.0f);
  EXPECT_EQ(out.gear, 'N');
}

TEST_F(ArbiterCoreTest, UndersizedJoy_StaysUnavailableAndStopped)
{
  JoyFrame joy = NeutralJoy();
  joy.buttons.pop_back();  // 10 buttons instead of 11

  const auto out = Tick(joy);
  EXPECT_FALSE(out.joystick_available);
  EXPECT_TRUE(out.is_emergency);
  EXPECT_FLOAT_EQ(out.brake_ratio, 1.0f);
}

TEST_F(ArbiterCoreTest, ValidNeutralJoy_StaysEmergencyUntilChordPressed)
{
  const auto out = Tick(NeutralJoy());
  EXPECT_TRUE(out.joystick_available);
  EXPECT_TRUE(out.is_emergency);  // default is_emergency_ = true, never cleared yet
  EXPECT_EQ(out.mode, ControlMode::kManual);
  EXPECT_FLOAT_EQ(out.brake_ratio, 1.0f);
}

TEST_F(ArbiterCoreTest, EmergencyChord_ClearsAndDrivesOnTheSameTick)
{
  JoyFrame joy = NeutralJoy();
  joy.buttons[Joy::ButtonLeftStick] = 1;
  joy.buttons[Joy::ButtonRightStick] = 1;
  joy.axes[Keybind::Accel] = -1.0f;  // full throttle
  joy.axes[Keybind::Steer] = 0.5f;

  const auto out = Tick(joy);
  EXPECT_FALSE(out.is_emergency);
  EXPECT_EQ(out.mode, ControlMode::kManual);  // mode was decided before the clear check
  EXPECT_FLOAT_EQ(out.accel_ratio, 1.0f);
  EXPECT_FLOAT_EQ(out.steer_ratio, 0.5f);
}

TEST_F(ArbiterCoreTest, EmergencyChord_WithSimultaneousEmergencyButton_StillDrives)
{
  // LB latches is_emergency_ this tick, but the unconditional LSB+RSB check clears it again
  // before the stop/drive decision -- faithfully reproducing the upstream ordering.
  JoyFrame joy = NeutralJoy();
  joy.buttons[Joy::ButtonLeftStick] = 1;
  joy.buttons[Joy::ButtonRightStick] = 1;
  joy.buttons[Joy::ButtonLB] = 1;

  const auto out = Tick(joy);
  EXPECT_FALSE(out.is_emergency);
  EXPECT_EQ(out.mode, ControlMode::kManual);
}

class ArbiterCoreUnlockedTest : public ArbiterCoreTest
{
protected:
  void SetUp() override { Unlock(); }
};

TEST_F(ArbiterCoreUnlockedTest, ModeButtons_SwitchMode)
{
  JoyFrame joy = NeutralJoy();
  joy.buttons[Joy::ButtonY] = 1;
  EXPECT_EQ(Tick(joy).mode, ControlMode::kAutonomous);

  joy = NeutralJoy();
  joy.buttons[Joy::ButtonX] = 1;
  EXPECT_EQ(Tick(joy).mode, ControlMode::kAutonomousSteerOnly);

  joy = NeutralJoy();
  joy.buttons[Joy::ButtonA] = 1;
  EXPECT_EQ(Tick(joy).mode, ControlMode::kManual);
}

TEST_F(ArbiterCoreUnlockedTest, NoModeButton_KeepsPreviousMode)
{
  JoyFrame joy = NeutralJoy();
  joy.buttons[Joy::ButtonY] = 1;
  ASSERT_EQ(Tick(joy).mode, ControlMode::kAutonomous);

  const auto out = Tick(NeutralJoy());  // no button pressed
  EXPECT_EQ(out.mode, ControlMode::kAutonomous);
}

TEST(ArbiterCoreEmergencyButtonTest, EachEmergencyButton_LatchesAndForcesManual)
{
  for (const int button : {Joy::ButtonLB, Joy::ButtonRB, Joy::ButtonStart, Joy::ButtonBack}) {
    // Fresh core per button: unlock, go AUTONOMOUS, then press the emergency button under test.
    ArbiterCore core{5.0};
    double t = 0.0;
    auto tick = [&](const JoyFrame & joy) {
      core.on_joy(joy, t);
      return core.step(t, {});
    };

    JoyFrame chord = NeutralJoy();
    chord.buttons[Joy::ButtonLeftStick] = 1;
    chord.buttons[Joy::ButtonRightStick] = 1;
    tick(chord);

    JoyFrame autonomous = NeutralJoy();
    autonomous.buttons[Joy::ButtonY] = 1;
    ASSERT_EQ(tick(autonomous).mode, ControlMode::kAutonomous) << "button index " << button;

    JoyFrame joy = NeutralJoy();
    joy.buttons[button] = 1;
    const auto out = tick(joy);
    EXPECT_TRUE(out.is_emergency) << "button index " << button;
    EXPECT_EQ(out.mode, ControlMode::kManual) << "button index " << button;
    EXPECT_FLOAT_EQ(out.brake_ratio, 1.0f) << "button index " << button;

    // Latched: stays emergency on the next neutral tick too.
    EXPECT_TRUE(tick(NeutralJoy()).is_emergency) << "button index " << button;
  }
}

TEST_F(ArbiterCoreUnlockedTest, AccelAxis_NeutralIsZero_FullDeflectionIsOne)
{
  JoyFrame joy = NeutralJoy();
  EXPECT_FLOAT_EQ(Tick(joy).accel_ratio, 0.0f);

  joy.axes[Keybind::Accel] = -1.0f;
  EXPECT_FLOAT_EQ(Tick(joy).accel_ratio, 1.0f);
}

TEST_F(ArbiterCoreUnlockedTest, BrakeAxis_NeutralIsZero_FullDeflectionIsOne)
{
  JoyFrame joy = NeutralJoy();
  EXPECT_FLOAT_EQ(Tick(joy).brake_ratio, 0.0f);

  joy.axes[Keybind::Brake] = -1.0f;
  EXPECT_FLOAT_EQ(Tick(joy).brake_ratio, 1.0f);
}

TEST_F(ArbiterCoreUnlockedTest, SteerAxis_ManualClampsToPoint9)
{
  JoyFrame joy = NeutralJoy();
  joy.axes[Keybind::Steer] = 1.0f;
  EXPECT_FLOAT_EQ(Tick(joy).steer_ratio, 0.9f);

  joy.axes[Keybind::Steer] = -1.0f;
  EXPECT_FLOAT_EQ(Tick(joy).steer_ratio, -0.9f);
}

TEST_F(ArbiterCoreUnlockedTest, GearDpad_ManualModeSelectsGear)
{
  JoyFrame drive = NeutralJoy();
  drive.axes[Joy::DpadVertical] = 1.0f;
  EXPECT_EQ(Tick(drive).gear, 'D');

  JoyFrame reverse = NeutralJoy();
  reverse.axes[Joy::DpadVertical] = -1.0f;
  EXPECT_EQ(Tick(reverse).gear, 'R');

  JoyFrame neutral_gear = NeutralJoy();
  neutral_gear.axes[Joy::DpadHorizontal] = 1.0f;
  EXPECT_EQ(Tick(neutral_gear).gear, 'N');

  // No dpad input: gear request is unchanged from the last one ('N' above).
  EXPECT_EQ(Tick(NeutralJoy()).gear, 'N');
}

TEST_F(ArbiterCoreUnlockedTest, AutonomousMode_UsesAutowareAccelBrakeAndSteer_IgnoresJoystick)
{
  JoyFrame autonomous = NeutralJoy();
  autonomous.buttons[Joy::ButtonY] = 1;
  ASSERT_EQ(Tick(autonomous).mode, ControlMode::kAutonomous);

  JoyFrame joy = NeutralJoy();
  joy.axes[Keybind::Accel] = -1.0f;  // would be full throttle in manual; must be ignored
  joy.axes[Keybind::Steer] = -1.0f;

  AutowareCommand cmd;
  cmd.available = true;
  cmd.accel_ratio = 0.4f;
  cmd.brake_ratio = 0.1f;
  cmd.steer_ratio = 0.5f;

  const auto out = Tick(joy, cmd);
  EXPECT_FLOAT_EQ(out.accel_ratio, 0.4f);
  EXPECT_FLOAT_EQ(out.brake_ratio, 0.1f);
  EXPECT_FLOAT_EQ(out.steer_ratio, 0.5f);
}

TEST_F(ArbiterCoreUnlockedTest, AutonomousSteerOnly_SteerFromAutoware_AccelBrakeStillFromJoystick)
{
  JoyFrame steer_only = NeutralJoy();
  steer_only.buttons[Joy::ButtonX] = 1;
  ASSERT_EQ(Tick(steer_only).mode, ControlMode::kAutonomousSteerOnly);

  JoyFrame joy = NeutralJoy();
  joy.axes[Keybind::Accel] = -1.0f;  // full throttle, from the human
  joy.axes[Keybind::Steer] = 0.0f;   // would be straight in manual; must be ignored

  AutowareCommand cmd;
  cmd.available = true;
  cmd.accel_ratio = 0.9f;  // must be ignored in steer-only mode
  cmd.steer_ratio = 1.0f;  // out of [-0.8, 0.8], must clamp

  const auto out = Tick(joy, cmd);
  EXPECT_FLOAT_EQ(out.accel_ratio, 1.0f);  // from joystick, not cmd.accel_ratio
  EXPECT_FLOAT_EQ(out.steer_ratio, 0.8f);  // from autoware, clamped
}

TEST_F(ArbiterCoreUnlockedTest, AutonomousSteerClampsToPoint8)
{
  JoyFrame autonomous = NeutralJoy();
  autonomous.buttons[Joy::ButtonY] = 1;
  ASSERT_EQ(Tick(autonomous).mode, ControlMode::kAutonomous);

  AutowareCommand cmd;
  cmd.available = true;
  cmd.steer_ratio = -1.0f;
  EXPECT_FLOAT_EQ(Tick(NeutralJoy(), cmd).steer_ratio, -0.8f);
}

TEST_F(ArbiterCoreUnlockedTest, AutonomousGear_FromAutowareGearCommand)
{
  JoyFrame autonomous = NeutralJoy();
  autonomous.buttons[Joy::ButtonY] = 1;
  ASSERT_EQ(Tick(autonomous).mode, ControlMode::kAutonomous);

  AutowareCommand cmd;
  cmd.available = true;

  cmd.gear_command = GearCommand::DRIVE;
  EXPECT_EQ(Tick(NeutralJoy(), cmd).gear, 'D');

  cmd.gear_command = GearCommand::REVERSE;
  EXPECT_EQ(Tick(NeutralJoy(), cmd).gear, 'R');

  cmd.gear_command = GearCommand::NEUTRAL;
  EXPECT_EQ(Tick(NeutralJoy(), cmd).gear, 'N');

  cmd.gear_command = GearCommand::NONE;  // unmapped value defaults to DRIVE
  EXPECT_EQ(Tick(NeutralJoy(), cmd).gear, 'D');
}

TEST_F(ArbiterCoreUnlockedTest, AutonomousUnavailable_SafeZeroInsteadOfUndefinedBehavior)
{
  JoyFrame autonomous = NeutralJoy();
  autonomous.buttons[Joy::ButtonY] = 1;
  ASSERT_EQ(Tick(autonomous).mode, ControlMode::kAutonomous);

  AutowareCommand cmd;  // available == false, ratios left at 0 but must not matter either way
  cmd.available = false;
  cmd.accel_ratio = 0.9f;  // deliberately non-zero to prove it's ignored

  const auto out = Tick(NeutralJoy(), cmd);
  EXPECT_FLOAT_EQ(out.accel_ratio, 0.0f);
  EXPECT_FLOAT_EQ(out.brake_ratio, 0.0f);
}

TEST_F(ArbiterCoreUnlockedTest, JoyTimeout_JustUnderThreshold_StaysAlive)
{
  core_.on_joy(NeutralJoy(), t_);
  t_ += 5.0;  // exactly at threshold: upstream uses a strict '<' comparison, so this must NOT fire
  const auto out = core_.step(t_, {});
  EXPECT_FALSE(out.is_emergency);
  EXPECT_TRUE(out.joystick_available);
}

TEST_F(ArbiterCoreUnlockedTest, JoyTimeout_PastThreshold_ForcesEmergencyAndStop)
{
  core_.on_joy(NeutralJoy(), t_);
  t_ += 5.0001;
  const auto out = core_.step(t_, {});
  EXPECT_TRUE(out.is_emergency);
  EXPECT_FALSE(out.joystick_available);  // input_ is reset to an empty frame on timeout
  EXPECT_FLOAT_EQ(out.brake_ratio, 1.0f);
  EXPECT_EQ(out.gear, 'N');
}

}  // namespace
}  // namespace racing_kart_sim_adapter
