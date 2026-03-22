from __future__ import annotations

import json
import re
import shutil
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

from mujina_assist.models import AppPaths, PolicyCacheEntry, PolicyCandidate, RuntimeState
from mujina_assist.services.checks import file_hash
from mujina_assist.services.workspace import capture_default_policy, run_onnx_self_test, run_workspace_build_with_options


MAX_CACHED_POLICIES = 10
MAX_CACHED_BYTES = 1024 * 1024 * 1024


def _timestamp() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def _load_policy_index(paths: AppPaths) -> dict:
    if not paths.policy_index_file.exists():
        return {"entries": []}
    try:
        return json.loads(paths.policy_index_file.read_text(encoding="utf-8"))
    except Exception:
        return {"entries": []}


def _save_policy_index(paths: AppPaths, data: dict) -> None:
    paths.policy_index_file.parent.mkdir(parents=True, exist_ok=True)
    paths.policy_index_file.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def _entries_from_index(paths: AppPaths) -> list[PolicyCacheEntry]:
    entries: list[PolicyCacheEntry] = []
    for item in _load_policy_index(paths).get("entries", []):
        try:
            entries.append(PolicyCacheEntry(**item))
        except TypeError:
            continue
    return entries


def _write_entries(paths: AppPaths, entries: list[PolicyCacheEntry]) -> None:
    _save_policy_index(paths, {"entries": [asdict(entry) for entry in entries]})


def _append_policy_history(paths: AppPaths, payload: dict) -> None:
    paths.policy_history_file.parent.mkdir(parents=True, exist_ok=True)
    with paths.policy_history_file.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False) + "\n")


def _sanitize_name(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", name)


def _entry_to_candidate(entry: PolicyCacheEntry, state: RuntimeState) -> PolicyCandidate:
    path = Path(entry.blob_path)
    manifest_path = Path(entry.manifest_path) if entry.manifest_path else None
    description_parts = [f"{entry.size_bytes / (1024 * 1024):.1f} MB", f"source={entry.source_kind}"]
    if entry.last_used_at:
        description_parts.append(f"last_used={entry.last_used_at}")
    return PolicyCandidate(
        label=entry.label,
        path=path,
        source_type="cache",
        description=" / ".join(description_parts),
        manifest_path=manifest_path if manifest_path and manifest_path.exists() else None,
        policy_hash=entry.policy_hash,
        size_bytes=entry.size_bytes,
        last_used_at=entry.last_used_at,
        use_count=entry.use_count,
        is_active=state.active_policy_hash == entry.policy_hash,
        sim_verified=bool(state.last_sim_success and state.last_sim_policy_hash == entry.policy_hash),
    )


def discover_usb_policies() -> list[PolicyCandidate]:
    user = Path.home().name
    roots = [Path("/media") / user, Path("/run/media") / user]
    candidates: list[PolicyCandidate] = []
    for root in roots:
        if not root.exists():
            continue
        for mounted in sorted(root.iterdir()):
            if not mounted.is_dir():
                continue
            for onnx_path in sorted(mounted.rglob("*.onnx")):
                manifest = onnx_path.with_suffix(".manifest.json")
                candidates.append(
                    PolicyCandidate(
                        label=f"USB: {onnx_path.name}",
                        path=onnx_path,
                        source_type="usb",
                        description=_describe_policy_file(onnx_path, manifest if manifest.exists() else None),
                        manifest_path=manifest if manifest.exists() else None,
                        policy_hash=file_hash(onnx_path),
                        size_bytes=onnx_path.stat().st_size,
                    )
                )
    return candidates


def cached_policy_candidates(paths: AppPaths, state: RuntimeState) -> list[PolicyCandidate]:
    candidates: list[PolicyCandidate] = []
    if paths.default_policy_cache.exists():
        default_hash = file_hash(paths.default_policy_cache)
        candidates.append(
            PolicyCandidate(
                label="公式デフォルト",
                path=paths.default_policy_cache,
                source_type="default",
                description="clone 時点の policy.onnx",
                policy_hash=default_hash,
                size_bytes=paths.default_policy_cache.stat().st_size,
                is_active=state.active_policy_hash == default_hash,
                sim_verified=bool(state.last_sim_success and state.last_sim_policy_hash == default_hash),
            )
        )
    for entry in sorted(_entries_from_index(paths), key=lambda item: item.last_used_at or item.first_seen_at, reverse=True):
        if Path(entry.blob_path).exists():
            candidates.append(_entry_to_candidate(entry, state))
    return candidates


def all_policy_candidates(paths: AppPaths, state: RuntimeState | None = None) -> list[PolicyCandidate]:
    runtime_state = state or RuntimeState()
    return cached_policy_candidates(paths, runtime_state) + discover_usb_policies()


def import_policy_to_cache(paths: AppPaths, candidate: PolicyCandidate) -> Path:
    if candidate.source_type in {"default", "cache"}:
        return candidate.path

    candidate_hash = candidate.policy_hash or file_hash(candidate.path)
    candidate_size = candidate.size_bytes or candidate.path.stat().st_size
    entries = _entries_from_index(paths)
    for entry in entries:
        if entry.policy_hash == candidate_hash and Path(entry.blob_path).exists():
            entry.last_used_at = _timestamp()
            entry.use_count += 1
            _write_entries(paths, entries)
            return Path(entry.blob_path)

    destination = paths.imported_policy_dir / f"{candidate_hash[:12]}-{_sanitize_name(candidate.path.name)}"
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(candidate.path, destination)

    manifest_destination = None
    if candidate.manifest_path and candidate.manifest_path.exists():
        manifest_destination = destination.with_suffix(".manifest.json")
        shutil.copy2(candidate.manifest_path, manifest_destination)

    entry = PolicyCacheEntry(
        policy_hash=candidate_hash,
        blob_path=str(destination),
        label=candidate.label,
        source_kind=candidate.source_type,
        original_path=str(candidate.path),
        size_bytes=candidate_size,
        first_seen_at=_timestamp(),
        last_used_at=_timestamp(),
        use_count=1,
        manifest_path=str(manifest_destination) if manifest_destination else "",
    )
    entries.append(entry)
    _write_entries(paths, entries)
    return destination


def cleanup_policy_cache(paths: AppPaths, state: RuntimeState, *, dry_run: bool = False) -> dict[str, int]:
    entries = _entries_from_index(paths)
    if not entries:
        return {"deleted_entries": 0, "deleted_bytes": 0, "remaining_entries": 0}

    protected_hashes = {state.active_policy_hash, state.last_sim_policy_hash}
    protected_paths = {state.active_policy_source, str(paths.default_policy_cache)}

    total_bytes = sum(entry.size_bytes for entry in entries)
    remaining_count = len(entries)
    remaining_bytes = total_bytes
    to_delete: list[PolicyCacheEntry] = []

    for entry in sorted(entries, key=lambda item: item.last_used_at or item.first_seen_at):
        if remaining_count <= MAX_CACHED_POLICIES and remaining_bytes <= MAX_CACHED_BYTES:
            break
        if entry.pinned:
            continue
        if entry.policy_hash in protected_hashes:
            continue
        if entry.blob_path in protected_paths:
            continue
        to_delete.append(entry)
        remaining_count -= 1
        remaining_bytes -= entry.size_bytes

    if not dry_run and to_delete:
        kept = [entry for entry in entries if entry not in to_delete]
        for entry in to_delete:
            blob = Path(entry.blob_path)
            if blob.exists():
                blob.unlink()
            if entry.manifest_path:
                manifest = Path(entry.manifest_path)
                if manifest.exists():
                    manifest.unlink()
        _write_entries(paths, kept)
        _append_policy_history(
            paths,
            {
                "timestamp": _timestamp(),
                "event": "cache_pruned",
                "deleted_entries": len(to_delete),
                "deleted_bytes": sum(entry.size_bytes for entry in to_delete),
            },
        )

    return {
        "deleted_entries": len(to_delete),
        "deleted_bytes": sum(entry.size_bytes for entry in to_delete),
        "remaining_entries": len(entries) - len(to_delete),
    }


def activate_policy(paths: AppPaths, state: RuntimeState, candidate: PolicyCandidate, log_path: Path) -> tuple[bool, str]:
    if not paths.upstream_dir.exists():
        return False, "workspace がまだ作成されていません。先に初回セットアップを完了してください。"

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
        suffix = "" if rollback_ok else " さらに元の policy への復旧にも失敗しています。"
        _append_policy_history(paths, {"timestamp": _timestamp(), "event": "switch_failed", "label": candidate.label, "result": "build_failed"})
        return False, f"policy 切り替え後のビルドに失敗しました。{suffix}"

    test_result = run_onnx_self_test(paths, log_path)
    if test_result.returncode != 0:
        rollback_ok = _restore_previous_policy(paths, previous_policy, state, previous_label, previous_source, previous_hash, log_path)
        suffix = "" if rollback_ok else " さらに元の policy への復旧にも失敗しています。"
        _append_policy_history(paths, {"timestamp": _timestamp(), "event": "switch_failed", "label": candidate.label, "result": "onnx_test_failed"})
        return False, f"ONNX 読み込みテストに失敗しました。{suffix}"

    active_hash = file_hash(paths.source_policy_path)
    state.active_policy_label = candidate.label
    state.active_policy_source = str(cached_source)
    state.active_policy_hash = active_hash
    state.last_action = "policy_switch"
    state.last_sim_success = False
    state.last_sim_policy_hash = ""
    state.last_sim_verified_at = ""
    state.last_sim_verified_label = ""
    state.last_sim_verified_source = ""
    _touch_cache_entry(paths, active_hash)
    cleanup_policy_cache(paths, state)
    _append_policy_history(
        paths,
        {
            "timestamp": _timestamp(),
            "event": "switch_succeeded",
            "policy_hash": active_hash,
            "label": candidate.label,
            "source": str(cached_source),
            "log_path": str(log_path),
        },
    )
    return True, f"{candidate.label} を現在の policy に設定しました。"


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

    rollback = run_workspace_build_with_options(
        paths,
        log_path,
        packages=["mujina_control"],
        run_rosdep_step=False,
        install_python_deps=False,
    )
    state.active_policy_label = previous_label
    state.active_policy_source = previous_source
    state.active_policy_hash = previous_hash
    return rollback.returncode == 0


def _touch_cache_entry(paths: AppPaths, policy_hash: str) -> None:
    entries = _entries_from_index(paths)
    changed = False
    for entry in entries:
        if entry.policy_hash == policy_hash:
            entry.last_used_at = _timestamp()
            entry.use_count += 1
            changed = True
            break
    if changed:
        _write_entries(paths, entries)


def _describe_policy_file(onnx_path: Path, manifest_path: Path | None) -> str:
    parts = [f"{onnx_path.stat().st_size / (1024 * 1024):.1f} MB", onnx_path.parent.name]
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
