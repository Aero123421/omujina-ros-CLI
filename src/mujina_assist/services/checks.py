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


def sim_policy_verified(state: RuntimeState) -> bool:
    return bool(
        state.last_sim_success
        and state.active_policy_hash
        and state.last_sim_policy_hash == state.active_policy_hash
    )


def detect_real_devices() -> dict[str, bool]:
    return {
        "/dev/rt_usb_imu": Path("/dev/rt_usb_imu").exists(),
        "/dev/usb_can": Path("/dev/usb_can").exists(),
        "can0": Path("/sys/class/net/can0").exists(),
        "/dev/input/js0": Path("/dev/input/js0").exists(),
    }


def list_serial_device_candidates(limit: int = 8) -> list[str]:
    candidates: list[str] = []
    for pattern in ("ttyUSB*", "ttyACM*"):
        for path in sorted(Path("/dev").glob(pattern)):
            candidates.append(str(path))
    serial_by_id_dir = Path("/dev/serial/by-id")
    if serial_by_id_dir.exists():
        for path in sorted(serial_by_id_dir.iterdir()):
            candidates.append(str(path))

    deduped: list[str] = []
    seen: set[str] = set()
    for candidate in candidates:
        if candidate in seen:
            continue
        seen.add(candidate)
        deduped.append(candidate)
    return deduped[:limit]


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


def workspace_clone_ready(paths: AppPaths) -> bool:
    return (
        paths.upstream_dir.exists()
        and (paths.upstream_dir / ".git").exists()
        and (paths.upstream_dir / "mujina_control").exists()
        and (paths.upstream_dir / "mujina_description").exists()
    )


def workspace_build_ready(paths: AppPaths) -> bool:
    return (
        (paths.workspace_dir / "install" / "setup.bash").exists()
        and (paths.workspace_dir / "install" / "mujina_control").exists()
    )


def build_doctor_report(paths: AppPaths, state: RuntimeState) -> DoctorReport:
    os_release = read_os_release()
    os_label = (
        f"{os_release.get('PRETTY_NAME', os_release.get('NAME', '不明なOS'))}"
        if os_release
        else os.name
    )
    ubuntu_24_04 = os_release.get("VERSION_ID") == "24.04"
    ros_installed = Path("/opt/ros/jazzy/setup.bash").exists()
    workspace_cloned = workspace_clone_ready(paths)
    workspace_built = workspace_build_ready(paths)
    usb_policy_count = count_usb_policies()
    active_policy = current_policy_label(paths, state)
    sim_ready = sim_policy_verified(state)
    devices = detect_real_devices()
    serial_candidates = list_serial_device_candidates()
    real_setup = real_setup_status()
    tool_status = {
        "git": command_exists("git"),
        "bash": command_exists("bash"),
        "terminal": graphical_terminal_available(),
        "tmux": command_exists("tmux"),
        "colcon": command_exists("colcon"),
        "rosdep": command_exists("rosdep"),
        "slcand": command_exists("slcand"),
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
    elif sim_ready:
        recommendation = "今の policy は SIM 確認済みです。実機前診断へ進めます。"
    elif devices["/dev/rt_usb_imu"] and devices["/dev/input/js0"] and (devices["can0"] or devices["/dev/usb_can"]):
        recommendation = "実機起動前チェックは良好です。"
    else:
        recommendation = "可視化または SIM から試すのがおすすめです。"
    if workspace_built and not tool_status["terminal"] and not tool_status["tmux"]:
        notes.append("GUI ターミナルも tmux も無いため、別ウィンドウ起動ができません。どちらかを導入してください。")
    elif workspace_built and not tool_status["terminal"] and tool_status["tmux"]:
        notes.append("GUI ターミナルが無い環境では tmux をフォールバックに使います。")
    if sim_ready:
        notes.append("現在の active policy は SIM 確認済みとして記録されています。")
    if state.last_action == "policy_switch" and not sim_ready:
        notes.append("policy を切り替えた直後です。まずは SIM と ONNX テストで確認してください。")
    if state.real_setup_requires_relogin and real_setup["udev_rule"] and not real_setup["dialout"]:
        notes.append("実機用設定は入りましたが、dialout を反映するには一度ログアウトして再ログインしてください。")
    elif workspace_cloned and (not real_setup["dialout"] or not real_setup["udev_rule"]):
        notes.append("実機を使う場合は dialout 追加と 90-mujina.rules の配置が必要です。")
    if real_setup["dialout"] and not real_setup["udev_rule"]:
        notes.append("/etc/udev/rules.d/90-mujina.rules がまだ入っていません。")
    if serial_candidates and (not devices["/dev/usb_can"] or not devices["/dev/rt_usb_imu"]):
        notes.append(
            "汎用 USB シリアル候補は見えていますが、期待する固定名デバイスが不足しています。"
            " 候補が IMU / USB-CAN のどちらかはこの CLI だけでは断定できません。"
        )
    if devices["/dev/usb_can"] and not tool_status["slcand"]:
        notes.append("serial CAN を使う構成では `slcand` が必要です。見つからない場合は `can-utils` を導入してください。")

    return DoctorReport(
        os_label=os_label,
        ubuntu_24_04=ubuntu_24_04,
        ros_installed=ros_installed,
        workspace_cloned=workspace_cloned,
        workspace_built=workspace_built,
        active_policy_label=active_policy,
        usb_policy_count=usb_policy_count,
        sim_ready=sim_ready,
        real_devices=devices,
        serial_candidates=serial_candidates,
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


def graphical_terminal_available() -> bool:
    return any(
        command_exists(name)
        for name in ("gnome-terminal", "mate-terminal", "konsole", "xfce4-terminal", "x-terminal-emulator")
    )
