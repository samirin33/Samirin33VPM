import argparse
import copy
import json
import os
from typing import Dict, Any


def load_json(path: str) -> Dict[str, Any]:
    if not os.path.exists(path):
        raise SystemExit(f"vpm.json が見つかりません: {path}")
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_json(path: str, data: Dict[str, Any]) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write("\n")


def ensure_packages_root(data: Dict[str, Any]) -> Dict[str, Any]:
    if "packages" not in data or not isinstance(data["packages"], dict):
        data["packages"] = {}
    return data["packages"]


def parse_deps(dep_args):
    """
    --dep com.vrchat.avatars>=3.7.0 のような形式を
    {"com.vrchat.avatars": ">=3.7.0"} に変換する
    """
    deps = {}
    for d in dep_args or []:
        if ">=" in d or "<=" in d or "==" in d or "<" in d or ">" in d:
            # 最初の記号の位置で分割
            for op in [">=", "<=", "==", "!=", ">","<"]:
                idx = d.find(op)
                if idx > 0:
                    pkg = d[:idx].strip()
                    rng = d[idx:].strip()
                    deps[pkg] = rng
                    break
        else:
            # 範囲指定がなければそのまま
            deps[d.strip()] = ""
    return deps


def add_or_update_version(
    vpm_path: str,
    package_id: str,
    version: str,
    zip_url: str,
    display_name: str = None,
    description: str = None,
    license_name: str = None,
    repo_url: str = None,
    author_name: str = None,
    dep_args=None,
) -> None:
    data = load_json(vpm_path)
    packages = ensure_packages_root(data)

    if package_id not in packages:
        # 新規パッケージ
        versions: Dict[str, Any] = {}
        versions[version] = {
            "name": package_id,
            "displayName": display_name or package_id,
            "version": version,
            "description": description or "",
            "author": {"name": author_name} if author_name else {},
            "url": zip_url,
            "repo": repo_url or data.get("url", ""),
            "vpmDependencies": parse_deps(dep_args),
            "license": license_name or "",
        }
        packages[package_id] = {"versions": versions}
    else:
        # 既存パッケージ: 既存バージョンの最後をコピーして上書き
        pkg = packages[package_id]
        versions = pkg.setdefault("versions", {})
        if versions:
            # キーの末尾を「最新」とみなす（プレーンな 0.1.0 などを想定）
            latest_key = sorted(versions.keys())[-1]
            base = copy.deepcopy(versions[latest_key])
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

        base["name"] = package_id
        base["version"] = version
        base["url"] = zip_url

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

        versions[version] = base

    save_json(vpm_path, data)
    print(f"更新しました: {vpm_path}")
    print(f"  package: {package_id}")
    print(f"  version: {version}")


def main():
    parser = argparse.ArgumentParser(
        description="vpm.json に新しいバージョン情報を追加 / 更新するツール"
    )
    parser.add_argument(
        "--vpm-path",
        default="vpm.json",
        help="vpm.json へのパス（既定: vpm.json）",
    )
    parser.add_argument(
        "--package-id",
        required=True,
        help="パッケージ ID（例: com.github.samirin33.samirin-vrc-utility.avatars）",
    )
    parser.add_argument(
        "--version",
        required=True,
        help="追加するバージョン（例: 0.1.2）",
    )
    parser.add_argument(
        "--zip-url",
        required=True,
        help="GitHub Releases の zip の URL",
    )
    parser.add_argument(
        "--display-name",
        help="displayName を明示的に指定（省略時は既存値 or package-id）",
    )
    parser.add_argument(
        "--description",
        help="説明文（省略時は既存値を維持）",
    )
    parser.add_argument(
        "--license",
        dest="license_name",
        help="ライセンス名（例: MIT）",
    )
    parser.add_argument(
        "--repo-url",
        help="repo フィールドに入れる URL（既定: vpm.json の url）",
    )
    parser.add_argument(
        "--author-name",
        help="author.name に入れる名前",
    )
    parser.add_argument(
        "--dep",
        action="append",
        help="依存パッケージ指定。例: --dep com.vrchat.avatars>=3.7.0 （複数指定可）",
    )

    args = parser.parse_args()

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

