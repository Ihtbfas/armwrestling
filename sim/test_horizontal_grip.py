"""Horizontal grip kinematics and geometric clearance, not a human model."""
from pathlib import Path
import unittest
import mujoco
import numpy as np


class HorizontalGripTests(unittest.TestCase):
    def setUp(self):
        self.model = mujoco.MjModel.from_xml_path(str(Path(__file__).with_name('arm.xml')))
        self.data = mujoco.MjData(self.model)

    def test_axis_stays_negative_y_through_joint_range(self):
        for angle in np.linspace(-0.55, 1.2, 101):
            self.data.qpos[0] = angle
            mujoco.mj_forward(self.model, self.data)
            axis = self.data.geom('handle').xmat.reshape(3, 3)[:, 2]
            np.testing.assert_allclose(axis, [0, -1, 0], atol=1e-12)

    def test_downstroke_clearance_of_grip_from_rods_pad_and_table(self):
        m, d = self.model, self.data
        for angle in np.linspace(-0.55, 0, 101):
            d.qpos[0] = angle
            mujoco.mj_forward(m, d)
            for name in ('arm_left', 'arm_right', 'elbow_pad', 'table_top'):
                distance = mujoco.mj_geomDistance(m, d, m.geom('handle').id,
                                                  m.geom(name).id, 1.0, None)
                self.assertGreater(distance, 0.01, (angle, name, distance))

    def test_midpoint_and_supported_back_end(self):
        m, d = self.model, self.data
        mujoco.mj_forward(m, d)
        np.testing.assert_allclose(d.geom('handle').xpos, [0, -.192638388534, .49], atol=1e-10)
        axis = d.geom('handle').xmat.reshape(3, 3)[:, 2]
        back_end = d.geom('handle').xpos - .08 * axis
        # Short support's top meets the back end; the grip extends toward user.
        support = d.geom('grip_support')
        support_axis = support.xmat.reshape(3, 3)[:, 2]
        ends = [support.xpos - m.geom('grip_support').size[1] * support_axis,
                support.xpos + m.geom('grip_support').size[1] * support_axis]
        self.assertLess(min(np.linalg.norm(back_end - p) for p in ends), 1e-10)
