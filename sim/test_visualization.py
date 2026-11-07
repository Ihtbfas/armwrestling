"""Arrow direction, scale and visibility checks without a window."""
import unittest
import numpy as np
import mujoco
from sim.visualization import brake_arrow_endpoints, update_brake_arrow, configure_camera


class VisualizationTests(unittest.TestCase):
    def test_arrow_is_tangential_signed_and_scaled(self):
        for handle in (np.array([0.35, 0, 0.3]), np.array([0, 0.35, 0.3])):
            pivot = np.array([0, 0, 0.18])
            start, end = brake_arrow_endpoints(handle, pivot, 0.4, [0, 0, 1])
            vector = end - start
            self.assertAlmostEqual(np.linalg.norm(vector), 0.4 * 0.22)
            self.assertAlmostEqual(np.dot(vector[:2], handle[:2]), 0)
            self.assertGreater(np.cross(handle - pivot, vector)[2], 0)
            reverse_start, reverse_end = brake_arrow_endpoints(handle, pivot, -0.4, [0, 0, 1])
            np.testing.assert_allclose(reverse_end - reverse_start, -vector)

    def test_zero_torque_hides_previous_arrow(self):
        model = mujoco.MjModel.from_xml_path('sim/arm.xml')
        scene = mujoco.MjvScene(model, maxgeom=10)
        update_brake_arrow(scene, [0.35, 0, 0.3], [0, 0, 0.18], -0.8, [0, 1, 0])
        self.assertEqual(scene.ngeom, 1)
        update_brake_arrow(scene, [0.35, 0, 0.3], [0, 0, 0.18], 0, [0, 1, 0])
        self.assertEqual(scene.ngeom, 0)

    def test_camera_remains_free(self):
        camera = mujoco.MjvCamera()
        configure_camera(camera)
        self.assertEqual(camera.type, mujoco.mjtCamera.mjCAMERA_FREE)
        self.assertGreater(camera.elevation, -90)
        self.assertLess(camera.elevation, 0)

    def test_horizontal_axis_arrow_opposes_grip_velocity(self):
        model = mujoco.MjModel.from_xml_path('sim/arm.xml')
        data = mujoco.MjData(model)
        for angle in (-0.5, 0, 0.5, 1.0):
            data.qpos[0] = angle
            mujoco.mj_forward(model, data)
            position = data.geom('handle').xpos.copy()
            start, end = brake_arrow_endpoints(position, data.xanchor[0], -0.4, data.xaxis[0])
            data.qpos[0] = angle + 1e-6
            mujoco.mj_forward(model, data)
            velocity_direction = (data.geom('handle').xpos - position) / 1e-6
            self.assertLess(np.dot(end - start, velocity_direction), 0)
