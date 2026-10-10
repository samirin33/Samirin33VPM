"""git / gh コマンドの実行。"""

import os
import shutil
import subprocess
from dataclasses import dataclass
from typing import Callable, List, Optional

_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


class CommandError(RuntimeError):
    pass


def run(args: List[str], cwd: Optional[str] = None, log: Optional[Callable[[str], None]] = None,
        check: bool = True, input_text: Optional[str] = None) -> subprocess.CompletedProcess:
    if log:
        log("> " + " ".join(args))
    try:
        proc = subprocess.run(
            args,
            cwd=cwd,
            input=input_text,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            creationflags=_NO_WINDOW,
        )
    except FileNotFoundError:
        raise CommandError(f"コマンドが見つかりません: {args[0]}")
    if log:
        for line in (proc.stdout + proc.stderr).splitlines():
            if line.strip():
                log("  " + line)
    if check and proc.returncode != 0:
        raise CommandError(f"{' '.join(args)} が失敗しました (exit {proc.returncode})\n{proc.stderr.strip()}")
    return proc


def git(repo: str, *args: str, log=None, check=True, input_text=None) -> subprocess.CompletedProcess:
    return run(["git", "-c", "core.quotepath=false", *args], cwd=repo, log=log, check=check, input_text=input_text)


@dataclass
class RepoStatus:
    branch: str = ""
    dirty_files: int = 0
    dirty_in_path: int = 0
    ahead: int = 0
    behind: int = 0
    has_upstream: bool = False
    error: str = ""

    def summary(self) -> str:
        if self.error:
            return self.error
        parts = [self.branch or "?"]
        if self.dirty_in_path:
            parts.append(f"未コミット {self.dirty_in_path}")
        elif self.dirty_files:
            parts.append(f"他の未コミット {self.dirty_files}")
        if self.has_upstream:
            if self.ahead:
                parts.append(f"未プッシュ {self.ahead}")
            if self.behind:
                parts.append(f"取り込み待ち {self.behind}")
        else:
            parts.append("上流なし")
        if len(parts) == 1:
            parts.append("クリーン")
        return " / ".join(parts)


def repo_status(repo: str, path: Optional[str] = None) -> RepoStatus:
    status = RepoStatus()
    if not os.path.isdir(os.path.join(repo, ".git")):
        status.error = "git リポジトリではありません"
        return status
    try:
        status.branch = git(repo, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip()
        lines = [l for l in git(repo, "status", "--porcelain").stdout.splitlines() if l.strip()]
        status.dirty_files = len(lines)
        if path:
            in_path = git(repo, "status", "--porcelain", "--", path).stdout.splitlines()
            status.dirty_in_path = len([l for l in in_path if l.strip()])
        upstream = git(repo, "rev-parse", "--abbrev-ref", "@{u}", check=False)
        if upstream.returncode == 0:
            status.has_upstream = True
            counts = git(repo, "rev-list", "--left-right", "--count", "@{u}...HEAD").stdout.split()
            status.behind, status.ahead = int(counts[0]), int(counts[1])
    except CommandError as e:
        status.error = str(e).splitlines()[0]
    return status


def commit_and_push(repo: str, paths: List[str], message: str, push: bool, log=None) -> bool:
    """paths だけをステージしてコミットする。変更が無ければ False。"""
    if not message.strip():
        raise CommandError("コミットメッセージが空です。")
    git(repo, "add", "-A", "--", *paths, log=log)
    staged = git(repo, "diff", "--cached", "--quiet", "--", *paths, check=False)
    committed = False
    if staged.returncode != 0:
        git(repo, "commit", "-F", "-", "--", *paths, log=log, input_text=message)
        committed = True
    elif log:
        log("コミットする変更はありません。")
    if push:
        git(repo, "push", log=log)
    return committed


def fetch(repo: str, log=None) -> None:
    git(repo, "fetch", "--prune", log=log)


def gh_ready() -> bool:
    if not shutil.which("gh"):
        return False
    try:
        return run(["gh", "auth", "status"], check=False).returncode == 0
    except CommandError:
        return False


def dispatch_workflow(github_repo: str, workflow: str, branch: str, log=None) -> None:
    run(["gh", "workflow", "run", workflow, "-R", github_repo, "--ref", branch], log=log)
