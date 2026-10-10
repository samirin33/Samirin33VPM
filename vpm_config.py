"""ツール設定（ローカルのパスを含むため vpm_tool_config.json は git 管理外）。"""

import copy
import json
import os
import sys
from typing import Any, Dict, List

CONFIG_FILE_NAME = "vpm_tool_config.json"

_DEV_PACKAGES = r"D:\UnityProjects\VRChatProjects\Samirin_Gimmick_Dev_V2\Packages"
_REPO_ROOT = r"D:\UnityProjects\VRChatProjects\SamirinVRCUtility"


def app_dir() -> str:
    """スクリプト直下。PyInstaller の exe は dist/ に置かれるので、その親を返す。"""
    if getattr(sys, "frozen", False):
        base = os.path.dirname(os.path.abspath(sys.executable))
        if os.path.basename(base).lower() == "dist":
            base = os.path.dirname(base)
        return base
    return os.path.dirname(os.path.abspath(__file__))


def default_config() -> Dict[str, Any]:
    root = app_dir()
    return {
        "vpm_json": os.path.join(root, "vpm.json"),
        "vpm_repo_dir": root,
        "packages": [
            {
                "id": "com.github.samirin33.samivrcblocks-avatar",
                "source_dir": os.path.join(_DEV_PACKAGES, "com.github.samirin33.samivrcblocks-avatar"),
                "repo_dir": os.path.join(_REPO_ROOT, "SamirinVRCUtility-Avatars"),
                "package_path": "Packages/com.github.samirin33.samivrcblocks-avatar",
                "github_repo": "samirin33/SamiVRCBlocks-Avatar",
                "workflow": "release.yml",
                "branch": "main",
                "exclude": ["Tests", "Tests.meta"],
            },
            {
                "id": "com.github.samirin33.samivrcblocks-avatar-editor",
                "source_dir": os.path.join(_DEV_PACKAGES, "com.github.samirin33.samivrcblocks-avatar-editor"),
                "repo_dir": os.path.join(_REPO_ROOT, "SamirinVRCUtility-AvatarEditor"),
                "package_path": "Packages/com.github.samirin33.samivrcblocks-avatar-editor",
                "github_repo": "samirin33/SamiVRCBlocks-AvatarEditor",
                "workflow": "release.yml",
                "branch": "main",
                "exclude": ["Tests", "Tests.meta"],
            },
        ],
    }


PACKAGE_FIELDS = ["id", "source_dir", "repo_dir", "package_path", "github_repo", "workflow", "branch", "exclude"]


def config_path() -> str:
    return os.path.join(app_dir(), CONFIG_FILE_NAME)


def load_config() -> Dict[str, Any]:
    path = config_path()
    if not os.path.exists(path):
        cfg = default_config()
        save_config(cfg)
        return cfg

    with open(path, "r", encoding="utf-8-sig") as f:
        cfg = json.load(f)

    defaults = default_config()
    for key in ("vpm_json", "vpm_repo_dir"):
        cfg.setdefault(key, defaults[key])
    packages: List[Dict[str, Any]] = cfg.setdefault("packages", [])
    template = defaults["packages"][0]
    for pkg in packages:
        pkg.setdefault("workflow", template["workflow"])
        pkg.setdefault("branch", template["branch"])
        pkg.setdefault("exclude", [])
    return cfg


def save_config(cfg: Dict[str, Any]) -> None:
    path = config_path()
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)
        f.write("\n")
    os.replace(tmp, path)


def target_package_dir(pkg: Dict[str, Any]) -> str:
    return os.path.normpath(os.path.join(pkg["repo_dir"], pkg["package_path"]))


def clone_config(cfg: Dict[str, Any]) -> Dict[str, Any]:
    return copy.deepcopy(cfg)
