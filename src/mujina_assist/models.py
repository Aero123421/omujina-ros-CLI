from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass(slots=True)
class AppPaths:
    repo_root: Path
    state_dir: Path
    cache_dir: Path
    imported_policy_dir: Path
    logs_dir: Path
    workspace_dir: Path
    workspace_src_dir: Path
    upstream_dir: Path
    runtime_state_file: Path
    config_file: Path
    default_policy_cache: Path

    @classmethod
    def from_repo_root(cls, repo_root: Path) -> "AppPaths":
        state_dir = repo_root / ".state"
        cache_dir = repo_root / "cache"
        workspace_dir = repo_root / "workspace"
        workspace_src_dir = workspace_dir / "src"
        upstream_dir = workspace_src_dir / "mujina_ros"
        imported_policy_dir = cache_dir / "imported_policies"
        logs_dir = repo_root / "logs"
        return cls(
            repo_root=repo_root,
            state_dir=state_dir,
            cache_dir=cache_dir,
            imported_policy_dir=imported_policy_dir,
            logs_dir=logs_dir,
            workspace_dir=workspace_dir,
            workspace_src_dir=workspace_src_dir,
            upstream_dir=upstream_dir,
            runtime_state_file=state_dir / "runtime.json",
            config_file=state_dir / "config.json",
            default_policy_cache=cache_dir / "default_policy.onnx",
        )

    def ensure_directories(self) -> None:
        for path in (
            self.state_dir,
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
    active_policy_label: str = "公式デフォルト"
    active_policy_source: str = ""
    active_policy_hash: str = ""
    last_action: str = ""
    last_sim_success: bool = False
    tmux_session_name: str = ""


@dataclass(slots=True)
class PolicyCandidate:
    label: str
    path: Path
    source_type: str
    description: str = ""
    manifest_path: Path | None = None


@dataclass(slots=True)
class DoctorReport:
    os_label: str
    ubuntu_24_04: bool
    ros_installed: bool
    workspace_cloned: bool
    workspace_built: bool
    active_policy_label: str
    usb_policy_count: int
    real_devices: dict[str, bool] = field(default_factory=dict)
    tool_status: dict[str, bool] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)
    recommendation: str = ""
