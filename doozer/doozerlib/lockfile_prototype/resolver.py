"""
RPM resolution via rpm-lockfile-prototype subprocess.

Invokes the rpm-lockfile-prototype tool through system Python so
that python3-dnf (a system package built for the system Python) is
available. The venv Python may be a different minor version where
dnf cannot be imported.
"""

import logging
import os
import re
import shutil
from pathlib import Path
from tempfile import TemporaryDirectory

import yaml
from artcommonlib import logutil
from artcommonlib.exectools import cmd_gather_async

from doozerlib.lockfile_prototype.constants import (
    DEFAULT_RPM_INFILE_NAME,
    DEFAULT_RPM_LOCKFILE_NAME,
    JENKINS_CACHE_DIR,
    RPM_LOCKFILE_ENTRY_POINT,
    RPMDB_CACHE_ERROR_PATTERNS,
    RPMDB_CACHE_SUBDIR,
    SYSTEM_PYTHON,
    VALID_PKG_NAME,
)
from doozerlib.lockfile_prototype.models import LockfileData, RpmsInConfig
from doozerlib.lockfile_prototype.utils import build_env


class RpmResolver:
    """
    Invokes rpm-lockfile-prototype via system Python subprocess.

    Maintains a persistent DNF repodata cache directory across
    resolve() calls so that repeated runs against the same repos
    (common during multi-image rebases) skip redundant downloads.
    """

    def __init__(self, working_dir: Path, logger: logging.Logger | None = None, cache_dir: str | None = None):
        self.logger = logger or logutil.get_logger(__name__)
        self._working_dir = str(working_dir)
        self._cache_dir_owner = (
            None if cache_dir else TemporaryDirectory(prefix="rpm-lockfile-cache-", dir=self._working_dir)
        )
        self._cache_path = cache_dir or self._cache_dir_owner.name

        # In Jenkins, redirect the rpm-lockfile-prototype RPMDB cache to a
        # persistent volume via XDG_CACHE_HOME so it survives across job runs
        # and stays off the small root volume (~/.cache).
        # Outside Jenkins, honour an existing XDG_CACHE_HOME if set.
        if os.environ.get("JENKINS_HOME"):
            self._xdg_cache_home: Path | None = JENKINS_CACHE_DIR
        else:
            self._xdg_cache_home = None
        xdg_env = os.environ.get("XDG_CACHE_HOME")
        cache_root = self._xdg_cache_home or (Path(xdg_env) if xdg_env else Path.home() / ".cache")
        self._rpmdb_cache_path = cache_root / RPMDB_CACHE_SUBDIR
        self.logger.info("RPMDB cache path: %s", self._rpmdb_cache_path)

    async def resolve(
        self,
        config: RpmsInConfig,
        image_pullspec: str | None = None,
    ) -> LockfileData:
        """
        Resolve RPM packages by running rpm-lockfile-prototype via
        system Python as a subprocess.

        On RPMDB corruption errors, clears the cached RPMDB for the
        image and retries once before raising.

        Arg(s):
            config (RpmsInConfig): Input configuration.
            image_pullspec (str | None): Base image for rpmdb context.
                None means bare resolution.
        Return Value(s):
            LockfileData: Resolved lockfile.
        """
        with TemporaryDirectory(dir=self._working_dir) as tmpdir:
            in_file = Path(tmpdir) / DEFAULT_RPM_INFILE_NAME
            out_file = Path(tmpdir) / DEFAULT_RPM_LOCKFILE_NAME

            in_file.write_text(yaml.safe_dump(config.model_dump(exclude_none=True), sort_keys=False))

            # Log the input configuration for debugging
            self.logger.debug("rpm-lockfile-prototype input config:\n%s", in_file.read_text())

            cmd = [SYSTEM_PYTHON, "-c", RPM_LOCKFILE_ENTRY_POINT]
            if image_pullspec:
                cmd.extend(["--image", image_pullspec])
            else:
                cmd.append("--bare")
            cmd.extend(["--outfile", str(out_file), str(in_file)])

            env = build_env()
            env["RPM_LOCKFILE_PROTOTYPE_DNF_CACHE"] = self._cache_path
            if self._xdg_cache_home:
                env["XDG_CACHE_HOME"] = str(self._xdg_cache_home)
            env["TMPDIR"] = self._working_dir
            self.logger.info("Calling rpm-lockfile-prototype with image=%s, arches=%s, packages=%s, reinstall=%s",
                           image_pullspec or "bare",
                           config.arches,
                           len(config.packages) if config.packages else 0,
                           len(config.reinstallPackages) if config.reinstallPackages else 0)
            rc, stdout, stderr = await cmd_gather_async(cmd, check=False, env=env)

            if rc != 0:
                self.logger.error("rpm-lockfile-prototype failed with exit code %d", rc)
                self.logger.error("stderr:\n%s", stderr)
                if stdout:
                    self.logger.debug("stdout:\n%s", stdout)
                if image_pullspec and self._is_rpmdb_corrupt(stderr):
                    self._clear_rpmdb_cache(image_pullspec)
                    self.logger.info("Retrying rpm-lockfile-prototype after RPMDB cache error")
                    rc, stdout, stderr = await cmd_gather_async(cmd, check=False, env=env)
                    if rc == 0:
                        result = LockfileData.model_validate(yaml.safe_load(out_file.read_text()))
                        self.logger.info("Retry succeeded, returned %d arches", len(result.arches))
                        return result
                    error_summary = stderr.strip().rsplit("\n", 1)[-1]
                    self.logger.warning("Retry also failed (exit code %d): %s", rc, error_summary)
                    self.logger.debug("Full retry stderr:\n%s", stderr)

                raise RuntimeError(f"rpm-lockfile-prototype failed (exit code {rc}): {stderr}")

            result = LockfileData.model_validate(yaml.safe_load(out_file.read_text()))
            arch_details = [(a.arch, len(a.packages), len(a.source)) for a in result.arches]
            self.logger.info("rpm-lockfile-prototype succeeded, returned %d arches: %s",
                           len(result.arches),
                           [f"{arch}({pkgs}p,{src}s)" for arch, pkgs, src in arch_details])

            # Log stderr even on success to catch DNF warnings/errors
            if stderr:
                # Check for DNF-specific errors or arch-specific messages
                stderr_lower = stderr.lower()
                has_errors = any(x in stderr_lower for x in ['error', 'failed', 'exception', 'traceback'])
                log_level = self.logger.warning if has_errors else self.logger.debug

                log_level("rpm-lockfile-prototype stderr (exit 0):\n%s", stderr)

                # Look for arch-specific DNF messages
                for arch in ['x86_64', 'aarch64', 'ppc64le', 's390x']:
                    if arch in stderr:
                        self.logger.info("Found arch %s mentioned in stderr", arch)

            # Log if any arches are empty
            empty_arches = [arch for arch, pkgs, src in arch_details if pkgs == 0 and src == 0]
            if empty_arches:
                self.logger.warning("rpm-lockfile-prototype returned empty results for arches: %s", empty_arches)
                self.logger.warning("This may indicate DNF errors for these arches - check stderr above")

            return result

    @staticmethod
    def _is_rpmdb_corrupt(stderr: str) -> bool:
        """
        Check if stderr indicates a corrupt RPMDB cache.

        Arg(s):
            stderr (str): Standard error output from rpm-lockfile-prototype.
        Return Value(s):
            bool: True if corruption patterns are detected.
        """
        return any(pattern in stderr for pattern in RPMDB_CACHE_ERROR_PATTERNS)

    def _clear_rpmdb_cache(self, image_pullspec: str) -> bool:
        """
        Delete cached RPMDB entries for an image digest across all arches.

        Arg(s):
            image_pullspec (str): Image pullspec containing a digest
                (e.g. "registry.example.com/repo@sha256:abc123...").
        Return Value(s):
            bool: True if any cache entries were deleted.
        """
        match = re.search(r"@(sha256:[a-f0-9]+)", image_pullspec)
        if not match:
            self.logger.warning("Cannot extract digest from pullspec %s, skipping RPMDB cache cleanup", image_pullspec)
            return False

        digest = match.group(1)
        cleared = False

        if not self._rpmdb_cache_path.is_dir():
            return False

        for arch_dir in self._rpmdb_cache_path.iterdir():
            cache_entry = arch_dir / digest
            if cache_entry.is_dir():
                self.logger.warning("Clearing corrupt RPMDB cache: %s", cache_entry)
                try:
                    shutil.rmtree(cache_entry)
                    cleared = True
                except FileNotFoundError:
                    continue
                except OSError as ex:
                    self.logger.warning("Failed to remove RPMDB cache %s: %s", cache_entry, ex)

        return cleared

    @staticmethod
    def parse_missing_packages(error_text: str) -> set[str]:
        """
        Parse missing package names from rpm-lockfile-prototype error output.

        Handles the CLI format ("missing packages: X, Y"), DNF install/upgrade
        errors ("No match for argument: X"), and DNF reinstall errors
        ("no package matched: X").

        Arg(s):
            error_text (str): Error message from rpm-lockfile-prototype.
        Return Value(s):
            set[str]: Set of package names that were not found.
        """
        missing: set[str] = set()
        for line in error_text.splitlines():
            m = re.search(r"missing packages:\s*(.+)", line.strip())
            if m:
                missing.update(pkg.strip() for pkg in m.group(1).split(","))
            m = re.search(r"No match for argument:\s*(\S+)", line.strip())
            if m:
                missing.add(m.group(1).strip().rstrip(":"))
            m = re.search(r"no package matched:\s*(\S+)", line.strip())
            if m:
                missing.add(m.group(1).strip().rstrip(":"))
        return {p for p in missing if VALID_PKG_NAME.match(p)}
