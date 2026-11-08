"""Winning-side downward motion must agree with input, brake and dial."""
from pathlib import Path
import unittest
import mujoco
import numpy as np
from sim.control import BrakeParameters, brake_state
from sim.run import keyboard_torque
from sim.visualization import set_boundary_marker


class WinningDirectionTests(unittest.TestCase):
    def test_default_is_negative_joint_direction(self):
        self.assertEqual(BrakeParameters().forward_sign, -1)

    def test_downward_free_space_linear_growth_and_cap(self):
        parameters = BrakeParameters()
        for angle, expected in [(0, 0), (-0.15, 0), (-0.25, 0), (-0.35, 0.2), (-0.45, 0.4), (-0.65, 0.8)]:
            state = brake_state(angle, -0.5, parameters)
            self.assertAlmostEqual(state.target_torque, expected)
        self.assertEqual(brake_state(0.4, 0.5, parameters).target_torque, 0)

    def test_keyboard_and_physics_drive_down_across_boundary(self):
        parameters = BrakeParameters()
        self.assertEqual(keyboard_torque(ord('1'), 0.5, parameters), -0.5)
        self.assertEqual(keyboard_torque(ord('2'), 0.5, parameters), 0.5)
        self.assertEqual(keyboard_torque(ord('0'), 0.5, parameters), 0)
        self.assertIsNone(keyboard_torque(ord('A'), 0.5, parameters))
        model = mujoco.MjModel.from_xml_path(str(Path(__file__).with_name('arm.xml')))
        data = mujoco.MjData(model)
        saw_free, saw_resistance = False, False
        previous_height = 0.49
        for _ in range(160):
            mujoco.mj_forward(model, data)
            height = float(data.geom('handle').xpos[2])
            self.assertLessEqual(height, previous_height + 1e-12)
            previous_height = height
            state = brake_state(data.qpos[0], data.qvel[0], parameters)
            if state.relative_angle <= parameters.free_boundary:
                self.assertEqual(state.target_torque, 0)
                saw_free = True
            else:
                self.assertGreater(state.applied_torque, 0)
                saw_resistance = True
            self.assertLessEqual(state.applied_torque * data.qvel[0], 0)
            data.qfrc_applied[0] = keyboard_torque(ord('1'), 0.5, parameters) + state.applied_torque
            mujoco.mj_step(model, data)
        self.assertTrue(saw_free and saw_resistance)
        self.assertLess(data.qpos[0], -0.25)
        self.assertGreater(data.qpos[0], model.jnt_range[0, 0] + 0.02)

    def test_negative_boundary_dial_and_downward_height(self):
        model = mujoco.MjModel.from_xml_path(str(Path(__file__).with_name('arm.xml')))
        set_boundary_marker(model, BrakeParameters())
        data = mujoco.MjData(model)
        data.qpos[0] = -0.25
        mujoco.mj_forward(model, data)
        self.assertAlmostEqual(data.geom('handle').xpos[2], 0.3956237530, places=9)
        self.assertLess(model.body('boundary_reference').quat[2], 0)
        radial = (data.geom('handle').xpos - data.xanchor[0])[[0, 2]]
        tick = (data.geom('free_boundary_marker').xpos - data.xanchor[0])[[0, 2]]
        np.testing.assert_allclose(radial / np.linalg.norm(radial), tick / np.linalg.norm(tick), atol=1e-7)

    def test_brake_opposes_both_directions_and_zero_transition(self):
        for speed in (-1, -0.01, 0, 0.01, 1):
            state = brake_state(-0.45, speed, BrakeParameters())
            self.assertLessEqual(state.applied_torque * speed, 0)
            if speed == 0:
                self.assertEqual(state.applied_torque, 0)
