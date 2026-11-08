"""Analytic geometry checks; no assumptions about population anatomy."""
import unittest
import numpy as np
import mujoco
from sim.fit_analysis import grip_path, wrist_path, make_layouts, MODEL_PATH


class FitAnalysisTests(unittest.TestCase):
    def test_analytic_path_matches_current_mujoco(self):
        m = mujoco.MjModel.from_xml_path(str(MODEL_PATH))
        d = mujoco.MjData(m)
        current = make_layouts()[0]
        angles = np.array([-.55, -.25, 0])
        expected = grip_path(current['axis'], current['grip0'], angles)
        for angle, position in zip(angles, expected):
            d.qpos[0] = angle
            mujoco.mj_forward(m, d)
            np.testing.assert_allclose(d.geom('handle').xpos, position, atol=1e-12)

    def test_fixed_hand_offset_aligned_axis_gives_constant_wrist_distance(self):
        angles = np.linspace(-.55, 0, 101)
        axis = np.array([0., 0., .223])
        elbow = np.array([0., -.3, .163])
        offset = np.array([0., 0., .06])
        path = grip_path(axis, np.array([0., -.2, .49]), angles)
        wrist = wrist_path(path, angles, offset, 'fixed')
        self.assertLess(np.ptp(np.linalg.norm(wrist - elbow, axis=1)), 1e-12)

    def test_corotating_hand_offset_aligned_axis_gives_constant_distance(self):
        angles = np.linspace(-.55, 0, 101)
        axis = np.array([0., 0., .163])
        elbow = np.array([0., -.3, .163])
        path = grip_path(axis, np.array([0., -.2, .49]), angles)
        wrist = wrist_path(path, angles, np.array([0., 0., .06]), 'corotating')
        self.assertLess(np.ptp(np.linalg.norm(wrist - elbow, axis=1)), 1e-12)

    def test_candidate_is_single_passive_dof_and_matches_analytic_path(self):
        candidate = MODEL_PATH.parent / 'candidates/elbow_axis.xml'
        m = mujoco.MjModel.from_xml_path(str(candidate))
        d = mujoco.MjData(m)
        layout = make_layouts()[2]
        self.assertEqual(m.nq, 1)
        self.assertEqual(m.nu, 0)
        self.assertEqual(float(np.max(np.abs(m.body_gravcomp))), 0)
        for angle in (0, -.25, -.55):
            d.qpos[0] = angle
            d.qvel[0] = 0
            mujoco.mj_forward(m, d)
            expected = grip_path(layout['axis'], layout['grip0'], np.array([angle]))[0]
            np.testing.assert_allclose(d.geom('handle').xpos, expected, atol=1e-12)
            np.testing.assert_allclose(d.geom('handle').xmat.reshape(3,3)[:,2], [0,-1,0], atol=1e-12)
            self.assertLess(abs(d.qfrc_bias[0]), 1e-12)
