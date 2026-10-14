"""OctopServer — process-level orchestrator."""

from __future__ import annotations

import asyncio
import gzip
import importlib
import logging
import os
import shutil
import sys
import time
from collections.abc import Awaitable, Callable
from contextlib import suppress
from dataclasses import dataclass
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path
from typing import TYPE_CHECKING, Any, TypeVar

from octop.config import OctopConfig, load_config
from octop.infra.agents.experts.catalog import ExpertCatalog, default_library_root
from octop.infra.agents.subagents.catalog import SubagentCatalog, default_package_root
from octop.infra.db.factory import open_database, should_defer_control_plane_db
from octop.infra.db.migrate import run_migrations
from octop.infra.db.services import SharedServices, build_shared_services
from octop.infra.errors import ErrorCode, OctopError
from octop.infra.mobile.config_probe import ensure_mobile_capabilities_probed
from octop.infra.setup import password_file as _wizard_pw
from octop.infra.setup.brand_migration import migrate_agent_paths, migrate_local_files
from octop.infra.setup.password_file import WIZARD_FILE_NAME
from octop.infra.setup.wizard_tokens import WizardTokenStore
from octop.infra.startup import StartupProgress
from octop.infra.users.manager import UserManager
from octop.infra.utils.paths import PathLayout

if TYPE_CHECKING:
    from octop.infra.agents.manager import AgentManager
    from octop.infra.agents.plugins.manager import PluginManager
    from octop.infra.auth.ldap.service import LdapAuthService
    from octop.infra.auth.ldap.throttle import LdapBindThrottle
    from octop.infra.auth.sso.service import SsoService
    from octop.infra.connectors.platform_collaboration import PlatformCollaboration
    from octop.infra.cron.manager import CronManager
    from octop.infra.db.services import SharedServices
    from octop.infra.engines.dsh import DSHService
    from octop.infra.engines.execution import ExecutionService
    from octop.infra.engines.packages import EnginePackages
    from octop.infra.gateway.gateway import Gateway
    from octop.infra.gateway.threads import ThreadRegistry
    from octop.infra.history.trajectory.service import TrajectoryService
    from octop.infra.proactive.scheduler import ProactiveCareScheduler

logger = logging.getLogger(__name__)
_T = TypeVar("_T")


def _preload_runtime_modules() -> None:
    """Import SDK graphs off the HTTP loop; construct no service objects here."""
    for module in (
        "octop.infra.agents.manager",
        "octop.infra.cron.manager",
        "octop.infra.gateway.gateway",
        "octop.infra.proactive.scheduler",
        "octop.infra.proactive.service",
        "octop.infra.utils.browser_media",
        "octop.infra.cron.delivery",
        "octop.infra.setup.tls.renewal",
        "octop.infra.backup.auto",
        "octop.infra.bridge.manager",
        "octop.infra.knowledge.jobs",
    ):
        importlib.import_module(module)


# Default 100 MiB per active log file before size-triggered rollover (in addition to daily).
DEFAULT_LOG_MAX_BYTES = 100 * 1024 * 1024
DEFAULT_LOG_RETENTION_DAYS = 14


class SizeTimedRotatingFileHandler(TimedRotatingFileHandler):
    """Daily rotation with an optional per-file byte cap (whichever triggers first)."""

    def __init__(
        self,
        filename: str | os.PathLike[str],
        *,
        max_bytes: int = 0,
        compress_rotated: bool = True,
        **kwargs: object,
    ) -> None:
        super().__init__(filename, **kwargs)  # type: ignore[arg-type]
        self.max_bytes = max(0, max_bytes)
        self.compress_rotated = compress_rotated

    def shouldRollover(self, record: logging.LogRecord) -> bool:
        if super().shouldRollover(record):
            return True
        if self.max_bytes <= 0:
            return False
        if self.stream is None:
            self.stream = self._open()
        self.stream.flush()
        return self.stream.tell() >= self.max_bytes

    def rotate(self, source: str, dest: str) -> None:
        super().rotate(source, dest)
        if self.compress_rotated:
            # logrotate delaycompress: leave this cycle's dest plain; gzip prior plains.
            delaycompress_rotated_logs(Path(dest).parent, enabled=True, keep=Path(dest))

    def rotation_filename(self, default_name: str) -> str:
        """Avoid overwriting an existing daily backup when size rolls twice same day."""
        if not os.path.exists(default_name) and not os.path.exists(f"{default_name}.gz"):
            return default_name
        index = 1
        while True:
            candidate = f"{default_name}-{index:03d}"
            if not os.path.exists(candidate) and not os.path.exists(f"{candidate}.gz"):
                return candidate
            index += 1


def gzip_rotated_log(path: Path) -> None:
    """Compress a rotated log file to ``{name}.gz`` and remove the plain file."""
    if not path.is_file() or path.suffix == ".gz":
        return
    gz_path = path.with_name(f"{path.name}.gz")
    if gz_path.exists():
        return
    try:
        with path.open("rb") as src, gzip.open(gz_path, "wb", compresslevel=6) as dst:
            shutil.copyfileobj(src, dst)
        path.unlink()
    except OSError:
        with suppress(OSError):
            if gz_path.exists():
                gz_path.unlink()


def _parse_log_compress() -> bool:
    raw = (os.environ.get("OCTOP_LOG_COMPRESS", "1") or "1").strip().lower()
    return raw not in ("0", "false", "no", "off")


def delaycompress_rotated_logs(
    log_dir: Path,
    *,
    enabled: bool,
    keep: Path | None = None,
) -> None:
    """Gzip older rotated plains; leave ``keep`` (or the newest) uncompressed.

    Matches logrotate ``compress`` + ``delaycompress``: postpone compression of the
    previous log file until the next rotation cycle
    (https://github.com/logrotate/logrotate).
    """
    if not enabled or not log_dir.is_dir():
        return
    plains = [
        entry
        for entry in log_dir.glob("xfriend.log.*")
        if entry.is_file() and not entry.name.endswith(".gz")
    ]
    if not plains:
        return
    keep_path = max(plains, key=lambda p: p.stat().st_mtime) if keep is None else keep
    keep_resolved = keep_path.resolve()
    for entry in plains:
        if entry.resolve() == keep_resolved:
            continue
        gzip_rotated_log(entry)


def _parse_log_retention_days() -> int:
    raw = os.environ.get("OCTOP_LOG_RETENTION_DAYS", str(DEFAULT_LOG_RETENTION_DAYS)) or str(
        DEFAULT_LOG_RETENTION_DAYS
    )
    try:
        return int(raw)
    except ValueError:
        return DEFAULT_LOG_RETENTION_DAYS


def _parse_log_max_bytes() -> int:
    raw = os.environ.get("OCTOP_LOG_MAX_BYTES", str(DEFAULT_LOG_MAX_BYTES)) or str(
        DEFAULT_LOG_MAX_BYTES
    )
    try:
        return max(0, int(raw))
    except ValueError:
        return DEFAULT_LOG_MAX_BYTES


def _build_log_handler(
    log_path: Path,
    retention_days: int,
    *,
    max_bytes: int = DEFAULT_LOG_MAX_BYTES,
    compress_rotated: bool = True,
) -> SizeTimedRotatingFileHandler:
    """Create a daily + size-capped rotating file handler with retention."""
    handler = SizeTimedRotatingFileHandler(
        log_path,
        when="midnight",
        interval=1,
        backupCount=retention_days,
        encoding="utf-8",
        max_bytes=max_bytes,
        compress_rotated=compress_rotated,
    )
    # Rotated files get a date suffix, e.g. xfriend.log.2026-07-16
    handler.suffix = "%Y-%m-%d"
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)-5s %(name)s — %(message)s"))
    return handler


def _purge_stale_logs(log_dir: Path, retention_days: int) -> None:
    """Delete rotated octop log files older than ``retention_days`` (mtime based).

    ``TimedRotatingFileHandler`` only trims by count at rollover time, so a service
    that is offline for a long stretch can accumulate stale files. This purges them
    on startup as a safety net.
    """
    if retention_days <= 0:
        return
    cutoff = time.time() - retention_days * 86400
    for entry in log_dir.glob("xfriend.log.*"):
        if entry.is_file() and entry.stat().st_mtime < cutoff:
            with suppress(OSError):
                entry.unlink()


def _attach_log_handler(target: logging.Logger, handler: TimedRotatingFileHandler) -> bool:
    """Add ``handler`` to ``target`` only if an equivalent one is not present yet."""
    if not any(
        isinstance(h, TimedRotatingFileHandler) and h.baseFilename == handler.baseFilename
        for h in target.handlers
    ):
        target.addHandler(handler)
        return True
    return False


@dataclass
class AppRuntime:
    """Live singletons — constructed after boot."""

    agent_registry: AgentManager
    gateway: Gateway
    cron_manager: CronManager
    user_manager: UserManager
    proactive_scheduler: ProactiveCareScheduler
    trajectory_service: TrajectoryService | None = None
    history_archive: Any | None = None
    bridge_manager: Any | None = None

    def replace_services(self, services: SharedServices, config: OctopConfig) -> None:
        """Retarget all runtime singletons onto a new SharedServices / config.

        Used when the setup wizard hot-swaps the control-plane DB while empty.
        """
        if self.history_archive is not None:
            raise ValueError("Restart the server to rebind a database with versioned history")
        self.user_manager.replace_services(services)
        self.agent_registry.replace_persistence(services.repos, config)
        self.gateway.replace_repos(services.repos)
        self.cron_manager.replace_repos(services.repos)
        self.proactive_scheduler.replace_persistence(
            config_repo=services.repos.proactive_care_config_repo,
            session_repo=services.repos.session_repo,
            care_push_repo=services.repos.care_push_repo,
        )
        if self.trajectory_service is not None:
            from octop.infra.history.trajectory.store import TrajectoryStore  # noqa: PLC0415

            self.trajectory_service.replace_store(TrajectoryStore(services.trajectory_event_repo))


class OctopServer:
    def _assert_plugins_idle(self) -> None:
        if self._defer_agents and not self.runtime_ready:
            raise OctopError.localized(ErrorCode.SERVICE_STARTING, status=503)
        if self.app_runtime is not None and (
            self.app_runtime.agent_registry.agents_pending
            or (self._defer_agents and not self.startup_progress.agents_ready)
        ):
            raise OctopError(
                ErrorCode.AGENT_BUSY, "Wait for expert startup before changing plugins"
            )
        if self._execution_service is not None and self._execution_service.has_active_tasks:
            raise OctopError(
                ErrorCode.AGENT_BUSY, "Wait for execution tasks before changing plugins"
            )
        if (
            self.app_runtime is not None
            and self.services is not None
            and any(
                self.app_runtime.agent_registry.is_agent_active(row.agent_id)
                for row in self.services.agent_repo.list_all()
            )
        ):
            raise OctopError(ErrorCode.AGENT_BUSY, "Wait for active agents before changing plugins")

    def __init__(self, home: Path | None = None) -> None:
        self.startup_progress = StartupProgress()
        self._logging_handler: TimedRotatingFileHandler | None = None
        self._logging_targets: list[logging.Logger] = []
        self._home = home or PathLayout.from_env().root
        self.paths = PathLayout(self._home)
        self.config: OctopConfig | None = None
        self.services: SharedServices | None = None
        self._dsh_service: DSHService | None = None
        self._dsh_web: Any | None = None
        self._engine_packages: EnginePackages | None = None
        self._execution_service: ExecutionService | None = None
        self._license_sync_task: asyncio.Task[None] | None = None
        self._background_startup_task: asyncio.Task[None] | None = None
        self._bridge_resume_task: asyncio.Task[None] | None = None
        self._runtime_ready_event = asyncio.Event()
        self._runtime_cleanup: list[Callable[[], Awaitable[None]]] = []
        self._runtime_ready_callbacks: list[Callable[[AppRuntime], None]] = []
        self._stopping = False
        self._defer_agents = False
        self._platform_collaboration: PlatformCollaboration | None = None
        self.app_runtime: AppRuntime | None = None
        self._user_manager: UserManager | None = None
        self.history_archive: Any | None = None
        self.trajectory_service: TrajectoryService | None = None
        self.thread_registry: ThreadRegistry | None = None
        self.expert_catalog: ExpertCatalog | None = None
        self.subagent_catalog: SubagentCatalog | None = None
        self.plugin_manager: PluginManager | None = None
        self.wizard_tokens = WizardTokenStore(ttl_seconds=300)
        self._started = False
        self._started_at: int | None = None
        self._sso_service: SsoService | None = None
        self._ldap_service: LdapAuthService | None = None
        self._ldap_bind_throttle: LdapBindThrottle | None = None
        self._ldap_bind_throttle_services: SharedServices | None = None

    # Backward compat: expose user_manager directly
    @property
    def engine_packages(self) -> EnginePackages:
        from octop.infra.engines.packages import EnginePackages

        if self._engine_packages is None:
            self._engine_packages = EnginePackages(
                self._home, self.dsh_service, self._assert_plugins_idle
            )
        return self._engine_packages

    @property
    def platform_collaboration(self) -> PlatformCollaboration:
        from octop.infra.connectors.platform_collaboration import PlatformCollaboration

        if self.services is None:
            raise RuntimeError("Platform collaboration requires a started server")
        if self._platform_collaboration is None:
            self._platform_collaboration = PlatformCollaboration(self.services)
        return self._platform_collaboration

    @property
    def execution_service(self) -> ExecutionService:
        from octop.infra.engines.adapters import DSHEngine, NativeEngine
        from octop.infra.engines.execution import ExecutionService

        if self.services is None or self.app_runtime is None:
            raise RuntimeError("Execution service requires a started server")
        if self._execution_service is None:
            gateway = self.app_runtime.gateway
            self._execution_service = ExecutionService(
                self.paths.root,
                self.services.repos,
                {
                    "native": NativeEngine(
                        self.app_runtime.agent_registry, gateway.processor, gateway.thread_registry
                    ),
                    "dsh": DSHEngine(self.dsh_service),
                },
            )
        return self._execution_service

    @property
    def dsh_service(self) -> DSHService:
        from octop.infra.agents.settings.acp import ACPSettingsStore
        from octop.infra.engines.dsh import DSHService
        from octop.infra.engines.dsh_connectors import DSHConnectorBridge

        if self.services is None:
            raise RuntimeError("DSH requires a started server")
        if self._dsh_service is None:
            self._dsh_service = DSHService(
                self.paths.root,
                ACPSettingsStore(
                    settings_repo=self.services.settings_repo,
                    agents_repo=self.services.agent_repo,
                    home=self.paths.root,
                ),
                DSHConnectorBridge(self.services),
            )
        return self._dsh_service

    @property
    def dsh_web(self) -> Any:
        from octop.infra.engines.dsh_web import DSHWebWorkspace

        if self._dsh_web is None:
            self._dsh_web = DSHWebWorkspace(self._home, self.dsh_service.settings, self.dsh_service)
        return self._dsh_web

    @property
    def user_manager(self) -> UserManager | None:
        return self._user_manager

    @property
    def runtime_ready(self) -> bool:
        return self.app_runtime is not None and self.startup_progress.runtime_ready

    def require_runtime(self) -> AppRuntime:
        if not self.runtime_ready or self.app_runtime is None:
            raise OctopError.localized(ErrorCode.SERVICE_STARTING, status=503)
        return self.app_runtime

    def register_runtime_ready_callback(self, callback: Callable[[AppRuntime], None]) -> None:
        """Let the transport wire its adapters without an infra-to-API import."""
        self._runtime_ready_callbacks.append(callback)
        if self.runtime_ready:
            callback(self.require_runtime())

    async def ensure_runtime_ready(self) -> AppRuntime:
        """Wait for shared engine assembly without cancelling other callers."""
        if not self.runtime_ready:
            self.start_background_services()
            if self._background_startup_task is None or self._stopping:
                raise OctopError.localized(ErrorCode.SERVICE_STARTING, status=503)
            await self._runtime_ready_event.wait()
        return self.require_runtime()

    async def _run_startup_io(self, call: Callable[[], _T]) -> _T:
        """Drain workers on cancellation before shutting down shared registries."""
        task = asyncio.create_task(asyncio.to_thread(call))
        try:
            return await asyncio.shield(task)
        except asyncio.CancelledError:
            while not task.done():
                try:
                    await asyncio.shield(task)
                except asyncio.CancelledError:
                    continue
                except Exception:
                    break
            if not task.cancelled():
                task.exception()
            raise

    @property
    def sso_service(self) -> SsoService:
        """Process-level SSO service so discovery/JWKS cache survives across requests."""
        from octop.infra.auth.sso.service import SsoService as SsoServiceCls  # noqa: PLC0415

        if self.services is None or self.user_manager is None:
            raise RuntimeError("SSO service requires a started server with user manager")
        if (
            self._sso_service is None
            or getattr(self._sso_service, "_services", None) is not self.services
            or getattr(self._sso_service, "_user_manager", None) is not self.user_manager
        ):
            self._sso_service = SsoServiceCls(self.services, self.user_manager)
        return self._sso_service

    @property
    def ldap_service(self) -> LdapAuthService:
        """Process-level LDAP service, rebound whenever services are swapped."""
        from octop.infra.auth.ldap.service import (
            LdapAuthService as LdapAuthServiceCls,  # noqa: PLC0415
        )

        if self.services is None or self.user_manager is None:
            raise RuntimeError("LDAP service requires a started server with user manager")
        if (
            self._ldap_service is None
            or getattr(self._ldap_service, "_services", None) is not self.services
            or getattr(self._ldap_service, "_user_manager", None) is not self.user_manager
        ):
            self._ldap_service = LdapAuthServiceCls(self.services, self.user_manager)
        return self._ldap_service

    @property
    def ldap_bind_throttle(self) -> LdapBindThrottle:
        """Process-level brute-force backoff for directory binds."""
        from octop.infra.auth.ldap.throttle import LdapBindThrottle as ThrottleCls  # noqa: PLC0415

        if self.services is None:
            raise RuntimeError("LDAP throttle requires a started server")
        if (
            self._ldap_bind_throttle is None
            or self._ldap_bind_throttle_services is not self.services
        ):
            config = self.services.config
            self._ldap_bind_throttle = ThrottleCls(
                max_attempts=config.login_max_attempts,
                window_seconds=60,
                block_seconds=config.login_lockout_seconds,
            )
            self._ldap_bind_throttle_services = self.services
        return self._ldap_bind_throttle

    @property
    def database_bound(self) -> bool:
        return self.services is not None and self.user_manager is not None

    async def start(self, *, defer_agents: bool = False) -> None:
        if self._started:
            return
        self.startup_progress = StartupProgress()
        self._runtime_ready_event.clear()
        self._stopping = False
        self._defer_agents = defer_agents
        self.startup_progress.mark("starting")
        try:
            await self._start()
        except BaseException as exc:
            await self.stop()
            self.startup_progress.failure = type(exc).__name__
            self.startup_progress.mark("failed")
            self._close_logging()
            raise
        self.startup_progress.core_ready = True
        if not defer_agents or not self.database_bound:
            self.startup_progress.agents_ready = True
            self.startup_progress.ready = True
            self.startup_progress.mark("ready")
        else:
            self.startup_progress.mark("core_ready")
        from octop.infra.licensing.service import load_profile

        if load_profile()[1]:
            self._license_sync_task = asyncio.create_task(self._sync_product_license())

    def start_background_services(self) -> None:
        """Begin desktop warmup after HTTP is listening; safe to call repeatedly."""
        if (
            not self._defer_agents
            or not self.database_bound
            or self._background_startup_task is not None
            or self._stopping
        ):
            return
        self._background_startup_task = asyncio.create_task(
            self._finish_background_startup(), name="xfriend-runtime-warmup"
        )

    async def _finish_background_startup(self) -> None:
        try:
            assert self.config is not None
            await self._prepare_runtime(self.config)
            rt = self.require_runtime()
            self.startup_progress.mark("restoring_agents")
            rt.agent_registry.begin_agent_restore()
            await rt.agent_registry.wait_for_agent_restore()
            self.startup_progress.agents_ready = True
            self.startup_progress.mark("agents_restored")
            await rt.gateway.refresh_media_backends()
            await rt.gateway.start_external_channels()
            rt.cron_manager.resume()
            await rt.proactive_scheduler.start_all()
            self.startup_progress.ready = True
            self.startup_progress.mark("ready")
        except Exception as exc:
            self.startup_progress.runtime_failed = True
            self.startup_progress.failure = type(exc).__name__
            self.startup_progress.mark("runtime_failed")
            logger.exception("Background runtime startup failed")
        finally:
            self._runtime_ready_event.set()

    async def _prepare_runtime(self, config: OctopConfig) -> None:
        self.startup_progress.mark("loading_engine_modules")
        await self._run_startup_io(_preload_runtime_modules)
        self.startup_progress.mark("engine_modules_loaded")
        assert self.plugin_manager is not None
        plugins = self.plugin_manager
        await self._run_startup_io(lambda: plugins.load_installed(install_deps=True))
        self.startup_progress.mark("plugins_loaded")
        try:
            await self._boot_runtime(config)
            assert self.app_runtime is not None
            for callback in self._runtime_ready_callbacks:
                callback(self.app_runtime)
        except BaseException:
            await self._shutdown_runtime()
            raise
        self.startup_progress.runtime_ready = True
        self.startup_progress.mark("runtime_ready")
        self._runtime_ready_event.set()

    async def _sync_product_license(self) -> None:
        from octop.infra.licensing.service import product_license

        while True:
            try:
                await product_license(self.paths.root).synchronize()
            except Exception as exc:
                logger.warning("Product license synchronization failed: %s", type(exc).__name__)
            await asyncio.sleep(60)

    async def _start(self) -> None:
        await asyncio.get_running_loop().run_in_executor(None, migrate_local_files, self.paths)
        self.startup_progress.mark("local_migration")
        # fnOS/容器内非 root 用户场景：把用户级 npm 全局 bin 目录（~/.npm-global）
        # 纳入进程 PATH，保证连接器 CLI（wecom-cli / lark-cli）可被检测与调用。
        from octop.infra.connectors.gateway.cli_install import ensure_cli_path  # noqa: PLC0415

        ensure_cli_path()
        from octop.infra.utils.env_file import apply_env_file, env_file_path  # noqa: PLC0415

        apply_env_file(env_file_path(self.paths.root))
        self._setup_logging()
        config = ensure_mobile_capabilities_probed(self.paths.config)
        self.config = config
        self.startup_progress.mark("configuration")

        self.expert_catalog = ExpertCatalog(
            default_library_root(),
            extra_roots=[self.paths.expert_market_dir],
        )
        self.expert_catalog.refresh()
        self.subagent_catalog = SubagentCatalog(default_package_root())
        self.subagent_catalog.refresh()

        from octop.infra.agents.plugins.manager import PluginManager

        self.plugin_manager = PluginManager(
            plugins_dir=self.paths.plugins_dir,
            config_path=self.paths.config,
            assert_idle=self._assert_plugins_idle,
        )
        self.plugin_manager.seed_bundled()
        self.startup_progress.mark("catalogs_metadata")

        import time  # noqa: PLC0415

        self._started_at = int(time.time())

        if should_defer_control_plane_db(config, self.paths):
            self._started = True
            self._emit_wizard_password(user_count=0)
            logger.info("control-plane database deferred until setup wizard chooses a backend")
            return

        db = open_database(config, self.paths)
        run_migrations(db)
        self.startup_progress.mark("database_migration")
        self.services = build_shared_services(db=db, paths=self.paths, config=config)
        from octop.infra.auth.captcha import boot_from_services  # noqa: PLC0415

        await asyncio.get_running_loop().run_in_executor(
            None, migrate_agent_paths, self.services, self.paths
        )
        boot_from_services(self.services.settings_repo, self.services.secret_repo)
        self._ensure_jwt_secret()
        await self._boot_control_plane(config)
        if not self._defer_agents:
            await self._prepare_runtime(config)
        self._started = True
        assert self.user_manager is not None
        self._emit_wizard_password(user_count=self.user_manager.count())

    async def bind_control_plane(self) -> None:
        """Open the configured control-plane DB and boot runtime (first-run wizard).

        Idempotent when already bound: no-op. Call after persisting ``database``
        into ``config.json``. Does not rebind an existing live pool — use
        ``rebind_control_plane`` only when swapping while ``user_count == 0``.
        """
        if self.database_bound:
            return
        config = load_config(self.paths.config)
        self.config = config
        db = open_database(config, self.paths)
        try:
            run_migrations(db)
        except Exception:
            db.close()
            raise
        self.services = build_shared_services(db=db, paths=self.paths, config=config)
        from octop.infra.auth.captcha import boot_from_services  # noqa: PLC0415

        await asyncio.get_running_loop().run_in_executor(
            None, migrate_agent_paths, self.services, self.paths
        )
        boot_from_services(self.services.settings_repo, self.services.secret_repo)
        self._ensure_jwt_secret()
        await self._boot_control_plane(config)
        if not self._defer_agents:
            await self._prepare_runtime(config)
        if self._defer_agents:
            self.startup_progress.ready = False
            self.startup_progress.agents_ready = False
            self.startup_progress.mark("core_ready")
            self.start_background_services()
        logger.info(
            "control-plane database bound driver=%s",
            config.database.driver,
        )

    async def _boot_control_plane(self, config: OctopConfig) -> None:
        """Bind account and archived-history state before importing engine SDKs."""
        from octop.infra.gateway.threads import ThreadRegistry

        assert self.services is not None
        self._user_manager = UserManager(self.services)
        await self._user_manager.boot()
        self.thread_registry = ThreadRegistry(
            session_repo=self.services.session_repo, thread_repo=self.services.thread_repo
        )
        from octop.infra.history.trajectory.live import TrajectoryLiveBus  # noqa: PLC0415
        from octop.infra.history.trajectory.service import TrajectoryService  # noqa: PLC0415
        from octop.infra.history.trajectory.store import TrajectoryStore  # noqa: PLC0415

        self.history_archive = None
        trajectory_store = TrajectoryStore(self.services.trajectory_event_repo)
        archive_path = self.paths.root / "history_v2.sqlite"
        if (
            config.history_v2_enabled
            or archive_path.exists()
            or archive_path.with_suffix(".required").exists()
        ):
            from octop.infra.history.service import HistoryArchive  # noqa: PLC0415
            from octop.infra.history.store import HistoryStore  # noqa: PLC0415
            from octop.infra.history.trajectory_compat import (
                ArchiveTrajectoryStore,  # noqa: PLC0415
            )

            identity = str(config.database.resolve_sqlite_path(self.paths.root).resolve())
            if not config.database.is_sqlite:
                raise ValueError("Versioned history currently requires the SQLite control plane")
            self.history_archive = HistoryArchive(
                HistoryStore(archive_path, identity=identity),
                self.services.thread_message_repo,
                self.services.trajectory_event_repo,
                enabled=config.history_v2_enabled,
            )
            trajectory_store = ArchiveTrajectoryStore(self.history_archive)
        self.trajectory_service = TrajectoryService(
            trajectory_store,
            TrajectoryLiveBus(),
        )

        self.startup_progress.mark("users_history_loaded")

    async def _boot_runtime(self, config: OctopConfig) -> None:
        from octop.infra.agents.manager import AgentManager
        from octop.infra.cron.manager import CronManager
        from octop.infra.gateway.gateway import Gateway
        from octop.infra.proactive.scheduler import ProactiveCareScheduler
        from octop.infra.proactive.service import ProactiveCareService

        assert self.services is not None
        assert self.expert_catalog is not None
        assert self.plugin_manager is not None
        assert self._user_manager is not None

        from octop.infra.utils.browser_media import (  # noqa: PLC0415
            configure_browser_idle_timeout,
        )

        configure_browser_idle_timeout(config.browser_idle_timeout_minutes)

        registry = AgentManager(
            repos=self.services.repos,
            paths=self.paths,
            config=config,
            expert_catalog=self.expert_catalog,
            plugin_manager=self.plugin_manager,
        )
        self._runtime_cleanup.append(registry.shutdown)

        gateway = Gateway(
            agent_manager=registry,
            repos=self.services.repos,
            trajectory_service=self.trajectory_service,
            history_archive=self.history_archive,
            thread_registry=self.thread_registry,
        )
        self._runtime_cleanup.append(gateway.shutdown)
        await gateway.boot(start_external_channels=False)

        from octop import __version__  # noqa: PLC0415

        started_at = self._started_at
        if started_at is None:
            import time  # noqa: PLC0415

            started_at = int(time.time())
        gateway.set_slash_meta(version=__version__, started_at=started_at)
        self._started_at = started_at

        from octop.infra.cron.delivery import CronDeliveryService  # noqa: PLC0415

        cron_delivery = CronDeliveryService(
            gateway=gateway,
            agent_manager=registry,
            repos=self.services.repos,
        )
        cron_mgr = CronManager(
            gateway=gateway,
            delivery_service=cron_delivery,
            repos=self.services.repos,
            timezone=config.default_timezone,
        )
        self._runtime_cleanup.append(cron_mgr.shutdown)
        await cron_mgr.boot(paused=True)

        from octop.infra.setup.tls.renewal import install_auto_renewal_job

        install_auto_renewal_job(cron_mgr, paths=self.paths)

        from octop.infra.backup.auto import install_auto_backup_job

        install_auto_backup_job(cron_mgr, server=self)

        registry.set_cron_manager(cron_mgr)
        registry.set_team_processor(gateway.processor.teams)
        hitl_session_store = gateway.processor.hitl_coordinator.session_policies
        hitl_session_store.replace_repo(self.services.repos.thread_repo)
        registry.set_hitl_session_store(hitl_session_store)

        care_service = ProactiveCareService(
            gateway=gateway,
            care_push_repo=self.services.repos.care_push_repo,
            agent_manager=registry,
            timezone=config.default_timezone,
        )
        proactive_scheduler = ProactiveCareScheduler(
            care_service=care_service,
            config_repo=self.services.repos.proactive_care_config_repo,
            session_repo=self.services.repos.session_repo,
            agent_repo=self.services.repos.agent_repo,
            user_repo=self.services.repos.user_repo,
            default_timezone=config.default_timezone,
        )
        self._runtime_cleanup.append(proactive_scheduler.shutdown)
        registry.set_proactive_scheduler(proactive_scheduler)

        self.startup_progress.mark("runtime_assembled")
        await registry.boot(restore_agents=not self._defer_agents)
        if not self._defer_agents:
            self.startup_progress.mark("agents_restored")
            await gateway.refresh_media_backends()

        from octop.infra.auth.tokens import sign_token
        from octop.infra.bridge.manager import BridgeManager, public_base_url_from_config

        services = self.services
        if services is None:
            raise RuntimeError("shared services not ready for bridge")

        def _sign_user_token(user: Any) -> str:
            secret = services.secret_repo.get("jwt")
            if secret is None:
                raise RuntimeError("jwt secret missing")
            ttl = services.config.access_token_ttl_seconds
            return sign_token(
                secret,
                sub=int(user.id),
                uname=str(user.username),
                role=str(user.role),
                ttl_seconds=ttl,
            )

        advertise = public_base_url_from_config(config.bind_host, config.port)
        bridge_mgr = BridgeManager(
            bridge_repo=services.bridge_connection_repo,
            secret_repo=services.secret_repo,
            user_repo=services.user_repo,
            advertise_base_url=advertise,
            token_signer=_sign_user_token,
        )

        self.app_runtime = AppRuntime(
            agent_registry=registry,
            gateway=gateway,
            cron_manager=cron_mgr,
            user_manager=self._user_manager,
            proactive_scheduler=proactive_scheduler,
            trajectory_service=self.trajectory_service,
            history_archive=self.history_archive,
            bridge_manager=bridge_mgr,
        )
        if not self._defer_agents:
            await gateway.start_external_channels()
            cron_mgr.resume()
            await proactive_scheduler.start_all()
        from octop.infra.knowledge.jobs import resume_pending_index_jobs  # noqa: PLC0415

        resume_pending_index_jobs(self.services)

        # Resume Bridge links that opted into auto-reconnect (best-effort).
        async def _resume_bridges() -> None:
            with suppress(Exception):
                await bridge_mgr.resume_auto_connections()

        self._bridge_resume_task = asyncio.create_task(_resume_bridges(), name="bridge-auto-resume")

    def _emit_wizard_password(self, *, user_count: int) -> None:
        config = self.config
        if config is None:
            return
        wizard_home = Path.home()
        if config.require_setup_password:
            try:
                new_pw = _wizard_pw.boot_self_heal(wizard_home, user_count=user_count)
            except OSError as err:
                logger.warning("wizard self-heal failed: %s", err)
                new_pw = None
        else:
            new_pw = None
        if new_pw is not None:
            banner = (
                "\n\033[33m"
                "╔══════════════════════════════════════════════════════════╗\n"
                "║  Octop first-run wizard password (one-time use):          ║\n"
                f"║  {new_pw:<54}  ║\n"
                "║  Open the dashboard and paste it into the setup wizard.  ║\n"
                "║  File: ~/xfriend-login.txt                                   ║\n"
                "╚══════════════════════════════════════════════════════════╝"
                "\033[0m\n"
            )
            print(banner, flush=True)
            logger.info(
                "wizard password generated; file=%s",
                wizard_home / WIZARD_FILE_NAME,
            )

    async def _shutdown_runtime(self) -> None:
        if self._bridge_resume_task is not None:
            self._bridge_resume_task.cancel()
            await asyncio.gather(self._bridge_resume_task, return_exceptions=True)
            self._bridge_resume_task = None
        cleanup, self._runtime_cleanup = self._runtime_cleanup, []
        for close in reversed(cleanup):
            try:
                await close()
            except Exception:
                logger.exception("Failed closing runtime component")
        self.app_runtime = None
        self.startup_progress.runtime_ready = False

    async def stop(self) -> None:
        self._stopping = True
        self._runtime_ready_event.set()
        if self._background_startup_task is not None:
            self._background_startup_task.cancel()
            await asyncio.gather(self._background_startup_task, return_exceptions=True)
            self._background_startup_task = None
        if self.app_runtime is not None:
            await self.app_runtime.agent_registry.cancel_agent_restore()
        if self._license_sync_task is not None:
            self._license_sync_task.cancel()
            await asyncio.gather(self._license_sync_task, return_exceptions=True)
            self._license_sync_task = None
        self.startup_progress.ready = False
        self.startup_progress.core_ready = False
        self.startup_progress.agents_ready = False
        self.startup_progress.mark("stopping")
        try:
            if self._execution_service is not None:
                await self._execution_service.shutdown()
            if self._dsh_service is not None:
                await self._dsh_service.shutdown()
            if self._dsh_web is not None:
                await self._dsh_web.shutdown()
                self._dsh_web = None
            if self._platform_collaboration is not None:
                await self._platform_collaboration.shutdown()
            agently_auth = sys.modules.get("octop.infra.connectors.gateway.agently_auth")
            if agently_auth is not None:
                await agently_auth.close()
            await self._shutdown_runtime()
            if self._user_manager is not None:
                await self._user_manager.shutdown_all()
            if self.history_archive is not None:
                self.history_archive.store.close()
            browse = sys.modules.get("octop.infra.backend.browse")
            if browse is not None:
                browse.dispose_all_browse_sessions()
        finally:
            if self.services is not None:
                self.services.db.close()
            self.services = None
            self._dsh_service = None
            self._execution_service = None
            self._platform_collaboration = None
            self.app_runtime = None
            self._user_manager = None
            self.history_archive = None
            self.trajectory_service = None
            self.thread_registry = None
            self._started = False
            logger.info("octop server stopped")
            self._close_logging()

    # ----- helpers -----

    def _setup_logging(self) -> None:
        self._close_logging()
        log_dir = self.paths.logs_dir
        log_dir.mkdir(parents=True, exist_ok=True)
        log_path = self.paths.log

        # Migrate the legacy single-file log (~/.xfriend/xfriend.log) into the new logs dir.
        legacy = self.paths.root / "xfriend.log"
        if legacy.exists() and not log_path.exists():
            with suppress(OSError):
                legacy.replace(log_path)

        retention_days = _parse_log_retention_days()
        max_bytes = _parse_log_max_bytes()
        compress_rotated = _parse_log_compress()
        handler = _build_log_handler(
            log_path,
            retention_days,
            max_bytes=max_bytes,
            compress_rotated=compress_rotated,
        )
        _purge_stale_logs(log_dir, retention_days)
        # Catch up: gzip older plains, leave the newest rotated file plain (delaycompress).
        delaycompress_rotated_logs(log_dir, enabled=compress_rotated)

        root = logging.getLogger()
        self._logging_handler = handler
        if _attach_log_handler(root, handler):
            self._logging_targets.append(root)
        # Persist framework (uvicorn) request/error logs into the same file too.
        for name in ("uvicorn", "uvicorn.access", "uvicorn.error"):
            target = logging.getLogger(name)
            if _attach_log_handler(target, handler):
                self._logging_targets.append(target)

        level = os.environ.get("OCTOP_LOG_LEVEL", "info").upper()
        root.setLevel(getattr(logging, level, logging.INFO))
        if root.level > logging.DEBUG:
            # httpx logs full request URLs at INFO, leaking query-string
            # secrets (OAuth access_token, corpsecret) into the log file.
            logging.getLogger("httpx").setLevel(logging.WARNING)
            logging.getLogger("httpcore").setLevel(logging.WARNING)

    def _close_logging(self) -> None:
        handler = self._logging_handler
        if handler is None:
            return
        for target in self._logging_targets:
            target.removeHandler(handler)
        handler.close()
        self._logging_targets.clear()
        self._logging_handler = None

    def _ensure_jwt_secret(self) -> None:
        assert self.services is not None
        self.services.secret_repo.get_or_create("jwt", lambda: os.urandom(32))
