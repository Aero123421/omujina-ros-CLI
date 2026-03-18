from __future__ import annotations

import hashlib
import json
import os
import shutil
from pathlib import Path

from mujina_assist.models import AppPaths, DoctorReport, RuntimeState

try:
    import grp
except ImportError:  # pragma: no cover - only happens on non-Unix hosts
    grp = None


def read_os_release() -> dict[str, str]:
    path = Path("/etc/os-release")
    data: dict[str, str] = {}
    if not path.exists():
        return data
    for line in path.read_text(encoding="utf-8").splitlines():
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        data[key] = value.strip().strip('"')
    return data


def file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def current_policy_label(paths: AppPaths, state: RuntimeState) -> str:
    source_policy = paths.source_policy_path
    if not source_policy.exists():
        return "未設定"
    current_hash = file_hash(source_policy)
    if state.active_policy_hash and current_hash == state.active_policy_hash:
        return state.active_policy_label
    if paths.default_policy_cache.exists():
        default_hash = file_hash(paths.default_policy_cache)
        if current_hash == default_hash:
            return "公式デフォルト"
    return "カスタムまたは未同期"


def detect_real_devices() -> dict[str, bool]:
    return {
        "/dev/rt_usb_imu": Path("/dev/rt_usb_imu").exists(),
        "/dev/usb_can": Path("/dev/usb_can").exists(),
        "can0": Path("/sys/class/net/can0").exists(),
        "/dev/input/js0": Path("/dev/input/js0").exists(),
    }


def real_setup_status() -> dict[str, bool]:
    in_dialout = False
    try:
        if grp is not None:
            group_names = {grp.getgrgid(gid).gr_name for gid in os.getgroups()}
            in_dialout = "dialout" in group_names
    except Exception:
        in_dialout = False
    return {
        "dialout": in_dialout,
        "udev_rule": Path("/etc/udev/rules.d/90-mujina.rules").exists(),
    }


def count_usb_policies() -> int:
    user = Path.home().name
    roots = [Path("/media") / user, Path("/run/media") / user]
    count = 0
    for root in roots:
        if not root.exists():
            continue
        for mounted in root.iterdir():
            if mounted.is_dir():
                count += len(list(mounted.rglob("*.onnx")))
    return count


def build_doctor_report(paths: AppPaths, state: RuntimeState) -> DoctorReport:
    os_release = read_os_release()
    os_label = (
        f"{os_release.get('PRETTY_NAME', os_release.get('NAME', '不明なOS'))}"
        if os_release
        else os.name
    )
    ubuntu_24_04 = os_release.get("VERSION_ID") == "24.04"
    ros_installed = Path("/opt/ros/jazzy/setup.bash").exists()
    workspace_cloned = paths.upstream_dir.exists()
    workspace_built = (paths.workspace_dir / "install" / "setup.bash").exists()
    usb_policy_count = count_usb_policies()
    active_policy = current_policy_label(paths, state)
    devices = detect_real_devices()
    real_setup = real_setup_status()
    tool_status = {
        "git": command_exists("git"),
        "bash": command_exists("bash"),
        "tmux": command_exists("tmux"),
        "colcon": command_exists("colcon"),
        "rosdep": command_exists("rosdep"),
    }
    notes: list[str] = []

    recommendation = "まずは「初回セットアップ」を実行してください。"
    if not ubuntu_24_04:
        recommendation = "Ubuntu 24.04 上で実行してください。"
        notes.append("Ubuntu 24.04 以外では公式手順どおりに進まない可能性があります。")
    elif not ros_installed:
        recommendation = "ROS 2 Jazzy の導入が必要です。"
        notes.append("/opt/ros/jazzy/setup.bash が見つかっていません。")
    elif not workspace_cloned:
        recommendation = "mujina_ros を clone してください。"
        notes.append("workspace/src/mujina_ros は初回セットアップ時に自動作成されます。")
    elif not workspace_built:
        recommendation = "build を実行してください。"
        notes.append("install/setup.bash が無いため ROS パッケージはまだ見えません。")
    elif usb_policy_count > 0:
        recommendation = "USB 上の ONNX を切り替え可能です。"
    elif devices["/dev/rt_usb_imu"] and devices["/dev/input/js0"] and (devices["can0"] or devices["/dev/usb_can"]):
        recommendation = "実機起動前チェックは良好です。"
    else:
        recommendation = "可視化または SIM から試すのがおすすめです。"
    if workspace_built and not tool_status["tmux"]:
        notes.append("tmux が無い場合、SIM や実機は単独プロセス起動になります。")
    if state.last_action == "policy_switch" and not state.last_sim_success:
        notes.append("policy を切り替えた直後です。まずは SIM と ONNX テストで確認してください。")
    if workspace_cloned and (not real_setup["dialout"] or not real_setup["udev_rule"]):
        notes.append("実機を使う場合は dialout 追加と 90-mujina.rules の配置が必要です。")
    if real_setup["dialout"] and not real_setup["udev_rule"]:
        notes.append("/etc/udev/rules.d/90-mujina.rules がまだ入っていません。")

    return DoctorReport(
        os_label=os_label,
        ubuntu_24_04=ubuntu_24_04,
        ros_installed=ros_installed,
        workspace_cloned=workspace_cloned,
        workspace_built=workspace_built,
        active_policy_label=active_policy,
        usb_policy_count=usb_policy_count,
        real_devices=devices,
        tool_status=tool_status,
        notes=notes,
        recommendation=recommendation,
    )


def write_config_file(paths: AppPaths) -> None:
    if paths.config_file.exists():
        return
    config = {
        "workspace": str(paths.workspace_dir),
        "upstream_repository": "https://github.com/rt-net/mujina_ros.git",
    }
    with paths.config_file.open("w", encoding="utf-8") as handle:
        json.dump(config, handle, indent=2, ensure_ascii=False)


def command_exists(name: str) -> bool:
    return shutil.which(name) is not None
