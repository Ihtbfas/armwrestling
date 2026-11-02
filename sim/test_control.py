"""Checks for the passive brake law and one-joint MuJoCo dynamics."""

from pathlib import Path
import unittest

from sim.control import BrakeParameters, brake_state


class BrakeLawTests(unittest.TestCase):
    def setUp(self):
        self.parameters = BrakeParameters(
            initial_angle=0.0,
            forward_sign=1,
            free_boundary=0.25,
            stiffness=2.0,
            max_torque=0.8,
            speed_transition=0.05,
        )

    def test_free_space_has_no_brake(self):
        for angle in (-0.3, 0.0, 0.24, 0.25):
            state = brake_state(angle, 1.0, self.parameters)
            self.assertEqual(state.target_torque, 0.0)
            self.assertEqual(state.applied_torque, 0.0)

    def test_linear_growth_and_cap(self):
        self.assertAlmostEqual(brake_state(0.35, 1.0, self.parameters).target_torque, 0.2)
        self.assertAlmostEqual(brake_state(0.45, 1.0, self.parameters).target_torque, 0.4)
        self.assertAlmostEqual(brake_state(0.65, 1.0, self.parameters).target_torque, 0.8)
        self.assertAlmostEqual(brake_state(1.0, 1.0, self.parameters).target_torque, 0.8)

    def test_passivity_for_both_motion_directions(self):
        for angle in (0.3, 0.5, 0.9):
            for speed in (-2.0, -0.01, 0.0, 0.01, 2.0):
                with self.subTest(angle=angle, speed=speed):
                    state = brake_state(angle, speed, self.parameters)
                    self.assertLessEqual(state.applied_torque * speed, 0.0)

    def test_zero_speed_transition_is_continuous(self):
        self.assertEqual(brake_state(0.5, 0.0, self.parameters).applied_torque, 0.0)
        negative = brake_state(0.5, -1e-6, self.parameters).applied_torque
        positive = brake_state(0.5, 1e-6, self.parameters).applied_torque
        self.assertGreater(negative, 0.0)
        self.assertLess(positive, 0.0)
        self.assertLess(abs(negative), 1e-4)
        self.assertLess(abs(positive), 1e-4)

    def test_reversed_angle_convention(self):
        parameters = BrakeParameters(0.1, -1, 0.25, 2.0, 0.8, 0.05)
        self.assertAlmostEqual(brake_state(-0.25, -1.0, parameters).relative_angle, 0.35)
        self.assertAlmostEqual(brake_state(-0.25, -1.0, parameters).target_torque, 0.2)


class MujocoDynamicsTests(unittest.TestCase):
    def test_user_torque_moves_joint_continuously_and_brake_opposes_it(self):
        import mujoco

        model = mujoco.MjModel.from_xml_path(str(Path(__file__).with_name("arm.xml")))
        joint_id = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_JOINT, "arm_hinge")
        self.assertGreaterEqual(joint_id, 0)
        dof = model.jnt_dofadr[joint_id]
        qpos = model.jnt_qposadr[joint_id]
        free_run = mujoco.MjData(model)
        braked_run = mujoco.MjData(model)

        # Stay below the mechanical joint stop, which is unrelated to the brake.
        for _ in range(250):
            free_run.qfrc_applied[dof] = 0.5
            mujoco.mj_step(model, free_run)

            state = brake_state(braked_run.qpos[qpos], braked_run.qvel[dof], BrakeParameters())
            braked_run.qfrc_applied[dof] = 0.5 + state.applied_torque
            mujoco.mj_step(model, braked_run)

        self.assertGreater(free_run.qpos[qpos], 0.0)
        self.assertGreater(braked_run.qpos[qpos], 0.0)
        self.assertLess(braked_run.qpos[qpos], free_run.qpos[qpos])
        self.assertGreater(braked_run.qpos[qpos], 0.25)
        self.assertGreater(free_run.qvel[dof], 0.0)

    def test_brake_power_stays_nonpositive_during_reversal(self):
        import mujoco

        model = mujoco.MjModel.from_xml_path(str(Path(__file__).with_name("arm.xml")))
        data = mujoco.MjData(model)
        parameters = BrakeParameters()
        brake_work = 0.0
        saw_reverse_in_braked_zone = False

        for step in range(600):
            user_torque = 0.8 if step < 250 else -1.0
            state = brake_state(data.qpos[0], data.qvel[0], parameters)
            power = state.applied_torque * data.qvel[0]
            self.assertLessEqual(power, 1e-12)
            brake_work += power * model.opt.timestep
            if state.relative_angle > parameters.free_boundary and data.qvel[0] < -0.05:
                saw_reverse_in_braked_zone = True
            data.qfrc_applied[0] = user_torque + state.applied_torque
            mujoco.mj_step(model, data)

        self.assertTrue(saw_reverse_in_braked_zone)
        self.assertLess(brake_work, 0.0)


if __name__ == "__main__":
    unittest.main()
