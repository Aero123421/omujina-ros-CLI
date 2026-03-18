from __future__ import annotations

import argparse
from pathlib import Path

from mujina_assist.models import AppPaths, PolicyCandidate
from mujina_assist.services.checks import build_doctor_report, detect_real_devices, workspace_build_ready, workspace_clone_ready, write_config_file
from mujina_assist.services.policy import activate_policy, all_policy_candidates
from mujina_assist.services.processes import attach_tmux_session, kill_tmux_session, start_real_session, start_sim_session, tmux_available, tmux_session_dead_panes, tmux_session_exists
from mujina_assist.services.shell import run_bash, shell_quote
from mujina_assist.services.state import load_runtime_state, save_runtime_state
from mujina_assist.services.workspace import capture_default_policy, ensure_upstream_clone, ros_prefix, run_initial_setup, run_onnx_self_test, run_real_device_setup, run_workspace_build, run_workspace_dependency_setup
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
        bullet(f"USB上のONNX: {report.usb_policy_count} 件")
        real_devices = ", ".join(
            [f"{name}={'OK' if ok else 'NG'}" for name, ok in report.real_devices.items()]
        )
        bullet(f"実機デバイス: {real_devices}")
        tools = ", ".join([f"{name}={'OK' if ok else 'NG'}" for name, ok in report.tool_status.items()])
        bullet(f"主要ツール: {tools}")
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
                "build する",
                "可視化する",
                "SIM を起動する",
                "実機を起動する",
                "ポリシーを切り替える",
                "ONNX 読み込みテスト",
                "モータの現在値を読む",
                "初期位置を設定する",
                "ログを見る",
                "終了",
            ]
            selection = select_from_list("やりたいことを選んでください。", options)
            print()
            if selection == 0:
                self.handle_setup()
            elif selection == 1:
                self.handle_doctor()
            elif selection == 2:
                self.handle_build()
            elif selection == 3:
                self.handle_viz()
            elif selection == 4:
                self.handle_sim()
            elif selection == 5:
                self.handle_real_robot()
            elif selection == 6:
                self.handle_policy_menu()
            elif selection == 7:
                self.handle_policy_test()
            elif selection == 8:
                self.handle_motor_read()
            elif selection == 9:
                self.handle_zero_position()
            elif selection == 10:
                self.handle_logs()
            elif selection == 11:
                return 0
            pause()

    def handle_doctor(self) -> int:
        self.print_status()
        return 0

    def handle_setup(self, skip_upgrade: bool = False) -> int:
        title("初回セットアップ")
        bullet("ROS 2 Jazzy の導入確認")
        bullet("mujina_ros の clone")
        bullet("依存関係導入")
        bullet("build")
        warn("途中で sudo パスワード入力が必要になることがあります。")
        if not ask_yes_no("このまま実行しますか？"):
            return 1

        log_path = self.paths.logs_dir / "setup.log"
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
            return result.returncode

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
                    "必要ならあとで `ログを見る` から clone 部分を確認してください。",
                ],
            )
            return clone_result.returncode

        dependency_result = run_workspace_dependency_setup(self.paths, log_path)
        if dependency_result.returncode != 0:
            self._report_failure(
                "依存関係の導入に失敗しました。",
                log_path,
                causes=[
                    "apt か pip の導入で不足パッケージが残っています。",
                    "Python パッケージ取得時にネットワークエラーが出ています。",
                ],
                next_steps=[
                    "ログの `rosdep` / `pip` 周辺を確認してください。",
                    "途中まで入っていれば、そのまま `初回セットアップ` を再実行して続きから復旧できることがあります。",
                ],
            )
            return dependency_result.returncode

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
            return build_result.returncode

        capture_default_policy(self.paths)
        self._sync_default_policy_state()
        if ask_yes_no("実機も使う予定なら dialout と udev ルールを設定しますか？", default=False):
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
        return 0

    def handle_build(self) -> int:
        title("build")
        if not self.paths.upstream_dir.exists():
            error("workspace がありません。先に初回セットアップを実行してください。")
            return 1
        log_path = self.paths.logs_dir / "build.log"
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
                    "`ログを見る` から build.log の末尾を確認してください。",
                    "初回セットアップ直後なら、もう一度 `初回セットアップ` をやり直すと復旧することがあります。",
                ],
            )
            return result.returncode
        capture_default_policy(self.paths)
        self._sync_default_policy_state()
        self.state.last_action = "build"
        self.state.last_sim_success = False
        self.state.last_sim_policy_hash = ""
        self.save_state()
        success("build が完了しました。")
        return 0

    def handle_viz(self) -> int:
        title("可視化")
        if not self._require_built_workspace():
            return 1
        info("これから RViz を起動してロボットの見た目を確認します。")
        if not ask_yes_no("続けますか？", default=True):
            return 1
        log_path = self.paths.logs_dir / "viz.log"
        script = " && ".join(
            [
                ros_prefix(self.paths),
                f"cd {shell_quote(self.paths.workspace_dir)}",
                "ros2 launch mujina_description display.launch.py",
            ]
        )
        result = run_bash(script, cwd=self.paths.workspace_dir, log_path=log_path, interactive=True)
        if result.returncode != 0:
            self._report_failure(
                "RViz の起動に失敗しました。",
                log_path,
                causes=[
                    "GUI セッションではない端末から起動しています。",
                    "ROS パッケージの build が途中で失敗しています。",
                    "xacro や RViz 関連パッケージが不足しています。",
                ],
                next_steps=[
                    "Ubuntu Desktop の画面上で実行しているか確認してください。",
                    "必要なら `build する` を再実行してからやり直してください。",
                ],
            )
        return result.returncode

    def handle_sim(self) -> int:
        title("SIM")
        if not self._require_built_workspace():
            return 1
        if not tmux_available():
            warn("tmux が見つからないため、sim 本体だけを単独プロセスで起動します。")
            bullet("この経路では joy ノードは自動起動しません。")
            bullet("別ターミナルで使うコマンド: ros2 run joy_linux joy_linux_node")
            log_path = self.paths.logs_dir / "sim.log"
            script = " && ".join(
                [
                    ros_prefix(self.paths),
                    f"cd {shell_quote(self.paths.workspace_dir)}",
                    "ros2 run mujina_control mujina_main --sim",
                ]
            )
            result = run_bash(script, cwd=self.paths.workspace_dir, log_path=log_path, interactive=True)
            if result.returncode != 0:
                self._report_failure(
                    "SIM 起動に失敗しました。",
                    log_path,
                    causes=[
                        "MuJoCo / ONNX Runtime の導入が不完全です。",
                        "workspace の build が壊れています。",
                        "policy.onnx の読み込みに失敗しています。",
                    ],
                    next_steps=[
                        "メニューの `ONNX 読み込みテスト` を先に試してください。",
                        "次に `build する` をやり直してから再試行してください。",
                    ],
                )
            else:
                bullet(f"ログ: {log_path}")
        else:
            session_name = "mujina-sim"
            if tmux_session_exists(session_name):
                if ask_yes_no("既存の sim セッションへそのまま戻りますか？", default=True):
                    result_code = attach_tmux_session(session_name)
                else:
                    kill_tmux_session(session_name)
                    result_code = start_sim_session(self.paths, session_name, with_joy=True)
            else:
                result_code = start_sim_session(self.paths, session_name, with_joy=True)
            result = type("SimResult", (), {"returncode": result_code})()
            self.state.tmux_session_name = session_name
        self.state.last_action = "sim"
        self.state.last_sim_success = result.returncode == 0
        self.state.last_sim_policy_hash = self.state.active_policy_hash if result.returncode == 0 else ""
        self.save_state()
        return result.returncode

    def handle_real_robot(self, can_mode: str = "auto") -> int:
        title("実機起動")
        if not self._require_built_workspace():
            return 1
        selected_can_mode = self._select_can_mode(can_mode)
        if selected_can_mode is None:
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
        if not self.state.last_sim_success or self.state.last_sim_policy_hash != self.state.active_policy_hash:
            warn("まだこの policy での直前 SIM 成功が確認できていません。")
            if not ask_yes_no("それでも実機起動へ進みますか？", default=False):
                return 1
        warn("この操作は実機を動かす可能性があります。")
        bullet("tmux 上で IMU / CAN + mujina_main / joy ノードをまとめて起動します。")
        bullet("停止するときは各ペインで Ctrl+C、または別端末から `tmux kill-session -t mujina-real` を使います。")
        bullet("周囲を空け、補助者がロボット横で監視している状態で進めてください。")
        bullet("SIM で確認済みでも、実機の安全は保証されません。")
        typed = ask_text("本当に実行する場合だけ REAL と入力してください。")
        if typed != "REAL":
            warn("実機起動を中止しました。")
            return 1
        if not tmux_available():
            error("実機起動は tmux 前提で実装しています。tmux を導入してください。")
            return 1
        session_name = "mujina-real"
        if tmux_session_exists(session_name):
            if ask_yes_no("既存の実機セッションへそのまま戻りますか？", default=True):
                result_code = attach_tmux_session(session_name)
            else:
                kill_tmux_session(session_name)
                result_code = start_real_session(self.paths, session_name, can_mode=selected_can_mode)
        else:
            result_code = start_real_session(self.paths, session_name, can_mode=selected_can_mode)
        if result_code == 0 and tmux_session_dead_panes(session_name) > 0:
            warn("tmux セッションは作れましたが、起動直後に落ちたペインがあります。")
            bullet("各ペインのログを確認してください。")
            result_code = 1
        self.state.tmux_session_name = session_name
        self.state.last_action = "real"
        self.save_state()
        return result_code

    def handle_policy_menu(self) -> int:
        title("ポリシー切り替え")
        if not self._require_built_workspace():
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
        log_path = self.paths.logs_dir / "policy.log"
        ok, message = activate_policy(self.paths, self.state, candidate, log_path)
        if ok:
            self.state.last_sim_success = False
            self.state.last_sim_policy_hash = ""
            self.save_state()
            success(message)
            bullet("この後はまず SIM で確認するのがおすすめです。")
            bullet("必要ならメニューの `ONNX 読み込みテスト` で先に形式確認ができます。")
            return 0
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
        return 1

    def handle_motor_read(self, ids: list[int] | None = None, can_mode: str = "auto") -> int:
        title("モータ確認")
        if not self._require_built_workspace():
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
        if not ask_yes_no("続けますか？", default=False):
            return 1
        script = self._build_motor_read_script(ids, selected_can_mode)
        log_path = self.paths.logs_dir / "motor-read.log"
        result = run_bash(script, cwd=self.paths.workspace_dir, log_path=log_path, interactive=True)
        if result.returncode != 0:
            self._report_failure(
                "モータ読み取りに失敗しました。",
                log_path,
                causes=[
                    "CAN 接続方式の選択が実機と合っていません。",
                    "can0 または /dev/usb_can の準備が不十分です。",
                    "対象 ID のモータが見えていません。",
                ],
                next_steps=[
                    "接続方式を確認してから再実行してください。",
                    "必要なら `実機` メニュー前に device 設定や CAN 設定を見直してください。",
                ],
            )
        return result.returncode

    def handle_zero_position(self, ids: list[int] | None = None, can_mode: str = "auto") -> int:
        title("初期位置設定")
        if not self._require_built_workspace():
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
        bullet("停止ややり直しが難しい操作なので、不安があればここで中止してください。")
        typed = ask_text("本当に実行する場合だけ ZERO と入力してください。")
        if typed != "ZERO":
            warn("初期位置設定を中止しました。")
            return 1
        ids = ids or self._ask_ids()
        if not ids:
            error("ID が指定されていません。")
            return 1
        preflight_script = self._build_motor_read_script(ids, selected_can_mode)
        preflight_log_path = self.paths.logs_dir / "motor-zero-preflight.log"
        preflight_result = run_bash(preflight_script, cwd=self.paths.workspace_dir, log_path=preflight_log_path, interactive=True)
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
            return preflight_result.returncode
        can_script = "./mujina_control/scripts/can_setup_serial.sh" if selected_can_mode == "serial" else "./mujina_control/scripts/can_setup_net.sh"
        script = " && ".join(
            [
                ros_prefix(self.paths),
                f"cd {shell_quote(self.paths.upstream_dir)}",
                can_script,
                "python3 mujina_control/scripts/motor_set_zero_position.py --ids " + " ".join(str(i) for i in ids),
            ]
        )
        log_path = self.paths.logs_dir / "motor-zero.log"
        result = run_bash(script, cwd=self.paths.workspace_dir, log_path=log_path, interactive=True)
        if result.returncode != 0:
            self._report_failure(
                "初期位置設定に失敗しました。",
                log_path,
                causes=[
                    "CAN 接続方式の選択が合っていません。",
                    "対象 ID のモータへ通信できていません。",
                    "実機姿勢が正しくなく、途中でエラーになっています。",
                ],
                next_steps=[
                    "姿勢確認をやり直してから再実行してください。",
                    "不安があれば motor read で先に通信だけ確認してください。",
                ],
            )
        return result.returncode

    def handle_policy_test(self) -> int:
        title("ONNX 読み込みテスト")
        if not self._require_built_workspace():
            return 1
        log_path = self.paths.logs_dir / "policy-test.log"
        result = run_onnx_self_test(self.paths, log_path)
        if result.returncode == 0:
            success("ONNX 読み込みテストに成功しました。")
        else:
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
        return result.returncode

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
        if self.paths.default_policy_cache.exists():
            self.state.active_policy_label = "公式デフォルト"
            self.state.active_policy_source = str(self.paths.default_policy_cache)
        try:
            from mujina_assist.services.checks import file_hash

            self.state.active_policy_hash = file_hash(self.paths.source_policy_path)
        except Exception:
            self.state.active_policy_hash = ""

    def handle_logs(self) -> int:
        title("ログ表示")
        log_files = sorted(self.paths.logs_dir.glob("*.log"))
        if not log_files:
            warn("まだログがありません。")
            return 0
        options = [f"{log_file.name} ({log_file.stat().st_size} bytes)" for log_file in log_files]
        selected = select_from_list("表示したいログを選んでください。", options)
        target = log_files[selected]
        section(f"{target.name} の末尾")
        content = target.read_text(encoding="utf-8", errors="ignore").splitlines()
        for line in content[-40:]:
            info(line)
        return 0

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
        net_available = devices.get("can0", False)
        serial_available = devices.get("/dev/usb_can", False)
        if preferred == "net" and net_available:
            return "net"
        if preferred == "serial" and serial_available:
            return "serial"
        if preferred in {"net", "serial"}:
            warn(f"指定された CAN モード {preferred} は今の接続状態では使えません。")
        options: list[tuple[str, str]] = []
        if net_available:
            options.append(("net", "network CAN: can0 を使います"))
        if serial_available:
            options.append(("serial", "serial CAN: /dev/usb_can を使います"))
        if not options:
            error("利用可能な CAN デバイスが見つかりません。")
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
            required.append("/dev/rt_usb_imu")
        if include_joy:
            required.append("/dev/input/js0")
        required.append("can0" if can_mode == "net" else "/dev/usb_can")
        return [name for name in required if not devices.get(name, False)]

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
        if include_imu and "/dev/rt_usb_imu" in missing:
            bullet("IMU を挿し直し、必要なら一度ログアウトして再ログインしてください。")
        if can_mode == "net" and "can0" in missing:
            bullet("network CAN を使うなら、CAN セットアップ後に `can0` が見える状態で再実行してください。")
            bullet("USB-CAN が serial 型なら、実行前に CAN モードを `serial` に切り替えてください。")
        if can_mode == "serial" and "/dev/usb_can" in missing:
            bullet("serial CAN アダプタを挿し直し、必要なら `dialout` と udev 設定後に再ログインしてください。")
            bullet("すでに `can0` がある構成なら、CAN モードを `net` に切り替えてください。")
        if include_joy and "/dev/input/js0" in missing:
            bullet("ゲームパッドをつなぎ直し、OS から認識されていることを確認してください。")

    def _build_motor_read_script(self, ids: list[int], can_mode: str) -> str:
        can_script = "./mujina_control/scripts/can_setup_serial.sh" if can_mode == "serial" else "./mujina_control/scripts/can_setup_net.sh"
        return " && ".join(
            [
                ros_prefix(self.paths),
                f"cd {shell_quote(self.paths.upstream_dir)}",
                can_script,
                "python3 mujina_control/scripts/motor_test_read_only.py --ids " + " ".join(str(i) for i in ids),
            ]
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
    subparsers.add_parser("build")
    subparsers.add_parser("viz")
    subparsers.add_parser("sim")
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
    if command == "build":
        return app.handle_build()
    if command == "viz":
        return app.handle_viz()
    if command == "sim":
        return app.handle_sim()
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

    parser.print_help()
    return 1
