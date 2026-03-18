from __future__ import annotations

import shutil
from pathlib import Path

from mujina_assist.models import AppPaths
from mujina_assist.services.checks import workspace_clone_ready
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
    if workspace_clone_ready(paths):
        return CommandResult(command="git clone", returncode=0, stdout="already cloned")
    if paths.upstream_dir.exists():
        shutil.rmtree(paths.upstream_dir, ignore_errors=True)
    script = f"git clone {UPSTREAM_REPO} {shell_quote(paths.upstream_dir)}"
    return run_bash(script, cwd=paths.repo_root, log_path=log_path, interactive=True)


def capture_default_policy(paths: AppPaths) -> None:
    if paths.default_policy_cache.exists():
        return
    source = paths.source_policy_path
    if source.exists():
        paths.default_policy_cache.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, paths.default_policy_cache)


def build_initial_setup_script(skip_upgrade: bool = False) -> str:
    setup_lines = [
        "set -e",
        "export DEBIAN_FRONTEND=noninteractive",
        "sudo apt update",
        "sudo apt install -y software-properties-common curl git python3-pip python3-venv lsb-release tmux",
        "sudo add-apt-repository -y universe",
        "sudo apt update && sudo apt install -y curl",
        "ROS_APT_SOURCE_REDIRECT=$(curl -fsSLI https://github.com/ros-infrastructure/ros-apt-source/releases/latest | awk 'BEGIN { IGNORECASE = 1 } /^location:/ { print $2 }' | tail -n 1 | tr -d '\\r')",
        "ROS_APT_SOURCE_VERSION=${ROS_APT_SOURCE_REDIRECT##*/}",
        "if [ -z \"$ROS_APT_SOURCE_VERSION\" ]; then echo 'Failed to resolve ros2-apt-source release version.' >&2; exit 1; fi",
        "curl -fL -o /tmp/ros2-apt-source.deb \"https://github.com/ros-infrastructure/ros-apt-source/releases/download/${ROS_APT_SOURCE_VERSION}/ros2-apt-source_${ROS_APT_SOURCE_VERSION}.$(. /etc/os-release && echo ${UBUNTU_CODENAME:-${VERSION_CODENAME}})_all.deb\"",
        "dpkg-deb --info /tmp/ros2-apt-source.deb >/dev/null",
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
    return "\n".join(setup_lines)


def run_initial_setup(paths: AppPaths, log_path: Path, skip_upgrade: bool = False) -> CommandResult:
    return run_bash(build_initial_setup_script(skip_upgrade=skip_upgrade), cwd=paths.repo_root, log_path=log_path, interactive=True)


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
    script = build_workspace_script(
        paths,
        packages=packages,
        run_rosdep_step=run_rosdep_step,
        install_python_deps=install_python_deps,
        run_colcon_build=run_colcon_build,
    )
    return run_bash(script, cwd=paths.workspace_dir, log_path=log_path, interactive=True)


def build_workspace_script(
    paths: AppPaths,
    *,
    packages: list[str] | None = None,
    run_rosdep_step: bool = True,
    install_python_deps: bool = True,
    run_colcon_build: bool = True,
) -> str:
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
        script_lines.extend(
            [
                "python3 -m pip install --break-system-packages mujoco onnxruntime",
                "python3 -m pip install --break-system-packages --index-url https://download.pytorch.org/whl/cpu torch torchvision torchaudio",
            ]
        )
    if run_colcon_build:
        script_lines.extend(
            [
                f"cd {shell_quote(paths.workspace_dir)}",
                f"colcon build --symlink-install{package_clause}",
            ]
        )
    return "\n".join(script_lines)


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
