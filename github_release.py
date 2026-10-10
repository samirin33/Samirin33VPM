"""GitHub Releases の確認。API のレート制限を避けるため、公開ダウンロード URL を直接見る。"""

import hashlib
import io
import json
import urllib.error
import urllib.request
import zipfile
from dataclasses import dataclass
from typing import Optional

_USER_AGENT = "Samirin33VPM-Tool"


def _request(url: str, method: str = "GET", timeout: float = 30):
    req = urllib.request.Request(url, method=method, headers={"User-Agent": _USER_AGENT})
    return urllib.request.urlopen(req, timeout=timeout)


def asset_exists(url: str) -> bool:
    try:
        with _request(url, method="HEAD", timeout=20) as res:
            return 200 <= res.status < 400
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return False
        raise


@dataclass
class VerifiedZip:
    sha256: str
    size: int
    name: str
    version: str


def download_and_verify(url: str, expected_name: str, expected_version: str) -> VerifiedZip:
    with _request(url, timeout=120) as res:
        data = res.read()
    sha256 = hashlib.sha256(data).hexdigest()
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        names = zf.namelist()
        manifest_name = "package.json" if "package.json" in names else next(
            (n for n in names if n.endswith("/package.json") and n.count("/") == 1), None)
        if manifest_name is None:
            raise ValueError("zip 直下に package.json がありません。")
        manifest = json.loads(zf.read(manifest_name).decode("utf-8-sig"))
    name, version = manifest.get("name", ""), manifest.get("version", "")
    if name != expected_name:
        raise ValueError(f"zip 内の name が {name} です（期待値 {expected_name}）。")
    if version != expected_version:
        raise ValueError(f"zip 内の version が {version} です（期待値 {expected_version}）。")
    return VerifiedZip(sha256=sha256, size=len(data), name=name, version=version)


def releases_page(github_repo: str, tag: Optional[str] = None) -> str:
    base = f"https://github.com/{github_repo}/releases"
    return f"{base}/tag/{tag}" if tag else base


def workflow_page(github_repo: str, workflow: str) -> str:
    return f"https://github.com/{github_repo}/actions/workflows/{workflow}"
