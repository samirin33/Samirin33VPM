"""開発プロジェクトのパッケージフォルダをリリース用リポジトリへミラーコピーする。"""

import fnmatch
import os
import shutil
from dataclasses import dataclass, field
from typing import Callable, Dict, Iterable, List, Optional

ALWAYS_EXCLUDED_DIRS = {".git"}

# git の autocrlf で改行が揃えられるため、改行コードだけの違いは差分として扱わない
TEXT_EXTENSIONS = {
    ".cs", ".json", ".md", ".txt", ".asmdef", ".asmref", ".meta", ".uxml", ".uss", ".shader",
    ".cginc", ".hlsl", ".compute", ".xml", ".yml", ".yaml", ".asset", ".prefab", ".mat",
    ".controller", ".anim", ".overrideController", ".mask", ".unity", ".html", ".css", ".js", ".py",
}


@dataclass
class SyncPlan:
    source: str
    target: str
    added: List[str] = field(default_factory=list)
    modified: List[str] = field(default_factory=list)
    deleted: List[str] = field(default_factory=list)
    unchanged: int = 0
    newline_only: int = 0

    @property
    def change_count(self) -> int:
        return len(self.added) + len(self.modified) + len(self.deleted)

    def summary(self) -> str:
        if self.change_count == 0:
            return "差分なし"
        return f"追加 {len(self.added)} / 変更 {len(self.modified)} / 削除 {len(self.deleted)}"


def is_excluded(rel_path: str, patterns: Iterable[str]) -> bool:
    """パターンに / が無ければフォルダ名・ファイル名単位、あればパッケージ直下からのパスで照合する。"""
    parts = rel_path.split("/")
    for raw in patterns or []:
        pattern = (raw or "").strip().replace("\\", "/").strip("/")
        if not pattern:
            continue
        if "/" in pattern:
            if fnmatch.fnmatch(rel_path, pattern) or fnmatch.fnmatch(rel_path, pattern + "/*"):
                return True
        elif any(fnmatch.fnmatch(part, pattern) for part in parts):
            return True
    return False


def list_files(root: str, excludes: Iterable[str] = ()) -> Dict[str, str]:
    """相対パス（/ 区切り）→ 絶対パス。"""
    result: Dict[str, str] = {}
    if not os.path.isdir(root):
        return result
    excludes = list(excludes or [])
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in ALWAYS_EXCLUDED_DIRS]
        rel_dir = os.path.relpath(dirpath, root).replace("\\", "/")
        rel_dir = "" if rel_dir == "." else rel_dir + "/"
        for name in filenames:
            rel = rel_dir + name
            if not is_excluded(rel, excludes):
                result[rel] = os.path.join(dirpath, name)
    return result


def _same_content(a: str, b: str) -> Optional[str]:
    """同一なら 'same'、改行コードだけ違うなら 'newline'、異なるなら None。"""
    if os.path.getsize(a) == os.path.getsize(b):
        with open(a, "rb") as fa, open(b, "rb") as fb:
            if fa.read() == fb.read():
                return "same"
    ext = os.path.splitext(a)[1].lower()
    if ext not in {e.lower() for e in TEXT_EXTENSIONS}:
        return None
    with open(a, "rb") as fa, open(b, "rb") as fb:
        if fa.read().replace(b"\r\n", b"\n") == fb.read().replace(b"\r\n", b"\n"):
            return "newline"
    return None


def compute_plan(source: str, target: str, excludes: Iterable[str] = ()) -> SyncPlan:
    if not os.path.isdir(source):
        raise FileNotFoundError(f"コピー元が見つかりません: {source}")
    plan = SyncPlan(source=source, target=target)
    # 除外したものはコピー先からも消す（配布物に含めない扱い）
    src_files = list_files(source, excludes)
    dst_files = list_files(target)

    for rel, src_path in sorted(src_files.items()):
        dst_path = dst_files.get(rel)
        if dst_path is None:
            plan.added.append(rel)
            continue
        same = _same_content(src_path, dst_path)
        if same == "same":
            plan.unchanged += 1
        elif same == "newline":
            plan.newline_only += 1
        else:
            plan.modified.append(rel)

    plan.deleted = sorted(rel for rel in dst_files if rel not in src_files)
    return plan


def validate_target(target: str, repo_dir: str, package_id: str) -> None:
    target_abs = os.path.normcase(os.path.abspath(target))
    repo_abs = os.path.normcase(os.path.abspath(repo_dir))
    if os.path.basename(target_abs) != os.path.normcase(package_id):
        raise ValueError(f"コピー先のフォルダ名がパッケージ ID と一致しません: {target}")
    if not target_abs.startswith(repo_abs + os.sep):
        raise ValueError(f"コピー先がリポジトリの外を指しています: {target}")
    if not os.path.isdir(os.path.join(repo_dir, ".git")):
        raise ValueError(f"コピー先リポジトリに .git がありません: {repo_dir}")


def apply_plan(plan: SyncPlan, log: Callable[[str], None] = print) -> None:
    for rel in plan.added + plan.modified:
        src = os.path.join(plan.source, *rel.split("/"))
        dst = os.path.join(plan.target, *rel.split("/"))
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.copy2(src, dst)
    for rel in plan.deleted:
        path = os.path.join(plan.target, *rel.split("/"))
        if os.path.exists(path):
            os.remove(path)
    _remove_empty_dirs(plan.target)
    log(f"コピー完了: {plan.summary()}")


def _remove_empty_dirs(root: str) -> None:
    for dirpath, dirnames, filenames in os.walk(root, topdown=False):
        if dirpath == root or ".git" in dirpath.split(os.sep):
            continue
        if not os.listdir(dirpath):
            os.rmdir(dirpath)
