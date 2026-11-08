"""Interactive MuJoCo viewer. Run from the repository root with python -m sim.run."""

import argparse
from pathlib import Path
import threading
import time

import mujoco
import mujoco.viewer

from sim.control import BrakeParameters, brake_state
from sim.visualization import configure_camera, update_brake_arrow, set_boundary_marker


MODEL_PATH = Path(__file__).with_name("arm.xml")


def keyboard_torque(keycode, magnitude, parameters):
    """1 pushes toward winning side, 2 returns, 0 releases; other keys do nothing."""
    if keycode == ord('1'):
        return parameters.forward_sign * magnitude
    if keycode == ord('2'):
        return -parameters.forward_sign * magnitude
    if keycode == ord('0'):
        return 0.0
    return None


def main():
    parser = argparse.ArgumentParser(description="Passive one-joint arm wrestling demo")
    parser.add_argument("--model", type=Path, default=MODEL_PATH, help="optional candidate MJCF file")
    parser.add_argument("--boundary", type=float, default=0.25, help="free boundary in radians")
    parser.add_argument("--stiffness", type=float, default=2.0, help="brake slope in N m/rad")
    parser.add_argument("--max-brake", type=float, default=0.8, help="maximum brake target in N m")
    parser.add_argument("--transition", type=float, default=0.05, help="low-speed smoothing in rad/s")
    parser.add_argument("--user-torque", type=float, default=0.5, help="magnitude of keyboard user torque in N m")
    parser.add_argument("--duration", type=float, default=None, help="optional seconds before automatic exit")
    args = parser.parse_args()
    if args.user_torque < 0 or (args.duration is not None and args.duration <= 0):
        parser.error("user-torque must be nonnegative and duration must be positive")

    parameters = BrakeParameters(
        free_boundary=args.boundary,
        stiffness=args.stiffness,
        max_torque=args.max_brake,
        speed_transition=args.transition,
    )
    print(f"Model: {args.model}", flush=True)
    model = mujoco.MjModel.from_xml_path(str(args.model))
    set_boundary_marker(model, parameters)
    data = mujoco.MjData(model)
    joint_id = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_JOINT, "arm_hinge")
    position_index = model.jnt_qposadr[joint_id]
    velocity_index = model.jnt_dofadr[joint_id]
    handle_id = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_GEOM, "handle")
    input_torque = [0.0]

    def on_key(keycode):
        requested = keyboard_torque(keycode, args.user_torque, parameters)
        if requested is not None and requested != input_torque[0]:
            input_torque[0] = requested
            print(f"Keyboard joint torque: {requested:+.3f} N m", flush=True)

    print("Viewer focused: 1 = winning/down, 2 = return/up, 0 = release; close window to quit", flush=True)
    print("Yellow dial tick = free-space boundary; angle and torques appear here every 0.25 s.", flush=True)
    print("Orange arrow = actual brake torque; drag the mouse to orbit/pan, scroll to zoom.", flush=True)
    print("Ideal passive balance: moving COM on horizontal shaft; no active gravity compensation.", flush=True)
    started = time.monotonic()
    next_report = started
    threads_before_viewer = set(threading.enumerate())
    with mujoco.viewer.launch_passive(
        model, data, key_callback=on_key, show_left_ui=False, show_right_ui=False
    ) as viewer:
        # MuJoCo's passive viewer runs on a daemon thread on Linux.
        viewer_thread = next(
            (
                thread for thread in threading.enumerate()
                if thread not in threads_before_viewer and thread.name.endswith("(_launch_internal)")
            ),
            None,
        )
        with viewer.lock():
            configure_camera(viewer.cam)
        while viewer.is_running():
            step_started = time.monotonic()
            if args.duration is not None and step_started - started >= args.duration:
                break
            with viewer.lock():
                angle = float(data.qpos[position_index])
                speed = float(data.qvel[velocity_index])
                state = brake_state(angle, speed, parameters)
                user_torque = input_torque[0]
                data.qfrc_applied[velocity_index] = user_torque + state.applied_torque
                mujoco.mj_step(model, data)
                # Refresh world positions after integration for the attached arrow.
                mujoco.mj_forward(model, data)
                update_brake_arrow(viewer.user_scn, data.geom_xpos[handle_id],
                                   data.xanchor[joint_id], state.applied_torque,
                                   data.xaxis[joint_id], data.geom_xmat[handle_id].reshape(3, 3)[:, 2])
            viewer.sync()
            if step_started >= next_report:
                print(
                    f"t={data.time:5.2f}s  angle={angle:+.3f} rad  "
                    f"q={state.relative_angle:+.3f} rad  boundary={parameters.free_boundary:.3f} rad  "
                    f"target={state.target_torque:.3f} N m  brake={state.applied_torque:+.3f} N m  "
                    f"user={user_torque:+.3f} N m",
                    flush=True,
                )
                next_report = step_started + 0.25
            time.sleep(max(0.0, model.opt.timestep - (time.monotonic() - step_started)))
    # Handle.close() requests exit but does not wait for GLFW cleanup.
    if viewer_thread is not None:
        viewer_thread.join(timeout=5.0)
        if viewer_thread.is_alive():
            raise RuntimeError("MuJoCo viewer did not finish closing")


if __name__ == "__main__":
    main()
