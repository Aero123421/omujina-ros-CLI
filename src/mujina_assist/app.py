from __future__ import annotations

import argparse
import traceback
from pathlib import Path
from uuid import uuid4

from mujina_assist.models import AppPaths, JobRecord, PolicyCandidate
from mujina_assist.services.checks import (
    build_doctor_report,
    detect_real_devices,
    list_serial_device_candidates,
    resolve_imu_port,
    sim_policy_verified,
    workspace_build_ready,
    workspace_clone_ready,
    write_config_file,
)
from mujina_assist.services.jobs import (
    active_jobs,
    create_job,
    job_log_path,
    load_job,
    mark_job_finished,
    mark_job_running,
    mark_job_stopped,
    recent_jobs,
    summarize_job,
    update_job,
)
from mujina_assist.services.policy import activate_policy, all_policy_candidates, import_policy_to_cache
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
from mujina_assist.services.terminals import launch_job
from mujina_assist.services.workspace import (
    capture_default_policy,
    ensure_upstream_clone,
    run_initial_setup,
    run_onnx_self_test,
    run_real_device_setup,
    run_workspace_build,
    run_workspace_dependency_setup,
)
from mujina_assist.ui import ask_text, ask_yes_no, bullet, error, info, pause, section, select_from_list, success, title, warn


class MujinaAssistApp:
    def __init__(self, repo_root: Path) -> None:
        self.paths = AppPaths.from_repo_root(repo_root)
        self.paths.ensure_directories()
        write_config_file(self.paths)
        self.state = load_runtime_state(self.paths.runtime_state_file)

    def save_state(self) -> None:
        save_runtime_state(self.paths.runtime_state_file, self.state)

    def print_status(self) -> None:
        report = build_doctor_report(self.paths, self.state)
        title("Mujina Assist")
        section("現在の状態")
        bullet(f"OS: {report.os_label}")
        bullet(f"Ubuntu 24.04: {'OK' if report.ubuntu_24_04 else '未確認'}")
        bullet(f"ROS 2 Jazzy: {'OK' if report.ros_installed else '未導入'}")
        bullet(f"workspace: {'OK' if report.workspace_cloned else '未作成'}")
        bullet(f"build: {'OK' if report.workspace_built else '未実行'}")
        bullet(f"active policy: {report.active_policy_label}")
        bullet(f"SIM確認済み: {'OK' if report.sim_ready else '未確認'}")
        bullet(f"USB上のONNX: {report.usb_policy_count} 件")
        if report.imu_port_label:
            suffix = " (fallback)" if report.imu_port_fallback else ""
            bullet(f"IMUポート: {report.imu_port_label}{suffix}")
        real_devices = ", ".join(
            [f"{name}={'OK' if ok else 'NG'}" for name, ok in report.real_devices.items()]
        )
        bullet(f"実機デバイス: {real_devices}")
        tools = ", ".join([f"{name}={'OK' if ok else 'NG'}" for name, ok in report.tool_status.items()])
        bullet(f"主要ツール: {tools}")
        if report.serial_candidates:
            bullet("USBシリアル候補: " + ", ".join(report.serial_candidates[:4]))

        running_jobs = active_jobs(self.paths)
        if running_jobs:
            section("実行中ジョブ")
            for job in running_jobs[:5]:
                bullet(f"{job.name} | ログ: {Path(job.log_path).name}")

        latest_jobs = [job for job in recent_jobs(self.paths, limit=5) if job.status not in {"queued", "running"}]
        if latest_jobs:
            section("最近の完了ジョブ")
            for job in latest_jobs[:3]:
                bullet(summarize_job(job))

        section("おすすめ")
        bullet(report.recommendation)
        if report.notes:
            section("補足")
            for note in report.notes:
                bullet(note)

    def run_menu(self) -> int:
        while True:
            self.print_status()
            section("メニュー")
            options = [
                "初回セットアップ",
                "状態確認",
                "実機前診断",
                "build する",
                "可視化する",
                "SIM を起動する",
                "SIM 確認を記録する",
                "実機を起動する",
                "ポリシーを切り替える",
                "ONNX 読み込みテスト",
                "モータの現在値を読む",
                "初期位置を設定する",
                "ジョブとログを見る",
                "終了",
            ]
            selection = select_from_list("やりたいことを選んでください。", options)
            print()
            if selection == 0:
                self.handle_setup()
            elif selection == 1:
                self.handle_doctor()
            elif selection == 2:
                self.handle_preflight()
            elif selection == 3:
                self.handle_build()
            elif selection == 4:
                self.handle_viz()
            elif selection == 5:
                self.handle_sim()
            elif selection == 6:
                self.handle_mark_sim_verified()
            elif selection == 7:
                self.handle_real_robot()
            elif selection == 8:
                self.handle_policy_menu()
            elif selection == 9:
                self.handle_policy_test()
            elif selection == 10:
                self.handle_motor_read()
            elif selection == 11:
                self.handle_zero_position()
            elif selection == 12:
                self.handle_logs()
            elif selection == 13:
                return 0
            pause()

    def handle_doctor(self) -> int:
        self.print_status()
        return 0

    def handle_preflight(self, can_mode: str = "auto") -> int:
        title("実機前診断")
        if not self._require_built_workspace():
            return 1
        self._sync_default_policy_state()
        report = build_doctor_report(self.paths, self.state)
        selected_can_mode = self._diagnostic_can_mode(can_mode)
        missing = self._missing_devices_for_can_mode(selected_can_mode, include_imu=True, include_joy=True)

        section("診断結果")
        bullet(f"active policy: {report.active_policy_label}")
        bullet(f"SIM確認済み: {'OK' if report.sim_ready else '未確認'}")
        bullet(f"想定 CAN モード: {selected_can_mode}")
        if report.imu_port_label:
            suffix = " (固定名が無いため fallback 使用)" if report.imu_port_fallback else ""
            bullet(f"IMUポート: {report.imu_port_label}{suffix}")
        else:
            bullet("IMUポート: 未検出")
        if selected_can_mode == "serial":
            bullet(f"slcand: {'OK' if report.tool_status.get('slcand', False) else 'NG'}")
        for name in ("/dev/rt_usb_imu", "/dev/input/js0", "can0" if selected_can_mode == "net" else "/dev/usb_can"):
            ok = report.real_devices.get(name, False)
            bullet(f"{name}: {'OK' if ok else 'NG'}")

        if report.serial_candidates:
            section("検出したUSBシリアル候補")
            for candidate in report.serial_candidates:
                bullet(candidate)

        if missing:
            self._report_missing_devices(
                "実機前診断で不足デバイスが見つかりました。",
                missing,
                can_mode=selected_can_mode,
                include_imu=True,
                include_joy=True,
            )
        else:
            success("必須デバイスの存在確認は通っています。")

        if selected_can_mode == "serial" and not report.tool_status.get("slcand", False):
            warn("serial CAN を使うには `slcand` が必要です。`can-utils` の導入を確認してください。")

        section("次のおすすめ")
        if not report.sim_ready:
            bullet("今の active policy で SIM の姿勢と入力応答を確認してください。")
            bullet("確認後に `SIM 確認を記録する` を実行すると、実機前ゲートが通りやすくなります。")
        else:
            bullet("今の active policy は SIM 確認済みとして記録されています。")
        bullet("必要なら `ONNX 読み込みテスト` を先に実行してください。")
        if not report.sim_ready and ask_yes_no("今の active policy で SIM 確認済みなら、ここで記録しますか？", default=False):
            return self._mark_current_policy_sim_verified(ask_confirmation=False)
        return 0

    def handle_mark_sim_verified(self) -> int:
        title("SIM確認を記録")
        return self._mark_current_policy_sim_verified(ask_confirmation=True)

    def handle_setup(self, skip_upgrade: bool = False) -> int:
        title("初回セットアップ")
        if not self._confirm_no_conflicting_jobs({"setup", "build", "policy_switch"}):
            return 1
        bullet("メインCLIは司令塔のままにして、実処理は別ターミナルで流します。")
        bullet("ROS 2 Jazzy の導入確認")
        bullet("mujina_ros の clone")
        bullet("依存関係導入")
        bullet("build")
        bullet("yes / Enter が必要な確認はここでまとめて済ませます。")
        wants_real_setup = ask_yes_no("実機も使う予定なら dialout と udev ルールも一緒に設定しますか？", default=False)
        if not ask_yes_no("この内容でセットアップジョブを起動しますか？", default=True):
            return 1
        job = create_job(
            self.paths,
            kind="setup",
            name="初回セットアップ",
            payload={
                "skip_upgrade": skip_upgrade,
                "setup_real_devices": wants_real_setup,
            },
        )
        return self._launch_job(job)

    def handle_build(self) -> int:
        title("build")
        if not self._require_cloned_workspace():
            return 1
        if not self._confirm_no_conflicting_jobs({"setup", "build", "policy_switch"}):
            return 1
        bullet("build は別ターミナルで実行します。")
        if not ask_yes_no("build ジョブを起動しますか？", default=True):
            return 1
        job = create_job(
            self.paths,
            kind="build",
            name="workspace build",
        )
        return self._launch_job(job)

    def handle_viz(self) -> int:
        title("可視化")
        if not self._require_built_workspace():
            return 1
        if not self._confirm_no_conflicting_jobs({"viz"}):
            return 1
        bullet("RViz は別ターミナルで起動します。")
        bullet("メニュー画面はこのまま残るので、必要なら状態確認やログ確認に戻れます。")
        if not ask_yes_no("可視化ジョブを起動しますか？", default=True):
            return 1
        job = create_job(
            self.paths,
            kind="viz",
            name="RViz 可視化",
        )
        return self._launch_job(job)

    def handle_sim(self) -> int:
        title("SIM")
        if not self._require_built_workspace():
            return 1
        if not self._confirm_no_conflicting_jobs({"sim_main", "sim_joy"}):
            return 1
        bullet("SIM 本体と joy ノードを別ターミナルで起動します。")
        bullet("先に ONNX 読み込みテストが通っていると安心です。")
        if not ask_yes_no("SIM を起動しますか？", default=True):
            return 1
        group_id = f"sim-{uuid4().hex[:8]}"
        jobs = [
            create_job(self.paths, kind="sim_main", name="SIM 本体", group_id=group_id),
            create_job(self.paths, kind="sim_joy", name="SIM 用 joy ノード", group_id=group_id),
        ]
        result = self._launch_job_group(jobs, heading="SIM ジョブを起動しました。")
        if result == 0:
            warn("実機へ進む前に、起動した別ターミナル側で姿勢と入力応答を実際に確認してください。")
            bullet("確認できたら、メニューの `SIM 確認を記録する` か `実機前診断` から記録してください。")
            self.state.last_action = "sim_launch"
            self.state.last_sim_success = False
            self.state.last_sim_policy_hash = ""
            self.save_state()
        return result

    def handle_real_robot(self, can_mode: str = "auto") -> int:
        title("実機起動")
        if not self._require_built_workspace():
            return 1
        if not self._confirm_no_conflicting_jobs({"real_imu", "real_main", "real_joy"}):
            return 1
        self._sync_default_policy_state()
        selected_can_mode = self._select_can_mode(can_mode)
        if selected_can_mode is None:
            return 1
        report = build_doctor_report(self.paths, self.state)
        if selected_can_mode == "serial" and not report.tool_status.get("slcand", False):
            error("serial CAN 用の `slcand` が見つかりません。")
            bullet("Ubuntu 24.04 では通常 `can-utils` の導入で入ります。")
            bullet("必要なら `sudo apt install -y can-utils` を実行してから再試行してください。")
            return 1
        missing = self._missing_devices_for_can_mode(selected_can_mode, include_imu=True, include_joy=True)
        if missing:
            self._report_missing_devices(
                "実機起動に必要なデバイスが足りません。",
                missing,
                can_mode=selected_can_mode,
                include_imu=True,
                include_joy=True,
            )
            return 1
        imu_port = self._resolve_runtime_imu_port()
        if imu_port is None:
            error("IMU のポートを確定できませんでした。")
            bullet("`/dev/rt_usb_imu` が無い場合は、IMU らしい `ttyACM*` / `ttyUSB*` を 1 本に絞る必要があります。")
            return 1
        if not sim_policy_verified(self.state):
            warn("今の policy で SIM 起動済みの記録がありません。")
            bullet("実機へ進む前に、少なくとも一度は同じ policy で SIM を起動して挙動確認するのがおすすめです。")
            if not ask_yes_no("それでも実機起動ジョブを起動しますか？", default=False):
                return 1
        warn("この操作は実機を動かす可能性があります。")
        bullet("IMU / CAN + mujina_main / joy ノードを別ターミナルで起動します。")
        bullet("すべての確認入力は今ここで済ませるので、ワーカー側で Enter 待ちは発生しません。")
        bullet("周囲を空け、補助者がロボット横で監視している状態で進めてください。")
        typed = ask_text("本当に実行する場合だけ REAL と入力してください。")
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
        result = self._launch_job_group(jobs, heading="実機ジョブを起動しました。")
        if result == 0:
            self.state.last_action = "real"
            self.save_state()
        return result

    def handle_policy_menu(self) -> int:
        title("ポリシー切り替え")
        if not self._require_built_workspace():
            return 1
        if not self._confirm_no_conflicting_jobs({"setup", "build", "policy_switch", "sim_main", "real_main"}):
            return 1
        capture_default_policy(self.paths)
        candidates = all_policy_candidates(self.paths)
        options = [f"{candidate.label} | {candidate.description}" for candidate in candidates]
        options.append("手元のパスを入力する")
        selected = select_from_list("使いたい policy を選んでください。", options)
        if selected == len(options) - 1:
            raw_path = Path(ask_text("ONNX ファイルの絶対パスを入力してください。"))
            if not raw_path.exists() or raw_path.suffix.lower() != ".onnx":
                error("有効な .onnx ファイルが見つかりません。")
                return 1
            candidate = PolicyCandidate(
                label=f"手動指定: {raw_path.name}",
                path=raw_path,
                source_type="path",
                description=str(raw_path),
            )
        else:
            candidate = candidates[selected]
        candidate = self._prepare_candidate_for_job(candidate)
        bullet("差し替え・再 build・ONNX 読み込みテストは別ターミナルでまとめて実行します。")
        if not ask_yes_no("この policy へ切り替えますか？", default=True):
            return 1
        job = create_job(
            self.paths,
            kind="policy_switch",
            name=f"policy 切替: {candidate.label}",
            payload=self._candidate_to_payload(candidate),
        )
        return self._launch_job(job)

    def handle_policy_test(self) -> int:
        title("ONNX 読み込みテスト")
        if not self._require_built_workspace():
            return 1
        if not self._confirm_no_conflicting_jobs({"policy_test"}):
            return 1
        bullet("現在の active policy を別ターミナルで読み込みテストします。")
        if not ask_yes_no("ONNX 読み込みテストを起動しますか？", default=True):
            return 1
        job = create_job(
            self.paths,
            kind="policy_test",
            name="ONNX 読み込みテスト",
        )
        return self._launch_job(job)

    def handle_motor_read(self, ids: list[int] | None = None, can_mode: str = "auto") -> int:
        title("モータ確認")
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
                "CAN 通信に必要なデバイスが足りません。",
                missing,
                can_mode=selected_can_mode,
                include_imu=False,
                include_joy=False,
            )
            return 1
        ids = ids or self._ask_ids()
        if not ids:
            error("ID が指定されていません。")
            return 1
        warn("この操作は読み取り専用ですが、CAN 通信を使います。")
        if not ask_yes_no("モータ確認ジョブを起動しますか？", default=True):
            return 1
        job = create_job(
            self.paths,
            kind="motor_read",
            name=f"モータ確認 ({' '.join(str(i) for i in ids)})",
            payload={"ids": ids, "can_mode": selected_can_mode},
        )
        return self._launch_job(job)

    def handle_zero_position(self, ids: list[int] | None = None, can_mode: str = "auto") -> int:
        return self._handle_zero_position_safe(ids=ids, can_mode=can_mode)
        title("初期位置設定")
        if not self._require_built_workspace():
            return 1
        if not self._confirm_no_conflicting_jobs({"motor_read", "zero", "real_main"}):
            return 1
        selected_can_mode = self._select_can_mode(can_mode)
        if selected_can_mode is None:
            return 1
        missing = self._missing_devices_for_can_mode(selected_can_mode, include_imu=False, include_joy=False)
        if missing:
            self._report_missing_devices(
                "CAN 通信に必要なデバイスが足りません。",
                missing,
                can_mode=selected_can_mode,
                include_imu=False,
                include_joy=False,
            )
            return 1
        warn("この操作はモータ原点を変更します。姿勢が誤っていると危険です。")
        bullet("README 記載の原点姿勢になっていることを確認してください。")
        bullet("補助者がいる状態で実行してください。")
        bullet("前提確認として read-only 通信テストもワーカー側で自動実行します。")
        bullet("`motor read` の成功は通信確認だけです。書き込みを伴う zero の成功までは保証しません。")
        typed = ask_text("本当に実行する場合だけ ZERO と入力してください。")
        if typed != "ZERO":
            warn("初期位置設定を中止しました。")
            return 1
        ids = ids or self._ask_ids()
        if not ids:
            error("ID が指定されていません。")
            return 1
        job = create_job(
            self.paths,
            kind="zero",
            name=f"初期位置設定 ({' '.join(str(i) for i in ids)})",
            payload={"ids": ids, "can_mode": selected_can_mode},
        )
        return self._launch_job(job)

    def _handle_zero_position_safe(self, ids: list[int] | None = None, can_mode: str = "auto") -> int:
        title("原点位置設定")
        if not self._require_built_workspace():
            return 1
        if not self._confirm_no_conflicting_jobs({"motor_read", "zero", "real_main"}):
            return 1
        selected_can_mode = self._select_can_mode(can_mode)
        if selected_can_mode is None:
            return 1
        missing = self._missing_devices_for_can_mode(selected_can_mode, include_imu=False, include_joy=False)
        if missing:
            self._report_missing_devices(
                "CAN 通信に必要なデバイスが足りません。",
                missing,
                can_mode=selected_can_mode,
                include_imu=False,
                include_joy=False,
            )
            return 1
        ids = ids or self._ask_ids()
        if not ids:
            error("ID が指定されていません。")
            return 1
        warn("この操作はモータの原点を変更します。姿勢が崩れていると危険です。")
        bullet("README 記載の原点姿勢になっていることを確認してください。")
        bullet("補助者がいる状態で実行してください。")
        bullet("zero の前に one-shot の疎通確認を自動実行し、同じ CAN 設定を使ったまま本処理へ進みます。")
        bullet("対象 ID: " + " ".join(str(i) for i in ids))
        confirmation_phrase = self._zero_confirmation_phrase(ids)
        typed = ask_text(f"本当に実行する場合のみ {confirmation_phrase} と入力してください。")
        if typed != confirmation_phrase:
            warn("原点位置設定を中止しました。")
            return 1
        job = create_job(
            self.paths,
            kind="zero",
            name=f"原点位置設定 ({' '.join(str(i) for i in ids)})",
            payload={"ids": ids, "can_mode": selected_can_mode},
        )
        return self._launch_job(job)

    def handle_logs(self) -> int:
        title("ジョブとログ")
        jobs = recent_jobs(self.paths, limit=10)
        if jobs:
            grouped_jobs: dict[str, list[JobRecord]] = {}
            for job in jobs:
                if job.group_id:
                    grouped_jobs.setdefault(job.group_id, []).append(job)
            if grouped_jobs:
                section("最近のジョブグループ")
                for _group_id, items in list(grouped_jobs.items())[:3]:
                    items = sorted(items, key=lambda item: item.created_at)
                    label = items[0].group_id.split("-", 1)[0].upper()
                    summary = ", ".join(f"{item.name}={item.status}" for item in items)
                    bullet(f"{label}: {summary}")
            section("最近のジョブ")
            for job in jobs:
                bullet(f"{summarize_job(job)} | ログ: {Path(job.log_path).name}")
        else:
            warn("まだジョブ履歴がありません。")
        log_files = sorted(self.paths.logs_dir.glob("*.log"), reverse=True)
        if not log_files:
            warn("まだログがありません。")
            return 0
        options = [f"{log_file.name} ({log_file.stat().st_size} bytes)" for log_file in log_files]
        selected = select_from_list("末尾を表示したいログを選んでください。", options)
        target = log_files[selected]
        section(f"{target.name} の末尾")
        content = target.read_text(encoding="utf-8", errors="ignore").splitlines()
        for line in content[-40:]:
            info(line)
        return 0

    def run_worker(self, job_file: Path) -> int:
        job = load_job(job_file)
        mark_job_running(job)
        try:
            returncode, message, allow_sigint_stop = self._dispatch_worker_job(job)
        except Exception as exc:  # pragma: no cover - defensive path
            log_path = job_log_path(job)
            log_path.parent.mkdir(parents=True, exist_ok=True)
            with log_path.open("a", encoding="utf-8") as handle:
                handle.write("\n[worker exception]\n")
                handle.write("".join(traceback.format_exception(exc)))
            error("ワーカージョブ内部で予期しない例外が発生しました。")
            bullet(f"ログ: {log_path}")
            mark_job_finished(job, returncode=1, message=str(exc))
            return 1

        if allow_sigint_stop and returncode == 130:
            mark_job_stopped(job, returncode=returncode, message=message)
        else:
            mark_job_finished(job, returncode=returncode, message=message)
        return returncode

    def _dispatch_worker_job(self, job: JobRecord) -> tuple[int, str, bool]:
        payload = job.payload
        if job.kind == "setup":
            return self._execute_setup_job(job, payload.get("skip_upgrade", False), payload.get("setup_real_devices", False))
        if job.kind == "build":
            return self._execute_build_job(job)
        if job.kind == "viz":
            return self._execute_shell_job(
                job,
                build_viz_script(self.paths),
                "RViz を閉じました。",
                causes=[
                    "GUI セッションではない端末から起動しています。",
                    "ROS パッケージの build が途中で失敗しています。",
                    "xacro や RViz 関連パッケージが不足しています。",
                ],
                next_steps=[
                    "Ubuntu Desktop の画面上で実行しているか確認してください。",
                    "必要なら `build する` を再実行してからやり直してください。",
                ],
                allow_sigint_stop=True,
            )
        if job.kind == "sim_main":
            return self._execute_shell_job(
                job,
                build_sim_main_script(self.paths),
                "SIM 本体を停止しました。",
                causes=[
                    "MuJoCo / ONNX Runtime の導入が不完全です。",
                    "workspace の build が壊れています。",
                    "policy.onnx の読み込みに失敗しています。",
                ],
                next_steps=[
                    "メニューの `ONNX 読み込みテスト` を先に試してください。",
                    "次に `build する` をやり直してから再試行してください。",
                ],
                allow_sigint_stop=True,
            )
        if job.kind == "sim_joy":
            return self._execute_shell_job(
                job,
                build_joy_script(self.paths),
                "SIM 用 joy ノードを停止しました。",
                causes=[
                    "joy_linux パッケージが不足しています。",
                    "ゲームパッドが接続されていません。",
                ],
                next_steps=[
                    "ゲームパッド接続を確認してください。",
                ],
                allow_sigint_stop=True,
            )
        if job.kind == "real_imu":
            imu_port = str(payload.get("imu_port", "/dev/rt_usb_imu"))
            return self._execute_shell_job(
                job,
                build_real_imu_script(self.paths, imu_port),
                "実機 IMU ノードを停止しました。",
                causes=[
                    "IMU デバイスが見えていません。",
                    "udev 設定や再ログインがまだです。",
                ],
                next_steps=[
                    "`doctor` で /dev/rt_usb_imu を確認してください。",
                ],
                allow_sigint_stop=True,
            )
        if job.kind == "real_main":
            can_mode = payload.get("can_mode", "net")
            return self._execute_shell_job(
                job,
                build_real_main_script(self.paths, can_mode),
                "実機 mujina_main を停止しました。",
                causes=[
                    "CAN 接続方式の選択が実機と合っていません。",
                    "can0 または /dev/usb_can の準備が不十分です。",
                    "policy.onnx の読み込みに失敗しています。",
                ],
                next_steps=[
                    "必要なら CAN モードを見直して再実行してください。",
                    "まず `ONNX 読み込みテスト` と `SIM` を確認してください。",
                ],
                allow_sigint_stop=True,
            )
        if job.kind == "real_joy":
            return self._execute_shell_job(
                job,
                build_joy_script(self.paths),
                "実機 joy ノードを停止しました。",
                causes=[
                    "joy_linux パッケージが不足しています。",
                    "ゲームパッドが接続されていません。",
                ],
                next_steps=[
                    "ゲームパッド接続を確認してください。",
                ],
                allow_sigint_stop=True,
            )
        if job.kind == "policy_switch":
            return self._execute_policy_switch_job(job)
        if job.kind == "policy_test":
            return self._execute_policy_test_job(job)
        if job.kind == "motor_read":
            ids = [int(value) for value in payload.get("ids", [])]
            can_mode = payload.get("can_mode", "net")
            return self._execute_shell_job(
                job,
                build_motor_read_script(self.paths, ids, can_mode),
                "モータ読み取りが完了しました。",
                causes=[
                    "CAN 接続方式の選択が実機と合っていません。",
                    "can0 または /dev/usb_can の準備が不十分です。",
                    "対象 ID のモータが見えていません。",
                ],
                next_steps=[
                    "接続方式を確認してから再実行してください。",
                ],
                allow_sigint_stop=True,
            )
        if job.kind == "zero":
            return self._execute_zero_job(job)
        error(f"未対応のジョブ種別です: {job.kind}")
        return 1, f"unsupported job kind: {job.kind}", False

    def _execute_setup_job(self, job: JobRecord, skip_upgrade: bool, setup_real_devices: bool) -> tuple[int, str, bool]:
        title("初回セットアップ")
        log_path = job_log_path(job)
        result = run_initial_setup(self.paths, log_path, skip_upgrade=skip_upgrade)
        if result.returncode != 0:
            self._report_failure(
                "ROS 2 Jazzy の導入処理で失敗しました。",
                log_path,
                causes=[
                    "ネットワーク接続が不安定です。",
                    "sudo パスワード入力に失敗しています。",
                    "Ubuntu 24.04 以外の環境で実行しています。",
                ],
                next_steps=[
                    "まずログ末尾を確認してください。",
                    "apt 系の失敗なら、ネットワークと sudo 権限を見直してから再実行してください。",
                ],
            )
            return result.returncode, "初回セットアップに失敗しました。", False

        clone_result = ensure_upstream_clone(self.paths, log_path)
        if clone_result.returncode != 0:
            self._report_failure(
                "mujina_ros の clone に失敗しました。",
                log_path,
                causes=[
                    "GitHub へアクセスできません。",
                    "git が導入されていないか、プロキシ設定が必要です。",
                ],
                next_steps=[
                    "ネットワーク疎通を確認してください。",
                ],
            )
            return clone_result.returncode, "mujina_ros の clone に失敗しました。", False

        dependency_result = run_workspace_dependency_setup(self.paths, log_path)
        if dependency_result.returncode != 0:
            self._report_failure(
                "依存関係の導入に失敗しました。",
                log_path,
                causes=[
                    "apt か pip の導入で不足パッケージが残っています。",
                    "Python パッケージ取得時にネットワークエラーが出ています。",
                    "ディスク空き容量が不足しています。",
                ],
                next_steps=[
                    "ログの `rosdep` / `pip` 周辺を確認してください。",
                    "空き容量不足なら `~/.cache/pip` などを整理してから再実行してください。",
                ],
            )
            return dependency_result.returncode, "依存関係の導入に失敗しました。", False

        build_result = run_workspace_build(self.paths, log_path)
        if build_result.returncode != 0:
            self._report_failure(
                "workspace build に失敗しました。",
                log_path,
                causes=[
                    "ROS の source が不完全です。",
                    "依存パッケージが一部不足しています。",
                    "upstream 側の build 条件が変わっています。",
                ],
                next_steps=[
                    "ログの末尾 40 行を優先して確認してください。",
                    "必要なら `build する` を単体でもう一度試してください。",
                ],
            )
            return build_result.returncode, "workspace build に失敗しました。", False

        capture_default_policy(self.paths)
        self._sync_default_policy_state()
        if setup_real_devices:
            real_setup_result = run_real_device_setup(self.paths, log_path)
            if real_setup_result.returncode != 0:
                warn("実機用のデバイス設定は完了しませんでした。SIM や可視化はそのまま使えます。")
            else:
                self.state.real_setup_requires_relogin = True
                warn("実機用のデバイス設定を反映するには、一度ログアウトして再ログインしてください。")

        self.state.last_action = "setup"
        self.state.last_sim_success = False
        self.state.last_sim_policy_hash = ""
        self.save_state()
        success("初回セットアップが完了しました。")
        bullet(f"ログ: {log_path}")
        return 0, "初回セットアップが完了しました。", False

    def _execute_build_job(self, job: JobRecord) -> tuple[int, str, bool]:
        title("build")
        log_path = job_log_path(job)
        result = run_workspace_build(self.paths, log_path)
        if result.returncode != 0:
            self._report_failure(
                "build に失敗しました。",
                log_path,
                causes=[
                    "依存が不足しています。",
                    "upstream のコード変更で build 条件が変わっています。",
                ],
                next_steps=[
                    "`ジョブとログを見る` から build.log の末尾を確認してください。",
                    "初回セットアップ直後なら、もう一度 `初回セットアップ` をやり直すと復旧することがあります。",
                ],
            )
            return result.returncode, "build に失敗しました。", False
        capture_default_policy(self.paths)
        self._sync_default_policy_state()
        self.state.last_action = "build"
        self.state.last_sim_success = False
        self.state.last_sim_policy_hash = ""
        self.save_state()
        success("build が完了しました。")
        return 0, "build が完了しました。", False

    def _execute_policy_switch_job(self, job: JobRecord) -> tuple[int, str, bool]:
        title("ポリシー切り替え")
        candidate = self._candidate_from_payload(job.payload)
        log_path = job_log_path(job)
        ok, message = activate_policy(self.paths, self.state, candidate, log_path)
        if ok:
            self.state.last_sim_success = False
            self.state.last_sim_policy_hash = ""
            self.save_state()
            success(message)
            bullet("この後はまず SIM で確認するのがおすすめです。")
            bullet("必要ならメニューの `ONNX 読み込みテスト` で先に形式確認ができます。")
            return 0, message, False
        self._report_failure(
            message,
            log_path,
            causes=[
                "選んだ ONNX が mujina_ros 想定の入出力 shape と合っていません。",
                "再 build か ONNX 読み込みテストで失敗しています。",
            ],
            next_steps=[
                "まずログの末尾を確認してください。",
                "失敗時はひとつ前の policy へ自動で戻すようにしてあります。",
            ],
        )
        return 1, message, False

    def _execute_policy_test_job(self, job: JobRecord) -> tuple[int, str, bool]:
        title("ONNX 読み込みテスト")
        log_path = job_log_path(job)
        result = run_onnx_self_test(self.paths, log_path)
        if result.returncode == 0:
            success("ONNX 読み込みテストに成功しました。")
            return 0, "ONNX 読み込みテストに成功しました。", False
        self._report_failure(
            "ONNX 読み込みテストに失敗しました。",
            log_path,
            causes=[
                "policy.onnx の形式や shape が合っていません。",
                "onnxruntime の導入が不完全です。",
            ],
            next_steps=[
                "まずログの末尾を確認してください。",
                "必要なら別の policy を選び直してください。",
            ],
        )
        return result.returncode, "ONNX 読み込みテストに失敗しました。", False

    def _execute_zero_job(self, job: JobRecord) -> tuple[int, str, bool]:
        return self._execute_zero_job_safe(job)
        title("初期位置設定")
        ids = [int(value) for value in job.payload.get("ids", [])]
        can_mode = job.payload.get("can_mode", "net")
        if not ids:
            log_path = job_log_path(job)
            self._report_failure(
                "初期位置設定の対象 ID がありません。",
                log_path,
                causes=[
                    "ジョブ作成時の ID 指定が空のままです。",
                    "途中でジョブ定義が壊れた可能性があります。",
                ],
                next_steps=[
                    "もう一度 `初期位置設定` を開き、対象 ID を入れ直してください。",
                    "不安があれば先に `モータの現在値を読む` を同じ ID で試してください。",
                ],
            )
            return 1, "初期位置設定の対象 ID がありません。", False
        preflight_log_path = job_log_path(job).with_suffix(".preflight.log")
        preflight_result = run_bash(
            build_motor_read_script(self.paths, ids, can_mode),
            cwd=self.paths.workspace_dir,
            log_path=preflight_log_path,
            interactive=True,
        )
        if preflight_result.returncode != 0:
            self._report_failure(
                "原点設定の前提確認に失敗しました。",
                preflight_log_path,
                causes=[
                    "選んだ CAN 接続方式と実機が一致していません。",
                    "指定した ID のモータへ read-only 通信できていません。",
                ],
                next_steps=[
                    "まず `モータの現在値を読む` を同じ ID で成功させてください。",
                    "成功してから改めて `初期位置設定` を実行してください。",
                ],
            )
            return preflight_result.returncode, "原点設定の前提確認に失敗しました。", False
        return self._execute_shell_job(
            job,
            build_zero_script(self.paths, ids, can_mode),
            "初期位置設定が完了しました。",
            causes=[
                "CAN 接続方式の選択が合っていません。",
                "対象 ID のモータへ通信できていません。",
                "read-only 通信は通っても、zero は書き込み処理のため別条件で失敗することがあります。",
                "実機姿勢が正しくなく、途中でエラーになっています。",
            ],
            next_steps=[
                "姿勢確認をやり直してから再実行してください。",
                "不安があれば motor read で先に通信だけ確認してください。",
            ],
            allow_sigint_stop=False,
        )

    def _execute_zero_job_safe(self, job: JobRecord) -> tuple[int, str, bool]:
        title("原点位置設定")
        ids = [int(value) for value in job.payload.get("ids", [])]
        can_mode = job.payload.get("can_mode", "net")
        if not ids:
            log_path = job_log_path(job)
            self._report_failure(
                "原点位置設定に対象 ID が含まれていません。",
                log_path,
                causes=[
                    "ジョブ作成時の ID 指定が空のままです。",
                    "途中でジョブ定義が壊れた可能性があります。",
                ],
                next_steps=[
                    "もう一度 `原点位置設定` を開き、対象 ID を入れ直してください。",
                    "不安があれば先に `モータの現在値を読む` を同じ ID で試してください。",
                ],
            )
            return 1, "原点位置設定に対象 ID が含まれていません。", False
        preflight_log_path = job_log_path(job).with_suffix(".preflight.log")
        preflight_result = run_bash(
            build_motor_probe_script(self.paths, ids, can_mode),
            cwd=self.paths.workspace_dir,
            log_path=preflight_log_path,
            interactive=True,
        )
        if preflight_result.returncode == 130:
            warn("原点設定の前提確認を中断しました。")
            bullet(f"ログ: {preflight_log_path}")
            return 130, "原点設定の前提確認を中断しました。", True
        if preflight_result.returncode != 0:
            self._report_failure(
                "原点設定の前提確認に失敗しました。",
                preflight_log_path,
                causes=[
                    "選んだ CAN 接続方式と実機が一致していません。",
                    "指定した ID のモータへ one-shot 通信できていません。",
                ],
                next_steps=[
                    "まず `モータの現在値を読む` を同じ ID で成功させてください。",
                    "成功してから改めて `初期位置設定` を実行してください。",
                ],
            )
            return preflight_result.returncode, "原点設定の前提確認に失敗しました。", False
        return self._execute_shell_job(
            job,
            build_zero_script(self.paths, ids, can_mode, include_can_setup=False),
            "初期位置設定が完了しました。",
            causes=[
                "CAN 接続方式の選択が合っていません。",
                "対象 ID のモータへ通信できていません。",
                "実機姿勢が正しくなく、途中でエラーになっています。",
            ],
            next_steps=[
                "姿勢確認をやり直してから再実行してください。",
                "不安があれば motor read で先に通信だけ確認してください。",
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
        log_path = job_log_path(job)
        result = run_bash(script, cwd=self.paths.workspace_dir, log_path=log_path, interactive=True)
        if result.returncode == 0:
            success(success_message)
            bullet(f"ログ: {log_path}")
            return 0, success_message, allow_sigint_stop
        if allow_sigint_stop and result.returncode == 130:
            warn("ジョブは Ctrl+C で停止されました。")
            bullet(f"ログ: {log_path}")
            return 130, "ユーザー操作で停止しました。", True
        self._report_failure(
            f"{job.name} に失敗しました。",
            log_path,
            causes=causes,
            next_steps=next_steps,
        )
        return result.returncode, f"{job.name} に失敗しました。", allow_sigint_stop

    def _launch_job(self, job: JobRecord) -> int:
        launch = launch_job(self.paths, job)
        if not launch.ok:
            mark_job_finished(job, returncode=1, message=launch.message)
            error("ジョブの起動に失敗しました。")
            bullet(launch.message)
            bullet(f"ログ予定先: {job.log_path}")
            return 1
        update_job(job, terminal_mode=launch.mode, terminal_label=launch.label)
        success(launch.message)
        bullet(f"ジョブ: {job.name}")
        bullet(f"ログ: {job.log_path}")
        if launch.mode == "tmux":
            bullet(f"確認コマンド: tmux attach -t {launch.label}")
        else:
            bullet("別ターミナルで実ログが流れます。メニュー画面はこのまま使えます。")
        return 0

    def _launch_job_group(self, jobs: list[JobRecord], *, heading: str) -> int:
        launches: list[tuple[JobRecord, str, str]] = []
        for job in jobs:
            launch = launch_job(self.paths, job)
            if not launch.ok:
                mark_job_finished(job, returncode=1, message=launch.message)
                error("ジョブグループの起動途中で失敗しました。")
                bullet(f"失敗したジョブ: {job.name}")
                bullet(launch.message)
                return 1
            update_job(job, terminal_mode=launch.mode, terminal_label=launch.label)
            launches.append((job, launch.mode, launch.label))
        success(heading)
        for job, mode, label in launches:
            bullet(f"{job.name} | ログ: {job.log_path}")
            if mode == "tmux":
                bullet(f"tmux attach -t {label}")
        if any(mode == "terminal" for _, mode, _ in launches):
            bullet("各ジョブは別ターミナルで動いています。停止はそれぞれの端末で Ctrl+C を使ってください。")
        return 0

    def _confirm_no_conflicting_jobs(self, relevant_kinds: set[str]) -> bool:
        conflicts = [job for job in active_jobs(self.paths) if job.kind in relevant_kinds]
        if not conflicts:
            return True
        warn("同系統のジョブがまだ実行中です。")
        for job in conflicts:
            bullet(f"{job.name} | ログ: {Path(job.log_path).name}")
        return ask_yes_no("それでも新しいジョブを起動しますか？", default=False)

    def _require_cloned_workspace(self) -> bool:
        if workspace_clone_ready(self.paths):
            return True
        error("mujina_ros の clone が未完了です。先に初回セットアップを実行してください。")
        return False

    def _require_built_workspace(self) -> bool:
        if not self._require_cloned_workspace():
            return False
        if workspace_build_ready(self.paths):
            return True
        error("build がまだ終わっていません。先に build を実行してください。")
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
        if preferred in {"net", "serial"}:
            warn(f"指定された CAN モード {preferred} は今の接続状態では使えません。")
            if preferred == "serial" and serial_candidates:
                bullet("汎用 USB シリアル機器は見えていますが、想定名 `/dev/usb_can` がありません。")
                for candidate in serial_candidates[:4]:
                    bullet(candidate)
        options: list[tuple[str, str]] = []
        if net_available:
            options.append(("net", "network CAN: can0 を使います"))
        if serial_available:
            options.append(("serial", "serial CAN: /dev/usb_can を使います"))
        if not options:
            error("利用可能な CAN デバイスが見つかりません。")
            if serial_candidates:
                bullet("汎用 USB シリアル候補は見えています。udev ルールで `/dev/usb_can` に固定されているか確認してください。")
                for candidate in serial_candidates[:4]:
                    bullet(candidate)
            return None
        if len(options) == 1:
            return options[0][0]
        selected = select_from_list("使う CAN 接続方式を選んでください。", [label for _, label in options])
        return options[selected][0]

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
            imu_port, _imu_fallback, _imu_candidates = resolve_imu_port()
            required.append("/dev/rt_usb_imu" if imu_port is None else imu_port)
        if include_joy:
            required.append("/dev/input/js0")
        required.append("can0" if can_mode == "net" else "/dev/usb_can")
        missing: list[str] = []
        for name in required:
            if name in {"/dev/rt_usb_imu", "/dev/usb_can", "/dev/input/js0", "can0"}:
                if not devices.get(name, False):
                    missing.append(name)
                continue
            if not Path(name).exists():
                missing.append(name)
        return missing

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
        serial_candidates = list_serial_device_candidates()
        for item in missing:
            bullet(item)
        section("次にやること")
        if include_imu and "/dev/rt_usb_imu" in missing:
            bullet("IMU を挿し直し、必要なら一度ログアウトして再ログインしてください。")
            if serial_candidates:
                bullet("他の USB シリアル候補は見えていますが、IMU か別機器かはこの CLI だけでは断定できません。")
        generic_imu_missing = [item for item in missing if item.startswith("/dev/ttyACM") or item.startswith("/dev/ttyUSB")]
        if include_imu and generic_imu_missing:
            bullet("IMU の固定名 `/dev/rt_usb_imu` はありませんが、generic serial を IMU 候補として見ています。")
            for candidate in generic_imu_missing[:2]:
                bullet(f"候補ポート: {candidate}")
            bullet("このポートで IMU ドライバを起動します。問題があれば udev ルール整備をおすすめします。")
        if can_mode == "net" and "can0" in missing:
            bullet("network CAN を使うなら、CAN セットアップ後に `can0` が見える状態で再実行してください。")
            bullet("USB-CAN が serial 型なら、実行前に CAN モードを `serial` に切り替えてください。")
        if can_mode == "serial" and "/dev/usb_can" in missing:
            bullet("serial CAN アダプタを挿し直し、必要なら `dialout` と udev 設定後に再ログインしてください。")
            bullet("すでに `can0` がある構成なら、CAN モードを `net` に切り替えてください。")
            if serial_candidates:
                bullet("他の USB シリアル候補は見えていますが、USB-CAN か別機器かはこの CLI だけでは断定できません。")
                for candidate in serial_candidates[:4]:
                    bullet(candidate)
                bullet("機器の VID/PID が `90-mujina.rules` と一致しているか確認してください。")
        if include_joy and "/dev/input/js0" in missing:
            bullet("ゲームパッドをつなぎ直し、OS から認識されていることを確認してください。")

    def _diagnostic_can_mode(self, preferred: str) -> str:
        devices = detect_real_devices()
        if preferred in {"net", "serial"}:
            return preferred
        net_available = devices.get("can0", False)
        serial_available = devices.get("/dev/usb_can", False)
        if net_available and serial_available:
            selected = select_from_list(
                "診断したい CAN モードを選んでください。",
                [
                    "network CAN を前提に診断する",
                    "serial CAN を前提に診断する",
                ],
            )
            return "net" if selected == 0 else "serial"
        if net_available:
            return "net"
        if serial_available:
            return "serial"
        serial_candidates = list_serial_device_candidates()
        if serial_candidates:
            warn("`can0` も `/dev/usb_can` も見えていません。")
            bullet("汎用 USB シリアル候補はありますが、IMU / USB-CAN のどちらかはこの CLI だけでは断定できません。")
            for candidate in serial_candidates[:4]:
                bullet(candidate)
            selected = select_from_list(
                "確認したい CAN モードを選んでください。",
                [
                    "network CAN を前提に診断する",
                    "serial CAN を前提に診断する",
                ],
            )
            return "net" if selected == 0 else "serial"
        return "net"

    def _resolve_runtime_imu_port(self) -> str | None:
        port, _fallback, candidates = resolve_imu_port()
        if port:
            return port
        if not candidates:
            return None
        selected = select_from_list("IMU として使うポートを選んでください。", candidates)
        return candidates[selected]

    def _zero_confirmation_phrase(self, ids: list[int]) -> str:
        return "ZERO " + " ".join(str(value) for value in ids)

    def _mark_current_policy_sim_verified(self, *, ask_confirmation: bool) -> int:
        if not self._require_built_workspace():
            return 1
        self._sync_default_policy_state()
        if not self.state.active_policy_hash:
            error("active policy の hash を取得できませんでした。先に build か policy 切替を確認してください。")
            return 1
        bullet("現在の active policy で、SIM の姿勢と入力応答を人が確認できたあとに使ってください。")
        if ask_confirmation and not ask_yes_no("今の active policy で SIM の確認を完了しましたか？", default=False):
            warn("SIM 確認記録を中止しました。")
            return 1
        self.state.last_action = "sim_verified"
        self.state.last_sim_success = True
        self.state.last_sim_policy_hash = self.state.active_policy_hash
        self.save_state()
        success("今の active policy を SIM 確認済みとして記録しました。")
        return 0

    def _ask_ids(self) -> list[int]:
        raw = ask_text("対象の motor ID を空白またはカンマ区切りで入力してください。例: 1 2 3 / 1,2,3")
        normalized = raw.replace(",", " ")
        tokens = [chunk for chunk in normalized.split() if chunk]
        if not tokens:
            return []
        invalid = [chunk for chunk in tokens if not chunk.isdigit()]
        if invalid:
            error("ID の入力に数字以外が含まれています。")
            bullet(f"解釈できなかった値: {', '.join(invalid)}")
            return []
        values = [int(chunk) for chunk in tokens]
        bullet("対象 ID: " + " ".join(str(value) for value in values))
        if not ask_yes_no("この ID で続けますか？", default=True):
            warn("ID 入力をやり直してください。")
            return []
        return values

    def _sync_default_policy_state(self) -> None:
        if not self.paths.source_policy_path.exists():
            return
        try:
            from mujina_assist.services.checks import file_hash

            current_hash = file_hash(self.paths.source_policy_path)
            self.state.active_policy_hash = current_hash
            if self.paths.default_policy_cache.exists():
                default_hash = file_hash(self.paths.default_policy_cache)
                if current_hash == default_hash:
                    self.state.active_policy_label = "公式デフォルト"
                    self.state.active_policy_source = str(self.paths.default_policy_cache)
        except Exception:
            self.state.active_policy_hash = ""

    def _candidate_to_payload(self, candidate: PolicyCandidate) -> dict[str, str]:
        return {
            "label": candidate.label,
            "path": str(candidate.path),
            "source_type": candidate.source_type,
            "description": candidate.description,
            "manifest_path": str(candidate.manifest_path) if candidate.manifest_path else "",
        }

    def _prepare_candidate_for_job(self, candidate: PolicyCandidate) -> PolicyCandidate:
        if candidate.source_type in {"default", "cache"}:
            return candidate
        cached_path = import_policy_to_cache(self.paths, candidate)
        return PolicyCandidate(
            label=candidate.label,
            path=cached_path,
            source_type="cache",
            description=candidate.description,
            manifest_path=None,
        )

    def _candidate_from_payload(self, payload: dict) -> PolicyCandidate:
        manifest_path = payload.get("manifest_path") or None
        return PolicyCandidate(
            label=str(payload.get("label", "policy")),
            path=Path(str(payload.get("path", ""))),
            source_type=str(payload.get("source_type", "path")),
            description=str(payload.get("description", "")),
            manifest_path=Path(manifest_path) if manifest_path else None,
        )

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
    parser = argparse.ArgumentParser(prog="mujina-assist", description="mujina_ros を案内付きで扱う CLI アプリ")
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

    robot_parser = subparsers.add_parser("robot")
    robot_parser.add_argument("--can-mode", choices=["auto", "net", "serial"], default="auto")

    policy_parser = subparsers.add_parser("policy")
    policy_parser.add_argument("--test", action="store_true")

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
    if command == "robot":
        return app.handle_real_robot(can_mode=args.can_mode)
    if command == "policy":
        return app.handle_policy_test() if args.test else app.handle_policy_menu()
    if command == "motor-read":
        return app.handle_motor_read(ids=args.ids, can_mode=args.can_mode)
    if command == "zero":
        return app.handle_zero_position(ids=args.ids, can_mode=args.can_mode)
    if command == "worker":
        return app.run_worker(Path(args.job_file))

    parser.print_help()
    return 1
