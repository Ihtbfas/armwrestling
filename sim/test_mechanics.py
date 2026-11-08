"""Mechanical layout and ideal passive balance checks."""
from pathlib import Path
import unittest
import mujoco
import numpy as np
from sim.control import BrakeParameters
from sim.visualization import set_boundary_marker


class MechanicsTests(unittest.TestCase):
    def setUp(self):
        self.model = mujoco.MjModel.from_xml_path(str(Path(__file__).with_name('arm.xml')))
        self.data = mujoco.MjData(self.model)

    def test_horizontal_axis_and_rigid_double_rods(self):
        m, d = self.model, self.data
        self.assertEqual(m.nv, 1)
        self.assertEqual(m.nu, 0)
        np.testing.assert_allclose(m.jnt_axis[0], [0, 1, 0])
        self.assertEqual(m.geom('arm_left').bodyid, m.geom('arm_right').bodyid)
        for angle in (-0.5, 0, 0.5, 1.2):
            d.qpos[0] = angle
            mujoco.mj_forward(m, d)
            r = d.geom('handle').xpos - d.xanchor[0]
            self.assertAlmostEqual(r[1], -0.12)
            self.assertAlmostEqual(np.linalg.norm(r[[0, 2]]), np.hypot(0.34, 0.33))
            self.assertGreater(d.geom('handle').xpos[2], 0.15)
            np.testing.assert_allclose(d.xaxis[0], [0, 1, 0], atol=1e-12)
        d.qpos[0] = 0
        mujoco.mj_forward(m, d)
        np.testing.assert_allclose(d.geom('handle').xmat.reshape(3, 3)[:, 2], [0, 0, 1])
        self.assertLess(d.geom('elbow_pad').xpos[1], d.geom('handle').xpos[1])

    def test_balance_has_no_gravity_torque_or_zero_input_drift(self):
        m, d = self.model, self.data
        self.assertEqual(np.max(np.abs(m.body_gravcomp)), 0)
        for angle in (-0.5, 0, 0.5, 1.0):
            mujoco.mj_resetData(m, d)
            d.qpos[0] = angle
            mujoco.mj_forward(m, d)
            self.assertLess(abs(d.qfrc_bias[0]), 1e-12)
            for _ in range(500):
                mujoco.mj_step(m, d)
            self.assertAlmostEqual(d.qpos[0], angle)
            self.assertLess(abs(d.qvel[0]), 1e-12)

    def test_boundary_marker_tracks_parameter(self):
        set_boundary_marker(self.model, BrakeParameters(free_boundary=0.4))
        mujoco.mj_forward(self.model, self.data)
        expected = np.array([np.cos(0.2), 0, np.sin(0.2), 0])
        np.testing.assert_allclose(self.model.body('boundary_reference').quat, expected)

    def test_initial_raised_pose_and_rod_length(self):
        m, d = self.model, self.data
        mujoco.mj_forward(m, d)
        np.testing.assert_allclose(d.xanchor[0], [0.34, 0.10, 0.16])
        np.testing.assert_allclose(d.geom('handle').xpos, [0.0, -0.02, 0.49])
        rod_axis = d.geom('arm_left').xmat.reshape(3, 3)[:, 2]
        self.assertGreater(np.arctan2(abs(rod_axis[2]), abs(rod_axis[0])), np.deg2rad(34))
        delta = d.geom('handle').xpos - d.geom('elbow_pad').xpos
        self.assertAlmostEqual(np.linalg.norm(delta[:2]), 0.18)
        self.assertAlmostEqual(delta[2], 0.39)
        for name in ('arm_left', 'arm_right'):
            self.assertAlmostEqual(2 * m.geom(name).size[1], np.sqrt(0.34**2 + 0.12**2 + 0.25**2))

    def test_dial_boundary_matches_grip_radial_direction(self):
        m, d = self.model, self.data
        set_boundary_marker(m, BrakeParameters(free_boundary=0.4))
        d.qpos[0] = 0.4
        mujoco.mj_forward(m, d)
        grip_radial = (d.geom('handle').xpos - d.xanchor[0])[[0, 2]]
        tick_radial = (d.geom('free_boundary_marker').xpos - d.xanchor[0])[[0, 2]]
        np.testing.assert_allclose(tick_radial / np.linalg.norm(tick_radial),
                                   grip_radial / np.linalg.norm(grip_radial))
