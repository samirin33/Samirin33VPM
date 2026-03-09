import json
import os
import subprocess
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

from update_vpm import add_or_update_version


class VpmGui(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Samirin VPM Editor")
        self.geometry("780x520")

        self.vpm_path_var = tk.StringVar(value="vpm.json")
        self.package_id_var = tk.StringVar()
        self.version_var = tk.StringVar()
        self.zip_url_var = tk.StringVar()
        self.display_name_var = tk.StringVar()
        self.author_name_var = tk.StringVar()
        self.license_var = tk.StringVar(value="MIT")
        self.repo_url_var = tk.StringVar()
        self.deps_var = tk.StringVar()  # カンマ区切りで指定

        # Git 用
        self.git_message_var = tk.StringVar()
        self.git_push_var = tk.BooleanVar(value=True)

        self.package_combo = None
        self.desc_text = None

        self._build_ui()

    # UI 構築 ------------------------------------------------------------
    def _build_ui(self):
        pad = {"padx": 6, "pady": 4}

        # vpm.json
        row = 0
        tk.Label(self, text="vpm.json パス:").grid(row=row, column=0, sticky="e", **pad)
        tk.Entry(self, textvariable=self.vpm_path_var, width=50).grid(
            row=row, column=1, sticky="we", **pad
        )
        tk.Button(self, text="参照...", command=self.browse_vpm).grid(
            row=row, column=2, sticky="w", **pad
        )

        # パッケージ ID
        row += 1
        tk.Label(self, text="Package ID:").grid(row=row, column=0, sticky="e", **pad)
        self.package_combo = ttk.Combobox(
            self, textvariable=self.package_id_var, width=50
        )
        self.package_combo.grid(row=row, column=1, columnspan=2, sticky="we", **pad)
        self.package_combo.bind("<<ComboboxSelected>>", self.on_package_changed)
        tk.Button(self, text="vpm.json から読み込み", command=self.load_packages).grid(
            row=row, column=3, sticky="w", **pad
        )

        # バージョン / Zip URL
        row += 1
        tk.Label(self, text="Version:").grid(row=row, column=0, sticky="e", **pad)
        tk.Entry(self, textvariable=self.version_var, width=20).grid(
            row=row, column=1, sticky="w", **pad
        )
        tk.Button(self, text="前バージョン読込", command=self.load_previous_version).grid(
            row=row, column=2, sticky="w", **pad
        )

        row += 1
        tk.Label(self, text="Zip URL:").grid(row=row, column=0, sticky="e", **pad)
        tk.Entry(self, textvariable=self.zip_url_var, width=70).grid(
            row=row, column=1, columnspan=3, sticky="we", **pad
        )

        # 任意フィールド
        row += 1
        tk.Label(self, text="Display Name:").grid(row=row, column=0, sticky="e", **pad)
        tk.Entry(self, textvariable=self.display_name_var, width=40).grid(
            row=row, column=1, columnspan=3, sticky="we", **pad
        )

        row += 1
        tk.Label(self, text="Author Name:").grid(row=row, column=0, sticky="e", **pad)
        tk.Entry(self, textvariable=self.author_name_var, width=40).grid(
            row=row, column=1, columnspan=3, sticky="we", **pad
        )

        row += 1
        tk.Label(self, text="License:").grid(row=row, column=0, sticky="e", **pad)
        tk.Entry(self, textvariable=self.license_var, width=20).grid(
            row=row, column=1, sticky="w", **pad
        )

        row += 1
        tk.Label(self, text="Repo URL:").grid(row=row, column=0, sticky="e", **pad)
        tk.Entry(self, textvariable=self.repo_url_var, width=70).grid(
            row=row, column=1, columnspan=3, sticky="we", **pad
        )

        row += 1
        tk.Label(self, text="Dependencies\n(, 区切り):").grid(
            row=row, column=0, sticky="e", **pad
        )
        tk.Entry(self, textvariable=self.deps_var, width=70).grid(
            row=row, column=1, columnspan=3, sticky="we", **pad
        )
        tk.Label(self, text="例: com.vrchat.avatars>=3.7.0, nadena.dev.ndmf>=1.6.0").grid(
            row=row + 1, column=1, columnspan=3, sticky="w", **pad
        )

        # 説明
        row += 2
        tk.Label(self, text="Description:").grid(row=row, column=0, sticky="ne", **pad)
        self.desc_text = tk.Text(self, height=4, width=70)
        self.desc_text.grid(row=row, column=1, columnspan=3, sticky="we", **pad)

        # 実行ボタン
        row += 1
        tk.Button(self, text="vpm.json を更新", command=self.run_update, width=20).grid(
            row=row, column=1, sticky="e", **pad
        )

        # Git 操作用 UI
        row += 1
        tk.Label(self, text="Git コミットメッセージ:").grid(
            row=row, column=0, sticky="e", **pad
        )
        tk.Entry(self, textvariable=self.git_message_var, width=50).grid(
            row=row, column=1, columnspan=2, sticky="we", **pad
        )
        tk.Checkbutton(
            self,
            text="commit 後に push も実行する",
            variable=self.git_push_var,
        ).grid(row=row, column=3, sticky="w", **pad)

        row += 1
        tk.Button(
            self, text="git commit / push 実行", command=self.run_git, width=24
        ).grid(row=row, column=1, sticky="w", **pad)

        self.columnconfigure(1, weight=1)
        self.columnconfigure(2, weight=0)

    # イベントハンドラ ----------------------------------------------------
    def browse_vpm(self):
        path = filedialog.askopenfilename(
            title="vpm.json を選択",
            filetypes=[("JSON files", "*.json"), ("All files", "*.*")],
        )
        if path:
            self.vpm_path_var.set(path)

    def load_packages(self):
        path = self.vpm_path_var.get().strip()
        if not path:
            messagebox.showerror("エラー", "vpm.json のパスを入力してください。")
            return
        if not os.path.exists(path):
            messagebox.showerror("エラー", f"ファイルが見つかりません:\n{path}")
            return
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception as e:
            messagebox.showerror("エラー", f"JSON の読み込みに失敗しました:\n{e}")
            return

        pkgs = list((data.get("packages") or {}).keys())
        if not pkgs:
            messagebox.showinfo("情報", "packages セクションが見つかりません。")
            return

        self.package_combo["values"] = pkgs
        # 既に値が空なら先頭をセット
        if not self.package_id_var.get() and pkgs:
            self.package_id_var.set(pkgs[0])

        if not self.repo_url_var.get():
            repo = data.get("url")
            if repo:
                self.repo_url_var.set(repo)

        # パッケージ ID 読み込み成功時に、前バージョンの情報も自動読み込み
        if self.package_id_var.get():
            self.load_previous_version(show_popup=False)

    def load_previous_version(self, show_popup: bool = True):
        """選択中の Package ID について、最新バージョンの情報をフォームに読み込む"""
        vpm_path = self.vpm_path_var.get().strip()
        package_id = self.package_id_var.get().strip()

        if not vpm_path or not package_id:
            messagebox.showerror(
                "エラー", "vpm.json パスと Package ID を先に指定してください。"
            )
            return
        if not os.path.exists(vpm_path):
            messagebox.showerror("エラー", f"ファイルが見つかりません:\n{vpm_path}")
            return

        try:
            with open(vpm_path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception as e:
            messagebox.showerror("エラー", f"JSON の読み込みに失敗しました:\n{e}")
            return

        pkg_info = (data.get("packages") or {}).get(package_id)
        if not pkg_info:
            if show_popup:
                messagebox.showerror("エラー", f"packages 内に {package_id} が見つかりません。")
            return

        versions = pkg_info.get("versions") or {}
        if not versions:
            if show_popup:
                messagebox.showinfo("情報", "versions がまだ登録されていません。")
            return

        latest_key = sorted(versions.keys())[-1]
        v = versions[latest_key]

        # フィールドへ反映
        self.version_var.set(latest_key)
        self.display_name_var.set(v.get("displayName", ""))
        self.author_name_var.set((v.get("author") or {}).get("name", ""))
        self.license_var.set(v.get("license", ""))
        self.repo_url_var.set(v.get("repo") or data.get("url", ""))
        self.zip_url_var.set(v.get("url", ""))

        # 依存関係
        deps = v.get("vpmDependencies") or {}
        dep_strs = []
        for k, rng in deps.items():
            dep_strs.append(f"{k}{rng}" if rng else k)
        self.deps_var.set(", ".join(dep_strs))

        # 説明
        self.desc_text.delete("1.0", "end")
        if v.get("description"):
            self.desc_text.insert("1.0", v["description"])

        if show_popup:
            messagebox.showinfo("完了", f"最新バージョン {latest_key} の情報を読み込みました。")

    def on_package_changed(self, event=None):
        """パッケージ選択変更時に自動で最新バージョン情報を反映"""
        if self.package_id_var.get().strip():
            self.load_previous_version(show_popup=False)

    def run_update(self):
        vpm_path = self.vpm_path_var.get().strip()
        package_id = self.package_id_var.get().strip()
        version = self.version_var.get().strip()
        zip_url = self.zip_url_var.get().strip()

        if not vpm_path or not package_id or not version or not zip_url:
            messagebox.showerror(
                "エラー", "vpm.json パス, Package ID, Version, Zip URL は必須です。"
            )
            return

        deps = None
        deps_str = self.deps_var.get().strip()
        if deps_str:
            deps = [d.strip() for d in deps_str.split(",") if d.strip()]

        try:
            add_or_update_version(
                vpm_path=vpm_path,
                package_id=package_id,
                version=version,
                zip_url=zip_url,
                display_name=self.display_name_var.get().strip() or None,
                description=self.desc_text.get("1.0", "end").strip() or None,
                license_name=self.license_var.get().strip() or None,
                repo_url=self.repo_url_var.get().strip() or None,
                author_name=self.author_name_var.get().strip() or None,
                dep_args=deps,
            )
        except SystemExit as e:
            messagebox.showerror("エラー", str(e))
            return
        except Exception as e:
            messagebox.showerror("エラー", f"更新中にエラーが発生しました:\n{e}")
            return

        # デフォルトのコミットメッセージをセット（未入力の場合）
        if not self.git_message_var.get().strip():
            self.git_message_var.set(f"Update vpm.json: {package_id} {version}")

        messagebox.showinfo("完了", f"vpm.json を更新しました。\n{os.path.abspath(vpm_path)}")

    def run_git(self):
        """git add / commit / push を実行"""
        msg = self.git_message_var.get().strip()
        if not msg:
            messagebox.showerror("エラー", "コミットメッセージを入力してください。")
            return

        vpm_path = self.vpm_path_var.get().strip() or "vpm.json"
        rel_vpm = os.path.relpath(vpm_path, os.getcwd())

        try:
            # vpm.json だけステージング
            subprocess.run(["git", "add", rel_vpm], check=True)
            subprocess.run(["git", "commit", "-m", msg], check=True)
            pushed = False
            if self.git_push_var.get():
                subprocess.run(["git", "push"], check=True)
                pushed = True
        except subprocess.CalledProcessError as e:
            messagebox.showerror("エラー", f"git コマンドでエラーが発生しました:\n{e}")
            return

        text = "git commit を実行しました。"
        if self.git_push_var.get():
            text += "\nその後 git push も実行しました。"
        messagebox.showinfo("完了", text)


if __name__ == "__main__":
    app = VpmGui()
    app.mainloop()

