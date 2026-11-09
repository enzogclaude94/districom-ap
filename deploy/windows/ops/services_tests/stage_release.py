"""Stage a release folder for the W2-C2 acceptance job, as C12 will package one (CI only, disposable machine).

``<root>\\releases\\<release>\\``:
  backend\\           the backend code and its virtual environment (no dev dependencies)
  frontend\\          the Next.js standalone server, with .next\\static and public
  windows\\ops\\      districom_ops and its virtual environment (with windows\\backup)

Python is installed under ``<root>\\runtime\\python`` and packages are copied, not linked, so the virtual service
accounts can read everything (a runner profile or a hard-linked uv cache would not be readable by them).

Usage: python stage_release.py <repo> <root> <release> <python version>. Standard library only.
"""

import os
import shutil
import subprocess
import sys
from pathlib import Path

IGNORE = shutil.ignore_patterns(
    ".venv", "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache", "test-results", "node_modules"
)


def uv(args: list[str], cwd: Path, env: dict[str, str]) -> str:
    result = subprocess.run(["uv", *args], cwd=cwd, env=env, capture_output=True, text=True, check=False)  # noqa: S607
    sys.stdout.write(result.stdout[-2000:])
    sys.stderr.write(result.stderr[-4000:])
    if result.returncode != 0:
        raise SystemExit(f"uv {' '.join(args)} failed with {result.returncode}")
    return result.stdout.strip()


def _pnpm_unit(path: Path) -> Path:
    """The ``.pnpm/<package>`` folder that holds ``path`` (its dependencies are siblings inside it), or itself."""
    for parent in (path, *path.parents):
        if parent.parent.name == ".pnpm":
            return parent
    return path


def copy_with_links(source: Path, target: Path, project: Path) -> int:
    """Copy the Next.js standalone output of a pnpm install keeping its directory links (junctions on Windows).

    pnpm resolves dependencies through those links, and on Windows the standalone output links back into the
    project's own ``node_modules``. Copying their contents breaks module resolution (``Cannot find module
    '@swc/helpers/...'``). So every link is re-created inside ``target`` and every ``.pnpm/<package>`` folder it
    reaches outside the standalone tree is copied once, at the same place relative to ``project``."""
    source, project = source.resolve(), project.resolve()
    links: list[tuple[Path, Path]] = []
    queue: list[tuple[Path, Path]] = [(source, target)]
    copied: set[Path] = set()

    def place(pointed: Path) -> Path:
        if pointed.is_relative_to(source):
            return target / pointed.relative_to(source)
        if not pointed.is_relative_to(project):
            raise SystemExit(f"a link points outside the project: {pointed}")
        unit = _pnpm_unit(pointed)
        unit_target = target / unit.relative_to(project)
        if unit not in copied:
            copied.add(unit)
            queue.append((unit, unit_target))
        return unit_target / pointed.relative_to(unit)

    while queue:
        tree, out_root = queue.pop()
        if out_root.exists() and tree != source:
            continue  # already part of the standalone copy
        for folder, dirs, files in os.walk(tree):
            here = Path(folder)
            out = out_root / here.relative_to(tree)
            out.mkdir(parents=True, exist_ok=True)
            for name in list(dirs):
                path = here / name
                if os.path.islink(path) or os.path.isjunction(path):
                    dirs.remove(name)
                    links.append((out / name, place(Path(os.path.realpath(path)))))
            for name in files:
                path = here / name
                shutil.copy2(os.path.realpath(path), out / name)
    for link, pointed in links:
        if link.exists() or os.path.isjunction(link) or os.path.islink(link):
            continue
        if sys.platform == "win32":
            import _winapi

            _winapi.CreateJunction(str(pointed), str(link))
        else:
            os.symlink(pointed, link, target_is_directory=True)
    return len(links)


def main(repo: Path, root: Path, release: str, python_version: str) -> None:
    target = root / "releases" / release
    if target.exists():
        shutil.rmtree(target)
    env = {**os.environ, "UV_PYTHON_INSTALL_DIR": str(root / "runtime" / "python"), "UV_LINK_MODE": "copy"}
    env.pop("VIRTUAL_ENV", None)
    uv(["python", "install", python_version], repo, env)
    python = uv(["python", "find", "--no-project", python_version], repo, env).splitlines()[-1]
    if not Path(python).resolve().is_relative_to((root / "runtime" / "python").resolve()):
        raise SystemExit(f"python is not under the runtime folder: {python}")

    shutil.copytree(repo / "backend", target / "backend", ignore=IGNORE)
    uv(["sync", "--frozen", "--no-dev", "--python", python], target / "backend", env)

    standalone = repo / "frontend" / ".next" / "standalone"
    if not (standalone / "server.js").is_file():
        raise SystemExit("no standalone build: build the frontend with NEXT_OUTPUT=standalone first")
    links = copy_with_links(standalone, target / "frontend", repo / "frontend")
    sys.stdout.write(f"frontend copied with {links} directory links\n")
    shutil.copytree(repo / "frontend" / ".next" / "static", target / "frontend" / ".next" / "static")
    if (repo / "frontend" / "public").is_dir():
        shutil.copytree(repo / "frontend" / "public", target / "frontend" / "public")

    for name in ("ops", "backup"):
        shutil.copytree(repo / "deploy" / "windows" / name, target / "windows" / name, ignore=IGNORE)
    uv(["sync", "--frozen", "--no-dev", "--python", python], target / "windows" / "ops", env)
    sys.stdout.write(f"release staged at {target}\n")


if __name__ == "__main__":
    main(Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3], sys.argv[4])
