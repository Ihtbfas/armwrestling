"""Check the provisional seated-user geometry, not human comfort or safety."""
from pathlib import Path
import unittest
import mujoco
import numpy as np


class OperatorLayoutTests(unittest.TestCase):
    def setUp(self):
        self.model = mujoco.MjModel.from_xml_path(str(Path(__file__).with_name('arm.xml')))
        self.data = mujoco.MjData(self.model)
        mujoco.mj_forward(self.model, self.data)

    def test_grip_faces_user_and_has_supported_back_end(self):
        grip = self.data.geom('handle')
        axis = grip.xmat.reshape(3, 3)[:, 2]
        expected = [0, -1, 0]
        np.testing.assert_allclose(axis, expected, atol=1e-9)
        bottom = grip.xpos - 0.08 * axis
        np.testing.assert_allclose(bottom, self.data.xanchor[0] + [-.34, -.12, .33], atol=1e-9)
        self.assertLess((grip.xpos + .08 * axis)[1], bottom[1])

    def test_forearm_target_is_in_front_of_elbow_on_same_centerline(self):
        grip = self.data.geom('handle').xpos
        pad = self.data.geom('elbow_pad').xpos
        np.testing.assert_allclose(pad, [0, -.30, .10], atol=1e-9)
        np.testing.assert_allclose(grip, [0, -.192638388534, .49], atol=1e-9)
        self.assertAlmostEqual(grip[0] - pad[0], 0)
        self.assertAlmostEqual(grip[1] - pad[1], .107361611466)
        self.assertLess(self.data.geom('user_edge').xpos[1], pad[1])
        self.assertGreater(self.data.xanchor[0, 0], grip[0] + .3)
        self.assertGreater(self.data.xanchor[0, 1], grip[1])
