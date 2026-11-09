"""Runtimes pinned by version and checksum (``templates/versions.json``): WinSW, PostgreSQL, Caddy and Node.

``fetch`` downloads each one into ``<root>\\downloads``, checks its hash **before** anything else touches it and
only then extracts it into ``<root>\\runtime\\<name>-<version>``. A file whose hash differs is deleted and nothing
is extracted. A verified runtime carries a stamp with its hash, so fetching again changes nothing.
Python is not here: uv installs it and verifies it against its own pinned hashes.
"""

import hashlib
import json
import shutil
import urllib.request
import zipfile
from dataclasses import dataclass
from importlib import resources
from pathlib import Path
from typing import Literal

STAMP = ".districom-artifact.json"
CHUNK = 1 << 20


class ArtifactError(Exception):
    pass


@dataclass(frozen=True)
class Artifact:
    name: str
    version: str
    url: str
    algorithm: Literal["sha256", "sha512"]
    digest: str
    kind: Literal["file", "zip"]
    target: str

    def home(self, runtime: Path) -> Path:
        return runtime / f"{self.name}-{self.version}"

    def path(self, runtime: Path) -> Path:
        """The executable file or folder the services use."""
        return self.home(runtime) / self.target


def manifest() -> dict[str, Artifact]:
    data = json.loads(resources.files("districom_ops").joinpath("templates/versions.json").read_text("utf-8"))
    artifacts = {}
    for name, item in data["artifacts"].items():
        if item["algorithm"] not in ("sha256", "sha512") or item["kind"] not in ("file", "zip"):
            raise ArtifactError(f"{name}: unsupported algorithm or kind in versions.json")
        artifacts[name] = Artifact(name=name, **item)
    return artifacts


def file_digest(path: Path, algorithm: str) -> str:
    digest = hashlib.new(algorithm)
    with path.open("rb") as handle:
        while chunk := handle.read(CHUNK):
            digest.update(chunk)
    return digest.hexdigest()


def verified(artifact: Artifact, runtime: Path) -> bool:
    """True when the runtime was extracted from a file with exactly the pinned hash."""
    stamp = artifact.home(runtime) / STAMP
    try:
        data = json.loads(stamp.read_text("utf-8"))
    except (OSError, ValueError):
        return False
    return (
        data.get("digest") == artifact.digest
        and data.get("version") == artifact.version
        and artifact.path(runtime).exists()
    )


def _download(url: str, target: Path) -> None:
    if not url.startswith("https://"):
        raise ArtifactError(f"refusing a non-HTTPS download: {url}")
    partial = target.with_name(target.name + ".partial")
    with urllib.request.urlopen(url, timeout=120) as response, partial.open("wb") as handle:  # noqa: S310 (https only)
        shutil.copyfileobj(response, handle, CHUNK)
    partial.replace(target)


def install(artifact: Artifact, archive: Path, runtime: Path) -> bool:
    """Verify ``archive`` and extract it. Returns False when the runtime was already verified (nothing done)."""
    if verified(artifact, runtime):
        return False
    actual = file_digest(archive, artifact.algorithm)
    if actual != artifact.digest:
        archive.unlink(missing_ok=True)
        raise ArtifactError(
            f"{artifact.name} {artifact.version}: {artifact.algorithm} mismatch, the download was deleted "
            f"(expected {artifact.digest[:16]}…, got {actual[:16]}…)"
        )
    home = artifact.home(runtime)
    if home.exists():
        shutil.rmtree(home)  # an unverified leftover: never reused
    staging = home.with_name(home.name + ".staging")
    if staging.exists():
        shutil.rmtree(staging)
    staging.mkdir(parents=True)
    if artifact.kind == "zip":
        with zipfile.ZipFile(archive) as bundle:
            for member in bundle.namelist():
                resolved = (staging / member).resolve()
                if not resolved.is_relative_to(staging.resolve()):
                    raise ArtifactError(f"{artifact.name}: unsafe path inside the zip")
            bundle.extractall(staging)
    else:
        shutil.copy2(archive, staging / artifact.target)
    stamp = {"name": artifact.name, "version": artifact.version, "algorithm": artifact.algorithm}
    (staging / STAMP).write_text(json.dumps({**stamp, "digest": artifact.digest}), encoding="utf-8")
    staging.replace(home)
    if not artifact.path(runtime).exists():
        raise ArtifactError(f"{artifact.name}: {artifact.target} missing after extraction")
    return True


def fetch(artifact: Artifact, downloads: Path, runtime: Path) -> bool:
    """Download (if needed), verify and extract. Returns True when something was installed."""
    if verified(artifact, runtime):
        return False
    downloads.mkdir(parents=True, exist_ok=True)
    archive = downloads / artifact.url.rsplit("/", 1)[-1]
    if not archive.exists():
        _download(artifact.url, archive)
    return install(artifact, archive, runtime)
