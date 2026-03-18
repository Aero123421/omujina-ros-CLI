from __future__ import annotations

import json
import re
import shutil
from datetime import datetime
from pathlib import Path

from mujina_assist.models import AppPaths, PolicyCandidate, RuntimeState
from mujina_assist.services.checks import file_hash
from mujina_assist.services.workspace import capture_default_policy, run_onnx_self_test, run_workspace_build_with_options


def discover_usb_policies() -> list[PolicyCandidate]:
    candidates: list[PolicyCandidate] = []
    user = Path.home().name
    roots = [Path("/media") / user, Path("/run/media") / user]
    for root in roots:
        if not root.exists():
            continue
        for mounted in sorted(root.iterdir()):
            if not mounted.is_dir():
                continue
            for onnx_path in sorted(mounted.rglob("*.onnx")):
                manifest = onnx_path.with_suffix(".manifest.json")
                desc = _describe_policy_file(onnx_path, manifest if manifest.exists() else None)
                candidates.append(
                    PolicyCandidate(
                        label=f"USB: {onnx_path.name}",
                        path=onnx_path,
                        source_type="usb",
                        description=desc,
                        manifest_path=manifest if manifest.exists() else None,
                    )
                )
    return candidates


def cached_policy_candidates(paths: AppPaths) -> list[PolicyCandidate]:
    candidates: list[PolicyCandidate] = []
    if paths.default_policy_cache.exists():
        candidates.append(
            PolicyCandidate(
                label="公式デフォルト",
                path=paths.default_policy_cache,
                source_type="default",
                description="最初に clone した公式 policy.onnx",
            )
        )
    for onnx_path in sorted(paths.imported_policy_dir.glob("*.onnx")):
        candidates.append(
            PolicyCandidate(
                label=f"キャッシュ: {onnx_path.name}",
                path=onnx_path,
                source_type="cache",
                description=f"保存済み {onnx_path.name}",
            )
        )
    return candidates


def all_policy_candidates(paths: AppPaths) -> list[PolicyCandidate]:
    return cached_policy_candidates(paths) + discover_usb_policies()


def import_policy_to_cache(paths: AppPaths, candidate: PolicyCandidate) -> Path:
    if candidate.source_type in {"default", "cache"}:
        return candidate.path
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    sanitized = re.sub(r"[^A-Za-z0-9._-]+", "_", candidate.path.name)
    destination = paths.imported_policy_dir / f"{timestamp}-{sanitized}"
    shutil.copy2(candidate.path, destination)
    if candidate.manifest_path and candidate.manifest_path.exists():
        manifest_destination = destination.with_suffix(".manifest.json")
        shutil.copy2(candidate.manifest_path, manifest_destination)
    return destination


def activate_policy(
    paths: AppPaths,
    state: RuntimeState,
    candidate: PolicyCandidate,
    log_path: Path,
) -> tuple[bool, str]:
    if not paths.upstream_dir.exists():
        return False, "workspace がまだ作られていません。先に初回セットアップを実行してください。"
    capture_default_policy(paths)
    cached_source = import_policy_to_cache(paths, candidate)
    previous_policy = paths.source_policy_path.read_bytes() if paths.source_policy_path.exists() else None
    previous_label = state.active_policy_label
    previous_source = state.active_policy_source
    previous_hash = state.active_policy_hash
    paths.source_policy_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(cached_source, paths.source_policy_path)

    build_result = run_workspace_build_with_options(
        paths,
        log_path,
        packages=["mujina_control"],
        run_rosdep_step=False,
        install_python_deps=False,
    )
    if build_result.returncode != 0:
        rollback_ok = _restore_previous_policy(paths, previous_policy, state, previous_label, previous_source, previous_hash, log_path)
        suffix = "" if rollback_ok else " 元の policy の復旧にも失敗したため、手動確認が必要です。"
        return False, f"policy の差し替え後の build に失敗しました。{suffix}"

    test_result = run_onnx_self_test(paths, log_path)
    if test_result.returncode != 0:
        rollback_ok = _restore_previous_policy(paths, previous_policy, state, previous_label, previous_source, previous_hash, log_path)
        suffix = "" if rollback_ok else " 元の policy の復旧にも失敗したため、手動確認が必要です。"
        return False, f"ONNX 読み込みテストに失敗しました。別の policy を選んでください。{suffix}"

    state.active_policy_label = candidate.label
    state.active_policy_source = str(cached_source)
    state.active_policy_hash = file_hash(paths.source_policy_path)
    state.last_action = "policy_switch"
    state.last_sim_success = False
    return True, f"{candidate.label} を active policy にしました。"


def _restore_previous_policy(
    paths: AppPaths,
    previous_policy: bytes | None,
    state: RuntimeState,
    previous_label: str,
    previous_source: str,
    previous_hash: str,
    log_path: Path,
) -> bool:
    if previous_policy is None:
        if paths.source_policy_path.exists():
            paths.source_policy_path.unlink()
    else:
        paths.source_policy_path.write_bytes(previous_policy)
    rollback_result = run_workspace_build_with_options(
        paths,
        log_path,
        packages=["mujina_control"],
        run_rosdep_step=False,
        install_python_deps=False,
    )
    state.active_policy_label = previous_label
    state.active_policy_source = previous_source
    state.active_policy_hash = previous_hash
    return rollback_result.returncode == 0


def _describe_policy_file(onnx_path: Path, manifest_path: Path | None) -> str:
    size_mb = onnx_path.stat().st_size / (1024 * 1024)
    parts = [f"{size_mb:.1f} MB", onnx_path.parent.name]
    if manifest_path and manifest_path.exists():
        try:
            data = json.loads(manifest_path.read_text(encoding="utf-8"))
        except Exception:
            data = {}
        if isinstance(data, dict):
            for key in ("name", "policy_name", "description"):
                value = data.get(key)
                if value:
                    parts.append(str(value))
                    break
    return " / ".join(parts)
