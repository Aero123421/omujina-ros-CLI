from __future__ import annotations

import argparse
import time
import traceback
from collections import deque
from datetime import datetime
from pathlib import Path
from uuid import uuid4

from mujina_assist.models import DEFAULT_MOTOR_IDS, AppPaths, JobRecord, PolicyCandidate
from mujina_assist.services.checks import (
    build_doctor_report,
    current_policy_label,
    detect_real_devices,
    inspect_can_status,
    list_serial_device_candidates,
    real_setup_status,
    resolve_imu_port,
    workspace_signature,
    workspace_build_ready,
    workspace_clone_ready,
    write_config_file,
)
from mujina_assist.services.jobs import (
    acquire_job_claim,
    active_jobs,
    create_job,
    job_log_path,
    list_jobs,
    load_job,
    mark_job_finished,
    mark_job_running,
    mark_job_stopped,
    recent_jobs,
    release_job_claim,
    summarize_job,
    update_job,
)
from mujina_assist.services.policy import (
    activate_policy,
    all_policy_candidates,
    cleanup_policy_cache,
    import_policy_to_cache,
)
from mujina_assist.services.processes import (
    build_joy_script,
    build_motor_probe_script,
    build_motor_read_script,
    build_real_imu_script,
    build_real_main_script,
    build_sim_main_script,
    build_viz_script,
    build_zero_script,
)
from mujina_assist.services.shell import run_bash
from mujina_assist.services.state import load_runtime_state, save_runtime_state
from mujina_assist.services.terminals import launch_job, stop_job_launch
from mujina_assist.services.workspace import (
    capture_default_policy,
    ensure_upstream_clone,
    run_initial_setup,
    run_onnx_self_test,
    run_real_device_setup,
    run_workspace_build,
    run_workspace_dependency_setup,
)
from mujina_assist.ui import (
    ask_text,
    ask_yes_no,
    bullet,
    error,
    pause,
    section,
    select_from_list,
    success,
    title,
    warn,
)

WORKER_CLAIM_TIMEOUT_SECONDS = 5.0
WORKER_CLAIM_POLL_SECONDS = 0.1


class MujinaAssistApp:
    def __init__(self, repo_root: Path) -> None:
        self.paths = AppPaths.from_repo_root(repo_root)
        self.paths.ensure_directories()
        write_config_file(self.paths)
        self.state = load_runtime_state(self.paths.runtime_state_file)
        if not self.state.active_policy_label:
            self.state.active_policy_label = current_policy_label(self.paths, self.state)

    def save_state(self) -> None:
        save_runtime_state(self.paths.runtime_state_file, self.state)

    def _clear_manual_recovery_state(self, *, kind: str | None = None) -> None:
        if kind is not None and self.state.manual_recovery_kind not in {"", kind}:
            return
        self.state.manual_recovery_required = False
        self.state.manual_recovery_kind = ""
        self.state.manual_recovery_summary = ""

    def _set_manual_recovery_state(self, *, kind: str, summary: str) -> None:
        self.state.manual_recovery_required = True
        self.state.manual_recovery_kind = kind
        self.state.manual_recovery_summary = summary

    def _current_workspace_signature(self) -> str:
        return workspace_signature(self.paths)

    def _sync_relogin_requirement(self) -> None:
        if not self.state.real_setup_requires_relogin:
            return
        setup_status = real_setup_status()
        if setup_status.get("dialout") and setup_status.get("udev_rule"):
            self.state.real_setup_requires_relogin = False
            self.save_state()

    def print_status(self) -> None:
        self._sync_relogin_requirement()
        report = build_doctor_report(self.paths, self.state)
        title("Mujina Assist")
        section("現在の状態")
        bullet(f"OS: {report.os_label}")
        bullet(f"Ubuntu 24.04: {'OK' if report.ubuntu_24_04 else '未確認'}")
        bullet(f"ROS 2 Jazzy: {'OK' if report.ros_installed else '未導入'}")
        bullet(f"workspace: {'OK' if report.workspace_cloned else '未作成'}")
        bullet(f"build: {'OK' if report.workspace_built else '未実行'}")
        bullet(f"現在の policy: {report.active_policy_label}")
        if report.active_policy_source:
            bullet(f"policy の由来: {report.active_policy_source}")
        bullet(f"SIM確認済み: {'OK' if report.sim_ready else '未確認'}")
        if report.sim_verified_at:
            bullet(f"最後にSIM確認した時刻: {report.sim_verified_at}")
        bullet(f"USB上のONNX: {report.usb_policy_count} 件")
        bullet(
            "policy キャッシュ: "
            f"{report.policy_cache_count} 件 / {report.policy_cache_size_bytes / (1024 * 1024):.1f} MB"
        )
        if report.imu_port_label:
            suffix = "（代替ポート）" if report.imu_port_fallback else ""
            bullet(f"IMUポート: {report.imu_port_label}{suffix}")
        if report.real_devices:
            devices = ", ".join(f"{name}={'OK' if ok else 'NG'}" for name, ok in report.real_devices.items())
            bullet(f"実機デバイス: {devices}")
        if report.tool_status:
            tools = ", ".join(f"{name}={'OK' if ok else 'NG'}" for name, ok in report.tool_status.items())
            bullet(f"主要ツール: {tools}")
        if report.serial_candidates:
            bullet("USBシリアル候補: " + ", ".join(report.serial_candidates[:4]))

        running_jobs = active_jobs(self.paths)
        if running_jobs:
            section("実行中ジョブ")
            for job in running_jobs[:8]:
                bullet(f"{job.name} | ログ: {Path(job.log_path).name}")

        completed_jobs = [job for job in recent_jobs(self.paths, limit=8) if job.status not in {"queued", "running"}]
        if completed_jobs:
            section("最近の完了ジョブ")
            for job in completed_jobs[:5]:
                bullet(summarize_job(job))

        section("おすすめ")
        bullet(report.recommendation or "まずは `保守・診断` から状態確認をしてください。")
        if self.state.real_setup_requires_relogin:
            section("要確認")
            bullet("dialout / udev の設定を反映した直後です。いったんログアウト / ログインしてから実機系を進めてください。")
        if self.state.manual_recovery_required and self.state.manual_recovery_summary:
            section("手動復旧が必要")
            bullet(self.state.manual_recovery_summary)
        if report.notes:
            section("補足")
            for note in report.notes:
                bullet(note)

    def run_menu(self) -> int:
        while True:
            self.print_status()
            section("メインメニュー")
            choice = select_from_list(
                "進めたい作業を選んでください。",
                [
                    "おすすめの流れ",
                    "実機操作",
                    "policy 管理",
                    "保守・診断",
                    "ログを見る",
                    "終了",
                ],
            )
            print()
            if choice == 0:
                self._run_guided_menu()
            elif choice == 1:
                self._run_robot_menu()
            elif choice == 2:
                self._run_policy_menu()
            elif choice == 3:
                self._run_diagnostics_menu()
            elif choice == 4:
                self.handle_logs()
                pause()
            else:
                return 0

    def _run_guided_menu(self) -> None:
        while True:
            title("おすすめの流れ")
            choice = select_from_list(
                "公式手順に沿って進めます。",
                [
                    "初回セットアップ",
                    "ビルドする",
                    "SIMで確認する",
                    "SIM確認済みにする",
                    "実機前診断",
                    "実機を起動する",
                ],
                allow_back=True,
            )
            if choice is None:
                return
            if choice == 0:
                self.handle_setup()
            elif choice == 1:
                self.handle_build()
            elif choice == 2:
                self.handle_sim()
            elif choice == 3:
                self.handle_mark_sim_verified()
            elif choice == 4:
                self.handle_preflight()
            elif choice == 5:
                self.handle_real_robot()
            pause()

    def _run_robot_menu(self) -> None:
        while True:
            title("実機操作")
            choice = select_from_list(
                "モータ確認や実機起動をここから行います。",
                [
                    "可視化する",
                    "モータを確認する",
                    "モータを自動診断する",
                    "ロボットを自動診断する",
                    "原点位置を設定する",
                    "実機を起動する",
                ],
                allow_back=True,
            )
            if choice is None:
                return
            if choice == 0:
                self.handle_viz()
            elif choice == 1:
                self.handle_motor_read()
            elif choice == 2:
                self.handle_motor_diagnostics()
            elif choice == 3:
                self.handle_robot_diagnostics()
            elif choice == 4:
                self.handle_zero_position()
            elif choice == 5:
                self.handle_real_robot()
            pause()

    def _run_policy_menu(self) -> None:
        while True:
            title("policy 管理")
            choice = select_from_list(
                "policy の切り替えと履歴整理を行います。",
                [
                    "現在の policy を確認する",
                    "policy を切り替える",
                    "ONNX 読み込みテストを行う",
                    "policy キャッシュを整理する",
                ],
                allow_back=True,
            )
            if choice is None:
                return
            if choice == 0:
                self.handle_doctor()
            elif choice == 1:
                self.handle_policy_menu()
            elif choice == 2:
                self.handle_policy_test()
            elif choice == 3:
                self.handle_policy_cache_cleanup()
            pause()

    def _run_diagnostics_menu(self) -> None:
        while True:
            title("保守・診断")
            choice = select_from_list(
                "状況確認と自動診断を行います。",
                [
                    "状態確認",
                    "実機前診断",
                    "モータ診断",
                    "ロボット診断",
                    "ジョブとログを見る",
                ],
                allow_back=True,
            )
            if choice is None:
                return
            if choice == 0:
                self.handle_doctor()
            elif choice == 1:
                self.handle_preflight()
            elif choice == 2:
                self.handle_motor_diagnostics()
            elif choice == 3:
                self.handle_robot_diagnostics()
            elif choice == 4:
                self.handle_logs()
            pause()

    def handle_doctor(self) -> int:
        self._sync_relogin_requirement()
        self.print_status()
        return 0

    def handle_preflight(self, can_mode: str = "auto") -> int:
        title("実機前診断")
        self._sync_relogin_requirement()
        if not self._require_built_workspace():
            return 1
        self._sync_default_policy_state()
        report = build_doctor_report(self.paths, self.state)
        selected_can_mode = self._diagnostic_can_mode(can_mode)
        missing = self._missing_devices_for_can_mode(selected_can_mode, include_imu=True, include_joy=True)

        section("診断結果")
        bullet(f"現在の policy: {report.active_policy_label}")
        bullet(f"SIM確認済み: {'OK' if report.sim_ready else '未確認'}")
        bullet(f"想定する CAN モード: {selected_can_mode}")
        if report.imu_port_label:
            suffix = "（代替ポート）" if report.imu_port_fallback else ""
            bullet(f"IMUポート: {report.imu_port_label}{suffix}")
        for check in report.checks:
            icon = {"ok": "OK", "warn": "WARN", "ng": "NG"}.get(check.status, check.status.upper())
            bullet(f"{check.label}: {icon} | {check.summary}")

        if missing:
            self._report_missing_devices(
                "実機起動に必要なデバイスがまだ揃っていません。",
                missing,
                can_mode=selected_can_mode,
                include_imu=True,
                include_joy=True,
            )
        else:
            success("必須デバイスの存在確認は通っています。")

        if report.notes:
            section("補足")
            for note in report.notes:
                bullet(note)
        return 0

    def handle_setup(self, skip_upgrade: bool = False) -> int:
        title("初回セットアップ")
        if not self._confirm_no_conflicting_jobs({"setup", "build", "policy_switch"}):
            return 1
        wants_real_setup = ask_yes_no("実機用の dialout / udev 設定も一緒に行いますか？", default=False)
        if not ask_yes_no("初回セットアップを開始しますか？", default=True):
            warn("初回セットアップを中止しました。")
            return 1
        job = create_job(
            self.paths,
            kind="setup",
            name="初回セットアップ",
            payload={"skip_upgrade": skip_upgrade, "setup_real_devices": wants_real_setup},
        )
        return self._launch_job(job)

    def handle_build(self) -> int:
        title("ビルド")
        if not self._require_cloned_workspace():
            return 1
        if not self._confirm_no_conflicting_jobs({"setup", "build", "policy_switch"}):
            return 1
        if not ask_yes_no("workspace をビルドしますか？", default=True):
            warn("ビルドを中止しました。")
            return 1
        job = create_job(self.paths, kind="build", name="workspace ビルド")
        return self._launch_job(job)

    def handle_viz(self) -> int:
        title("可視化")
        if not self._require_built_workspace():
            return 1
        if not self._confirm_no_conflicting_jobs({"viz"}):
            return 1
        if not ask_yes_no("RViz を起動しますか？", default=True):
            warn("可視化を中止しました。")
            return 1
        job = create_job(self.paths, kind="viz", name="RViz 可視化")
        return self._launch_job(job)

    def handle_sim(self) -> int:
        title("SIM")
        if not self._require_built_workspace():
            return 1
        if not self._confirm_no_conflicting_jobs({"sim_main", "sim_joy"}):
            return 1
        self._sync_default_policy_state()
        if not ask_yes_no("SIM を起動しますか？", default=True):
            warn("SIM 起動を中止しました。")
            return 1
        group_id = f"sim-{uuid4().hex[:8]}"
        sim_payload = {
            "policy_hash": self.state.active_policy_hash,
            "policy_label": self.state.active_policy_label,
            "workspace_signature": self._current_workspace_signature(),
        }
        jobs = [
            create_job(self.paths, kind="sim_main", name="SIM 本体", group_id=group_id, payload=dict(sim_payload)),
            create_job(self.paths, kind="sim_joy", name="SIM joy ノード", group_id=group_id, payload=dict(sim_payload)),
        ]
        result = self._launch_job_group(jobs, heading="SIM を起動しました。")
        if result == 0:
            self.state.last_action = "sim_launch"
            self.state.last_sim_success = False
            self.state.last_sim_policy_hash = ""
            self.save_state()
            warn("起動した別ターミナルで姿勢と入力応答を確認してから、`SIM確認済み` を付けてください。")
        return result

    def handle_mark_sim_verified(self) -> int:
        title("SIM確認済みにする")
        return self._mark_current_policy_sim_verified(ask_confirmation=True)

    def handle_real_robot(self, can_mode: str = "auto") -> int:
        title("実機起動")
        self._sync_relogin_requirement()
        if not self._require_built_workspace():
            return 1
        if self.state.real_setup_requires_relogin:
            error("dialout / udev の設定を反映した直後なので、先にログアウト / ログインしてください。")
            bullet("再ログイン後に `実機前診断` を実行し、デバイス状態を再確認してください。")
            return 1
        if not self._confirm_no_conflicting_jobs(
            {"motor_read", "zero", "real_main", "real_joy", "real_imu", "sim_main", "sim_joy"},
            allow_override=False,
        ):
            return 1
        self._sync_default_policy_state()
        selected_can_mode = self._select_can_mode(can_mode)
        if selected_can_mode is None:
            return 1
        report = build_doctor_report(self.paths, self.state)
        missing = self._missing_devices_for_can_mode(selected_can_mode, include_imu=True, include_joy=True)
        if selected_can_mode == "serial" and not report.tool_status.get("slcand", False):
            error("serial CAN を使うには `slcand` が必要です。")
            bullet("Ubuntu 24.04 なら通常 `sudo apt install -y can-utils` で入ります。")
            return 1
        policy_ready, policy_reason = self._active_policy_real_world_ready()
        if not policy_ready:
            error("現在の policy は provenance / 実機互換の確認が不足しています。")
            bullet(policy_reason)
            return 1
        if missing:
            self._report_missing_devices(
                "実機起動に必要なデバイスが足りません。",
                missing,
                can_mode=selected_can_mode,
                include_imu=True,
                include_joy=True,
            )
            return 1
        if not self._ensure_can_mode_ready(selected_can_mode):
            return 1
        imu_port = self._resolve_runtime_imu_port()
        if imu_port is None:
            error("IMU ポートを確定できませんでした。")
            bullet("`/dev/rt_usb_imu` が無い場合は、IMU として使うポートを手動で絞り込んでください。")
            return 1
        if not report.sim_ready:
            error("現在の workspace + policy では SIM確認済みの記録がありません。")
            bullet("先に `SIM` を起動し、同じ条件で姿勢と入力応答を確認してから `SIM確認済み` を付けてください。")
            return 1
        if not self._confirm_real_robot_safety_checklist():
            warn("実機起動前チェックを中止しました。")
            return 1
        typed = ask_text("本当に実機を起動する場合だけ `REAL` と入力してください。")
        if typed != "REAL":
            warn("実機起動を中止しました。")
            return 1
        group_id = f"real-{uuid4().hex[:8]}"
        jobs = [
            create_job(
                self.paths,
                kind="real_imu",
                name=f"実機 IMU ノード ({Path(imu_port).name})",
                group_id=group_id,
                payload={"imu_port": imu_port},
            ),
            create_job(
                self.paths,
                kind="real_main",
                name="実機 mujina_main",
                group_id=group_id,
                payload={"can_mode": selected_can_mode},
            ),
            create_job(self.paths, kind="real_joy", name="実機 joy ノード", group_id=group_id),
        ]
        result = self._launch_job_group(jobs, heading="実機用ジョブを起動しました。")
        if result == 0:
            self.state.last_action = "real_launch"
            self.save_state()
        return result

    def handle_policy_menu(self) -> int:
        title("policy を切り替える")
        if not self._require_built_workspace():
            return 1
        if not self._confirm_no_conflicting_jobs({"policy_switch", "build", "setup"}):
            return 1
        capture_default_policy(self.paths)
        candidates = all_policy_candidates(self.paths, self.state)
        options = [self._format_policy_option(candidate) for candidate in candidates]
        options.append("手元の ONNX ファイルを指定する")
        selected = select_from_list(
            "使いたい policy を選んでください。",
            options,
            allow_back=True,
        )
        if selected is None:
            return 1
        if selected == len(options) - 1:
            raw_path = Path(ask_text("ONNX ファイルの絶対パスを入力してください。"))
            if not raw_path.exists() or raw_path.suffix.lower() != ".onnx":
                error("有効な ONNX ファイルが見つかりませんでした。")
                return 1
            candidate = PolicyCandidate(
                label=f"手動指定: {raw_path.name}",
                path=raw_path,
                source_type="path",
                description=str(raw_path),
            )
        else:
            candidate = candidates[selected]
        self._show_policy_summary(candidate)
        if candidate.source_type in {"usb", "path"} and candidate.manifest_path is None:
            warn("この policy には manifest が無く、学習元や robot revision の手掛かりが不足しています。")
            bullet("実機投入前に、学習元 task・作成日時・対象 robot revision を別途確認してください。")
            if not ask_yes_no("それでもこの policy を候補として扱いますか？", default=False):
                warn("policy 切り替えを中止しました。")
                return 1
        if not ask_yes_no("この policy に切り替えますか？", default=True):
            warn("policy 切り替えを中止しました。")
            return 1
        prepared = self._prepare_candidate_for_job(candidate)
        job = create_job(
            self.paths,
            kind="policy_switch",
            name=f"policy 切替: {prepared.label}",
            payload=self._candidate_to_payload(prepared),
        )
        return self._launch_job(job)

    def handle_policy_test(self) -> int:
        title("ONNX 読み込みテスト")
        if not self._require_built_workspace():
            return 1
        if not self._confirm_no_conflicting_jobs({"policy_test"}):
            return 1
        if not ask_yes_no("現在の policy で ONNX 読み込みテストを行いますか？", default=True):
            warn("ONNX 読み込みテストを中止しました。")
            return 1
        job = create_job(self.paths, kind="policy_test", name="ONNX 読み込みテスト")
        return self._launch_job(job)

    def handle_policy_cache_cleanup(self) -> int:
        title("policy キャッシュ整理")
        dry_run = cleanup_policy_cache(self.paths, self.state, dry_run=True)
        bullet(f"削除候補: {dry_run['deleted_entries']} 件")
        bullet(f"削除予定容量: {dry_run['deleted_bytes'] / (1024 * 1024):.1f} MB")
        bullet(f"整理後の件数: {dry_run['remaining_entries']} 件")
        if dry_run["deleted_entries"] == 0:
            success("今のところ整理対象はありません。")
            return 0
        if not ask_yes_no("キャッシュ整理を実行しますか？", default=True):
            warn("キャッシュ整理を中止しました。")
            return 1
        result = cleanup_policy_cache(self.paths, self.state, dry_run=False)
        success("policy キャッシュを整理しました。")
        bullet(f"削除した件数: {result['deleted_entries']} 件")
        bullet(f"削除した容量: {result['deleted_bytes'] / (1024 * 1024):.1f} MB")
        return 0

    def handle_motor_read(self, ids: list[int] | None = None, can_mode: str = "auto") -> int:
        title("モータ確認")
        self._sync_relogin_requirement()
        if not self._require_built_workspace():
            return 1
        if not self._confirm_no_conflicting_jobs({"motor_read", "zero"}):
            return 1
        selected_can_mode = self._select_can_mode(can_mode)
        if selected_can_mode is None:
            return 1
        missing = self._missing_devices_for_can_mode(selected_can_mode, include_imu=False, include_joy=False)
        if missing:
            self._report_missing_devices(
                "モータ確認に必要な CAN デバイスが足りません。",
                missing,
                can_mode=selected_can_mode,
                include_imu=False,
                include_joy=False,
            )
            return 1
        if not self._ensure_can_mode_ready(selected_can_mode):
            return 1
        target_ids = ids or self._ask_ids(default_to_all=True)
        if not target_ids:
            error("モータ ID が指定されていません。")
            return 1
        job = create_job(
            self.paths,
            kind="motor_read",
            name=f"モータ確認 ({' '.join(str(i) for i in target_ids)})",
            payload={"ids": target_ids, "can_mode": selected_can_mode},
        )
        return self._launch_job(job)

    def handle_motor_diagnostics(self, ids: list[int] | None = None, can_mode: str = "auto") -> int:
        title("モータ診断")
        self._sync_relogin_requirement()
        if not self._require_built_workspace():
            return 1
        selected_can_mode = self._select_can_mode(can_mode)
        if selected_can_mode is None:
            return 1
        target_ids = ids or DEFAULT_MOTOR_IDS
        missing = self._missing_devices_for_can_mode(selected_can_mode, include_imu=False, include_joy=False)
        if missing:
            self._report_missing_devices(
                "モータ診断に必要な CAN デバイスが足りません。",
                missing,
                can_mode=selected_can_mode,
                include_imu=False,
                include_joy=False,
            )
            return 1
        if not self._ensure_can_mode_ready(selected_can_mode):
            return 1
        script = build_motor_probe_script(self.paths, target_ids, selected_can_mode)
        log_path = self.paths.logs_dir / f"motor-diagnostics-{datetime.now().strftime('%Y%m%d-%H%M%S')}.log"
        result = run_bash(script, cwd=self.paths.workspace_dir, log_path=log_path, interactive=False)
        if result.returncode == 0:
            success("モータ診断が完了しました。")
            bullet(f"対象 ID: {' '.join(str(i) for i in target_ids)}")
            bullet(f"ログ: {log_path}")
            return 0
        self._report_failure(
            "モータ診断に失敗しました。",
            log_path,
            causes=[
                "CAN モードの選択が実機と一致していません。",
                "一部モータの配線や電源が不安定です。",
                "対象 ID のどれかが応答していません。",
            ],
            next_steps=[
                "所定姿勢と配線を確認してください。",
                "`モータ確認` で少数の ID から順に切り分けてください。",
            ],
        )
        return result.returncode

    def handle_robot_diagnostics(self, can_mode: str = "auto") -> int:
        title("ロボット診断")
        preflight_result = self.handle_preflight(can_mode=can_mode)
        if preflight_result != 0:
            return preflight_result
        if ask_yes_no("続けて全モータの one-shot 診断も行いますか？", default=True):
            return self.handle_motor_diagnostics(can_mode=can_mode)
        return 0

    def handle_zero_position(self, ids: list[int] | None = None, can_mode: str = "auto") -> int:
        title("原点位置設定")
        self._sync_relogin_requirement()
        if not self._require_built_workspace():
            return 1
        if not self._confirm_no_conflicting_jobs({"motor_read", "zero", "real_main"}, allow_override=False):
            return 1
        selected_can_mode = self._select_can_mode(can_mode)
        if selected_can_mode is None:
            return 1
        missing = self._missing_devices_for_can_mode(selected_can_mode, include_imu=False, include_joy=False)
        if missing:
            self._report_missing_devices(
                "原点位置設定に必要な CAN デバイスが足りません。",
                missing,
                can_mode=selected_can_mode,
                include_imu=False,
                include_joy=False,
            )
            return 1
        if not self._ensure_can_mode_ready(selected_can_mode):
            return 1
        target_ids = ids or self._ask_ids(default_to_all=False)
        if not target_ids:
            error("対象のモータ ID が指定されていません。")
            return 1
        if not self._confirm_zero_position_safety_checklist(target_ids):
            warn("原点位置設定前チェックを中止しました。")
            return 1
        confirmation_phrase = self._zero_confirmation_phrase(target_ids)
        typed = ask_text(f"本当に実行する場合だけ `{confirmation_phrase}` と入力してください。")
        if typed != confirmation_phrase:
            warn("原点位置設定を中止しました。")
            return 1
        job = create_job(
            self.paths,
            kind="zero",
            name=f"原点位置設定 ({' '.join(str(i) for i in target_ids)})",
            payload={"ids": target_ids, "can_mode": selected_can_mode},
        )
        return self._launch_job(job)

    def handle_logs(self) -> int:
        title("ジョブとログ")
        jobs = list_jobs(self.paths)
        if not jobs:
            warn("まだジョブ履歴がありません。")
            return 0
        for job in jobs[:20]:
            bullet(f"{summarize_job(job)} | ログ: {Path(job.log_path).name}")
        selected = select_from_list(
            "詳細を見たいジョブを選んでください。",
            [f"{job.name} | {job.status}" for job in jobs[:20]],
            allow_back=True,
        )
        if selected is None:
            return 0
        job = jobs[selected]
        log_path = Path(job.log_path)
        if not log_path.exists():
            warn("まだログファイルがありません。")
            return 0
        section("ログ末尾")
        with log_path.open("r", encoding="utf-8", errors="ignore") as handle:
            lines = deque(handle, maxlen=40)
        for line in lines:
            line = line.rstrip("\n")
            print(line)
        return 0

    def run_worker(self, job_file: Path) -> int:
        job = load_job(job_file)
        claim_token = acquire_job_claim(job, ttl_seconds=6 * 3600)
        if claim_token is None:
            print(f"[Mujina Assist] このジョブは別 worker が処理中です: {job.name}")
            return 0
        try:
            job = load_job(job_file)
            if job.status in {"succeeded", "failed", "stopped"}:
                print(f"[Mujina Assist] このジョブはすでに終了済みです: {job.name}")
                return 0
            if job.status == "running":
                print(f"[Mujina Assist] このジョブはすでに実行中です: {job.name}")
                return 0
            mark_job_running(job, terminal_mode=job.terminal_mode or "worker", terminal_label=job.terminal_label or "worker")
            try:
                if job.kind == "setup":
                    returncode, message, stopped = self._execute_setup_job(job)
                elif job.kind == "build":
                    returncode, message, stopped = self._execute_build_job(job)
                elif job.kind == "viz":
                    returncode, message, stopped = self._execute_shell_job(
                        job,
                        build_viz_script(self.paths),
                        "RViz を終了しました。",
                        causes=["RViz 起動に必要な build が終わっていません。"],
                        next_steps=["先に `ビルドする` を完了させてください。"],
                        allow_sigint_stop=True,
                    )
                elif job.kind == "sim_main":
                    returncode, message, stopped = self._execute_shell_job(
                        job,
                        build_sim_main_script(self.paths),
                        "SIM 本体を終了しました。",
                        causes=["build が終わっていません。", "依存関係が不足しています。"],
                        next_steps=["`ビルドする` と `ONNX 読み込みテスト` を確認してください。"],
                        allow_sigint_stop=True,
                    )
                elif job.kind == "sim_joy":
                    returncode, message, stopped = self._execute_shell_job(
                        job,
                        build_joy_script(self.paths),
                        "SIM joy ノードを終了しました。",
                        causes=["joy ノードの依存関係が不足しています。"],
                        next_steps=["`初回セットアップ` を確認してください。"],
                        allow_sigint_stop=True,
                    )
                elif job.kind == "real_imu":
                    imu_port = str(job.payload.get("imu_port", "/dev/rt_usb_imu"))
                    returncode, message, stopped = self._execute_shell_job(
                        job,
                        build_real_imu_script(self.paths, port_name=imu_port),
                        "実機 IMU ノードを終了しました。",
                        causes=["IMU ポート名が違います。", "IMU の配線や認識が不安定です。"],
                        next_steps=["`ロボット診断` で IMU ポートを確認してください。"],
                        allow_sigint_stop=True,
                    )
                elif job.kind == "real_main":
                    can_mode = str(job.payload.get("can_mode", "net"))
                    returncode, message, stopped = self._execute_shell_job(
                        job,
                        build_real_main_script(self.paths, can_mode),
                        "実機 mujina_main を終了しました。",
                        causes=[
                            "CAN モードの選択が違います。",
                            "電源再投入後に can_setup が必要です。",
                            "姿勢が所定位置からずれていて、実機開始直後に保護が入っています。",
                        ],
                        next_steps=[
                            "まず所定姿勢に置き直してください。",
                            "必要なら upstream 手順で can_setup をやり直してください。",
                            "`モータ診断` で全軸の疎通を確認してください。",
                        ],
                        allow_sigint_stop=True,
                    )
                elif job.kind == "real_joy":
                    returncode, message, stopped = self._execute_shell_job(
                        job,
                        build_joy_script(self.paths),
                        "実機 joy ノードを終了しました。",
                        causes=["ゲームパッドが認識されていません。"],
                        next_steps=["`ロボット診断` で `/dev/input/js0` を確認してください。"],
                        allow_sigint_stop=True,
                    )
                elif job.kind == "policy_switch":
                    returncode, message, stopped = self._execute_policy_switch_job(job)
                elif job.kind == "policy_test":
                    returncode, message, stopped = self._execute_policy_test_job(job)
                elif job.kind == "motor_read":
                    returncode, message, stopped = self._execute_motor_read_job(job)
                elif job.kind == "zero":
                    returncode, message, stopped = self._execute_zero_job(job)
                else:
                    returncode, message, stopped = 1, f"未対応のジョブ種別です: {job.kind}", False
            except Exception as exc:
                traceback.print_exc()
                returncode, message, stopped = 1, f"予期しない例外が発生しました: {exc}", False
            if stopped:
                mark_job_stopped(job, returncode=returncode, message=message)
            else:
                mark_job_finished(job, returncode=returncode, message=message)
            return returncode
        finally:
            release_job_claim(job, claim_token)

    def _execute_setup_job(self, job: JobRecord) -> tuple[int, str, bool]:
        log_path = job_log_path(job)
        skip_upgrade = bool(job.payload.get("skip_upgrade", False))
        setup_real_devices = bool(job.payload.get("setup_real_devices", False))

        initial = run_initial_setup(self.paths, log_path, skip_upgrade=skip_upgrade)
        if initial.returncode != 0:
            self._report_failure(
                "初回セットアップの OS 準備に失敗しました。",
                log_path,
                causes=["apt の更新や ROS 2 の導入に失敗しています。"],
                next_steps=["ログを確認して依存関係を整えてください。"],
            )
            return initial.returncode, "初回セットアップに失敗しました。", False
        clone_result = ensure_upstream_clone(self.paths, log_path)
        if clone_result.returncode != 0:
            self._report_failure(
                "mujina_ros の clone に失敗しました。",
                log_path,
                causes=["ネットワーク接続や GitHub へのアクセスに問題があります。"],
                next_steps=["ネットワークを確認して再試行してください。"],
            )
            return clone_result.returncode, "mujina_ros の clone に失敗しました。", False
        deps = run_workspace_dependency_setup(self.paths, log_path)
        if deps.returncode != 0:
            self._report_failure(
                "workspace の依存関係セットアップに失敗しました。",
                log_path,
                causes=["rosdep か Python 依存導入に失敗しています。"],
                next_steps=["ログを見て足りない依存関係を解消してください。"],
            )
            return deps.returncode, "依存関係セットアップに失敗しました。", False
        build = run_workspace_build(self.paths, log_path)
        if build.returncode != 0:
            self._report_failure(
                "workspace のビルドに失敗しました。",
                log_path,
                causes=["colcon build が通っていません。"],
                next_steps=["ログを確認して build エラーを修正してください。"],
            )
            return build.returncode, "workspace のビルドに失敗しました。", False
        if setup_real_devices:
            real = run_real_device_setup(self.paths, log_path)
            if real.returncode != 0:
                self._report_failure(
                    "実機用の dialout / udev 設定に失敗しました。",
                    log_path,
                    causes=["sudo 実行や udev ルール反映に失敗しています。"],
                    next_steps=["ログを確認して再試行してください。"],
                )
                return real.returncode, "実機用設定に失敗しました。", False
            setup_status = real_setup_status()
            self.state.real_setup_requires_relogin = not (setup_status.get("dialout") and setup_status.get("udev_rule"))
        capture_default_policy(self.paths)
        self._clear_manual_recovery_state(kind="policy")
        self.state.last_action = "setup"
        self.state.active_policy_label = current_policy_label(self.paths, self.state)
        self._sync_default_policy_state()
        self.save_state()
        return 0, "初回セットアップが完了しました。", False

    def _execute_build_job(self, job: JobRecord) -> tuple[int, str, bool]:
        log_path = job_log_path(job)
        result = run_workspace_build(self.paths, log_path)
        if result.returncode != 0:
            self._report_failure(
                "ビルドに失敗しました。",
                log_path,
                causes=["依存関係不足、または upstream 側のビルド失敗です。"],
                next_steps=["`初回セットアップ` を見直すか、ログから build エラーを確認してください。"],
            )
            return result.returncode, "ビルドに失敗しました。", False
        capture_default_policy(self.paths)
        self._clear_manual_recovery_state(kind="policy")
        self._sync_default_policy_state()
        self.state.last_action = "build"
        self.save_state()
        return 0, "ビルドが完了しました。", False

    def _execute_policy_switch_job(self, job: JobRecord) -> tuple[int, str, bool]:
        candidate = self._candidate_from_payload(job.payload)
        ok, message = activate_policy(self.paths, self.state, candidate, job_log_path(job))
        self.save_state()
        if ok:
            self._clear_manual_recovery_state(kind="policy")
            self.save_state()
            success(message)
            return 0, message, False
        self._report_failure(
            message,
            job_log_path(job),
            causes=[
                "対象 ONNX が壊れています。",
                "mujina_control の再ビルドに失敗しました。",
                "ONNX 読み込みテストに失敗しました。",
            ],
            next_steps=[
                "ログを確認して policy を切り替え直してください。",
                "必要なら公式デフォルトへ戻して比較してください。",
            ],
        )
        return 1, message, False

    def _execute_policy_test_job(self, job: JobRecord) -> tuple[int, str, bool]:
        result = run_onnx_self_test(self.paths, job_log_path(job))
        if result.returncode == 0:
            return 0, "ONNX 読み込みテストに成功しました。", False
        self._report_failure(
            "ONNX 読み込みテストに失敗しました。",
            job_log_path(job),
            causes=["現在の policy が読み込めません。"],
            next_steps=["policy を入れ直すか、公式デフォルトへ戻して比較してください。"],
        )
        return result.returncode, "ONNX 読み込みテストに失敗しました。", False

    def _execute_motor_read_job(self, job: JobRecord) -> tuple[int, str, bool]:
        ids = [int(value) for value in job.payload.get("ids", [])]
        can_mode = str(job.payload.get("can_mode", "net"))
        return self._execute_shell_job(
            job,
            build_motor_read_script(self.paths, ids, can_mode),
            "モータ確認を終了しました。",
            causes=[
                "CAN モードの選択が違います。",
                "対象モータの電源、配線、ID に問題があります。",
            ],
            next_steps=[
                "少数の ID から順に切り分けてください。",
                "必要なら upstream 手順で同じコマンドを直接実行して比較してください。",
            ],
            allow_sigint_stop=True,
        )

    def _execute_zero_job(self, job: JobRecord) -> tuple[int, str, bool]:
        ids = [int(value) for value in job.payload.get("ids", [])]
        can_mode = str(job.payload.get("can_mode", "net"))
        if not ids:
            return 1, "原点位置設定に対象 ID がありません。", False
        preflight_log_path = job_log_path(job).with_suffix(".preflight.log")
        preflight_result = run_bash(
            build_motor_probe_script(self.paths, ids, can_mode),
            cwd=self.paths.workspace_dir,
            log_path=preflight_log_path,
            interactive=True,
        )
        if preflight_result.returncode == 130:
            warn("原点位置設定の前提確認を中断しました。")
            bullet(f"ログ: {preflight_log_path}")
            return 130, "原点位置設定の前提確認を中断しました。", True
        if preflight_result.returncode != 0:
            self._report_failure(
                "原点位置設定の前提確認に失敗しました。",
                preflight_log_path,
                causes=[
                    "選んだ CAN 接続方式と実機が一致していません。",
                    "指定した ID のモータへ one-shot 通信できていません。",
                ],
                next_steps=[
                    "まず `モータ確認` を同じ ID で成功させてください。",
                    "成功してから改めて `原点位置設定` を実行してください。",
                ],
            )
            return preflight_result.returncode, "原点位置設定の前提確認に失敗しました。", False
        return self._execute_shell_job(
            job,
            build_zero_script(self.paths, ids, can_mode, include_can_setup=False),
            "原点位置設定が完了しました。",
            causes=[
                "姿勢が所定位置からずれています。",
                "対象モータの一部に書き込みが通っていません。",
            ],
            next_steps=[
                "所定姿勢をやり直してください。",
                "必要なら upstream の zero 手順で再比較してください。",
            ],
            allow_sigint_stop=False,
        )

    def _execute_shell_job(
        self,
        job: JobRecord,
        script: str,
        success_message: str,
        *,
        causes: list[str],
        next_steps: list[str],
        allow_sigint_stop: bool,
    ) -> tuple[int, str, bool]:
        result = run_bash(script, cwd=self.paths.workspace_dir, log_path=job_log_path(job), interactive=True)
        if result.returncode == 0:
            success(success_message)
            bullet(f"ログ: {job.log_path}")
            return 0, success_message, False
        if allow_sigint_stop and result.returncode == 130:
            warn("ユーザー操作で停止しました。")
            bullet(f"ログ: {job.log_path}")
            return 130, "ユーザー操作で停止しました。", True
        self._report_failure(f"{job.name} に失敗しました。", job_log_path(job), causes=causes, next_steps=next_steps)
        return result.returncode, f"{job.name} に失敗しました。", False

    def _launch_job(self, job: JobRecord) -> int:
        launch = launch_job(self.paths, job)
        if not launch.ok:
            mark_job_finished(job, returncode=1, message=launch.message)
            error("ジョブを起動できませんでした。")
            bullet(launch.message)
            bullet(f"ログ予定先: {job.log_path}")
            return 1
        update_job(job, terminal_mode=launch.mode, terminal_label=launch.label, terminal_pid=launch.pid)
        claimed = self._wait_for_worker_claim(job)
        if claimed is None:
            stop_error = stop_job_launch(mode=launch.mode, label=launch.label, pid=launch.pid)
            if stop_error is None:
                mark_job_finished(job, returncode=1, message="worker が起動確認タイムアウト内に開始しませんでした。")
            else:
                update_job(job, message=f"worker 起動を確認できませんでした。停止確認できませんでした: {stop_error}")
                self._set_manual_recovery_state(
                    kind="job_launch",
                    summary=f"{job.name} の worker 起動を確認できず、停止確認もできませんでした。",
                )
                self.save_state()
            error("ジョブの worker 起動を確認できませんでした。")
            bullet(f"ログ: {job.log_path}")
            return 1
        if claimed.status == "failed":
            error("ジョブは起動しましたが、直後に失敗しました。")
            bullet(claimed.message or f"ログ: {job.log_path}")
            return 1
        if claimed.status == "stopped":
            warn("ジョブは起動しましたが、直後に停止しました。")
            bullet(claimed.message or f"ログ: {job.log_path}")
            return 1
        success(launch.message)
        bullet(f"ログ: {job.log_path}")
        if launch.mode == "tmux":
            bullet(f"確認コマンド: tmux attach -t {launch.label}")
        return 0

    def _launch_job_group(self, jobs: list[JobRecord], *, heading: str) -> int:
        launches: list[tuple[JobRecord, str, str, int | None]] = []
        manual_recovery_jobs: list[str] = []
        for job in jobs:
            launch = launch_job(self.paths, job)
            if not launch.ok:
                mark_job_finished(job, returncode=1, message=launch.message)
                error("ジョブグループの起動途中で失敗しました。")
                bullet(f"失敗したジョブ: {job.name}")
                bullet(launch.message)
                if launches:
                    section("巻き戻し")
                for launched_job, mode, label, pid in launches:
                    stop_error = stop_job_launch(mode=mode, label=label, pid=pid)
                    if stop_error is None:
                        mark_job_stopped(launched_job, message="ジョブグループ起動失敗のため停止しました。")
                        bullet(f"{launched_job.name} を停止しました。")
                    else:
                        update_job(launched_job, message=f"ジョブグループ起動失敗。停止確認できませんでした: {stop_error}")
                        manual_recovery_jobs.append(launched_job.name)
                        bullet(f"{launched_job.name} は停止確認できませんでした: {stop_error}")
                if manual_recovery_jobs:
                    self._set_manual_recovery_state(
                        kind="job_launch",
                        summary="ジョブグループ起動失敗後に停止確認できなかったジョブがあります: " + ", ".join(manual_recovery_jobs),
                    )
                    self.save_state()
                return 1
            update_job(job, terminal_mode=launch.mode, terminal_label=launch.label, terminal_pid=launch.pid)
            claimed = self._wait_for_worker_claim(job)
            if claimed is None or claimed.status in {"failed", "stopped"}:
                failure_message = "worker 起動を確認できませんでした。" if claimed is None else (claimed.message or f"{job.name} は起動直後に失敗しました。")
                mark_job_finished(job, returncode=1, message=failure_message)
                error("ジョブグループの起動途中で失敗しました。")
                bullet(f"失敗したジョブ: {job.name}")
                bullet(failure_message)
                rollback_targets = launches + [(job, launch.mode, launch.label, launch.pid)]
                if rollback_targets:
                    section("巻き戻し")
                for launched_job, mode, label, pid in rollback_targets:
                    stop_error = stop_job_launch(mode=mode, label=label, pid=pid)
                    if stop_error is None:
                        mark_job_stopped(launched_job, message="ジョブグループ起動失敗のため停止しました。")
                        bullet(f"{launched_job.name} を停止しました。")
                    else:
                        update_job(launched_job, message=f"ジョブグループ起動失敗。停止確認できませんでした: {stop_error}")
                        manual_recovery_jobs.append(launched_job.name)
                        bullet(f"{launched_job.name} は停止確認できませんでした: {stop_error}")
                if manual_recovery_jobs:
                    self._set_manual_recovery_state(
                        kind="job_launch",
                        summary="ジョブグループ起動失敗後に停止確認できなかったジョブがあります: " + ", ".join(manual_recovery_jobs),
                    )
                    self.save_state()
                return 1
            launches.append((job, launch.mode, launch.label, launch.pid))
        success(heading)
        for job, mode, label, _pid in launches:
            bullet(f"{job.name} | ログ: {job.log_path}")
            if mode == "tmux":
                bullet(f"tmux attach -t {label}")
        return 0

    def _confirm_no_conflicting_jobs(self, relevant_kinds: set[str], *, allow_override: bool = True) -> bool:
        conflicts = [job for job in active_jobs(self.paths) if job.kind in relevant_kinds]
        if conflicts:
            warn("同系統のジョブ記録が残っています。必要ならログで確認してください。")
            for job in conflicts:
                bullet(f"{job.name} | ログ: {Path(job.log_path).name}")
            if not allow_override:
                section("次にやること")
                bullet("先に実行中ジョブを停止し、ログで状態を確認してから再実行してください。")
                return False
            return ask_yes_no("それでも続けますか？", default=True)
        return True

    def _require_cloned_workspace(self) -> bool:
        if workspace_clone_ready(self.paths):
            return True
        error("workspace がまだ作成されていません。先に `初回セットアップ` を実行してください。")
        return False

    def _require_built_workspace(self) -> bool:
        if not self._require_cloned_workspace():
            return False
        if workspace_build_ready(self.paths):
            return True
        error("ビルドがまだ終わっていません。先に `ビルドする` を実行してください。")
        return False

    def _select_can_mode(self, preferred: str) -> str | None:
        devices = detect_real_devices()
        serial_candidates = list_serial_device_candidates()
        net_available = devices.get("can0", False)
        serial_available = devices.get("/dev/usb_can", False)
        if preferred == "net" and net_available:
            return "net"
        if preferred == "serial" and serial_available:
            return "serial"
        if preferred in {"net", "serial"} and preferred not in {"auto"}:
            warn(f"指定した CAN モード `{preferred}` は今の接続状態では使えないようです。")
        options: list[tuple[str, str]] = []
        if net_available:
            options.append(("net", "network CAN を使う（can0）"))
        if serial_available:
            options.append(("serial", "serial CAN を使う（/dev/usb_can）"))
        if not options:
            error("使える CAN デバイスが見つかりませんでした。")
            if serial_candidates:
                bullet("USB シリアル候補は見えています。udev ルールで `/dev/usb_can` になるか確認してください。")
                for candidate in serial_candidates[:4]:
                    bullet(candidate)
            return None
        if len(options) == 1:
            return options[0][0]
        selected = select_from_list("使う CAN モードを選んでください。", [label for _, label in options], allow_back=True)
        if selected is None:
            return None
        return options[selected][0]

    def _diagnostic_can_mode(self, preferred: str) -> str:
        if preferred in {"net", "serial"}:
            return preferred
        devices = detect_real_devices()
        serial_candidates = list_serial_device_candidates()
        if not devices.get("can0", False) and not devices.get("/dev/usb_can", False) and serial_candidates:
            warn("`can0` も `/dev/usb_can` も見えていません。")
            bullet("ただし USB シリアル候補は見えているので、USB-CAN か IMU のどちらかが固定名になっていない可能性があります。")
            for candidate in serial_candidates[:4]:
                bullet(candidate)
            selected = select_from_list(
                "診断したい CAN モードを選んでください。",
                ["network CAN を前提に診断する", "serial CAN を前提に診断する"],
                allow_back=True,
            )
            if selected == 1:
                return "serial"
            return "net"
        if devices.get("can0", False):
            return "net"
        if devices.get("/dev/usb_can", False):
            return "serial"
        return "net"

    def _missing_devices_for_can_mode(
        self,
        can_mode: str,
        *,
        include_imu: bool,
        include_joy: bool,
    ) -> list[str]:
        devices = detect_real_devices()
        required: list[str] = []
        if include_imu:
            imu_port, _fallback, _candidates = resolve_imu_port()
            required.append("/dev/rt_usb_imu" if imu_port is None else imu_port)
        if include_joy:
            required.append("/dev/input/js0")
        required.append("can0" if can_mode == "net" else "/dev/usb_can")
        missing: list[str] = []
        for item in required:
            if item in {"/dev/rt_usb_imu", "/dev/usb_can", "/dev/input/js0", "can0"}:
                if not devices.get(item, False):
                    missing.append(item)
            elif not Path(item).exists():
                missing.append(item)
        return missing

    def _ensure_can_mode_ready(self, can_mode: str) -> bool:
        if can_mode != "net":
            return True
        can_status = inspect_can_status()
        if not can_status.get("present", False):
            return True
        if can_status.get("ok", False):
            return True
        error("can0 は見えていますが、現在の状態は健全ではありません。")
        operstate = str(can_status.get("operstate") or "unknown")
        controller_state = str(can_status.get("controller_state") or "unknown")
        bullet(f"operstate={operstate}, controller_state={controller_state}")
        bullet("電源再投入後に公式の can_setup 手順をやり直してから再実行してください。")
        return False

    def _report_missing_devices(
        self,
        summary: str,
        missing: list[str],
        *,
        can_mode: str,
        include_imu: bool,
        include_joy: bool,
    ) -> None:
        error(summary)
        for item in missing:
            bullet(item)
        section("次にやること")
        if include_imu and any(item.startswith("/dev/tty") or item == "/dev/rt_usb_imu" for item in missing):
            bullet("IMU を挿し直し、必要なら udev ルールと `/dev/rt_usb_imu` を確認してください。")
        if include_joy and "/dev/input/js0" in missing:
            bullet("ゲームパッドを接続し直し、OS から認識されているか確認してください。")
        if can_mode == "net" and "can0" in missing:
            bullet("network CAN を使うなら、電源再投入後に公式の can_setup 手順をやり直してください。")
        if can_mode == "serial" and "/dev/usb_can" in missing:
            bullet("serial CAN を使うなら `/dev/usb_can` が出ているか、または udev ルールを確認してください。")

    def _confirm_real_robot_safety_checklist(self) -> bool:
        section("実機起動前チェック")
        bullet("所定姿勢に置き、周囲 50cm 以上の離隔を確保してください。")
        bullet("補助者が横につき、独立した停止手段をすぐ使える状態にしてください。")
        bullet("gamepad は Logicool F710 / F310 の X mode、MODE LED OFF を前提にしてください。")
        bullet("選ぶ policy の由来と学習条件を把握したうえで進めてください。")
        prompts = [
            "周囲の離隔、補助者、停止手段を確認しましたか？",
            "gamepad の X mode と MODE LED OFF を確認しましたか？",
            "今の policy の由来と学習条件を把握していますか？",
        ]
        return all(ask_yes_no(prompt, default=False) for prompt in prompts)

    def _confirm_zero_position_safety_checklist(self, ids: list[int]) -> bool:
        section("原点位置設定前チェック")
        bullet("README 記載の所定姿勢に置いてから実行してください。")
        bullet("実際の書き込みは upstream の `motor_set_zero_position.py` をそのまま呼びます。")
        bullet("対象 ID: " + " ".join(str(value) for value in ids))
        prompts = [
            "所定姿勢と対象 ID を確認しましたか？",
            "周囲の離隔と停止手段を確認しましたか？",
        ]
        return all(ask_yes_no(prompt, default=False) for prompt in prompts)

    def _wait_for_worker_claim(self, job: JobRecord) -> JobRecord | None:
        deadline = time.monotonic() + WORKER_CLAIM_TIMEOUT_SECONDS
        while time.monotonic() < deadline:
            try:
                current = load_job(Path(job.job_file))
            except Exception:
                return None
            if current.status != "queued" or current.started_at or current.finished_at:
                return current
            time.sleep(WORKER_CLAIM_POLL_SECONDS)
        return None

    def _resolve_runtime_imu_port(self) -> str | None:
        port, fallback, candidates = resolve_imu_port()
        if port and not fallback:
            return port
        if port and fallback:
            bullet(f"固定名 `/dev/rt_usb_imu` が無いため、代替ポート {port} を IMU 候補として使います。")
            if ask_yes_no("このポートを IMU として使いますか？", default=True):
                return port
            return None
        if not candidates:
            return None
        selected = select_from_list("IMU として使うポートを選んでください。", candidates, allow_back=True)
        if selected is None:
            return None
        return candidates[selected]

    def _zero_confirmation_phrase(self, ids: list[int]) -> str:
        return "ZERO " + " ".join(str(value) for value in ids)

    def _mark_current_policy_sim_verified(self, *, ask_confirmation: bool) -> int:
        if not self._require_built_workspace():
            return 1
        self._sync_default_policy_state()
        if not self.state.active_policy_hash:
            error("現在の policy のハッシュを取得できませんでした。")
            bullet("先に `ビルドする` か `policy を切り替える` を確認してください。")
            return 1
        current_workspace_signature = self._current_workspace_signature()
        if not self._has_live_sim_session(self.state.active_policy_hash, current_workspace_signature):
            error("今の policy / workspace に対応する実行中の SIM セッションを確認できません。")
            bullet("先に `SIM` を起動し、別ターミナルで姿勢と入力応答を確認してから再度実行してください。")
            return 1
        if ask_confirmation and not ask_yes_no("今の policy で SIM の確認を完了しましたか？", default=False):
            warn("SIM確認済みの記録を中止しました。")
            return 1
        self.state.last_action = "sim_verified"
        self.state.last_sim_success = True
        self.state.last_sim_policy_hash = self.state.active_policy_hash
        self.state.last_sim_verified_at = datetime.now().astimezone().isoformat(timespec="seconds")
        self.state.last_sim_verified_label = self.state.active_policy_label
        self.state.last_sim_verified_source = self.state.active_policy_source
        self.state.last_sim_verified_workspace_signature = current_workspace_signature
        self.save_state()
        success("今の policy を SIM確認済みとして記録しました。")
        return 0

    def _has_live_sim_session(self, policy_hash: str, workspace_signature_value: str) -> bool:
        groups: dict[str, set[str]] = {}
        for job in active_jobs(self.paths):
            if job.kind not in {"sim_main", "sim_joy"}:
                continue
            if str(job.payload.get("policy_hash", "")) != policy_hash:
                continue
            if str(job.payload.get("workspace_signature", "")) != workspace_signature_value:
                continue
            if not job.group_id or not job.started_at:
                continue
            groups.setdefault(job.group_id, set()).add(job.kind)
        return any(kinds == {"sim_main", "sim_joy"} for kinds in groups.values())

    def _current_active_policy_candidate(self) -> PolicyCandidate | None:
        if not self.state.active_policy_hash:
            return None
        for candidate in all_policy_candidates(self.paths, self.state):
            if candidate.policy_hash and candidate.policy_hash == self.state.active_policy_hash:
                return candidate
        return None

    def _active_policy_real_world_ready(self) -> tuple[bool, str]:
        candidate = self._current_active_policy_candidate()
        if candidate is None:
            if current_policy_label(self.paths, self.state) == "公式デフォルト" and self.paths.source_policy_path.exists():
                return True, ""
            return False, "現在の policy の由来をキャッシュから特定できません。切り替え直して provenance を確定してください。"
        if candidate.source_type == "default":
            return True, ""
        if candidate.manifest_path is None or not candidate.manifest_path.exists():
            return False, "manifest が無く、robot revision や学習条件を検証できません。manifest 付き policy を使ってください。"
        return True, ""

    def _ask_ids(self, *, default_to_all: bool = False) -> list[int]:
        prompt = "対象のモータ ID を空白またはカンマ区切りで入力してください。"
        if default_to_all:
            prompt += " 空欄なら既定の 12 軸を使います。"
        raw = ask_text(prompt)
        if not raw:
            if default_to_all:
                bullet("既定の 12 軸を使います。")
                return list(DEFAULT_MOTOR_IDS)
            return []
        normalized = raw.replace(",", " ")
        tokens = [chunk for chunk in normalized.split() if chunk]
        if not tokens:
            return []
        if any(not token.isdigit() for token in tokens):
            error("数字以外が含まれています。")
            return []
        values = [int(token) for token in tokens]
        bullet("対象 ID: " + " ".join(str(value) for value in values))
        if not ask_yes_no("この ID で続けますか？", default=True):
            return []
        return values

    def _sync_default_policy_state(self) -> None:
        if not self.paths.source_policy_path.exists():
            self.state.active_policy_label = "未設定"
            self.state.active_policy_hash = ""
            self.state.active_policy_source = ""
            return
        from mujina_assist.services.checks import file_hash

        default_hash = file_hash(self.paths.default_policy_cache) if self.paths.default_policy_cache.exists() else ""
        candidates = all_policy_candidates(self.paths, self.state)
        current_hash = next((candidate.policy_hash for candidate in candidates if candidate.path == self.paths.source_policy_path), "")
        if not current_hash:
            current_hash = file_hash(self.paths.source_policy_path)
        current_source = Path(self.state.active_policy_source) if self.state.active_policy_source else None
        if current_source and current_source.exists():
            if file_hash(current_source) == current_hash:
                self.state.active_policy_hash = current_hash
                return
        if self.state.active_policy_source and self.state.active_policy_source != str(self.paths.default_policy_cache):
            if not default_hash or current_hash != default_hash:
                self.state.active_policy_hash = current_hash
                return
        self.state.active_policy_hash = current_hash
        for candidate in candidates:
            if candidate.policy_hash and candidate.policy_hash == current_hash:
                self.state.active_policy_label = candidate.label
                self.state.active_policy_source = str(candidate.path)
                return
        self.state.active_policy_label = current_policy_label(self.paths, self.state)
        self.state.active_policy_source = str(self.paths.source_policy_path)

    def _candidate_to_payload(self, candidate: PolicyCandidate) -> dict[str, str]:
        return {
            "label": candidate.label,
            "path": str(candidate.path),
            "source_type": candidate.source_type,
            "description": candidate.description,
            "manifest_path": str(candidate.manifest_path) if candidate.manifest_path else "",
            "policy_hash": candidate.policy_hash,
        }

    def _prepare_candidate_for_job(self, candidate: PolicyCandidate) -> PolicyCandidate:
        if candidate.source_type in {"default", "cache"}:
            return candidate
        cached_path = import_policy_to_cache(self.paths, candidate)
        cleanup_policy_cache(self.paths, self.state)
        cached_manifest = cached_path.with_suffix(".manifest.json")
        return PolicyCandidate(
            label=candidate.label,
            path=cached_path,
            source_type="cache",
            description=candidate.description,
            manifest_path=cached_manifest if cached_manifest.exists() else None,
            policy_hash=candidate.policy_hash,
            size_bytes=candidate.size_bytes,
            last_used_at=candidate.last_used_at,
            use_count=candidate.use_count,
            is_active=candidate.is_active,
            sim_verified=candidate.sim_verified,
        )

    def _candidate_from_payload(self, payload: dict) -> PolicyCandidate:
        manifest_path = str(payload.get("manifest_path") or "")
        return PolicyCandidate(
            label=str(payload.get("label", "policy")),
            path=Path(str(payload.get("path", ""))),
            source_type=str(payload.get("source_type", "path")),
            description=str(payload.get("description", "")),
            manifest_path=Path(manifest_path) if manifest_path else None,
            policy_hash=str(payload.get("policy_hash", "")),
        )

    def _format_policy_option(self, candidate: PolicyCandidate) -> str:
        chips: list[str] = []
        if candidate.is_active:
            chips.append("使用中")
        if candidate.sim_verified:
            chips.append("SIM確認済み")
        if candidate.use_count:
            chips.append(f"使用回数 {candidate.use_count}")
        chip_text = f" [{' / '.join(chips)}]" if chips else ""
        description = f" | {candidate.description}" if candidate.description else ""
        return f"{candidate.label}{chip_text}{description}"

    def _show_policy_summary(self, candidate: PolicyCandidate) -> None:
        section("選んだ policy")
        bullet(f"名前: {candidate.label}")
        bullet(f"場所: {candidate.path}")
        bullet(f"manifest: {candidate.manifest_path if candidate.manifest_path else 'なし'}")
        if candidate.description:
            bullet(f"説明: {candidate.description}")
        if candidate.policy_hash:
            bullet(f"ハッシュ: {candidate.policy_hash[:12]}")

    def _report_failure(
        self,
        summary: str,
        log_path: Path,
        *,
        causes: list[str] | None = None,
        next_steps: list[str] | None = None,
    ) -> None:
        error(summary)
        bullet(f"ログ: {log_path}")
        if causes:
            section("よくある原因")
            for cause in causes:
                bullet(cause)
        if next_steps:
            section("次にやること")
            for step in next_steps:
                bullet(step)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="mujina-assist", description="mujina_ros を日本語で案内する CLI")
    subparsers = parser.add_subparsers(dest="command")

    subparsers.add_parser("menu")

    setup_parser = subparsers.add_parser("setup")
    setup_parser.add_argument("--skip-upgrade", action="store_true")

    subparsers.add_parser("doctor")

    preflight_parser = subparsers.add_parser("preflight")
    preflight_parser.add_argument("--can-mode", choices=["auto", "net", "serial"], default="auto")

    subparsers.add_parser("build")
    subparsers.add_parser("viz")
    subparsers.add_parser("sim")
    subparsers.add_parser("sim-verified")
    subparsers.add_parser("logs")
    subparsers.add_parser("motor-diagnostics")

    robot_parser = subparsers.add_parser("robot")
    robot_parser.add_argument("--can-mode", choices=["auto", "net", "serial"], default="auto")

    policy_parser = subparsers.add_parser("policy")
    policy_parser.add_argument("--test", action="store_true")
    policy_parser.add_argument("--cleanup-cache", action="store_true")

    motor_parser = subparsers.add_parser("motor-read")
    motor_parser.add_argument("--ids", nargs="+", type=int)
    motor_parser.add_argument("--can-mode", choices=["auto", "net", "serial"], default="auto")

    zero_parser = subparsers.add_parser("zero")
    zero_parser.add_argument("--ids", nargs="+", type=int)
    zero_parser.add_argument("--can-mode", choices=["auto", "net", "serial"], default="auto")

    worker_parser = subparsers.add_parser("worker")
    worker_parser.add_argument("--job-file", required=True)

    return parser


def run_app(repo_root: Path, argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    app = MujinaAssistApp(repo_root)
    command = args.command or "menu"

    if command == "menu":
        return app.run_menu()
    if command == "setup":
        return app.handle_setup(skip_upgrade=args.skip_upgrade)
    if command == "doctor":
        return app.handle_doctor()
    if command == "preflight":
        return app.handle_preflight(can_mode=args.can_mode)
    if command == "build":
        return app.handle_build()
    if command == "viz":
        return app.handle_viz()
    if command == "sim":
        return app.handle_sim()
    if command == "sim-verified":
        return app.handle_mark_sim_verified()
    if command == "logs":
        return app.handle_logs()
    if command == "motor-diagnostics":
        return app.handle_motor_diagnostics()
    if command == "robot":
        return app.handle_real_robot(can_mode=args.can_mode)
    if command == "policy":
        if args.cleanup_cache:
            return app.handle_policy_cache_cleanup()
        return app.handle_policy_test() if args.test else app.handle_policy_menu()
    if command == "motor-read":
        return app.handle_motor_read(ids=args.ids, can_mode=args.can_mode)
    if command == "zero":
        return app.handle_zero_position(ids=args.ids, can_mode=args.can_mode)
    if command == "worker":
        return app.run_worker(Path(args.job_file))

    parser.print_help()
    return 1
