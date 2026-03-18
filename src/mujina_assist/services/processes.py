from __future__ import annotations

import shlex

from mujina_assist.models import AppPaths
from mujina_assist.services.checks import command_exists
from mujina_assist.services.shell import run_bash, shell_quote
from mujina_assist.services.workspace import ros_prefix


def tmux_available() -> bool:
    return command_exists("tmux")


def tmux_session_exists(session_name: str) -> bool:
    result = run_bash(f"tmux has-session -t {shlex.quote(session_name)}", interactive=False)
    return result.returncode == 0


def kill_tmux_session(session_name: str) -> None:
    run_bash(f"tmux kill-session -t {shlex.quote(session_name)}", interactive=False)


def attach_tmux_session(session_name: str) -> int:
    result = run_bash(f"tmux attach -t {shlex.quote(session_name)}", interactive=True)
    return result.returncode


def start_sim_session(paths: AppPaths, session_name: str, with_joy: bool = True) -> int:
    main_command = " && ".join(
        [
            ros_prefix(paths),
            f"cd {shell_quote(paths.workspace_dir)}",
            "ros2 run mujina_control mujina_main --sim",
        ]
    )
    script_parts = [
        f"tmux new-session -d -s {shlex.quote(session_name)} {shlex.quote(f'bash -lc {shlex.quote(main_command)}')}",
    ]
    if with_joy:
        joy_command = " && ".join(
            [
                ros_prefix(paths),
                f"cd {shell_quote(paths.workspace_dir)}",
                "ros2 run joy_linux joy_linux_node",
            ]
        )
        script_parts.append(
            f"tmux split-window -h -t {shlex.quote(session_name)} {shlex.quote(f'bash -lc {shlex.quote(joy_command)}')}"
        )
        script_parts.append(f"tmux select-layout -t {shlex.quote(session_name)} tiled")
    script_parts.append(f"tmux attach -t {shlex.quote(session_name)}")
    result = run_bash(" && ".join(script_parts), interactive=True)
    return result.returncode


def start_real_session(paths: AppPaths, session_name: str, can_mode: str = "net") -> int:
    imu_command = " && ".join(
        [
            ros_prefix(paths),
            f"cd {shell_quote(paths.workspace_dir)}",
            'ros2 run rt_usb_imu_driver rt_usb_imu_driver --ros-args -p "port_name:=/dev/rt_usb_imu"',
        ]
    )
    can_script = "./mujina_control/scripts/can_setup_serial.sh" if can_mode == "serial" else "./mujina_control/scripts/can_setup_net.sh"
    main_command = " && ".join(
        [
            ros_prefix(paths),
            f"cd {shell_quote(paths.upstream_dir)}",
            can_script,
            "ros2 run mujina_control mujina_main",
        ]
    )
    joy_command = " && ".join(
        [
            ros_prefix(paths),
            f"cd {shell_quote(paths.workspace_dir)}",
            "ros2 run joy_linux joy_linux_node",
        ]
    )
    script_parts = [
        f"tmux new-session -d -s {shlex.quote(session_name)} {shlex.quote(f'bash -lc {shlex.quote(imu_command)}')}",
        f"tmux split-window -h -t {shlex.quote(session_name)} {shlex.quote(f'bash -lc {shlex.quote(main_command)}')}",
        f"tmux split-window -v -t {shlex.quote(session_name)} {shlex.quote(f'bash -lc {shlex.quote(joy_command)}')}",
        f"tmux select-layout -t {shlex.quote(session_name)} tiled",
        f"tmux attach -t {shlex.quote(session_name)}",
    ]
    result = run_bash(" && ".join(script_parts), interactive=True)
    return result.returncode
