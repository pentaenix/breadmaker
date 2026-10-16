from __future__ import annotations

import argparse
import os
import shutil
import sys
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_HOME = Path.home() / ".breadmaker-editor"
LOCK_NAME = "breadmaker_editor.lock"


class ResetError(RuntimeError):
    pass


def configured_home() -> Path:
    raw = Path(os.environ.get("BREADMAKER_HOME", str(DEFAULT_HOME))).expanduser()
    # Keep the unrevolved path long enough to reject a symlink root. A destructive
    # utility must never let a symlink redirect deletion outside Breadmaker state.
    if raw.exists() and raw.is_symlink():
        raise ResetError("Breadmaker data home is a symbolic link; reset refuses to follow it.")
    return raw.absolute()


def validate_home(root: Path) -> None:
    resolved = root.resolve(strict=False)
    home = Path.home().resolve()
    forbidden = {
        Path("/").resolve(),
        home,
        APP_ROOT.resolve(),
        APP_ROOT.parent.resolve(),
    }
    if resolved in forbidden:
        raise ResetError(f"Refusing to reset unsafe path: {resolved}")
    if resolved.parent == resolved:
        raise ResetError(f"Refusing to reset filesystem root: {resolved}")
    # A custom BREADMAKER_HOME is supported, but it still needs enough path depth
    # that a typo such as /Users/name cannot erase a home directory.
    if len(resolved.parts) < 3:
        raise ResetError(f"Refusing to reset unusually broad path: {resolved}")


def _pid_is_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def ensure_not_running(root: Path) -> None:
    projects = root / "projects"
    if not projects.exists():
        return
    for lock_path in projects.glob(f"*/{LOCK_NAME}"):
        if not lock_path.is_file():
            continue
        handle = lock_path.open("a+")
        try:
            try:
                import fcntl
            except ImportError:
                handle.seek(0)
                raw = handle.read().strip()
                if raw.isdigit() and _pid_is_alive(int(raw)):
                    raise ResetError("Breadmaker appears to be running. Close it before resetting.")
                continue
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise ResetError("Breadmaker is running. Close it before resetting.") from exc
            else:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        finally:
            handle.close()


def remove_workspace(root: Path) -> None:
    if not root.exists():
        return
    if not root.is_dir():
        raise ResetError(f"Breadmaker data home is not a directory: {root}")
    # shutil.rmtree removes the directory entry itself and does not follow symlinked
    # child directories. External imports/exports are therefore never traversed.
    shutil.rmtree(root)


def reset(confirm: bool) -> Path:
    if not confirm:
        raise ResetError("Reset confirmation flag is required.")
    root = configured_home()
    validate_home(root)
    ensure_not_running(root)
    remove_workspace(root)
    return root


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="./reset",
        description="Permanently erase Breadmaker-managed application data and return to first-run state.",
        add_help=True,
    )
    parser.add_argument("--confirm", action="store_true", help="required destructive confirmation")
    args, extras = parser.parse_known_args(argv)
    if extras:
        parser.error("unknown arguments: " + " ".join(extras))
    if not args.confirm:
        print("DANGER: this permanently deletes every Breadmaker-managed project, backup, revision, preference, and cache.", file=sys.stderr)
        print("External files you imported from or exported to are not touched.", file=sys.stderr)
        print("To proceed, run exactly: ./reset --confirm", file=sys.stderr)
        return 2
    try:
        root = reset(True)
    except ResetError as exc:
        print(f"Reset refused: {exc}", file=sys.stderr)
        return 1
    except OSError as exc:
        print(f"Reset failed: {exc}", file=sys.stderr)
        return 1
    print(f"Breadmaker reset complete: {root}")
    print("The next ./run will start as a brand-new installation.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
