"""Rendering helpers; no forces or gravity compensation are applied here."""
import mujoco
import numpy as np

ARROW_METERS_PER_NM = 0.22


def configure_camera(camera):
    """Initialize a free camera from the user's front left."""
    camera.type = mujoco.mjtCamera.mjCAMERA_FREE
    camera.lookat[:] = [0.04, 0.0, 0.10]
    camera.distance = 1.7
    camera.azimuth = 125
    camera.elevation = -35


def set_boundary_marker(model, parameters):
    """Rotate the shaft-mounted boundary tick about the arm's +Y axis."""
    angle = parameters.initial_angle + parameters.forward_sign * parameters.free_boundary
    model.body('boundary_reference').quat[:] = [np.cos(angle / 2), 0, np.sin(angle / 2), 0]


def brake_arrow_endpoints(handle_position, pivot_position, brake_torque,
                          joint_axis, grip_axis=(0, 0, 1)):
    """Signed joint torque becomes an equivalent tangent at the grip."""
    if abs(brake_torque) < 1e-6:
        return None
    axis = np.asarray(joint_axis, dtype=float)
    axis = axis / np.linalg.norm(axis)
    radial = np.asarray(handle_position) - np.asarray(pivot_position)
    tangent = np.cross(axis, radial)
    radius = np.linalg.norm(tangent)
    if radius < 1e-9:
        return None
    tangent /= radius
    start = np.asarray(handle_position) + 0.11 * np.asarray(grip_axis)
    end = start + ARROW_METERS_PER_NM * brake_torque * tangent
    return start, end


def update_brake_arrow(scene, handle_position, pivot_position, brake_torque,
                       joint_axis, grip_axis=(0, 0, 1)):
    """Rebuild one decorative arrow in the viewer's user scene."""
    scene.ngeom = 0
    endpoints = brake_arrow_endpoints(handle_position, pivot_position, brake_torque,
                                     joint_axis, grip_axis)
    if endpoints is None:
        return
    geom = scene.geoms[0]
    mujoco.mjv_initGeom(geom, mujoco.mjtGeom.mjGEOM_ARROW, np.zeros(3),
                       np.zeros(3), np.eye(3).ravel(), np.array([1.0, 0.18, 0.03, 1.0]))
    mujoco.mjv_connector(geom, mujoco.mjtGeom.mjGEOM_ARROW, 0.012, *endpoints)
    scene.ngeom = 1
