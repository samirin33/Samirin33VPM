import argparse
import copy
import os
from typing import Any, Dict

from vpm_config import app_dir
from vpm_core import (
    build_listing_entry,
    build_zip_url,
    ensure_packages_root,
    get_versions,
    latest_version,
    load_json,
    normalize_zip_url,
    parse_deps,
    read_package_json,
    save_json,
    update_url_version,
    upsert_version,
)

DEFAULT_VPM_PATH = os.path.join(app_dir(), "vpm.json")


def add_or_update_version(
    vpm_path: str,
    package_id: str,
    version: str,
    zip_url: str = None,
    display_name: str = None,
    description: str = None,
    license_name: str = None,
    repo_url: str = None,
    author_name: str = None,
    dep_args=None,
) -> None:
    """最新版のエントリを複製して、指定した項目だけ上書きする（手入力用）。"""
    try:
        data = load_json(vpm_path)
    except FileNotFoundError as e:
        raise SystemExit(str(e))
    packages = ensure_packages_root(data)

    if package_id not in packages:
        if not zip_url:
            raise SystemExit("新規パッケージ追加時は --zip-url が必要です。")
        packages[package_id] = {"versions": {}}
        base: Dict[str, Any] = {
            "name": package_id,
            "displayName": display_name or package_id,
            "description": description or "",
            "author": {"name": author_name} if author_name else {},
            "repo": repo_url or data.get("url", ""),
            "vpmDependencies": parse_deps(dep_args),
            "license": license_name or "",
        }
        old_version, old_url = "", ""
    else:
        versions = get_versions(data, package_id)
        latest_key = latest_version(versions.keys())
        if latest_key:
            base = copy.deepcopy(versions[latest_key])
            old_version = str(base.get("version", latest_key))
            old_url = str(base.get("url", ""))
        else:
            base = {
                "name": package_id,
                "displayName": display_name or package_id,
                "description": description or "",
                "author": {"name": author_name} if author_name else {},
                "repo": repo_url or data.get("url", ""),
                "vpmDependencies": {},
                "license": license_name or "",
            }
            old_version, old_url = "", ""

    base["name"] = package_id
    base["version"] = version
    base["url"] = normalize_zip_url(
        update_url_version(zip_url or old_url, old_version, version), package_id, version
    )
    base.pop("zipSHA256", None)

    if display_name is not None:
        base["displayName"] = display_name
    if description is not None:
        base["description"] = description
    if license_name is not None:
        base["license"] = license_name
    if repo_url is not None:
        base["repo"] = repo_url
    if author_name is not None:
        base["author"] = {"name": author_name}
    if dep_args is not None:
        base["vpmDependencies"] = parse_deps(dep_args)

    upsert_version(data, package_id, base)
    save_json(vpm_path, data)
    print(f"更新しました: {vpm_path}")
    print(f"  package: {package_id}")
    print(f"  version: {version}")


def update_from_package_json(vpm_path: str, package_dir: str, github_repo: str) -> None:
    """package.json の内容をそのままリスティングに登録する。"""
    package_json = read_package_json(package_dir)
    package_id = package_json["name"]
    version = package_json["version"]
    data = load_json(vpm_path)
    versions = get_versions(data, package_id)
    previous_key = latest_version(versions.keys())
    entry = build_listing_entry(
        package_json,
        build_zip_url(github_repo, package_id, version),
        versions.get(previous_key) if previous_key else None,
    )
    upsert_version(data, package_id, entry)
    save_json(vpm_path, data)
    print(f"更新しました: {vpm_path}")
    print(f"  package: {package_id}")
    print(f"  version: {version}")


def main():
    parser = argparse.ArgumentParser(description="vpm.json に新しいバージョン情報を追加 / 更新するツール")
    parser.add_argument("--vpm-path", default=DEFAULT_VPM_PATH, help="vpm.json へのパス（既定: このスクリプトと同じフォルダの vpm.json）")
    parser.add_argument("--from-package-json", metavar="PACKAGE_DIR", help="package.json のあるフォルダ。指定すると内容をそのまま登録する")
    parser.add_argument("--github-repo", help="--from-package-json 用。owner/name（例: samirin33/SamiVRCBlocks-Avatar）")
    parser.add_argument("--package-id", help="パッケージ ID（例: com.github.samirin33.samivrcblocks-avatar）")
    parser.add_argument("--version", help="追加するバージョン（例: 0.1.2）")
    parser.add_argument("--zip-url", help="GitHub Releases の zip の URL（既存パッケージ更新時は省略可）")
    parser.add_argument("--display-name", help="displayName を明示的に指定（省略時は既存値 or package-id）")
    parser.add_argument("--description", help="説明文（省略時は既存値を維持）")
    parser.add_argument("--license", dest="license_name", help="ライセンス名（例: MIT）")
    parser.add_argument("--repo-url", help="repo フィールドに入れる URL（既定: vpm.json の url）")
    parser.add_argument("--author-name", help="author.name に入れる名前")
    parser.add_argument("--dep", action="append", help="依存パッケージ指定。例: --dep com.vrchat.avatars>=3.7.0 （複数指定可）")

    args = parser.parse_args()

    if args.from_package_json:
        if not args.github_repo:
            parser.error("--from-package-json には --github-repo が必要です。")
        update_from_package_json(args.vpm_path, args.from_package_json, args.github_repo)
        return

    if not args.package_id or not args.version:
        parser.error("--package-id と --version は必須です（--from-package-json を使わない場合）。")

    add_or_update_version(
        vpm_path=args.vpm_path,
        package_id=args.package_id,
        version=args.version,
        zip_url=args.zip_url,
        display_name=args.display_name,
        description=args.description,
        license_name=args.license_name,
        repo_url=args.repo_url,
        author_name=args.author_name,
        dep_args=args.dep,
    )


if __name__ == "__main__":
    main()
