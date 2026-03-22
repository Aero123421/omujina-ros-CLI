from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


DEFAULT_MOTOR_IDS = [10, 11, 12, 7, 8, 9, 4, 5, 6, 1, 2, 3]


@dataclass(slots=True)
class AppPaths:
    repo_root: Path
    state_dir: Path
    jobs_dir: Path
    job_scripts_dir: Path
    cache_dir: Path
    imported_policy_dir: Path
    logs_dir: Path
    workspace_dir: Path
    workspace_src_dir: Path
    upstream_dir: Path
    runtime_state_file: Path
    config_file: Path
    default_policy_cache: Path
    policy_index_file: Path
    policy_history_file: Path

    @classmethod
    def from_repo_root(cls, repo_root: Path) -> "AppPaths":
        state_dir = repo_root / ".state"
        jobs_dir = state_dir / "jobs"
        job_scripts_dir = state_dir / "job_scripts"
        cache_dir = repo_root / "cache"
        workspace_dir = repo_root / "workspace"
        workspace_src_dir = workspace_dir / "src"
        upstream_dir = workspace_src_dir / "mujina_ros"
        imported_policy_dir = cache_dir / "imported_policies"
        logs_dir = repo_root / "logs"
        return cls(
            repo_root=repo_root,
            state_dir=state_dir,
            jobs_dir=jobs_dir,
            job_scripts_dir=job_scripts_dir,
            cache_dir=cache_dir,
            imported_policy_dir=imported_policy_dir,
            logs_dir=logs_dir,
            workspace_dir=workspace_dir,
            workspace_src_dir=workspace_src_dir,
            upstream_dir=upstream_dir,
            runtime_state_file=state_dir / "runtime.json",
            config_file=state_dir / "config.json",
            default_policy_cache=cache_dir / "default_policy.onnx",
            policy_index_file=cache_dir / "policy_index.json",
            policy_history_file=state_dir / "policy_history.jsonl",
        )

    def ensure_directories(self) -> None:
        for path in (
            self.state_dir,
            self.jobs_dir,
            self.job_scripts_dir,
            self.cache_dir,
            self.imported_policy_dir,
            self.logs_dir,
            self.workspace_dir,
            self.workspace_src_dir,
        ):
            path.mkdir(parents=True, exist_ok=True)

    @property
    def source_policy_path(self) -> Path:
        return self.upstream_dir / "mujina_control" / "models" / "policy.onnx"


@dataclass(slots=True)
class RuntimeState:
    active_policy_label: str = "未設定"
    active_policy_source: str = ""
    active_policy_hash: str = ""
    last_action: str = ""
    last_sim_success: bool = False
    last_sim_policy_hash: str = ""
    last_sim_verified_at: str = ""
    last_sim_verified_label: str = ""
    last_sim_verified_source: str = ""
    last_sim_verified_workspace_signature: str = ""
    real_setup_requires_relogin: bool = False
    manual_recovery_required: bool = False
    manual_recovery_kind: str = ""
    manual_recovery_summary: str = ""
    tmux_session_name: str = ""


@dataclass(slots=True)
class JobRecord:
    job_id: str
    kind: str
    name: str
    status: str
    log_path: str
    created_at: str
    job_file: str
    script_path: str
    payload: dict[str, Any] = field(default_factory=dict)
    terminal_mode: str = ""
    group_id: str = ""
    started_at: str = ""
    finished_at: str = ""
    returncode: int | None = None
    message: str = ""
    terminal_label: str = ""
    terminal_pid: int | None = None


@dataclass(slots=True)
class PolicyCandidate:
    label: str
    path: Path
    source_type: str
    description: str = ""
    manifest_path: Path | None = None
    policy_hash: str = ""
    size_bytes: int = 0
    last_used_at: str = ""
    use_count: int = 0
    is_active: bool = False
    sim_verified: bool = False


@dataclass(slots=True)
class DoctorCheck:
    key: str
    label: str
    status: str
    summary: str
    details: list[str] = field(default_factory=list)
    next_steps: list[str] = field(default_factory=list)


@dataclass(slots=True)
class DoctorReport:
    os_label: str
    ubuntu_24_04: bool
    ros_installed: bool
    workspace_cloned: bool
    workspace_built: bool
    active_policy_label: str
    active_policy_source: str = ""
    active_policy_hash: str = ""
    usb_policy_count: int = 0
    sim_ready: bool = False
    sim_verified_at: str = ""
    real_devices: dict[str, bool] = field(default_factory=dict)
    serial_candidates: list[str] = field(default_factory=list)
    imu_port_label: str = ""
    imu_port_fallback: bool = False
    tool_status: dict[str, bool] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)
    recommendation: str = ""
    checks: list[DoctorCheck] = field(default_factory=list)
    policy_cache_count: int = 0
    policy_cache_size_bytes: int = 0


@dataclass(slots=True)
class PolicyCacheEntry:
    policy_hash: str
    blob_path: str
    label: str
    source_kind: str
    original_path: str
    size_bytes: int
    first_seen_at: str
    last_used_at: str
    use_count: int = 0
    pinned: bool = False
    manifest_path: str = ""
