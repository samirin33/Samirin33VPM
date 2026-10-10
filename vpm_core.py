"""vpm.json と package.json を扱う共通処理。GUI と CLI の両方から使う。"""

import copy
import json
import os
import re
from typing import Any, Dict, Iterable, List, Optional, Tuple

_SEMVER_RE = re.compile(r"^(\d+)\.(\d+)\.(\d+)(?:-([0-9A-Za-z.-]+))?(?:\+[0-9A-Za-z.-]+)?$")
_VERSION_FIELD_RE = re.compile(r'("version"\s*:\s*")([^"]*)(")')


# バージョン ------------------------------------------------------------

def parse_semver(version: str) -> Optional[Tuple[int, int, int, Optional[str]]]:
    m = _SEMVER_RE.match((version or "").strip())
    if not m:
        return None
    return int(m.group(1)), int(m.group(2)), int(m.group(3)), m.group(4)


def semver_key(version: str):
    """文字列順では 0.8.10 < 0.8.9 になるため、数値として比較するためのキー。"""
    parsed = parse_semver(version)
    if parsed is None:
        return (-1, -1, -1, 0, ())
    major, minor, patch, pre = parsed
    if pre is None:
        return (major, minor, patch, 1, ())
    ids = tuple((0, int(x), "") if x.isdigit() else (1, 0, x) for x in pre.split("."))
    return (major, minor, patch, 0, ids)


def compare_versions(a: str, b: str) -> int:
    ka, kb = semver_key(a), semver_key(b)
    return (ka > kb) - (ka < kb)


def sorted_versions(versions: Iterable[str], descending: bool = False) -> List[str]:
    return sorted(versions, key=semver_key, reverse=descending)


def latest_version(versions: Iterable[str]) -> Optional[str]:
    versions = list(versions)
    return max(versions, key=semver_key) if versions else None


def bump_version(version: str, part: str) -> str:
    parsed = parse_semver(version)
    if parsed is None:
        raise ValueError(f"バージョンの形式が正しくありません: {version}")
    major, minor, patch, pre = parsed
    if part == "major":
        return f"{major + 1}.0.0"
    if part == "minor":
        return f"{major}.{minor + 1}.0"
    if pre is not None:
        return f"{major}.{minor}.{patch}"
    return f"{major}.{minor}.{patch + 1}"


# JSON ------------------------------------------------------------------

def load_json(path: str) -> Dict[str, Any]:
    if not os.path.exists(path):
        raise FileNotFoundError(f"ファイルが見つかりません: {path}")
    with open(path, "r", encoding="utf-8-sig") as f:
        return json.load(f)


def _detect_newline(path: str) -> str:
    if os.path.exists(path):
        with open(path, "rb") as f:
            if b"\r\n" in f.read(4096):
                return "\r\n"
    return "\n"


def save_json(path: str, data: Dict[str, Any]) -> None:
    """既存ファイルの改行コードを保ったまま書き出す。"""
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline=_detect_newline(path)) as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write("\n")
    os.replace(tmp, path)


# package.json ----------------------------------------------------------

def read_package_json(package_dir: str) -> Dict[str, Any]:
    return load_json(os.path.join(package_dir, "package.json"))


def _rewrite_text(path: str, rewrite) -> None:
    with open(path, "rb") as f:
        raw = f.read()
    bom = raw.startswith(b"\xef\xbb\xbf")
    text = raw[3:].decode("utf-8") if bom else raw.decode("utf-8")
    new_text = rewrite(text)
    if new_text == text:
        return
    data = new_text.encode("utf-8")
    with open(path, "wb") as f:
        f.write((b"\xef\xbb\xbf" if bom else b"") + data)


def set_package_version(package_dir: str, new_version: str) -> None:
    """書式や改行を崩さないよう、version の値だけを書き換える。"""
    if parse_semver(new_version) is None:
        raise ValueError(f"バージョンの形式が正しくありません: {new_version}")
    path = os.path.join(package_dir, "package.json")

    def rewrite(text: str) -> str:
        new_text, count = _VERSION_FIELD_RE.subn(lambda m: m.group(1) + new_version + m.group(3), text, count=1)
        if count == 0:
            raise ValueError(f"package.json に version が見つかりません: {path}")
        return new_text

    _rewrite_text(path, rewrite)


def set_package_dependency(package_dir: str, dependency_id: str, version_range: str) -> bool:
    """vpmDependencies の既存エントリの範囲だけを書き換える。見つからなければ False。"""
    path = os.path.join(package_dir, "package.json")
    pattern = re.compile(r'("' + re.escape(dependency_id) + r'"\s*:\s*")([^"]*)(")')
    found = [False]

    def rewrite(text: str) -> str:
        block = re.search(r'"vpmDependencies"\s*:\s*\{[^}]*\}', text)
        if not block:
            return text
        new_block, count = pattern.subn(lambda m: m.group(1) + version_range + m.group(3), block.group(0), count=1)
        if count == 0:
            return text
        found[0] = True
        return text[:block.start()] + new_block + text[block.end():]

    _rewrite_text(path, rewrite)
    return found[0]


def minimum_of_range(version_range: str) -> Optional[str]:
    m = re.search(r">=\s*([0-9][0-9A-Za-z.+-]*)", version_range or "")
    if m:
        return m.group(1)
    m = re.match(r"^\s*\^?~?([0-9][0-9A-Za-z.+-]*)\s*$", version_range or "")
    return m.group(1) if m else None


# vpm.json --------------------------------------------------------------

def ensure_packages_root(data: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(data.get("packages"), dict):
        data["packages"] = {}
    return data["packages"]


def get_versions(data: Dict[str, Any], package_id: str) -> Dict[str, Any]:
    pkg = (data.get("packages") or {}).get(package_id) or {}
    return pkg.get("versions") or {}


def latest_listed_version(data: Dict[str, Any], package_id: str) -> Optional[str]:
    return latest_version(get_versions(data, package_id).keys())


def build_zip_url(github_repo: str, package_id: str, version: str) -> str:
    return f"https://github.com/{github_repo}/releases/download/{version}/{package_id}-{version}.zip"


def build_listing_entry(
    package_json: Dict[str, Any],
    zip_url: str,
    previous_entry: Optional[Dict[str, Any]] = None,
    zip_sha256: Optional[str] = None,
) -> Dict[str, Any]:
    """package.json を正としてリスティング用エントリを作る（url は zip、repo は前版から引き継ぐ）。"""
    entry = copy.deepcopy(package_json)
    entry["url"] = zip_url
    if previous_entry and "repo" in previous_entry and "repo" not in entry:
        entry["repo"] = previous_entry["repo"]
    entry.pop("zipSHA256", None)
    if zip_sha256:
        entry["zipSHA256"] = zip_sha256
    return entry


def upsert_version(data: Dict[str, Any], package_id: str, entry: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    version = entry.get("version")
    if not version:
        raise ValueError("エントリに version がありません。")
    if entry.get("name") != package_id:
        raise ValueError(f"エントリの name ({entry.get('name')}) が {package_id} と一致しません。")
    packages = ensure_packages_root(data)
    versions = packages.setdefault(package_id, {}).setdefault("versions", {})
    previous = versions.get(version)
    versions[version] = entry
    return previous


def remove_version(data: Dict[str, Any], package_id: str, version: str) -> bool:
    versions = get_versions(data, package_id)
    if version not in versions:
        return False
    del versions[version]
    return True


# 旧 CLI 互換 ------------------------------------------------------------

def parse_deps(dep_args) -> Dict[str, str]:
    """com.vrchat.avatars>=3.7.0 のような指定を {"com.vrchat.avatars": ">=3.7.0"} に変換する。"""
    deps: Dict[str, str] = {}
    for d in dep_args or []:
        d = d.strip()
        if not d:
            continue
        m = re.match(r"^([^<>=!^~\s]+)\s*(.*)$", d)
        deps[m.group(1)] = m.group(2).strip()
    return deps


def update_url_version(url: str, old_version: str, new_version: str) -> str:
    if not url or old_version == new_version:
        return url
    if old_version and old_version in url:
        return url.replace(old_version, new_version)
    return re.sub(r"\d+\.\d+\.\d+", new_version, url)


def normalize_zip_url(url: str, package_id: str, version: str) -> str:
    """GitHub Releases の zip ファイル名を package_id-version.zip に揃える。"""
    if not url:
        return url
    return re.sub(r"/[^/]+-\d+\.\d+\.\d+\.zip$", f"/{package_id}-{version}.zip", url)
