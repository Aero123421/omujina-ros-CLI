from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
from pathlib import Path

from mujina_assist.models import AppPaths, DoctorCheck, DoctorReport, RuntimeState


def read_os_release() -> dict[str, str]:
    path = Path("/etc/os-release")
    data: dict[str, str] = {}
    if not path.exists():
        return data
    for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        data[key] = value.strip().strip('"')
    return data


def file_hash(path: Path) -> str:
    if not path.exists():
        return ""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def current_policy_label(paths: AppPaths, state: RuntimeState) -> str:
    if not paths.source_policy_path.exists():
        return "未設定"
    current_hash = file_hash(paths.source_policy_path)
    if paths.default_policy_cache.exists() and current_hash == file_hash(paths.default_policy_cache):
        return "公式デフォルト"
    if state.active_policy_hash and state.active_policy_hash == current_hash and state.active_policy_label:
        return state.active_policy_label
    return f"現在の policy ({paths.source_policy_path.name})"


def sim_policy_verified(state: RuntimeState) -> bool:
    return bool(state.last_sim_success and state.last_sim_policy_hash and state.last_sim_policy_hash == state.active_policy_hash)


def detect_real_devices() -> dict[str, bool]:
    return {
        "/dev/rt_usb_imu": Path("/dev/rt_usb_imu").exists(),
        "/dev/usb_can": Path("/dev/usb_can").exists(),
        "/dev/input/js0": Path("/dev/input/js0").exists(),
        "can0": Path("/sys/class/net/can0").exists(),
    }


def list_serial_device_candidates() -> list[str]:
    candidates: list[str] = []
    for pattern in ("/dev/ttyUSB*", "/dev/ttyACM*", "/dev/serial/by-id/*"):
        for path in sorted(Path("/").glob(pattern.lstrip("/"))):
            candidates.append(str(path))
    seen: set[str] = set()
    unique: list[str] = []
    for candidate in candidates:
        if candidate not in seen:
            seen.add(candidate)
            unique.append(candidate)
    return unique


def resolve_imu_port() -> tuple[str | None, bool, list[str]]:
    fixed = Path("/dev/rt_usb_imu")
    candidates = list_serial_device_candidates()
    if fixed.exists():
        return str(fixed), False, candidates
    generic = [candidate for candidate in candidates if "/dev/ttyUSB" in candidate or "/dev/ttyACM" in candidate]
    if len(generic) == 1:
        return generic[0], True, candidates
    return None, False, candidates


def real_setup_status() -> dict[str, bool]:
    dialout = False
    try:
        if os.name != "nt":
            groups = subprocess.run(["id", "-nG"], text=True, capture_output=True, check=False)
            dialout = "dialout" in (groups.stdout or "").split()
    except Exception:
        dialout = False
    return {
        "dialout": dialout,
        "udev_rule": Path("/etc/udev/rules.d/90-mujina.rules").exists(),
    }


def count_usb_policies() -> int:
    user = Path.home().name
    total = 0
    for root in (Path("/media") / user, Path("/run/media") / user):
        if not root.exists():
            continue
        total += len(list(root.rglob("*.onnx")))
    return total


def workspace_clone_ready(paths: AppPaths) -> bool:
    return (paths.upstream_dir / ".git").exists()


def workspace_build_ready(paths: AppPaths) -> bool:
    return (paths.workspace_dir / "install" / "setup.bash").exists() and (paths.workspace_dir / "install" / "mujina_control").exists()


def command_exists(name: str) -> bool:
    return shutil.which(name) is not None


def graphical_terminal_available() -> bool:
    return bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))


def write_config_file(paths: AppPaths) -> None:
    if paths.config_file.exists():
        return
    payload = {
        "repo_root": str(paths.repo_root),
        "workspace_dir": str(paths.workspace_dir),
        "upstream_dir": str(paths.upstream_dir),
    }
    paths.config_file.parent.mkdir(parents=True, exist_ok=True)
    paths.config_file.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")


def inspect_can_status() -> dict[str, object]:
    status: dict[str, object] = {
        "present": Path("/sys/class/net/can0").exists(),
        "operstate": "missing",
        "controller_state": "",
        "txqueuelen": None,
        "raw": "",
        "ok": False,
        "warn": False,
    }
    if not status["present"]:
        return status
    operstate_path = Path("/sys/class/net/can0/operstate")
    if operstate_path.exists():
        status["operstate"] = operstate_path.read_text(encoding="utf-8", errors="ignore").strip()
    if command_exists("ip"):
        result = subprocess.run(
            ["ip", "-details", "-statistics", "link", "show", "can0"],
            text=True,
            capture_output=True,
            check=False,
        )
        raw = (result.stdout or "") + (result.stderr or "")
        status["raw"] = raw.strip()
        for line in raw.splitlines():
            stripped = line.strip()
            if "can state " in stripped:
                controller_state = stripped.split("can state ", 1)[1].split()[0]
                status["controller_state"] = controller_state
            if "qlen " in stripped:
                try:
                    status["txqueuelen"] = int(stripped.split("qlen ", 1)[1].split()[0])
                except Exception:
                    pass
    controller_state = str(status["controller_state"] or "").lower()
    operstate = str(status["operstate"] or "").lower()
    if operstate in {"up", "unknown"} and controller_state not in {"stopped", "bus-off", "error-passive", "error-warning"}:
        status["ok"] = True
    elif status["present"]:
        status["warn"] = True
    return status


def build_doctor_report(paths: AppPaths, state: RuntimeState) -> DoctorReport:
    os_release = read_os_release()
    os_label = os_release.get("PRETTY_NAME", "不明")
    ubuntu_24_04 = os_release.get("VERSION_ID") == "24.04"
    ros_installed = Path("/opt/ros/jazzy/setup.bash").exists()
    workspace_cloned = workspace_clone_ready(paths)
    workspace_built = workspace_build_ready(paths)
    devices = detect_real_devices()
    setup_status = real_setup_status()
    serial_candidates = list_serial_device_candidates()
    imu_port, imu_fallback, _ = resolve_imu_port()
    can_status = inspect_can_status()

    active_policy_hash = file_hash(paths.source_policy_path) if paths.source_policy_path.exists() else ""
    active_policy_label = current_policy_label(paths, state)
    active_policy_source = state.active_policy_source
    if not active_policy_source and paths.source_policy_path.exists():
        active_policy_source = str(paths.source_policy_path)

    policy_cache_count = 0
    policy_cache_size_bytes = 0
    if paths.policy_index_file.exists():
        try:
            entries = json.loads(paths.policy_index_file.read_text(encoding="utf-8")).get("entries", [])
            policy_cache_count = len(entries)
            policy_cache_size_bytes = sum(int(entry.get("size_bytes", 0)) for entry in entries)
        except Exception:
            policy_cache_count = 0
            policy_cache_size_bytes = 0

    tool_status = {
        "git": command_exists("git"),
        "bash": command_exists("bash"),
        "terminal": graphical_terminal_available(),
        "tmux": command_exists("tmux"),
        "colcon": command_exists("colcon"),
        "rosdep": command_exists("rosdep"),
        "slcand": command_exists("slcand"),
    }

    checks: list[DoctorCheck] = [
        DoctorCheck("os", "Ubuntu 24.04", "ok" if ubuntu_24_04 else "warn", os_label),
        DoctorCheck("ros", "ROS 2 Jazzy", "ok" if ros_installed else "ng", "導入済み" if ros_installed else "未導入"),
        DoctorCheck("workspace", "workspace", "ok" if workspace_cloned else "ng", "clone 済み" if workspace_cloned else "未作成"),
        DoctorCheck("build", "ビルド", "ok" if workspace_built else "warn", "完了" if workspace_built else "未実行"),
        DoctorCheck(
            "policy",
            "現在の policy",
            "ok" if active_policy_hash else "warn",
            active_policy_label,
            details=[active_policy_source] if active_policy_source else [],
        ),
        DoctorCheck(
            "sim",
            "SIM確認",
            "ok" if sim_policy_verified(state) else "warn",
            "確認済み" if sim_policy_verified(state) else "未確認",
        ),
        DoctorCheck(
            "imu",
            "IMU",
            "ok" if imu_port and not imu_fallback else "warn",
            imu_port or "未検出",
            details=["固定名が無いため代替ポートを使用しています。"] if imu_fallback and imu_port else [],
        ),
        DoctorCheck(
            "can",
            "CAN",
            "ok" if can_status["ok"] else ("warn" if devices.get("can0", False) or devices.get("/dev/usb_can", False) else "warn"),
            _can_summary(devices, can_status),
        ),
        DoctorCheck(
            "joy",
            "ゲームパッド",
            "ok" if devices.get("/dev/input/js0", False) else "warn",
            "接続済み" if devices.get("/dev/input/js0", False) else "未接続",
        ),
        DoctorCheck(
            "real_setup",
            "実機用設定",
            "ok" if setup_status["dialout"] and setup_status["udev_rule"] else "warn",
            _real_setup_summary(setup_status),
        ),
    ]

    notes: list[str] = []
    if not sim_policy_verified(state):
        notes.append("現在の policy はまだ SIM 確認済みとして記録されていません。")
    if imu_fallback and imu_port:
        notes.append(f"IMU は固定名ではなく代替ポート {imu_port} を使う見込みです。")
    if serial_candidates and (not devices.get("/dev/usb_can", False) or not devices.get("/dev/rt_usb_imu", False)):
        notes.append("汎用 USB シリアル候補は見えています。固定名デバイスが出ない場合は udev ルールと VID/PID を確認してください。")
    if can_status["present"] and not can_status["ok"]:
        notes.append("can0 は見えていますが、現在の状態は健全ではありません。電源再投入後は公式の can_setup 手順をやり直してください。")

    recommendation = "まず `保守・診断` で不足項目を潰してから進めてください。"
    if (
        workspace_built
        and sim_policy_verified(state)
        and devices.get("/dev/input/js0", False)
        and imu_port
        and not imu_fallback
        and (devices.get("can0", False) or devices.get("/dev/usb_can", False))
    ):
        recommendation = "実機前チェックは概ね整っています。所定姿勢を確認してから進めてください。"
    elif workspace_built and active_policy_hash:
        recommendation = "次は SIM を起動し、姿勢と入力応答を確認するのがおすすめです。"
    elif workspace_cloned:
        recommendation = "次は `ビルドする` を実行して workspace を完成させてください。"
    else:
        recommendation = "まずは `初回セットアップ` を完了させてください。"

    return DoctorReport(
        os_label=os_label,
        ubuntu_24_04=ubuntu_24_04,
        ros_installed=ros_installed,
        workspace_cloned=workspace_cloned,
        workspace_built=workspace_built,
        active_policy_label=active_policy_label,
        active_policy_source=active_policy_source,
        active_policy_hash=active_policy_hash,
        usb_policy_count=count_usb_policies(),
        sim_ready=sim_policy_verified(state),
        sim_verified_at=state.last_sim_verified_at,
        real_devices=devices,
        serial_candidates=serial_candidates,
        imu_port_label=imu_port or "",
        imu_port_fallback=imu_fallback,
        tool_status=tool_status,
        notes=notes,
        recommendation=recommendation,
        checks=checks,
        policy_cache_count=policy_cache_count,
        policy_cache_size_bytes=policy_cache_size_bytes,
    )


def _real_setup_summary(setup_status: dict[str, bool]) -> str:
    parts: list[str] = []
    parts.append("dialout 済み" if setup_status["dialout"] else "dialout 未設定")
    parts.append("udev ルールあり" if setup_status["udev_rule"] else "udev ルール未配置")
    return " / ".join(parts)


def _can_summary(devices: dict[str, bool], can_status: dict[str, object]) -> str:
    if devices.get("can0", False):
        operstate = can_status.get("operstate") or "unknown"
        controller_state = can_status.get("controller_state") or "unknown"
        txq = can_status.get("txqueuelen")
        suffix = f" / qlen={txq}" if isinstance(txq, int) else ""
        return f"can0: operstate={operstate}, controller={controller_state}{suffix}"
    if devices.get("/dev/usb_can", False):
        return "/dev/usb_can が見えています"
    return "CAN デバイスが未検出です"
