from __future__ import annotations

from mujina_assist.models import AppPaths
from mujina_assist.services.shell import shell_quote
from mujina_assist.services.workspace import ros_prefix


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


def build_real_imu_script(paths: AppPaths) -> str:
    return " && ".join(
        [
            ros_prefix(paths),
            f"cd {shell_quote(paths.workspace_dir)}",
            'ros2 run rt_usb_imu_driver rt_usb_imu_driver --ros-args -p "port_name:=/dev/rt_usb_imu"',
        ]
    )


def build_real_main_script(paths: AppPaths, can_mode: str) -> str:
    can_script = "./mujina_control/scripts/can_setup_serial.sh" if can_mode == "serial" else "./mujina_control/scripts/can_setup_net.sh"
    return " && ".join(
        [
            ros_prefix(paths),
            f"cd {shell_quote(paths.upstream_dir)}",
            can_script,
            "ros2 run mujina_control mujina_main",
        ]
    )


def build_motor_read_script(paths: AppPaths, ids: list[int], can_mode: str) -> str:
    can_script = "./mujina_control/scripts/can_setup_serial.sh" if can_mode == "serial" else "./mujina_control/scripts/can_setup_net.sh"
    return " && ".join(
        [
            ros_prefix(paths),
            f"cd {shell_quote(paths.upstream_dir)}",
            can_script,
            "python3 mujina_control/scripts/motor_test_read_only.py --ids " + " ".join(str(i) for i in ids),
        ]
    )


def build_zero_script(paths: AppPaths, ids: list[int], can_mode: str) -> str:
    can_script = "./mujina_control/scripts/can_setup_serial.sh" if can_mode == "serial" else "./mujina_control/scripts/can_setup_net.sh"
    return " && ".join(
        [
            ros_prefix(paths),
            f"cd {shell_quote(paths.upstream_dir)}",
            can_script,
            "python3 mujina_control/scripts/motor_set_zero_position.py --ids " + " ".join(str(i) for i in ids),
        ]
    )
