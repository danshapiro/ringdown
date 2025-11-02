from __future__ import annotations

import argparse
import datetime as _dt
import logging
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Sequence

from shutil import which

import yaml

log = logging.getLogger("pipecat-agent-deploy")

REPO_ROOT = Path(__file__).resolve().parent
DEFAULT_CONFIG_PATH = REPO_ROOT / "pipelines" / "pipecat-agent" / "deploy-config.yaml"


@dataclass
class PipecatAgentConfig:
    agent_name: str
    image_repository: str
    image_credentials: str
    secret_set: str
    agent_profile: str
    min_agents: int = 0
    max_agents: int = 1
    enable_krisp: bool = False
    enable_managed_keys: bool = False
    organization: str | None = None
    docker_extra_args: list[str] | None = None


def _gcloud_executable() -> str:
    """Return an invocable gcloud command, handling Windows .cmd wrappers."""

    candidates = ["gcloud"]
    if os.name == "nt":
        candidates = ["gcloud.cmd", "gcloud.CMD"] + candidates

    for candidate in candidates:
        path = which(candidate)
        if path:
            return path

    return "gcloud"


def _run_command(
    args: Sequence[str],
    *,
    capture: bool = True,
    cwd: Path | str | None = None,
    env: dict[str, str] | None = None,
    input_text: str | None = None,
) -> str:
    cmd_str = " ".join(args)
    log.info("$ %s", cmd_str)
    proc = subprocess.run(  # noqa: S603
        list(args),
        cwd=str(cwd) if cwd else None,
        env=env,
        input=input_text,
        text=True,
        encoding="utf-8",
        errors="replace",
        stdout=subprocess.PIPE if capture else None,
        stderr=subprocess.PIPE if capture else None,
    )
    if proc.returncode != 0:
        stdout = proc.stdout or ""
        stderr = proc.stderr or ""
        log.error("Command failed (%s)\nstdout: %s\nstderr: %s", proc.returncode, stdout, stderr)
        raise RuntimeError(f"Command failed: {cmd_str}\n{stderr}")
    if not capture:
        return ""
    return (proc.stdout or "").strip()


def _find_pipecat_cli() -> str:
    exe = which("pipecatcloud")
    if exe:
        return exe

    candidates: list[Path] = []

    userprofile = os.environ.get("USERPROFILE")
    if userprofile:
        candidates.append(Path(userprofile) / ".local" / "bin" / "pipecatcloud.exe")

    username = os.environ.get("USERNAME") or os.environ.get("USER")
    users_root = Path("/mnt/c/Users")
    if username:
        candidates.append(users_root / username / ".local" / "bin" / "pipecatcloud.exe")

    if users_root.exists():
        try:
            for entry in users_root.iterdir():
                candidates.append(entry / ".local" / "bin" / "pipecatcloud.exe")
        except PermissionError:
            pass

    for candidate in candidates:
        try:
            if candidate and candidate.is_file():
                return str(candidate)
        except OSError:
            continue

    raise FileNotFoundError(
        "pipecatcloud CLI not found in PATH. Install it with `uv tool install pipecatcloud` "
        "or ensure the executable is discoverable."
    )


def _repo_relative(path: Path) -> Path:
    try:
        return path.resolve().relative_to(REPO_ROOT)
    except ValueError:
        return path


def _git_tree_hash(path: Path) -> str | None:
    rel = _repo_relative(path).as_posix()
    if not rel:
        return None
    try:
        return _run_command(["git", "rev-parse", f"HEAD:{rel}"])
    except RuntimeError:
        return None


def _git_path_dirty(path: Path) -> bool:
    rel = _repo_relative(path).as_posix()
    if not rel:
        return False
    output = _run_command(["git", "status", "--porcelain=1", "--", rel])
    return bool(output.strip())


def _parse_image_repository(image_repo: str) -> tuple[str, str, str, str]:
    parts = image_repo.split("/")
    if len(parts) != 4:
        raise SystemExit(
            "Pipecat agent image_repository must be of the form "
            "<region>-docker.pkg.dev/<project>/<repository>/<image>: "
            f"{image_repo}"
        )
    host, project_id, repository, image_name = parts
    return host, project_id, repository, image_name


def _artifact_registry_tag_exists(image_repo: str, project_id: str, tag: str) -> bool:
    try:
        output = _run_command(
            [
                _gcloud_executable(),
                "artifacts",
                "docker",
                "images",
                "list",
                image_repo,
                "--include-tags",
                f"--project={project_id}",
                f"--filter=tags:{tag}",
                "--format=value(version)",
                "--limit=1",
            ]
        )
    except RuntimeError as exc:
        log.warning("Unable to inspect existing Pipecat agent tags: %s", exc)
        return False
    return bool(output.strip())


def _docker_is_running() -> bool:
    try:
        _run_command(["docker", "info"], capture=False)
        return True
    except Exception:  # noqa: BLE001
        return False


def _docker_build(
    image: str,
    *,
    context_dir: Path,
    build_args: list[str] | None = None,
    labels: list[str] | None = None,
    extra_args: list[str] | None = None,
    no_cache: bool = False,
) -> None:
    args: list[str] = ["docker", "build"]
    if no_cache:
        args.append("--no-cache")
    if build_args:
        for value in build_args:
            args.extend(["--build-arg", value])
    if labels:
        for value in labels:
            args.extend(["--label", value])
    if extra_args:
        args.extend(extra_args)
    args.extend(["-t", image, "."])
    _run_command(args, capture=False, cwd=context_dir)


def _docker_push(image: str) -> None:
    _run_command(["docker", "push", image], capture=False)


def _docker_tag(source: str, target: str) -> None:
    _run_command(["docker", "tag", source, target], capture=False)


def _docker_login(host: str) -> None:
    token = _run_command([_gcloud_executable(), "auth", "print-access-token"])
    _run_command(
        ["docker", "login", "-u", "oauth2accesstoken", "--password-stdin", f"https://{host}"],
        capture=False,
        input_text=token + "\n",
    )


def _ensure_repository(host: str, project_id: str, repository: str) -> None:
    location = host.split("-docker.pkg.dev")[0]
    try:
        _run_command(
            [
                _gcloud_executable(),
                "artifacts",
                "repositories",
                "describe",
                repository,
                f"--location={location}",
                f"--project={project_id}",
            ]
        )
    except RuntimeError:
        log.info("Creating Artifact Registry repository %s in %s", repository, location)
        _run_command(
            [
                _gcloud_executable(),
                "artifacts",
                "repositories",
                "create",
                repository,
                "--repository-format=docker",
                f"--location={location}",
                "--quiet",
                f"--project={project_id}",
            ]
        )


def _pipecat_cli_env() -> dict[str, str]:
    env = os.environ.copy()
    env.setdefault("PYTHONIOENCODING", "utf-8")
    env.setdefault("PYTHONUTF8", "1")
    env.setdefault("COLUMNS", "200")
    env.setdefault("CI", "1")
    return env


def _deploy_with_cli(config: PipecatAgentConfig, image_uri: str) -> None:
    args: list[str] = [
        _find_pipecat_cli(),
        "deploy",
        config.agent_name,
        image_uri,
        "--credentials",
        config.image_credentials,
        "--secrets",
        config.secret_set,
        "--profile",
        config.agent_profile,
        "--min-agents",
        str(config.min_agents),
        "--max-agents",
        str(config.max_agents),
        "--force",
    ]
    if config.organization:
        args.extend(["--organization", config.organization])
    if config.enable_krisp:
        args.append("--enable-krisp")
    if config.enable_managed_keys:
        args.append("--enable-managed-keys")

    _run_command(args, capture=False, env=_pipecat_cli_env())


def load_config(config_path: Path | None = None) -> PipecatAgentConfig | None:
    path = config_path or DEFAULT_CONFIG_PATH
    if not path.exists():
        log.info("Pipecat agent config %s not found; skipping.", path)
        return None

    with path.open("r", encoding="utf-8") as fh:
        raw = yaml.safe_load(fh) or {}

    required = (
        "agent_name",
        "image_repository",
        "image_credentials",
        "secret_set",
        "agent_profile",
    )
    missing = [key for key in required if key not in raw]
    if missing:
        raise SystemExit(
            f"Pipecat agent config {path} missing required keys: {', '.join(missing)}"
        )

    extra_args: list[str] | None = None
    if "docker_extra_args" in raw and raw["docker_extra_args"] is not None:
        value = raw["docker_extra_args"]
        if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
            raise SystemExit(
                f"'docker_extra_args' in {path} must be a list of strings, got {value!r}"
            )
        extra_args = list(value)

    try:
        return PipecatAgentConfig(
            agent_name=str(raw["agent_name"]),
            image_repository=str(raw["image_repository"]),
            image_credentials=str(raw["image_credentials"]),
            secret_set=str(raw["secret_set"]),
            agent_profile=str(raw["agent_profile"]),
            min_agents=int(raw.get("min_agents", 0)),
            max_agents=int(raw.get("max_agents", 1)),
            enable_krisp=bool(raw.get("enable_krisp", False)),
            enable_managed_keys=bool(raw.get("enable_managed_keys", False)),
            organization=str(raw["organization"]) if raw.get("organization") else None,
            docker_extra_args=extra_args,
        )
    except (TypeError, ValueError) as exc:
        raise SystemExit(f"Invalid Pipecat agent config {path}: {exc}") from exc


def deploy_if_needed(
    config: PipecatAgentConfig,
    *,
    timestamp: str,
    no_cache: bool = False,
    skip_if_clean: bool = True,
) -> str | None:
    agent_dir = REPO_ROOT / "pipelines" / "pipecat-agent"
    if not agent_dir.is_dir():
        raise SystemExit(f"Pipecat agent directory missing: {agent_dir}")

    host, project_id, repository, image_name = _parse_image_repository(config.image_repository)
    repo_path = "/".join([host, project_id, repository, image_name])

    dirty = _git_path_dirty(agent_dir)
    tree_hash = _git_tree_hash(agent_dir)
    tree_tag = f"tree-{tree_hash[:12]}" if tree_hash else None

    if skip_if_clean and not dirty and tree_tag and _artifact_registry_tag_exists(repo_path, project_id, tree_tag):
        log.info("Pipecat agent unchanged (%s); skipping image rebuild.", tree_tag)
        return None

    if not _docker_is_running():
        raise SystemExit("Docker daemon not running - unable to build Pipecat agent image.")

    build_args = [f"BUILD_VERSION={timestamp}"]
    labels = [f"build_version={timestamp}"]
    if tree_hash:
        labels.append(f"source_tree={tree_hash}")

    image_with_timestamp = f"{repo_path}:{timestamp}"
    _docker_build(
        image_with_timestamp,
        context_dir=agent_dir,
        build_args=build_args,
        labels=labels,
        extra_args=config.docker_extra_args,
        no_cache=no_cache,
    )

    tree_image = None
    if tree_tag:
        tree_image = f"{repo_path}:{tree_tag}"
        _docker_tag(image_with_timestamp, tree_image)

    _ensure_repository(host, project_id, repository)
    _run_command([_gcloud_executable(), "auth", "configure-docker", host], capture=False)
    _docker_login(host)

    _docker_push(image_with_timestamp)
    if tree_image:
        _docker_push(tree_image)

    _deploy_with_cli(config, image_with_timestamp)
    log.info("Pipecat agent deployed with image %s", image_with_timestamp)
    return image_with_timestamp


def _setup_logging(verbosity: int) -> None:
    level = logging.INFO if verbosity == 0 else logging.DEBUG
    logging.basicConfig(level=level, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


def main(argv: Optional[list[str]] = None) -> None:
    parser = argparse.ArgumentParser(description="Build and deploy the Pipecat managed A/V agent")
    parser.add_argument(
        "--config",
        default=str(DEFAULT_CONFIG_PATH),
        help="Path to Pipecat agent deploy config (default: %(default)s)",
    )
    parser.add_argument(
        "--timestamp",
        help="Override timestamp tag (default: current UTC in YYYYMMDDHHMM)",
    )
    parser.add_argument("--no-cache", action="store_true", help="Build Docker image with --no-cache")
    parser.add_argument("--force", action="store_true", help="Force rebuild even if git tree is clean")
    parser.add_argument("-v", "--verbose", action="count", default=0, help="Increase logging verbosity")

    args = parser.parse_args(argv)
    _setup_logging(args.verbose)
    config = load_config(Path(args.config))
    if not config:
        print("Pipecat agent config not found; nothing to deploy.")
        return

    timestamp = args.timestamp or _dt.datetime.utcnow().strftime("%Y%m%d%H%M")
    deploy_if_needed(
        config,
        timestamp=timestamp,
        no_cache=args.no_cache,
        skip_if_clean=not args.force,
    )


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        raise SystemExit(1)
