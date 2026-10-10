import json
import os
import queue
import threading
import time
import traceback
import webbrowser
import tkinter as tk
from dataclasses import dataclass, field
from tkinter import ttk, filedialog, messagebox
from tkinter.scrolledtext import ScrolledText
from typing import Any, Callable, Dict, List, Optional

import git_ops
import github_release
import package_sync
import vpm_config
import vpm_core

RELEASE_WAIT_SECONDS = 20 * 60
RELEASE_POLL_SECONDS = 15


class TaskCancelled(Exception):
    pass


@dataclass
class PackageState:
    cfg: Dict[str, Any]
    source_json: Optional[Dict[str, Any]] = None
    target_json: Optional[Dict[str, Any]] = None
    listed_version: Optional[str] = None
    plan: Optional[package_sync.SyncPlan] = None
    repo: Optional[git_ops.RepoStatus] = None
    errors: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)

    @property
    def id(self) -> str:
        return self.cfg["id"]

    @property
    def source_version(self) -> str:
        return (self.source_json or {}).get("version", "")

    @property
    def target_version(self) -> str:
        return (self.target_json or {}).get("version", "")

    @property
    def target_dir(self) -> str:
        return vpm_config.target_package_dir(self.cfg)


def collect_state(pkg: Dict[str, Any], vpm_data: Optional[Dict[str, Any]]) -> PackageState:
    st = PackageState(cfg=pkg)
    try:
        st.source_json = vpm_core.read_package_json(pkg["source_dir"])
    except Exception as e:
        st.errors.append(f"開発側 package.json: {e}")
    try:
        st.target_json = vpm_core.read_package_json(st.target_dir)
    except Exception as e:
        st.errors.append(f"コピー先 package.json: {e}")
    if vpm_data is not None:
        st.listed_version = vpm_core.latest_listed_version(vpm_data, pkg["id"])
    if os.path.isdir(pkg["source_dir"]):
        try:
            st.plan = package_sync.compute_plan(pkg["source_dir"], st.target_dir, pkg.get("exclude") or [])
        except Exception as e:
            st.errors.append(f"差分: {e}")
    st.repo = git_ops.repo_status(pkg["repo_dir"], pkg["package_path"])

    sv, tv, lv = st.source_version, st.target_version, st.listed_version
    if sv and tv and vpm_core.compare_versions(sv, tv) < 0:
        st.warnings.append(f"開発版 {sv} がコピー先 {tv} より古い")
    if sv and lv and vpm_core.compare_versions(sv, lv) <= 0 and st.plan and st.plan.change_count:
        st.warnings.append("差分があるのにバージョンが公開済み")
    if st.source_json and st.source_json.get("name") != pkg["id"]:
        st.warnings.append("package.json の name が設定の ID と違う")
    return st


class VpmManager(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Samirin33 VPM Manager")
        self.geometry("1280x900")
        self.minsize(1000, 720)
        try:
            ttk.Style(self).theme_use("vista")
        except tk.TclError:
            pass
        ttk.Style(self).configure("Treeview", rowheight=22)

        self.cfg = vpm_config.load_config()
        self.states: Dict[str, PackageState] = {}
        self.vpm_data: Optional[Dict[str, Any]] = None
        self.vpm_status: Optional[git_ops.RepoStatus] = None
        self.gh_ok = False
        self.selected_id: Optional[str] = None
        self._shown_id: Optional[str] = None

        self.ui_queue: "queue.Queue[Callable[[], None]]" = queue.Queue()
        self.busy = False
        self.cancel_event = threading.Event()
        self.action_widgets: List[tk.Widget] = []

        self._build_ui()
        self.after(100, self._drain_queue)
        self.after(150, self.refresh_all)

    # スレッド補助 ----------------------------------------------------------
    def post(self, fn: Callable, *args) -> None:
        self.ui_queue.put(lambda: fn(*args))

    def _drain_queue(self) -> None:
        try:
            while True:
                self.ui_queue.get_nowait()()
        except queue.Empty:
            pass
        self.after(100, self._drain_queue)

    def ask_main(self, fn: Callable[[], Any]) -> Any:
        """ワーカースレッドからダイアログを出して結果を待つ。"""
        if threading.current_thread() is threading.main_thread():
            return fn()
        done = threading.Event()
        box: Dict[str, Any] = {}

        def wrapper():
            try:
                box["value"] = fn()
            finally:
                done.set()

        self.post(wrapper)
        done.wait()
        return box.get("value")

    def log(self, text: str) -> None:
        stamp = time.strftime("%H:%M:%S")
        self.post(self._append_log, f"[{stamp}] {text}")

    def _append_log(self, text: str) -> None:
        self.log_text.configure(state="normal")
        self.log_text.insert("end", text + "\n")
        self.log_text.see("end")
        self.log_text.configure(state="disabled")

    def run_task(self, title: str, work: Callable[[], Any], on_done: Optional[Callable[[Any], None]] = None,
                 refresh_after: bool = True) -> None:
        if self.busy:
            messagebox.showinfo("実行中", "別の処理が実行中です。終わるまでお待ちください。", parent=self)
            return
        self.busy = True
        self.cancel_event.clear()
        self._set_actions_enabled(False)
        self.status_var.set(f"{title} ...")
        self.log(f"--- {title} ---")

        def target():
            result, failed = None, False
            try:
                result = work()
            except TaskCancelled:
                failed = True
                self.log("中止しました。")
            except Exception as e:
                failed = True
                self.log(traceback.format_exc().rstrip())
                self.post(messagebox.showerror, "エラー", f"{title} に失敗しました。\n\n{e}")
            self.post(self._finish_task, title, result, failed, on_done, refresh_after)

        threading.Thread(target=target, daemon=True).start()

    def _finish_task(self, title, result, failed, on_done, refresh_after) -> None:
        self.busy = False
        self._set_actions_enabled(True)
        self.status_var.set(f"{title}: {'失敗/中止' if failed else '完了'}")
        if not failed and on_done:
            on_done(result)
        if refresh_after:
            self.refresh_all()

    def check_cancel(self) -> None:
        if self.cancel_event.is_set():
            raise TaskCancelled()

    def _set_actions_enabled(self, enabled: bool) -> None:
        for w in self.action_widgets:
            try:
                w.configure(state="normal" if enabled else "disabled")
            except tk.TclError:
                pass
        self.cancel_button.configure(state="disabled" if enabled else "normal")

    def _action(self, widget: tk.Widget) -> tk.Widget:
        self.action_widgets.append(widget)
        return widget

    # UI 構築 ---------------------------------------------------------------
    def _build_ui(self) -> None:
        pad = {"padx": 6, "pady": 4}

        top = ttk.Frame(self)
        top.pack(fill="x", **pad)
        ttk.Label(top, text="vpm.json:").pack(side="left")
        self.vpm_path_var = tk.StringVar(value=self.cfg["vpm_json"])
        ttk.Entry(top, textvariable=self.vpm_path_var, state="readonly", width=70).pack(side="left", fill="x", expand=True, padx=4)
        self._action(ttk.Button(top, text="設定...", command=self.open_settings)).pack(side="left", padx=2)
        self._action(ttk.Button(top, text="すべて再読込", command=self.refresh_all)).pack(side="left", padx=2)

        pkg_frame = ttk.LabelFrame(self, text="パッケージ")
        pkg_frame.pack(fill="x", **pad)
        columns = ("source", "target", "listed", "diff", "repo", "note")
        self.pkg_tree = ttk.Treeview(pkg_frame, columns=columns, height=3, selectmode="browse")
        self.pkg_tree.heading("#0", text="パッケージ ID")
        self.pkg_tree.column("#0", width=340, stretch=False)
        for col, text, width in [
            ("source", "開発版", 60), ("target", "コピー先", 64), ("listed", "vpm.json", 64),
            ("diff", "コピー差分", 210), ("repo", "コピー先リポジトリ", 140), ("note", "注意", 240),
        ]:
            self.pkg_tree.heading(col, text=text)
            self.pkg_tree.column(col, width=width, stretch=col == "note")
        self.pkg_tree.tag_configure("warn", background="#fff4ce")
        self.pkg_tree.tag_configure("error", background="#fde7e9")
        self.pkg_tree.bind("<<TreeviewSelect>>", self.on_package_selected)

        side = ttk.Frame(pkg_frame)
        side.pack(side="right", fill="y", padx=4, pady=4)
        self._action(ttk.Button(side, text="まとめてリリース...", command=self.release_pipeline)).pack(fill="x", pady=2)
        ttk.Label(side, text="コピー → コミット → Actions\n→ vpm.json 更新 を順に実行", foreground="#666").pack()
        self.pkg_tree.pack(side="top", fill="x", expand=True, padx=4, pady=(4, 0))
        self.notes_var = tk.StringVar()
        ttk.Label(pkg_frame, textvariable=self.notes_var, foreground="#9d5d00").pack(side="top", anchor="w", padx=6, pady=(2, 4))

        paned = ttk.PanedWindow(self, orient="vertical")
        paned.pack(fill="both", expand=True, **pad)

        self.notebook = ttk.Notebook(paned)
        paned.add(self.notebook, weight=3)
        self._build_sync_tab()
        self._build_release_tab()
        self._build_vpm_tab()

        log_frame = ttk.LabelFrame(paned, text="ログ")
        paned.add(log_frame, weight=0)
        self.log_text = ScrolledText(log_frame, height=5, state="disabled", font=("Consolas", 9))
        self.log_text.pack(fill="both", expand=True, padx=4, pady=4)

        bottom = ttk.Frame(self)
        bottom.pack(fill="x", padx=6, pady=(0, 6))
        self.status_var = tk.StringVar(value="準備完了")
        ttk.Label(bottom, textvariable=self.status_var).pack(side="left")
        self.cancel_button = ttk.Button(bottom, text="中止", command=self.cancel_event.set, state="disabled")
        self.cancel_button.pack(side="right")

    def _build_sync_tab(self) -> None:
        tab = ttk.Frame(self.notebook)
        self.notebook.add(tab, text="① バージョン・コピー")
        tab.columnconfigure(1, weight=1)
        pad = {"padx": 6, "pady": 3}

        self.source_dir_var = tk.StringVar()
        self.target_dir_var = tk.StringVar()
        for row, (label, var, opener) in enumerate([
            ("コピー元:", self.source_dir_var, lambda: self.open_folder(self.source_dir_var.get())),
            ("コピー先:", self.target_dir_var, lambda: self.open_folder(self.target_dir_var.get())),
        ]):
            ttk.Label(tab, text=label).grid(row=row, column=0, sticky="e", **pad)
            ttk.Entry(tab, textvariable=var, state="readonly").grid(row=row, column=1, sticky="we", **pad)
            ttk.Button(tab, text="開く", command=opener).grid(row=row, column=2, sticky="w", **pad)

        ttk.Label(tab, text="除外:").grid(row=2, column=0, sticky="e", **pad)
        exclude_row = ttk.Frame(tab)
        exclude_row.grid(row=2, column=1, sticky="we", **pad)
        self.exclude_var = tk.StringVar()
        ttk.Entry(exclude_row, textvariable=self.exclude_var, width=30).pack(side="left")
        ttk.Label(
            exclude_row, foreground="#666",
            text="カンマ区切り。名前 or パッケージ直下からのパス。除外分はコピー先からも削除",
        ).pack(side="left", padx=6)
        self._action(ttk.Button(tab, text="保存", command=self.save_excludes)).grid(row=2, column=2, sticky="w", **pad)

        middle = ttk.Frame(tab)
        middle.grid(row=4, column=0, columnspan=3, sticky="we", **pad)
        middle.columnconfigure(1, weight=1)

        ver = ttk.LabelFrame(middle, text="バージョン")
        ver.grid(row=0, column=0, sticky="nsw", padx=(0, 6))
        self.version_info_var = tk.StringVar()
        ttk.Label(ver, textvariable=self.version_info_var).grid(row=0, column=0, columnspan=5, sticky="w", padx=6, pady=(4, 2))
        ttk.Label(ver, text="新バージョン:").grid(row=1, column=0, sticky="e", padx=(6, 2))
        self.new_version_var = tk.StringVar()
        ttk.Entry(ver, textvariable=self.new_version_var, width=10).grid(row=1, column=1, padx=2)
        for col, (text, part) in enumerate([("メジャー+1", "major"), ("マイナー+1", "minor"), ("パッチ+1", "patch")], start=2):
            ttk.Button(ver, text=text, width=9, command=lambda p=part: self.suggest_bump(p)).grid(row=1, column=col, padx=1)
        self._action(ttk.Button(ver, text="開発側 package.json に書き込む", command=self.write_source_version)).grid(
            row=2, column=0, columnspan=5, sticky="we", padx=6, pady=4)

        deps = ttk.LabelFrame(middle, text="依存パッケージ（開発側 package.json）")
        deps.grid(row=0, column=1, sticky="nsew")
        self.deps_var = tk.StringVar()
        ttk.Label(deps, textvariable=self.deps_var, justify="left").pack(side="top", anchor="w", padx=6, pady=(4, 2))
        self._action(ttk.Button(deps, text="管理中パッケージへの依存を開発版に揃える", command=self.align_managed_dependencies)).pack(
            side="bottom", anchor="e", padx=6, pady=4)

        diff_frame = ttk.LabelFrame(tab, text="コピー差分（開発側 → コピー先）")
        diff_frame.grid(row=6, column=0, columnspan=3, sticky="nsew", **pad)
        tab.rowconfigure(6, weight=1)
        self.diff_tree = ttk.Treeview(diff_frame, columns=("kind",), height=8)
        self.diff_tree.heading("#0", text="パス")
        self.diff_tree.heading("kind", text="種類")
        self.diff_tree.column("kind", width=80, stretch=False)
        self.diff_tree.tag_configure("added", foreground="#107c10")
        self.diff_tree.tag_configure("modified", foreground="#0063b1")
        self.diff_tree.tag_configure("deleted", foreground="#c50f1f")
        sb = ttk.Scrollbar(diff_frame, orient="vertical", command=self.diff_tree.yview)
        self.diff_tree.configure(yscrollcommand=sb.set)
        self.diff_tree.pack(side="left", fill="both", expand=True, padx=(4, 0), pady=4)
        sb.pack(side="left", fill="y", pady=4)

        actions = ttk.Frame(tab)
        actions.grid(row=7, column=0, columnspan=3, sticky="we", **pad)
        self.diff_summary_var = tk.StringVar()
        ttk.Label(actions, textvariable=self.diff_summary_var).pack(side="left")
        self._action(ttk.Button(actions, text="コピーを実行", command=self.copy_selected)).pack(side="right", padx=2)
        self._action(ttk.Button(actions, text="差分を再計算", command=self.refresh_all)).pack(side="right", padx=2)

    def _build_release_tab(self) -> None:
        tab = ttk.Frame(self.notebook)
        self.notebook.add(tab, text="② コミット・リリース")
        tab.columnconfigure(1, weight=1)
        pad = {"padx": 6, "pady": 3}

        self.repo_dir_var = tk.StringVar()
        ttk.Label(tab, text="リポジトリ:").grid(row=0, column=0, sticky="e", **pad)
        ttk.Entry(tab, textvariable=self.repo_dir_var, state="readonly").grid(row=0, column=1, sticky="we", **pad)
        ttk.Button(tab, text="開く", command=lambda: self.open_folder(self.repo_dir_var.get())).grid(row=0, column=2, **pad)

        self.repo_status_var = tk.StringVar()
        ttk.Label(tab, text="状態:").grid(row=1, column=0, sticky="e", **pad)
        ttk.Label(tab, textvariable=self.repo_status_var).grid(row=1, column=1, sticky="w", **pad)
        self._action(ttk.Button(tab, text="fetch", command=self.fetch_selected)).grid(row=1, column=2, **pad)

        ttk.Label(tab, text="コミット\nメッセージ:").grid(row=2, column=0, sticky="ne", **pad)
        self.commit_text = tk.Text(tab, height=5, wrap="word")
        self.commit_text.grid(row=2, column=1, columnspan=2, sticky="we", **pad)

        row3 = ttk.Frame(tab)
        row3.grid(row=3, column=1, columnspan=2, sticky="we", **pad)
        self.push_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(row3, text="コミット後に push する", variable=self.push_var).pack(side="left")
        ttk.Label(row3, text="（コミットするのはパッケージフォルダだけです）", foreground="#666").pack(side="left", padx=6)
        self._action(ttk.Button(row3, text="コミット＆プッシュ", command=self.commit_selected)).pack(side="right")

        ttk.Separator(tab).grid(row=4, column=0, columnspan=3, sticky="we", pady=8)

        self.github_info_var = tk.StringVar()
        ttk.Label(tab, text="GitHub:").grid(row=5, column=0, sticky="e", **pad)
        ttk.Label(tab, textvariable=self.github_info_var).grid(row=5, column=1, columnspan=2, sticky="w", **pad)

        row6 = ttk.Frame(tab)
        row6.grid(row=6, column=1, columnspan=2, sticky="we", **pad)
        self._action(ttk.Button(row6, text="リリースを作成（Actions を実行して完了まで待つ）", command=self.create_release_selected)).pack(side="left", padx=2)
        self._action(ttk.Button(row6, text="リリース状況を確認", command=self.check_release_selected)).pack(side="left", padx=2)
        ttk.Button(row6, text="リリースページを開く", command=self.open_release_page).pack(side="left", padx=2)

        self.release_status_var = tk.StringVar()
        ttk.Label(tab, text="リリース:").grid(row=7, column=0, sticky="e", **pad)
        ttk.Label(tab, textvariable=self.release_status_var).grid(row=7, column=1, columnspan=2, sticky="w", **pad)

    def _build_vpm_tab(self) -> None:
        tab = ttk.Frame(self.notebook)
        self.notebook.add(tab, text="③ vpm.json")
        tab.columnconfigure(0, weight=2)
        tab.columnconfigure(1, weight=3)
        tab.rowconfigure(0, weight=1)
        pad = {"padx": 6, "pady": 3}

        left = ttk.LabelFrame(tab, text="登録済みバージョン（新しい順）")
        left.grid(row=0, column=0, sticky="nsew", **pad)
        btns = ttk.Frame(left)
        btns.pack(side="bottom", fill="x", padx=4, pady=4)
        ttk.Button(btns, text="内容を表示", command=self.show_listed_version).pack(side="left", padx=2)
        self._action(ttk.Button(btns, text="選択バージョンを削除", command=self.delete_listed_version)).pack(side="left", padx=2)
        self.version_tree = ttk.Treeview(left, columns=("deps", "sha"), height=8, selectmode="browse")
        self.version_tree.heading("#0", text="バージョン")
        self.version_tree.heading("deps", text="依存")
        self.version_tree.heading("sha", text="SHA256")
        self.version_tree.column("#0", width=80, stretch=False)
        self.version_tree.column("sha", width=60, stretch=False)
        sb = ttk.Scrollbar(left, orient="vertical", command=self.version_tree.yview)
        self.version_tree.configure(yscrollcommand=sb.set)
        self.version_tree.pack(side="left", fill="both", expand=True, padx=(4, 0), pady=4)
        sb.pack(side="left", fill="y", pady=4)
        self.version_tree.bind("<Double-1>", lambda _e: self.show_listed_version())

        right = ttk.LabelFrame(tab, text="追加するエントリ（編集可）")
        right.grid(row=0, column=1, sticky="nsew", **pad)
        opts = ttk.Frame(right)
        opts.pack(side="bottom", fill="x", padx=4, pady=4)
        self.entry_text = ScrolledText(right, height=10, font=("Consolas", 9), wrap="none")
        self.entry_text.pack(side="top", fill="both", expand=True, padx=4, pady=4)
        self.sha_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(opts, text="リリース zip を検証して zipSHA256 を記録", variable=self.sha_var).pack(side="left")
        self._action(ttk.Button(opts, text="vpm.json に反映", command=self.apply_entry)).pack(side="right", padx=2)
        self._action(ttk.Button(opts, text="コピー先 package.json から作成", command=self.build_entry_selected)).pack(side="right", padx=2)

        bottom = ttk.LabelFrame(tab, text="Samirin33VPM リポジトリ")
        bottom.grid(row=1, column=0, columnspan=2, sticky="we", **pad)
        bottom.columnconfigure(1, weight=1)
        self.vpm_status_var = tk.StringVar()
        ttk.Label(bottom, text="状態:").grid(row=0, column=0, sticky="e", **pad)
        ttk.Label(bottom, textvariable=self.vpm_status_var).grid(row=0, column=1, sticky="w", **pad)
        ttk.Label(bottom, text="メッセージ:").grid(row=1, column=0, sticky="e", **pad)
        self.vpm_commit_var = tk.StringVar()
        ttk.Entry(bottom, textvariable=self.vpm_commit_var).grid(row=1, column=1, sticky="we", **pad)
        self.vpm_push_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(bottom, text="push", variable=self.vpm_push_var).grid(row=1, column=2, **pad)
        self._action(ttk.Button(bottom, text="vpm.json をコミット", command=self.commit_vpm)).grid(row=1, column=3, **pad)

    # 状態の読み込み ----------------------------------------------------------
    def refresh_all(self) -> None:
        if self.busy:
            return
        cfg = self.cfg

        def work():
            vpm_data = None
            try:
                vpm_data = vpm_core.load_json(cfg["vpm_json"])
            except Exception as e:
                self.log(f"vpm.json を読み込めません: {e}")
            states = {pkg["id"]: collect_state(pkg, vpm_data) for pkg in cfg["packages"]}
            vpm_rel = os.path.relpath(cfg["vpm_json"], cfg["vpm_repo_dir"])
            vpm_status = git_ops.repo_status(cfg["vpm_repo_dir"], vpm_rel)
            return vpm_data, states, vpm_status, git_ops.gh_ready()

        self.run_task("状態を読み込み", work, on_done=self._apply_refresh, refresh_after=False)

    def _apply_refresh(self, result) -> None:
        self.vpm_data, self.states, self.vpm_status, self.gh_ok = result
        self.vpm_path_var.set(self.cfg["vpm_json"])
        self.pkg_tree.delete(*self.pkg_tree.get_children())
        for pkg_id, st in self.states.items():
            notes = st.errors + st.warnings
            tag = "error" if st.errors else ("warn" if st.warnings else "")
            self.pkg_tree.insert("", "end", iid=pkg_id, text=pkg_id, tags=(tag,) if tag else (), values=(
                st.source_version or "-",
                st.target_version or "-",
                st.listed_version or "-",
                st.plan.summary() if st.plan else "-",
                st.repo.summary() if st.repo else "-",
                " / ".join(notes),
            ))
        if self.selected_id not in self.states and self.states:
            self.selected_id = next(iter(self.states))
        if self.selected_id:
            self.pkg_tree.selection_set(self.selected_id)
            self._show_package()
        self.vpm_status_var.set(self.vpm_status.summary() if self.vpm_status else "-")
        for st in self.states.values():
            for msg in st.errors:
                self.log(f"{st.id}: {msg}")

    def on_package_selected(self, _event=None) -> None:
        sel = self.pkg_tree.selection()
        if sel and sel[0] != self.selected_id:
            self.selected_id = sel[0]
            self._show_package()

    def current(self) -> Optional[PackageState]:
        return self.states.get(self.selected_id) if self.selected_id else None

    def _show_package(self) -> None:
        st = self.current()
        if not st:
            return
        cfg = st.cfg
        self.source_dir_var.set(cfg["source_dir"])
        self.target_dir_var.set(st.target_dir)
        self.exclude_var.set(", ".join(cfg.get("exclude") or []))
        self.repo_dir_var.set(cfg["repo_dir"])
        self.repo_status_var.set(st.repo.summary() if st.repo else "-")

        self.version_info_var.set(
            f"開発版 {st.source_version or '-'}   コピー先 {st.target_version or '-'}   vpm.json 最新 {st.listed_version or '-'}"
        )
        self.new_version_var.set(self._suggested_version(st))
        self.deps_var.set(self._describe_dependencies(st))

        self.diff_tree.delete(*self.diff_tree.get_children())
        if st.plan:
            for kind, label, items in [("added", "追加", st.plan.added), ("modified", "変更", st.plan.modified), ("deleted", "削除", st.plan.deleted)]:
                for rel in items:
                    self.diff_tree.insert("", "end", text=rel, values=(label,), tags=(kind,))
            extra = f"（改行コードのみの違い {st.plan.newline_only} 件は無視）" if st.plan.newline_only else ""
            self.diff_summary_var.set(f"{st.plan.summary()}　同一 {st.plan.unchanged} 件{extra}")
        else:
            self.diff_summary_var.set("差分を計算できませんでした")

        gh = "gh: ログイン済み（Actions を自動実行）" if self.gh_ok else "gh: 未ログイン（Actions はブラウザで実行。`gh auth login` で自動化できます）"
        self.github_info_var.set(f"{cfg['github_repo']}   workflow: {cfg['workflow']} ({cfg['branch']})   {gh}")
        self.release_status_var.set("")
        notes = st.errors + st.warnings + self._dependency_warnings(st)
        self.notes_var.set("注意: " + " / ".join(notes) if notes else "")
        if self._shown_id != st.id or not self.commit_text.get("1.0", "end").strip():
            self._reset_commit_message(f"ver{self.new_version_var.get() or st.source_version}")
        self._shown_id = st.id

        self.version_tree.delete(*self.version_tree.get_children())
        versions = vpm_core.get_versions(self.vpm_data or {}, st.id)
        for v in vpm_core.sorted_versions(versions.keys(), descending=True):
            entry = versions[v]
            deps = ", ".join(f"{k.split('.')[-1]} {r}" for k, r in (entry.get("vpmDependencies") or {}).items())
            self.version_tree.insert("", "end", iid=v, text=v, values=(deps, "あり" if entry.get("zipSHA256") else ""))
        self.entry_text.delete("1.0", "end")

    def _suggested_version(self, st: PackageState) -> str:
        """開発版が公開済み（vpm.json / コピー先）以下なら、その次のパッチ版を提案する。"""
        sv = st.source_version
        published = [v for v in (st.listed_version, st.target_version) if v]
        newest = vpm_core.latest_version(published)
        if sv and newest and vpm_core.compare_versions(sv, newest) <= 0:
            return vpm_core.bump_version(newest, "patch")
        return sv

    def _dependency_warnings(self, st: PackageState) -> List[str]:
        result = []
        for dep_id, rng in ((st.source_json or {}).get("vpmDependencies") or {}).items():
            managed = self.states.get(dep_id)
            minimum = vpm_core.minimum_of_range(rng)
            if managed and managed.source_version and minimum and vpm_core.compare_versions(minimum, managed.source_version) < 0:
                result.append(f"{dep_id} への依存下限 {rng} が開発版 {managed.source_version} より古い")
        return result

    def _describe_dependencies(self, st: PackageState) -> str:
        deps = (st.source_json or {}).get("vpmDependencies") or {}
        if not deps:
            return "なし"
        lines = []
        for dep_id, rng in deps.items():
            line = f"{dep_id} {rng}"
            managed = self.states.get(dep_id)
            if managed and managed.source_version:
                minimum = vpm_core.minimum_of_range(rng)
                line += f"   （管理中: 開発版 {managed.source_version}"
                if minimum and vpm_core.compare_versions(minimum, managed.source_version) < 0:
                    line += "。下限が古い可能性あり"
                line += "）"
            lines.append(line)
        return "\n".join(lines)

    # ① バージョン・コピー ---------------------------------------------------
    def suggest_bump(self, part: str) -> None:
        st = self.current()
        if not st:
            return
        candidates = [v for v in (st.source_version, st.target_version, st.listed_version) if v]
        base = vpm_core.latest_version(candidates) or "0.0.0"
        self.new_version_var.set(vpm_core.bump_version(base, part))

    def write_source_version(self) -> None:
        st = self.current()
        if not st:
            return
        new_version = self.new_version_var.get().strip()
        if vpm_core.parse_semver(new_version) is None:
            messagebox.showerror("エラー", f"バージョンの形式が正しくありません: {new_version}", parent=self)
            return
        if new_version == st.source_version:
            messagebox.showinfo("情報", "開発版と同じバージョンです。", parent=self)
            return
        if st.listed_version and vpm_core.compare_versions(new_version, st.listed_version) <= 0:
            if not messagebox.askyesno("確認", f"vpm.json の最新 {st.listed_version} 以下です。このまま書き込みますか？", parent=self):
                return

        def work():
            vpm_core.set_package_version(st.cfg["source_dir"], new_version)
            self.log(f"{st.id}: 開発側 package.json を {st.source_version} → {new_version} に変更")
            self.post(self._reset_commit_message, f"ver{new_version}")

        self.run_task("バージョンを書き込み", work)

    def _reset_commit_message(self, text: str) -> None:
        self.commit_text.delete("1.0", "end")
        self.commit_text.insert("1.0", text)

    def align_managed_dependencies(self) -> None:
        st = self.current()
        if not st or not st.source_json:
            return
        changes = []
        for dep_id, rng in (st.source_json.get("vpmDependencies") or {}).items():
            managed = self.states.get(dep_id)
            if not managed or not managed.source_version:
                continue
            minimum = vpm_core.minimum_of_range(rng)
            if minimum and vpm_core.compare_versions(minimum, managed.source_version) >= 0:
                continue
            changes.append((dep_id, rng, f">={managed.source_version}"))
        if not changes:
            messagebox.showinfo("情報", "揃える必要のある依存はありません。", parent=self)
            return
        text = "\n".join(f"{d}: {old} → {new}" for d, old, new in changes)
        if not messagebox.askyesno("確認", f"開発側 package.json の依存を変更します。\n\n{text}\n\n依存先も同時にリリースしてください。", parent=self):
            return

        def work():
            for dep_id, _old, new in changes:
                vpm_core.set_package_dependency(st.cfg["source_dir"], dep_id, new)
                self.log(f"{st.id}: 依存 {dep_id} を {new} に変更")

        self.run_task("依存を揃える", work)

    def save_excludes(self) -> None:
        st = self.current()
        if not st:
            return
        patterns = [p.strip() for p in self.exclude_var.get().split(",") if p.strip()]
        for pkg in self.cfg["packages"]:
            if pkg["id"] == st.id:
                pkg["exclude"] = patterns
        vpm_config.save_config(self.cfg)
        self.log(f"{st.id}: 除外パターンを保存 {patterns}")
        self.refresh_all()

    def _copy(self, st: PackageState, confirm: Callable[[str], bool]) -> bool:
        cfg = st.cfg
        package_sync.validate_target(st.target_dir, cfg["repo_dir"], cfg["id"])
        plan = package_sync.compute_plan(cfg["source_dir"], st.target_dir, cfg.get("exclude") or [])
        if plan.change_count == 0:
            self.log(f"{st.id}: コピーする差分はありません。")
            return True
        status = git_ops.repo_status(cfg["repo_dir"], cfg["package_path"])
        warnings = []
        if status.dirty_in_path:
            warnings.append(f"コピー先に未コミットの変更が {status.dirty_in_path} 件あり、上書きされます。")
        source = vpm_core.read_package_json(cfg["source_dir"]).get("version", "")
        target = vpm_core.read_package_json(st.target_dir).get("version", "") if os.path.exists(os.path.join(st.target_dir, "package.json")) else ""
        if source and target and vpm_core.compare_versions(source, target) < 0:
            warnings.append(f"開発版 {source} がコピー先 {target} より古いです。")
        deleted_preview = "\n".join("  - " + d for d in plan.deleted[:15])
        if len(plan.deleted) > 15:
            deleted_preview += f"\n  ...ほか {len(plan.deleted) - 15} 件"
        message = f"{st.id}\n{plan.summary()}\n"
        if plan.deleted:
            message += f"\n削除されるファイル:\n{deleted_preview}\n"
        if warnings:
            message += "\n注意:\n" + "\n".join("・" + w for w in warnings) + "\n"
        message += "\nコピーを実行しますか？"
        if not confirm(message):
            return False
        package_sync.apply_plan(plan, log=self.log)
        return True

    def copy_selected(self) -> None:
        st = self.current()
        if not st:
            return
        self.run_task("コピー", lambda: self._copy(st, self._confirm_from_worker))

    def _confirm_from_worker(self, message: str) -> bool:
        return bool(self.ask_main(lambda: messagebox.askyesno("確認", message, parent=self)))

    # ② コミット・リリース ----------------------------------------------------
    def fetch_selected(self) -> None:
        st = self.current()
        if st:
            self.run_task("fetch", lambda: git_ops.fetch(st.cfg["repo_dir"], log=self.log))

    def commit_selected(self) -> None:
        st = self.current()
        if not st:
            return
        message = self.commit_text.get("1.0", "end").strip()
        if not message:
            messagebox.showerror("エラー", "コミットメッセージを入力してください。", parent=self)
            return
        push = self.push_var.get()
        self.run_task("コミット", lambda: git_ops.commit_and_push(
            st.cfg["repo_dir"], [st.cfg["package_path"]], message, push, log=self.log))

    def _release_zip_url(self, st: PackageState, version: str) -> str:
        return vpm_core.build_zip_url(st.cfg["github_repo"], st.id, version)

    def check_release_selected(self) -> None:
        st = self.current()
        if not st or not st.target_version:
            return
        version = st.target_version
        url = self._release_zip_url(st, version)

        def work():
            exists = github_release.asset_exists(url)
            self.log(f"{st.id} {version}: {'リリース済み' if exists else '未リリース'} ({url})")
            return exists

        self.run_task("リリース確認", work, on_done=lambda ok: self.release_status_var.set(
            f"{version}: {'リリース済み' if ok else '未リリース'}"), refresh_after=False)

    def open_release_page(self) -> None:
        st = self.current()
        if st:
            webbrowser.open(github_release.releases_page(st.cfg["github_repo"]))

    def _start_release(self, st: PackageState, version: str) -> None:
        cfg = st.cfg
        url = self._release_zip_url(st, version)
        if github_release.asset_exists(url):
            raise RuntimeError(f"{version} はすでにリリース済みです。バージョンを上げてください。")
        status = git_ops.repo_status(cfg["repo_dir"], cfg["package_path"])
        if status.dirty_in_path or status.ahead:
            raise RuntimeError(f"コピー先リポジトリがプッシュされていません（{status.summary()}）。先にコミット＆プッシュしてください。")
        if self.gh_ok:
            git_ops.dispatch_workflow(cfg["github_repo"], cfg["workflow"], cfg["branch"], log=self.log)
            self.log("Actions を起動しました。")
        else:
            page = github_release.workflow_page(cfg["github_repo"], cfg["workflow"])
            webbrowser.open(page)
            ok = self.ask_main(lambda: messagebox.askokcancel(
                "Actions の実行",
                f"ブラウザで開いたページの「Run workflow」→「Run workflow」を押してから OK を押してください。\n\n{page}",
                parent=self))
            if not ok:
                raise TaskCancelled()
        self._wait_release(url, version)

    def _wait_release(self, url: str, version: str) -> None:
        self.log(f"リリース {version} の完了を待っています（最大 {RELEASE_WAIT_SECONDS // 60} 分、中止ボタンで中断できます）")
        deadline = time.time() + RELEASE_WAIT_SECONDS
        while time.time() < deadline:
            for _ in range(RELEASE_POLL_SECONDS):
                self.check_cancel()
                time.sleep(1)
            if github_release.asset_exists(url):
                self.log(f"リリース {version} を確認しました。")
                return
            self.log("  まだリリースされていません...")
        raise RuntimeError("リリースの完了を確認できませんでした。Actions の実行結果を確認してください。")

    def create_release_selected(self) -> None:
        st = self.current()
        if not st or not st.target_version:
            return
        version = st.target_version
        if not messagebox.askyesno("確認", f"{st.cfg['github_repo']} で {version} のリリースを作成しますか？", parent=self):
            return
        self.run_task("リリース作成", lambda: self._start_release(st, version),
                      on_done=lambda _r: self.release_status_var.set(f"{version}: リリース済み"))

    # ③ vpm.json --------------------------------------------------------------
    def _build_entry(self, st: PackageState, verify: bool) -> Dict[str, Any]:
        package_json = vpm_core.read_package_json(st.target_dir)
        if package_json.get("name") != st.id:
            raise RuntimeError(f"コピー先 package.json の name が {package_json.get('name')} です。")
        version = package_json["version"]
        url = self._release_zip_url(st, version)
        sha = None
        if verify:
            self.log(f"zip をダウンロードして検証: {url}")
            verified = github_release.download_and_verify(url, st.id, version)
            sha = verified.sha256
            self.log(f"  OK ({verified.size:,} bytes, sha256 {sha})")
        vpm_data = vpm_core.load_json(self.cfg["vpm_json"])
        versions = vpm_core.get_versions(vpm_data, st.id)
        previous_key = vpm_core.latest_version(versions.keys())
        return vpm_core.build_listing_entry(package_json, url, versions.get(previous_key) if previous_key else None, sha)

    def build_entry_selected(self) -> None:
        st = self.current()
        if not st:
            return
        verify = self.sha_var.get()

        def done(entry):
            self.entry_text.delete("1.0", "end")
            self.entry_text.insert("1.0", json.dumps(entry, ensure_ascii=False, indent=2))

        self.run_task("エントリ作成", lambda: self._build_entry(st, verify), on_done=done, refresh_after=False)

    def _write_entry(self, st: PackageState, entry: Dict[str, Any], confirm: Callable[[str], bool]) -> bool:
        version = entry.get("version", "")
        if vpm_core.parse_semver(version) is None:
            raise RuntimeError(f"version が正しくありません: {version}")
        if not str(entry.get("url", "")).startswith("https://"):
            raise RuntimeError("url が https の zip URL になっていません。")
        data = vpm_core.load_json(self.cfg["vpm_json"])
        versions = vpm_core.get_versions(data, st.id)
        latest = vpm_core.latest_version(versions.keys())
        if version in versions and not confirm(f"{version} はすでに登録されています。上書きしますか？"):
            return False
        if latest and vpm_core.compare_versions(version, latest) < 0 and not confirm(
                f"{version} は登録済みの最新 {latest} より古いバージョンです。追加しますか？"):
            return False
        vpm_core.upsert_version(data, st.id, entry)
        vpm_core.save_json(self.cfg["vpm_json"], data)
        self.log(f"vpm.json に {st.id} {version} を書き込みました。")
        self.post(self.vpm_commit_var.set, f"Update vpm.json: {st.id} {version}")
        return True

    def apply_entry(self) -> None:
        st = self.current()
        if not st:
            return
        try:
            entry = json.loads(self.entry_text.get("1.0", "end"))
        except json.JSONDecodeError as e:
            messagebox.showerror("エラー", f"JSON として読めません。\n先に「コピー先 package.json から作成」を押してください。\n\n{e}", parent=self)
            return
        self.run_task("vpm.json に反映", lambda: self._write_entry(st, entry, self._confirm_from_worker))

    def show_listed_version(self) -> None:
        st = self.current()
        sel = self.version_tree.selection()
        if not st or not sel:
            return
        entry = vpm_core.get_versions(self.vpm_data or {}, st.id).get(sel[0])
        self.entry_text.delete("1.0", "end")
        self.entry_text.insert("1.0", json.dumps(entry, ensure_ascii=False, indent=2))

    def delete_listed_version(self) -> None:
        st = self.current()
        sel = self.version_tree.selection()
        if not st or not sel:
            return
        version = sel[0]
        if not messagebox.askyesno("確認", f"vpm.json から {st.id} {version} を削除しますか？\n（インストール済みのユーザーは更新できなくなる場合があります）", parent=self):
            return

        def work():
            data = vpm_core.load_json(self.cfg["vpm_json"])
            if vpm_core.remove_version(data, st.id, version):
                vpm_core.save_json(self.cfg["vpm_json"], data)
                self.log(f"vpm.json から {st.id} {version} を削除しました。")
                self.post(self.vpm_commit_var.set, f"Remove {st.id} {version} from vpm.json")

        self.run_task("バージョン削除", work)

    def _commit_vpm(self, message: str, push: bool) -> None:
        rel = os.path.relpath(self.cfg["vpm_json"], self.cfg["vpm_repo_dir"])
        git_ops.commit_and_push(self.cfg["vpm_repo_dir"], [rel], message, push, log=self.log)
        if push:
            self.log("GitHub Pages への反映には数分かかることがあります。")

    def commit_vpm(self) -> None:
        message = self.vpm_commit_var.get().strip()
        if not message:
            messagebox.showerror("エラー", "コミットメッセージを入力してください。", parent=self)
            return
        push = self.vpm_push_var.get()
        self.run_task("vpm.json をコミット", lambda: self._commit_vpm(message, push))

    # まとめてリリース ------------------------------------------------------------
    def release_pipeline(self) -> None:
        st = self.current()
        if not st or not st.source_json:
            return
        commit_message = self.commit_text.get("1.0", "end").strip()
        requested_version = self.new_version_var.get().strip()
        verify = self.sha_var.get()
        dependency_warnings = self._dependency_warnings(st)

        def work():
            cfg = st.cfg
            current_version = vpm_core.read_package_json(cfg["source_dir"])["version"]
            version = requested_version or current_version
            if vpm_core.parse_semver(version) is None:
                raise RuntimeError(f"新バージョンの形式が正しくありません: {version}")
            # 「ver0.8.4」のような自動入力のままなら、実際に出すバージョンに合わせる
            auto_message = not commit_message or (
                commit_message.startswith("ver") and vpm_core.parse_semver(commit_message[3:]) is not None)
            message = f"ver{version}" if auto_message else commit_message
            listed = vpm_core.latest_listed_version(vpm_core.load_json(self.cfg["vpm_json"]), st.id)
            if listed and vpm_core.compare_versions(version, listed) <= 0:
                raise RuntimeError(f"新バージョン {version} が vpm.json の最新 {listed} 以下です。バージョンを上げてください。")
            url = self._release_zip_url(st, version)
            if github_release.asset_exists(url):
                raise RuntimeError(f"{version} はすでにリリース済みです。バージョンを上げてください。")
            plan = package_sync.compute_plan(cfg["source_dir"], st.target_dir, cfg.get("exclude") or [])
            actions = "Actions を起動" if self.gh_ok else "ブラウザで Actions を実行（Run workflow を押す）"
            bump = f"0. 開発側 package.json のバージョンを {current_version} → {version} に変更\n" if version != current_version else ""
            notes = "".join(f"\n注意: {w}" for w in dependency_warnings)
            summary = (
                f"{st.id} {version} をリリースします。\n\n{bump}"
                f"1. コピー（{plan.summary()}）\n"
                f"2. コピー先をコミット＆プッシュ\n    メッセージ: {message.splitlines()[0]}\n"
                f"3. {actions}し、リリース完了を待つ\n"
                f"4. zip を{'検証して ' if verify else ''}vpm.json に {version} を追加\n"
                f"5. vpm.json をコミット＆プッシュ\n{notes}\n\n実行しますか？"
            )
            if not self._confirm_from_worker(summary):
                raise TaskCancelled()

            if version != current_version:
                self.log(f"[0/5] 開発側 package.json を {version} に変更")
                vpm_core.set_package_version(cfg["source_dir"], version)
            self.log("[1/5] コピー")
            if not self._copy(st, lambda m: "注意:" not in m or self._confirm_from_worker(m)):
                raise TaskCancelled()
            self.check_cancel()
            self.log("[2/5] コミット＆プッシュ")
            git_ops.commit_and_push(cfg["repo_dir"], [cfg["package_path"]], message, True, log=self.log)
            self.check_cancel()
            self.log("[3/5] リリース作成")
            self._start_release(st, version)
            self.log("[4/5] vpm.json 更新")
            entry = self._build_entry(st, verify)
            if not self._write_entry(st, entry, self._confirm_from_worker):
                raise TaskCancelled()
            self.log("[5/5] vpm.json をコミット＆プッシュ")
            self._commit_vpm(f"Update vpm.json: {st.id} {version}", True)
            self.log(f"{st.id} {version} のリリースが完了しました。")
            return version

        self.run_task("まとめてリリース", work, on_done=lambda v: messagebox.showinfo(
            "完了", f"{st.id} {v} のリリースと vpm.json の更新が完了しました。", parent=self))

    # その他 ------------------------------------------------------------------
    def open_folder(self, path: str) -> None:
        if path and os.path.isdir(path):
            os.startfile(path)
        else:
            messagebox.showerror("エラー", f"フォルダが見つかりません:\n{path}", parent=self)

    def open_settings(self) -> None:
        SettingsDialog(self, self.cfg, self._on_settings_saved)

    def _on_settings_saved(self, cfg: Dict[str, Any]) -> None:
        self.cfg = cfg
        vpm_config.save_config(cfg)
        self.log(f"設定を保存しました: {vpm_config.config_path()}")
        self.refresh_all()


class SettingsDialog(tk.Toplevel):
    FIELDS = [
        ("id", "パッケージ ID", None),
        ("source_dir", "コピー元（開発側のパッケージフォルダ）", "dir"),
        ("repo_dir", "コピー先リポジトリ", "dir"),
        ("package_path", "リポジトリ内のパッケージパス", None),
        ("github_repo", "GitHub リポジトリ（owner/name）", None),
        ("workflow", "リリース用 workflow ファイル", None),
        ("branch", "ブランチ", None),
        ("exclude", "除外パターン（, 区切り）", None),
    ]

    def __init__(self, master: VpmManager, cfg: Dict[str, Any], on_save: Callable[[Dict[str, Any]], None]):
        super().__init__(master)
        self.title("設定")
        self.geometry("820x480")
        self.transient(master)
        self.cfg = vpm_config.clone_config(cfg)
        self.on_save = on_save
        self.current_index: Optional[int] = None
        pad = {"padx": 6, "pady": 3}

        top = ttk.Frame(self)
        top.pack(fill="x", **pad)
        top.columnconfigure(1, weight=1)
        self.vpm_json_var = tk.StringVar(value=self.cfg["vpm_json"])
        self.vpm_repo_var = tk.StringVar(value=self.cfg["vpm_repo_dir"])
        for row, (label, var, kind) in enumerate([("vpm.json", self.vpm_json_var, "file"), ("VPM リポジトリ", self.vpm_repo_var, "dir")]):
            ttk.Label(top, text=label).grid(row=row, column=0, sticky="e", **pad)
            ttk.Entry(top, textvariable=var).grid(row=row, column=1, sticky="we", **pad)
            ttk.Button(top, text="参照...", command=lambda v=var, k=kind: self._browse(v, k)).grid(row=row, column=2, **pad)

        body = ttk.Frame(self)
        body.pack(fill="both", expand=True, **pad)
        left = ttk.Frame(body)
        left.pack(side="left", fill="y")
        self.listbox = tk.Listbox(left, width=42, exportselection=False)
        self.listbox.pack(fill="y", expand=True)
        self.listbox.bind("<<ListboxSelect>>", self._on_select)
        lb_btns = ttk.Frame(left)
        lb_btns.pack(fill="x", pady=4)
        ttk.Button(lb_btns, text="追加", command=self._add).pack(side="left", padx=2)
        ttk.Button(lb_btns, text="削除", command=self._remove).pack(side="left", padx=2)

        form = ttk.Frame(body)
        form.pack(side="left", fill="both", expand=True, padx=8)
        form.columnconfigure(1, weight=1)
        self.vars: Dict[str, tk.StringVar] = {}
        for row, (key, label, kind) in enumerate(self.FIELDS):
            ttk.Label(form, text=label).grid(row=row, column=0, sticky="e", **pad)
            var = tk.StringVar()
            self.vars[key] = var
            ttk.Entry(form, textvariable=var).grid(row=row, column=1, sticky="we", **pad)
            if kind:
                ttk.Button(form, text="参照...", command=lambda v=var, k=kind: self._browse(v, k)).grid(row=row, column=2, **pad)

        bottom = ttk.Frame(self)
        bottom.pack(fill="x", **pad)
        ttk.Label(bottom, text=f"保存先: {vpm_config.config_path()}", foreground="#666").pack(side="left")
        ttk.Button(bottom, text="キャンセル", command=self.destroy).pack(side="right", padx=2)
        ttk.Button(bottom, text="保存", command=self._save).pack(side="right", padx=2)

        self._reload_list()
        if self.cfg["packages"]:
            self.listbox.selection_set(0)
            self._load(0)
        self.grab_set()

    def _browse(self, var: tk.StringVar, kind: str) -> None:
        if kind == "file":
            path = filedialog.askopenfilename(parent=self, filetypes=[("JSON", "*.json"), ("All", "*.*")])
        else:
            path = filedialog.askdirectory(parent=self)
        if path:
            var.set(os.path.normpath(path))

    def _reload_list(self) -> None:
        self.listbox.delete(0, "end")
        for pkg in self.cfg["packages"]:
            self.listbox.insert("end", pkg.get("id") or "(未設定)")

    def _store(self) -> None:
        if self.current_index is None or self.current_index >= len(self.cfg["packages"]):
            return
        pkg = self.cfg["packages"][self.current_index]
        for key, _label, _kind in self.FIELDS:
            value = self.vars[key].get().strip()
            pkg[key] = [p.strip() for p in value.split(",") if p.strip()] if key == "exclude" else value
        if pkg["id"] and not pkg["package_path"]:
            pkg["package_path"] = f"Packages/{pkg['id']}"

    def _load(self, index: int) -> None:
        self.current_index = index
        pkg = self.cfg["packages"][index]
        for key, _label, _kind in self.FIELDS:
            value = pkg.get(key, "")
            self.vars[key].set(", ".join(value) if isinstance(value, list) else value)

    def _on_select(self, _event=None) -> None:
        sel = self.listbox.curselection()
        if not sel:
            return
        self._store()
        self._reload_list()
        self.listbox.selection_set(sel[0])
        self._load(sel[0])

    def _add(self) -> None:
        self._store()
        self.cfg["packages"].append({
            "id": "", "source_dir": "", "repo_dir": "", "package_path": "", "github_repo": "",
            "workflow": "release.yml", "branch": "main", "exclude": [],
        })
        self._reload_list()
        index = len(self.cfg["packages"]) - 1
        self.listbox.selection_clear(0, "end")
        self.listbox.selection_set(index)
        self._load(index)

    def _remove(self) -> None:
        sel = self.listbox.curselection()
        if not sel or not messagebox.askyesno("確認", "選択したパッケージを一覧から外しますか？（ファイルは削除しません）", parent=self):
            return
        del self.cfg["packages"][sel[0]]
        self.current_index = None
        self._reload_list()
        if self.cfg["packages"]:
            self.listbox.selection_set(0)
            self._load(0)

    def _save(self) -> None:
        self._store()
        self.cfg["vpm_json"] = self.vpm_json_var.get().strip()
        self.cfg["vpm_repo_dir"] = self.vpm_repo_var.get().strip()
        ids = [p["id"] for p in self.cfg["packages"]]
        problems = []
        if any(not i for i in ids):
            problems.append("パッケージ ID が空のものがあります。")
        if len(set(ids)) != len(ids):
            problems.append("パッケージ ID が重複しています。")
        for pkg in self.cfg["packages"]:
            if pkg["id"] and os.path.basename(pkg["package_path"].replace("\\", "/").rstrip("/")) != pkg["id"]:
                problems.append(f"{pkg['id']}: リポジトリ内のパスの末尾はパッケージ ID にしてください。")
            if pkg["github_repo"] and pkg["github_repo"].count("/") != 1:
                problems.append(f"{pkg['id']}: GitHub リポジトリは owner/name の形式で指定してください。")
        if problems:
            messagebox.showerror("設定エラー", "\n".join(problems), parent=self)
            return
        self.on_save(self.cfg)
        self.destroy()


if __name__ == "__main__":
    VpmManager().mainloop()
