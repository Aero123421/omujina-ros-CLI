from __future__ import annotations

import shutil
from pathlib import Path

from mujina_assist.models import AppPaths
from mujina_assist.services.shell import CommandResult, run_bash, shell_quote


UPSTREAM_REPO = "https://github.com/rt-net/mujina_ros.git"


def ros_prefix(paths: AppPaths, use_workspace: bool = True) -> str:
    parts = ["source /opt/ros/jazzy/setup.bash"]
    workspace_setup = paths.workspace_dir / "install" / "setup.bash"
    if use_workspace and workspace_setup.exists():
        parts.append(f"source {shell_quote(workspace_setup)}")
    return " && ".join(parts)


def ensure_upstream_clone(paths: AppPaths, log_path: Path) -> CommandResult:
    paths.workspace_src_dir.mkdir(parents=True, exist_ok=True)
    if paths.upstream_dir.exists():
        return CommandResult(command="git clone", returncode=0, stdout="already cloned")
    script = f"git clone {UPSTREAM_REPO} {shell_quote(paths.upstream_dir)}"
    return run_bash(script, cwd=paths.repo_root, log_path=log_path, interactive=True)


def capture_default_policy(paths: AppPaths) -> None:
    if paths.default_policy_cache.exists():
        return
    source = paths.source_policy_path
    if source.exists():
        paths.default_policy_cache.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, paths.default_policy_cache)


def run_initial_setup(paths: AppPaths, log_path: Path, skip_upgrade: bool = False) -> CommandResult:
    setup_lines = [
        "set -e",
        "sudo apt update",
        "sudo apt install -y software-properties-common curl git python3-pip python3-venv lsb-release tmux",
        "sudo add-apt-repository -y universe",
        "sudo apt update && sudo apt install -y curl",
        "export ROS_APT_SOURCE_VERSION=$(curl -s https://api.github.com/repos/ros-infrastructure/ros-apt-source/releases/latest | grep -F 'tag_name' | awk -F\\\" '{print $4}')",
        "curl -L -o /tmp/ros2-apt-source.deb \"https://github.com/ros-infrastructure/ros-apt-source/releases/download/${ROS_APT_SOURCE_VERSION}/ros2-apt-source_${ROS_APT_SOURCE_VERSION}.$(. /etc/os-release && echo ${UBUNTU_CODENAME:-${VERSION_CODENAME}})_all.deb\"",
        "sudo dpkg -i /tmp/ros2-apt-source.deb",
        "sudo apt update",
    ]
    if not skip_upgrade:
        setup_lines.append("sudo apt upgrade -y")
    setup_lines.extend(
        [
            "sudo apt install -y ros-jazzy-desktop ros-dev-tools python3-rosdep python3-colcon-common-extensions ros-jazzy-xacro ros-jazzy-joint-state-publisher-gui ros-jazzy-joy-linux",
            "sudo rosdep init || true",
            "rosdep update",
        ]
    )
    return run_bash("\n".join(setup_lines), cwd=paths.repo_root, log_path=log_path, interactive=True)


def run_workspace_build(paths: AppPaths, log_path: Path, packages: list[str] | None = None) -> CommandResult:
    return run_workspace_build_with_options(paths, log_path, packages=packages)


def run_workspace_dependency_setup(paths: AppPaths, log_path: Path) -> CommandResult:
    return run_workspace_build_with_options(
        paths,
        log_path,
        packages=None,
        run_rosdep_step=True,
        install_python_deps=True,
        run_colcon_build=False,
    )


def run_workspace_build_with_options(
    paths: AppPaths,
    log_path: Path,
    packages: list[str] | None = None,
    run_rosdep_step: bool = True,
    install_python_deps: bool = True,
    run_colcon_build: bool = True,
) -> CommandResult:
    package_clause = ""
    if packages:
        package_clause = " --packages-select " + " ".join(packages)
    script_lines = [
        "set -e",
        ros_prefix(paths, use_workspace=False),
        f"cd {shell_quote(paths.workspace_src_dir)}",
    ]
    if run_rosdep_step:
        script_lines.append("rosdep install -r -y -i --from-paths .")
    if install_python_deps:
        script_lines.append(
            "python3 -m pip install --break-system-packages mujoco torch torchvision torchaudio onnxruntime"
        )
    if run_colcon_build:
        script_lines.extend(
            [
                f"cd {shell_quote(paths.workspace_dir)}",
                f"colcon build --symlink-install{package_clause}",
            ]
        )
    script = "\n".join(script_lines)
    return run_bash(script, cwd=paths.workspace_dir, log_path=log_path, interactive=True)


def run_onnx_self_test(paths: AppPaths, log_path: Path) -> CommandResult:
    script = "\n".join(
        [
            "set -e",
            ros_prefix(paths),
            f"cd {shell_quote(paths.upstream_dir)}",
            "python3 -m mujina_control.mujina_utils.mujina_onnx",
        ]
    )
    return run_bash(script, cwd=paths.workspace_dir, log_path=log_path, interactive=False)


def run_real_device_setup(paths: AppPaths, log_path: Path) -> CommandResult:
    rules_path = paths.upstream_dir / "mujina" / "config" / "90-mujina.rules"
    script = "\n".join(
        [
            "set -e",
            'sudo usermod -aG dialout "$USER"',
            f"sudo cp {shell_quote(rules_path)} /etc/udev/rules.d/90-mujina.rules",
            "sudo udevadm control --reload",
            "sudo udevadm trigger",
        ]
    )
    return run_bash(script, cwd=paths.workspace_dir, log_path=log_path, interactive=True)
