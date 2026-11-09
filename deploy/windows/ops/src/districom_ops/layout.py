"""Folder layout of an installation (``docs/WINDOWS_DEPLOYMENT.md §3``).

```
<root>\\releases\\<version>\\   backend and frontend of each version
<root>\\current               link to the version in use (created by C12, never here)
<root>\\config\\               variables and secrets (ACL restricted by C3)
<root>\\logs\\
<root>\\state\\                operations record, maintenance window
```
"""

from dataclasses import dataclass
from pathlib import Path

FOLDERS = ("releases", "config", "logs", "state")


class LayoutError(Exception):
    pass


@dataclass(frozen=True)
class Layout:
    root: Path

    def __post_init__(self) -> None:
        if not self.root.is_absolute():
            raise LayoutError("the installation root must be an absolute path")

    @property
    def releases(self) -> Path:
        return self.root / "releases"

    @property
    def current(self) -> Path:
        return self.root / "current"

    @property
    def config(self) -> Path:
        return self.root / "config"

    @property
    def logs(self) -> Path:
        return self.root / "logs"

    @property
    def state(self) -> Path:
        return self.root / "state"

    @property
    def config_file(self) -> Path:
        return self.config / "districom.env"

    def ensure(self) -> list[Path]:
        """Create the folders that are missing; returns the ones created. Running it again changes nothing."""
        created = []
        for name in FOLDERS:
            folder = self.root / name
            if folder.exists() and not folder.is_dir():
                raise LayoutError(f"{name} exists and is not a folder")
            if not folder.exists():
                folder.mkdir(parents=True)
                created.append(folder)
        return created

    def missing(self) -> list[str]:
        return [name for name in FOLDERS if not (self.root / name).is_dir()]
