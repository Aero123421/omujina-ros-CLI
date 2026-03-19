from __future__ import annotations

import textwrap

from mujina_assist.models import AppPaths
from mujina_assist.services.shell import shell_quote
from mujina_assist.services.workspace import ros_prefix


def _can_setup_command(can_mode: str) -> str:
    return "./mujina_control/scripts/can_setup_serial.sh" if can_mode == "serial" else "./mujina_control/scripts/can_setup_net.sh"


def _workspace_command(paths: AppPaths, *commands: str) -> str:
    return " && ".join(
        [
            ros_prefix(paths),
            f"cd {shell_quote(paths.upstream_dir)}",
            *commands,
        ]
    )


def _python_heredoc(source: str) -> str:
    return "python3 - <<'PY'\n" + source.rstrip() + "\nPY"


def build_viz_script(paths: AppPaths) -> str:
    return " && ".join(
        [
            ros_prefix(paths),
            f"cd {shell_quote(paths.workspace_dir)}",
            "ros2 launch mujina_description display.launch.py",
        ]
    )


def build_sim_main_script(paths: AppPaths) -> str:
    return " && ".join(
        [
            ros_prefix(paths),
            f"cd {shell_quote(paths.workspace_dir)}",
            "ros2 run mujina_control mujina_main --sim",
        ]
    )


def build_joy_script(paths: AppPaths) -> str:
    return " && ".join(
        [
            ros_prefix(paths),
            f"cd {shell_quote(paths.workspace_dir)}",
            "ros2 run joy_linux joy_linux_node",
        ]
    )


def build_real_imu_script(paths: AppPaths, port_name: str = "/dev/rt_usb_imu") -> str:
    return " && ".join(
        [
            ros_prefix(paths),
            f"cd {shell_quote(paths.workspace_dir)}",
            f'ros2 run rt_usb_imu_driver rt_usb_imu_driver --ros-args -p "port_name:={port_name}"',
        ]
    )


def build_real_main_script(paths: AppPaths, can_mode: str) -> str:
    return _workspace_command(paths, _can_setup_command(can_mode), "ros2 run mujina_control mujina_main")


def build_motor_read_script(paths: AppPaths, ids: list[int], can_mode: str, *, device_name: str = "can0") -> str:
    return _workspace_command(
        paths,
        _can_setup_command(can_mode),
        "python3 mujina_control/scripts/motor_test_read_only.py"
        + f" --device {shell_quote(device_name)}"
        + " --ids "
        + " ".join(str(i) for i in ids),
    )


def build_motor_probe_script(
    paths: AppPaths,
    ids: list[int],
    can_mode: str,
    *,
    device_name: str = "can0",
    include_can_setup: bool = True,
) -> str:
    commands: list[str] = []
    if include_can_setup:
        commands.append(_can_setup_command(can_mode))
    ids_literal = ", ".join(str(i) for i in ids)
    probe_source = textwrap.dedent(
        f"""
        from mujina_control.motor_lib.motor_lib import CanMotorController

        device = {device_name!r}
        ids = [{ids_literal}]
        print('# using Socket {{}} for can communication'.format(device))
        print('# motor ids: {{}}'.format(ids))
        assert ids, 'please input motor ids'

        for motor_id in ids:
            motor_controller = CanMotorController(
                device, motor_id, 1, 'RobStride02', external_gear_ratio=1.0
            )
            pos, vel, cur, tem = motor_controller.send_rad_command(0.0, 0.0, 0, 0, 0)
            print(
                'Motor {{}} Position: {{}}, Velocity: {{}}, Torque: {{}}, Temp: {{}}'.format(
                    motor_id, pos, vel, cur, tem
                )
            )

        print('Motor probe completed.')
        """
    )
    commands.append(_python_heredoc(probe_source))
    return _workspace_command(paths, *commands)


def build_zero_script(
    paths: AppPaths,
    ids: list[int],
    can_mode: str,
    *,
    device_name: str = "can0",
    include_can_setup: bool = True,
) -> str:
    commands: list[str] = []
    if include_can_setup:
        commands.append(_can_setup_command(can_mode))
    commands.append(
        "python3 mujina_control/scripts/motor_set_zero_position.py"
        + f" --device {shell_quote(device_name)}"
        + " --ids "
        + " ".join(str(i) for i in ids)
    )
    return _workspace_command(paths, *commands)
