# --- Standard Library Imports ---
import os
import sys
import shutil
import fnmatch
import tempfile
from pathlib import Path
import subprocess
import json
import time
import traceback
import threading
import queue

# --- UI and Core Logic Imports ---
import tkinter as tk
from tkinter import ttk
from tkinter import filedialog, messagebox, simpledialog, BooleanVar, Text, Toplevel
from tkinter.constants import (
    BOTH,
    END,
    WORD,
    VERTICAL,
    LEFT,
    RIGHT,
    X,
    Y,
    N,
    S,
    E,
    W,
    NSEW,
    FLAT,
)

# --- Optional Dependency Imports ---
try:
    from watchdog.observers import Observer
    from watchdog.events import FileSystemEventHandler

    WATCHDOG_AVAILABLE = True
except ImportError:
    Observer, FileSystemEventHandler = None, None
    WATCHDOG_AVAILABLE = False
    print("Warning: 'watchdog' library not found. Automatic refresh disabled.")
try:
    import pathspec

    PATHSPEC_AVAILABLE = True
except ImportError:
    pathspec = None
    PATHSPEC_AVAILABLE = False
    print("Warning: 'pathspec' library not found. .gitignore handling disabled.")


# --- File System Event Handler (Watchdog) ---
class ProjectChangeHandler(FileSystemEventHandler):
    """Handles file system events detected by watchdog."""

    def __init__(self, callback_queue):
        super().__init__()
        self.queue = callback_queue
        self.last_event_time = 0
        self.debounce_delay = 1.0  # Debounce delay in seconds

    def schedule_refresh(self):
        """Schedules a refresh call via the queue with debouncing."""
        current_time = time.time()
        if (
            current_time - self.last_event_time >= self.debounce_delay
            or self.last_event_time == 0
        ):
            self.queue.put("refresh")
            self.last_event_time = current_time  # Record time of this queued event

    def on_any_event(self, event):
        """Called when watchdog detects any file system change."""
        if not event.is_directory:
            path_str = event.src_path
            # Basic check to avoid common noisy events - might need refinement
            # Check against the temp_dir name directly
            if ".git" not in path_str and Progomatter.TEMP_DIR_NAME not in path_str:
                self.schedule_refresh()


# --- Main Application Class ---
class Progomatter:
    """Main application class for Progomatter using standard tkinter/ttk."""

    TEMP_DIR_NAME = "progomatter_files"

    def __init__(self):
        self.root = tk.Tk()
        self.root.title("Progomatter v3.2")  # Version bump
        self.root.geometry("600x420")  # Adjusted size

        # --- Initialize variables ---
        self.projects_file = Path("projects.json")
        self.projects = self.load_projects()
        self.selected_project = None
        self.temp_dir = Path(tempfile.gettempdir()) / self.TEMP_DIR_NAME
        self.temp_dir.mkdir(exist_ok=True)  # Ensure it exists
        self.gitignore_spec = None
        self.include_patterns = []

        # --- Tkinter Option Vars ---
        # Renamed: Controls JSON with file content
        self.create_files_json_var = BooleanVar(value=True)
        # New: Controls JSON with file tree only (no content)
        self.create_tree_json_var = BooleanVar(value=False)
        # Controls copying individual files to temp dir
        self.copy_individual_files_var = BooleanVar(value=False)
        # Controls conversion of *copied* files
        self.convert_copied_files_var = BooleanVar(value=False)
        # Auto refresh
        self.auto_refresh_var = BooleanVar(value=WATCHDOG_AVAILABLE)

        # --- File Watching Setup ---
        self.observer = None
        self.observer_thread = None
        self.watch_path = None
        self.callback_queue = queue.Queue()
        self.include_editor_text_widget = None

        # --- Build GUI ---
        self.setup_gui()
        self.update_dependent_checkbox_states()  # Set initial states for dependent checkboxes
        self.check_queue()
        self.root.protocol("WM_DELETE_WINDOW", self.on_closing)

    def setup_gui(self):
        """Creates and arranges all the GUI widgets using tkinter/ttk."""
        main_frame = ttk.Frame(self.root, padding=10)
        main_frame.pack(fill=BOTH, expand=True)
        main_frame.columnconfigure(0, weight=1)

        # --- Row 0: Project Management ---
        project_row_frame = ttk.Frame(main_frame)
        project_row_frame.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        project_row_frame.columnconfigure(1, weight=1)
        ttk.Label(project_row_frame, text="Project:").pack(side=LEFT, padx=(0, 5))
        self.project_dropdown = ttk.Combobox(
            project_row_frame,
            state="readonly",
            values=[p["project_name"] for p in self.projects],
        )
        self.project_dropdown.pack(side=LEFT, fill=X, expand=True, padx=(0, 5))
        self.project_dropdown.bind("<<ComboboxSelected>>", self.load_selected_project)
        edit_include_btn = ttk.Button(
            project_row_frame,
            text="Edit .include",
            command=self.edit_include_file,
            width=11,
        )
        edit_include_btn.pack(side=LEFT, padx=(0, 3))
        new_project_btn = ttk.Button(
            project_row_frame,
            text="New",
            command=self.create_new_project,
            width=10,
        )
        new_project_btn.pack(side=LEFT, padx=(0, 3))
        delete_project_btn = ttk.Button(
            project_row_frame, text="Delete", command=self.delete_project, width=7
        )
        delete_project_btn.pack(side=LEFT)

        # --- Row 1: Directory Display ---
        dir_frame = ttk.Frame(main_frame)
        dir_frame.grid(row=1, column=0, sticky="ew", pady=3)
        dir_frame.columnconfigure(1, weight=1)
        ttk.Label(dir_frame, text="Directory:").pack(side=LEFT, padx=(0, 5))
        self.dir_label = ttk.Label(
            dir_frame,
            text="No project selected",
            anchor="w",
            relief=FLAT,
            padding=2,
            borderwidth=1,
        )
        self.dir_label.pack(side=LEFT, fill=X, expand=True)

        # --- Row 2: Prompt Rules ---
        prompt_frame = ttk.Frame(main_frame)
        prompt_frame.grid(row=2, column=0, sticky="ew", pady=3)
        prompt_frame.columnconfigure(1, weight=1)
        ttk.Label(prompt_frame, text="Prompt Rules:").pack(side=LEFT, padx=(0, 5))
        self.prompt_rules_input = ttk.Entry(prompt_frame)
        self.prompt_rules_input.pack(side=LEFT, fill=X, expand=True, padx=(0, 5))
        save_prompt_btn = ttk.Button(
            prompt_frame, text="Save Prompt", command=self.save_prompt_rules, width=11
        )
        save_prompt_btn.pack(side=LEFT)

        # --- Row 3: JSON Output Options ---
        options_frame_json = ttk.Frame(main_frame, padding=(0, 5))
        options_frame_json.grid(row=3, column=0, sticky="w", pady=(5, 0))

        # Renamed Checkbox for JSON with content (no longer controls tree state)
        self.files_json_cb = ttk.Checkbutton(
            options_frame_json,
            text="Create content JSON (`project_files.json`)",
            variable=self.create_files_json_var,
            command=self.on_option_change,
        )
        self.files_json_cb.pack(side=LEFT, padx=(0, 10))

        # New Checkbox for tree-only JSON (independent)
        self.tree_json_cb = ttk.Checkbutton(
            options_frame_json,
            text="Create tree-only JSON (`project_file_tree.json`)",
            variable=self.create_tree_json_var,
            command=self.on_option_change,
        )
        self.tree_json_cb.pack(side=LEFT, padx=(0, 10))

        # --- Row 4: Individual File Output Options ---
        options_frame_files = ttk.Frame(main_frame, padding=(0, 0))
        options_frame_files.grid(row=4, column=0, sticky="w", pady=(0, 0))

        copy_individual_cb = ttk.Checkbutton(
            options_frame_files,
            text="Copy individual files",
            variable=self.copy_individual_files_var,
            command=self.on_copy_individual_change,  # Link to handler
        )
        copy_individual_cb.pack(side=LEFT, padx=(0, 10))

        self.convert_cb = ttk.Checkbutton(
            options_frame_files,
            text="Convert copied files to .txt",
            variable=self.convert_copied_files_var,
            command=self.on_option_change,
        )
        self.convert_cb.pack(side=LEFT, padx=(0, 10))

        # --- Row 5: Auto Refresh ---
        options_frame_auto = ttk.Frame(main_frame, padding=(0, 0))
        options_frame_auto.grid(row=5, column=0, sticky="w", pady=(0, 5))

        if WATCHDOG_AVAILABLE:
            auto_refresh_cb = ttk.Checkbutton(
                options_frame_auto,
                text="Auto Refresh",
                variable=self.auto_refresh_var,
                command=self.toggle_observer,
            )
            auto_refresh_cb.pack(side=LEFT)
        else:
            ttk.Label(
                options_frame_auto, text="Auto Refresh N/A (requires 'watchdog')"
            ).pack(side=LEFT)

        # --- Row 6: Action Buttons ---
        action_frame = ttk.Frame(main_frame)
        action_frame.grid(row=6, column=0, sticky="w", pady=(8, 5))  # Grid row updated

        refresh_btn = ttk.Button(
            action_frame, text="Refresh Output", command=self.refresh_files, width=15
        )  # Changed text
        refresh_btn.pack(side=LEFT, padx=(0, 10))

        open_btn = ttk.Button(
            action_frame,
            text="Open Output Folder",
            command=self.open_temp_folder,
            width=18,
        )
        open_btn.pack(side=LEFT)

        # --- Row 7: Status Display ---
        status_frame = ttk.Frame(main_frame)
        status_frame.grid(
            row=7, column=0, sticky="nsew", pady=(5, 0)
        )  # Grid row updated
        status_frame.rowconfigure(0, weight=1)
        status_frame.columnconfigure(0, weight=1)
        main_frame.rowconfigure(7, weight=1)  # Grid row updated

        self.status_text = Text(
            status_frame,
            height=8,
            wrap=WORD,
            relief=FLAT,
            borderwidth=1,
            font=("Consolas", 9),  # Keep monospace font for logs
            # Standard Text bg/fg will be used
        )
        self.status_text.grid(row=0, column=0, sticky="nsew")
        status_scrollbar = ttk.Scrollbar(
            status_frame, orient=VERTICAL, command=self.status_text.yview
        )  # ttk.Scrollbar
        status_scrollbar.grid(row=0, column=1, sticky="ns")
        self.status_text.config(yscrollcommand=status_scrollbar.set)

        # Set initial states
        if self.auto_refresh_var.get() and self.selected_project:
            self.start_observer()

    # --- Option Callbacks ---
    def on_option_change(self):
        """Called when most checkbox states change."""
        # Trigger refresh immediately (optional)
        if self.selected_project:
            self.refresh_files()

    # Removed on_files_json_change method as tree checkbox is now independent

    def on_copy_individual_change(self):
        """Called when 'Copy individual files' checkbox changes."""
        self.update_dependent_checkbox_states()
        self.on_option_change()  # Also trigger refresh

    def update_dependent_checkbox_states(self):
        """Enable/disable 'Convert' checkbox based on 'Copy' checkbox."""
        # Only 'Convert' checkbox state depends on 'Copy' checkbox state
        if self.copy_individual_files_var.get():
            self.convert_cb.config(state=tk.NORMAL)
        else:
            self.convert_cb.config(state=tk.DISABLED)
            # self.convert_copied_files_var.set(False) # Optionally uncheck convert when disabling copy

        # REMOVED logic that disabled tree_json_cb based on files_json_cb

    # --- Logging, Project Load/Save (Unchanged) ---
    def log_status(self, message):
        timestamp = time.strftime("%H:%M:%S")
        full_message = f"[{timestamp}] {message}\n"

        def update_widget():
            try:
                if self.status_text.winfo_exists():
                    self.status_text.insert(END, full_message)
                    self.status_text.see(END)
            except tk.TclError:
                pass

        try:
            if threading.current_thread() is threading.main_thread():
                update_widget()
            else:
                if self.root.winfo_exists():
                    self.root.after(0, update_widget)
        except Exception as e:
            print(f"Error logging status: {e}")
        print(f"LOG: {message}")  # Keep console log

    def clear_status(self):
        try:
            if self.status_text.winfo_exists():
                self.status_text.delete(1.0, END)
        except tk.TclError:
            pass

    def load_projects(self):
        if not self.projects_file.exists():
            return []
        try:
            with open(self.projects_file, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            messagebox.showerror(
                "Load Error",
                f"Could not load projects:\n{e}",
                parent=self.root if self.root else None,
            )
            return []

    def save_projects(self):
        try:
            with open(self.projects_file, "w", encoding="utf-8") as f:
                json.dump(self.projects, f, indent=4)
        except Exception as e:
            self.log_status(f"Error saving projects: {e}")
            messagebox.showerror(
                "Save Error", f"Could not save projects: {e}", parent=self.root
            )

    # --- Project Management (Unchanged) ---
    def create_new_project(self):
        project_name = simpledialog.askstring(
            "New Project", "Enter Project Name:", parent=self.root
        )
        if not project_name or not project_name.strip():
            return
        project_name = project_name.strip()
        if any(p["project_name"] == project_name for p in self.projects):
            return messagebox.showerror(
                "Error", f"Project '{project_name}' already exists.", parent=self.root
            )
        selected_dir = filedialog.askdirectory(
            title=f"Select Root Directory for '{project_name}'", parent=self.root
        )
        if not selected_dir:
            return
        selected_path = Path(selected_dir).resolve()
        new_project = {
            "project_name": project_name,
            "directory": str(selected_path),
            "prompt_rules": "",
        }
        self.projects.append(new_project)
        self.save_projects()
        include_path = selected_path / ".include"
        if not include_path.exists():
            try:
                with open(include_path, "w", encoding="utf-8") as f:
                    f.write("# Include patterns (*.py, *.html)\n")
                self.log_status(f"Created default .include in {selected_path}")
            except Exception as e:
                self.log_status(f"Warn: Could not create .include: {e}")
        self.project_dropdown["values"] = [p["project_name"] for p in self.projects]
        self.project_dropdown.set(project_name)
        self.load_selected_project()

    def delete_project(self):
        if not self.selected_project:
            return messagebox.showerror(
                "Error", "No project selected.", parent=self.root
            )
        name = self.selected_project["project_name"]
        if messagebox.askyesno(
            "Confirm Delete", f"Delete project '{name}' entry?", parent=self.root
        ):
            self.stop_observer()
            self.projects = [p for p in self.projects if p["project_name"] != name]
            self.save_projects()
            self.selected_project = None
            self.project_dropdown.set("")
            self.project_dropdown["values"] = [p["project_name"] for p in self.projects]
            self.dir_label.config(text="No project selected")
            self.prompt_rules_input.delete(0, END)
            self.include_patterns = []
            self.gitignore_spec = None
            self.clear_status()
            self.clear_temp_folder()
            self.log_status(f"Project '{name}' deleted.")

    def load_selected_project(self, event=None):
        name = self.project_dropdown.get()
        project = next((p for p in self.projects if p["project_name"] == name), None)
        if project:
            self.stop_observer()
            self.selected_project = project
            self.dir_label.config(text=project["directory"])
            self.prompt_rules_input.delete(0, END)
            self.prompt_rules_input.insert(0, project.get("prompt_rules", ""))
            self.clear_status()
            self.log_status(f"Loading project: {name}...")
            self.load_gitignore()
            self.load_include_patterns()
            self.save_prompt_rules()  # Save/write prompt.txt
            self.refresh_files()  # Refresh based on new project and default options
            if self.auto_refresh_var.get() and WATCHDOG_AVAILABLE:
                self.start_observer()
        else:
            self.log_status(f"Error: Could not find project data for '{name}'")
            self.selected_project = None
            self.dir_label.config(text="Project not found")
            self.prompt_rules_input.delete(0, END)

    # --- Prompt Rules (Unchanged) ---
    def save_prompt_rules(self):
        if not self.selected_project:
            return
        user_prompt = self.prompt_rules_input.get().strip()
        if self.selected_project.get("project_name"):
            self.selected_project["prompt_rules"] = user_prompt
            self.save_projects()
        prompt_file_path = self.temp_dir / "prompt.txt"
        try:
            if user_prompt:
                with open(prompt_file_path, "w", encoding="utf-8") as f:
                    f.write(user_prompt)
            elif prompt_file_path.exists():
                prompt_file_path.unlink()
        except Exception as e:
            self.log_status(f"Error writing/deleting prompt file: {e}")

    # --- Pattern Loading (Unchanged) ---
    def load_gitignore(self):
        self.gitignore_spec = None
        path = (
            Path(self.selected_project["directory"]) / ".gitignore"
            if self.selected_project and PATHSPEC_AVAILABLE
            else None
        )
        if path and path.is_file():
            try:
                with open(path, "r", encoding="utf-8", errors="ignore") as f:
                    self.gitignore_spec = pathspec.PathSpec.from_lines(
                        pathspec.patterns.GitWildMatchPattern, f
                    )
                self.log_status("Loaded .gitignore patterns.")
            except Exception as e:
                self.log_status(f"Error reading .gitignore: {e}")

    def load_include_patterns(self):
        self.include_patterns = []
        path = (
            Path(self.selected_project["directory"]) / ".include"
            if self.selected_project
            else None
        )
        if path and path.is_file():
            try:
                with open(path, "r", encoding="utf-8", errors="ignore") as f:
                    self.include_patterns = [
                        ln.strip()
                        for ln in f
                        if ln.strip() and not ln.strip().startswith("#")
                    ]
                self.log_status(
                    f"Loaded {len(self.include_patterns)} patterns from .include."
                )
            except Exception as e:
                self.log_status(f"Error reading .include: {e}")

    # --- File Filtering (Unchanged) ---
    def should_ignore(self, path_obj: Path, is_dir: bool) -> bool:
        if not self.selected_project:
            return False
        project_dir = Path(self.selected_project["directory"])
        if ".git" in path_obj.parts:
            return True
        try:
            if path_obj.resolve() == self.temp_dir.resolve():
                return True
        except OSError:
            pass
        if self.gitignore_spec:
            try:
                rel_path = path_obj.relative_to(project_dir)
                path_str = str(rel_path.as_posix()) + ("/" if is_dir else "")
                return self.gitignore_spec.match_file(path_str)
            except ValueError:
                return False
            except Exception:
                return False  # Ignore errors during matching for robustness
        return False

    def should_include(self, path_obj: Path) -> bool:
        if not self.include_patterns:
            return True
        name = path_obj.name
        for pattern in self.include_patterns:
            try:
                if fnmatch.fnmatch(name, pattern):
                    return True
            except Exception:
                pass  # Ignore pattern errors
        return False

    # --- Helpers for JSON Structure (Unchanged) ---
    def add_to_files_structure(self, structure, path_parts, content):
        """Adds content to the nested dictionary for project_files.json."""
        current_level = structure
        for i, part in enumerate(path_parts):
            is_last_part = i == len(path_parts) - 1
            if is_last_part:  # File
                if isinstance(current_level.get(part), dict):
                    pass  # Conflict: Dir exists
                else:
                    current_level[part] = content
            else:  # Directory
                if part not in current_level:
                    current_level[part] = {}
                elif not isinstance(current_level[part], dict):
                    return  # Conflict: File exists
                current_level = current_level[part]

    def add_to_tree_structure(self, tree, path_parts):
        """Adds nodes to the nested dictionary for project_file_tree.json."""
        current_level = tree
        for i, part in enumerate(path_parts):
            is_last_part = i == len(path_parts) - 1
            if is_last_part:  # File node
                # Use None to mark a file, don't overwrite existing dir
                if part not in current_level or not isinstance(
                    current_level.get(part), dict
                ):
                    current_level[part] = None
            else:  # Directory node
                if part not in current_level:
                    current_level[part] = {}
                elif not isinstance(current_level[part], dict):
                    return  # Conflict: file exists
                current_level = current_level[part]

    # --- Temp Folder (Unchanged - Robust Clear) ---
    def clear_temp_folder(self):
        if not self.temp_dir.exists():
            try:
                self.temp_dir.mkdir(exist_ok=True)
                return
            except Exception as e:
                self.log_status(f"Error creating temp directory: {e}")
                return
        # self.log_status(f"Clearing temp folder: {self.temp_dir}")
        for item in self.temp_dir.iterdir():
            try:
                if item.is_file() or item.is_symlink():
                    item.unlink()
                elif item.is_dir():
                    shutil.rmtree(item)
            except Exception as e:
                self.log_status(f"Warn: Error deleting temp item {item.name}: {e}")

    def open_temp_folder(self):
        if not self.temp_dir.exists():
            try:
                self.temp_dir.mkdir(parents=True, exist_ok=True)
                self.log_status(f"Created missing temp directory: {self.temp_dir}")
            except Exception as e:
                messagebox.showerror(
                    "Error",
                    f"Temp directory missing/could not be created:\n{e}",
                    parent=self.root,
                )
                return
        try:
            path = str(self.temp_dir.resolve())
            self.log_status(f"Opening folder: {path}")
            if sys.platform == "win32":
                os.startfile(path)
            elif sys.platform == "darwin":
                subprocess.run(["open", path], check=True)
            else:
                subprocess.run(["xdg-open", path], check=True)
        except Exception as e:
            messagebox.showerror(
                "Error",
                f"Could not open temp folder:\n{e}\nPath: {path}",
                parent=self.root,
            )

    # --- Core File Refresh ---
    def refresh_files(self):
        """Refreshes output based on selected options (JSON files, individual files)."""
        if not self.selected_project:
            self.log_status("Refresh skipped: No project selected.")
            return
        self.log_status("Refreshing output...")
        start_time = time.time()
        self.clear_temp_folder()  # Start clean

        source_dir_str = self.selected_project.get("directory")
        if not source_dir_str:
            self.log_status("Error: Project directory not set.")
            return
        source_dir = Path(source_dir_str)
        if not source_dir.is_dir():
            self.log_status(f"Error: Source directory not found: {source_dir}")
            messagebox.showerror(
                "Error",
                f"Project source directory not found:\n{source_dir}",
                parent=self.root,
            )
            return

        # Get option states
        do_files_json = self.create_files_json_var.get()
        do_tree_json = self.create_tree_json_var.get()  # Independent check
        do_copy = self.copy_individual_files_var.get()
        do_convert = self.convert_copied_files_var.get() and do_copy

        # Initialize collectors
        project_files_data = {} if do_files_json else None
        project_tree_data = {} if do_tree_json else None
        files_in_temp = (
            set() if do_copy else None
        )  # Track names for collision if copying

        copied_count, ignored_git_count, ignored_incl_count = 0, 0, 0
        converted_count, read_error_count, collision_skips = 0, 0, 0
        files_json_entries, tree_json_nodes = 0, 0

        self.save_prompt_rules()  # Always save/write prompt.txt

        try:
            for root, dirs, files in os.walk(source_dir, topdown=True):
                root_path = Path(root)
                original_dir_count = len(dirs)
                filtered_dirs = [
                    d for d in dirs if not self.should_ignore(root_path / d, True)
                ]
                ignored_git_count += original_dir_count - len(filtered_dirs)
                dirs[:] = filtered_dirs  # Modify dirs in-place for os.walk

                # --- Process files in current directory ---
                for filename in files:
                    file_path = root_path / filename
                    file_content = None  # Cache content

                    # 1. Check .gitignore / .include
                    if self.should_ignore(file_path, False):
                        ignored_git_count += 1
                        continue
                    if self.include_patterns and not self.should_include(file_path):
                        ignored_incl_count += 1
                        continue

                    # --- Passed filters ---
                    relative_path = file_path.relative_to(source_dir)

                    # --- Action: Create Files JSON (with content) ---
                    if do_files_json:
                        try:
                            if file_content is None:  # Read only if not already read
                                with open(
                                    file_path, "r", encoding="utf-8", errors="ignore"
                                ) as f:
                                    file_content = f.read()
                            self.add_to_files_structure(
                                project_files_data, relative_path.parts, file_content
                            )
                            files_json_entries += 1
                        except Exception as read_err:
                            self.log_status(
                                f"Warn: Could not read '{relative_path}' for Files JSON:"
                                f" {read_err}"
                            )
                            read_error_count += 1

                    # --- Action: Create Tree-Only JSON ---
                    # Note: This runs even if files_json failed reading, just adds the node
                    if do_tree_json:
                        self.add_to_tree_structure(
                            project_tree_data, relative_path.parts
                        )
                        tree_json_nodes += 1  # Simple count of file nodes added

                    # --- Action: Individual File Copy/Convert ---
                    if do_copy:
                        path_prefix = "-".join(relative_path.parts[:-1])
                        unique_flat_filename = (
                            f"{path_prefix}-{filename}" if path_prefix else filename
                        )
                        target_flat_filename = (
                            unique_flat_filename + ".txt"
                            if do_convert
                            else unique_flat_filename
                        )

                        if target_flat_filename in files_in_temp:
                            self.log_status(
                                f"Warn: Skipping copy '{relative_path}' ->"
                                f" '{target_flat_filename}' (collision)."
                            )
                            collision_skips += 1
                            continue

                        source_copy_path = self.temp_dir / unique_flat_filename
                        try:
                            shutil.copy2(file_path, source_copy_path)
                            copied_count += 1
                            final_dest_path = source_copy_path
                            if do_convert:
                                target_dest_path = self.temp_dir / target_flat_filename
                                try:
                                    source_copy_path.rename(target_dest_path)
                                    converted_count += 1
                                    final_dest_path = target_dest_path
                                except Exception as rename_err:
                                    self.log_status(
                                        f"Error renaming {source_copy_path.name}:"
                                        f" {rename_err}"
                                    )
                                    # Decrement count only if copy succeeded before rename failed
                                    if source_copy_path.exists():
                                        copied_count -= 1
                                        try:
                                            source_copy_path.unlink()
                                        except OSError:
                                            pass  # Might fail if rename partially worked
                                    continue  # Skip adding to set
                            files_in_temp.add(final_dest_path.name)
                        except Exception as copy_err:
                            self.log_status(
                                f"Error copying '{relative_path}': {copy_err}"
                            )
                            read_error_count += (
                                1  # Treat copy error like a read error for summary
                            )
                            # Decrement count only if it was incremented for this file attempt
                            copied_count = max(
                                0, copied_count - 1
                            )  # Prevent going negative if copy failed immediately
                            if (
                                source_copy_path.exists()
                            ):  # Cleanup partially copied file
                                try:
                                    source_copy_path.unlink()
                                except OSError:
                                    pass

            # --- Post-Processing: Write JSON Files ---
            output_actions = []
            if do_files_json and project_files_data is not None:
                json_output_path = self.temp_dir / "project_files.json"  # Renamed file
                self.log_status(
                    f"Writing content JSON ({files_json_entries} entries)..."
                )
                try:
                    with open(json_output_path, "w", encoding="utf-8") as f:
                        json.dump(project_files_data, f, indent=4)
                    output_actions.append(f"Created {json_output_path.name}")
                except Exception as write_err:
                    self.log_status(
                        f"Error writing {json_output_path.name}: {write_err}"
                    )
                    output_actions.append(f"Failed {json_output_path.name}")

            # Independent check for tree JSON
            if do_tree_json and project_tree_data is not None:
                tree_output_path = self.temp_dir / "project_file_tree.json"  # New file
                # Simple node count isn't very informative, maybe count top-level keys?
                self.log_status("Writing tree JSON...")
                try:
                    with open(tree_output_path, "w", encoding="utf-8") as f:
                        json.dump(project_tree_data, f, indent=4)
                    output_actions.append(f"Created {tree_output_path.name}")
                except Exception as write_err:
                    self.log_status(
                        f"Error writing {tree_output_path.name}: {write_err}"
                    )
                    output_actions.append(f"Failed {tree_output_path.name}")

            # --- Final Logging ---
            duration = time.time() - start_time
            ignored_total = ignored_git_count + ignored_incl_count
            summary = []
            if do_files_json:
                summary.append(f"FilesJSON Entries: {files_json_entries}")
            if do_tree_json:
                summary.append("TreeJSON Created")  # Simple confirmation
            if do_copy:
                summary.append(f"Copied: {copied_count}")
            if do_convert:
                summary.append(f"Converted: {converted_count}")
            summary.append(
                f"Skipped: {ignored_total} ({ignored_git_count} gitignore,"
                f" {ignored_incl_count} include)"
            )
            if collision_skips > 0:
                summary.append(f"CopyCollisions: {collision_skips}")
            if read_error_count > 0:
                summary.append(f"Read/Copy Errors: {read_error_count}")

            action_str = (
                ", ".join(output_actions)
                if output_actions
                else "No output files generated."
            )
            if do_copy:
                action_str = (
                    action_str + f" (+{copied_count} individual files)"
                    if output_actions
                    else f"Copied {copied_count} individual files."
                )

            self.log_status(f"Refresh finished ({duration:.2f}s). {action_str}")
            self.log_status(f"Summary: {', '.join(summary)}.")

        except Exception as e:
            self.log_status(f"Critical Error during file refresh: {str(e)}")
            traceback.print_exc()

    # --- .include Editor (Unchanged) ---
    def edit_include_file(self):
        if not self.selected_project:
            return messagebox.showerror(
                "Error", "No project selected.", parent=self.root
            )
        project_dir = Path(self.selected_project["directory"])
        include_path = project_dir / ".include"
        if not include_path.is_file():
            try:
                with open(include_path, "w", encoding="utf-8") as f:
                    f.write("# Include patterns (*.py, *.html)\n")
                self.log_status(f"Created missing .include file: {include_path}")
            except Exception as e:
                messagebox.showerror(
                    "Error", f"Could not create .include file:\n{e}", parent=self.root
                )
                return
        editor_win = Toplevel(self.root)
        editor_win.title(f"Edit .include - {self.selected_project['project_name']}")
        editor_win.geometry("500x450")
        editor_win.transient(self.root)
        editor_win.grab_set()
        editor_frame = ttk.Frame(editor_win, padding=10)
        editor_frame.pack(fill=BOTH, expand=True)
        text_frame = ttk.Frame(editor_frame)
        text_frame.pack(fill=BOTH, expand=True, pady=(0, 10))
        text_frame.rowconfigure(0, weight=1)
        text_frame.columnconfigure(0, weight=1)
        include_text_widget_editor = Text(
            text_frame, wrap=WORD, undo=True, font=("Consolas", 10)
        )
        include_text_widget_editor.grid(row=0, column=0, sticky="nsew")
        scrollbar = ttk.Scrollbar(
            text_frame, orient=VERTICAL, command=include_text_widget_editor.yview
        )
        scrollbar.grid(row=0, column=1, sticky="ns")
        include_text_widget_editor.config(yscrollcommand=scrollbar.set)
        self.include_editor_text_widget = include_text_widget_editor
        try:
            with open(include_path, "r", encoding="utf-8") as f:
                include_text_widget_editor.insert("1.0", f.read())
            include_text_widget_editor.edit_reset()
        except Exception as e:
            messagebox.showerror(
                "Error", f"Could not read .include file:\n{e}", parent=editor_win
            )
            self.include_editor_text_widget = None
            editor_win.destroy()
            return
        button_frame = ttk.Frame(editor_frame)
        button_frame.pack(fill=X)

        def save_changes():
            content = include_text_widget_editor.get("1.0", END).strip()
            try:
                with open(include_path, "w", encoding="utf-8") as f:
                    f.write(content + ("\n" if content else ""))
                self.log_status(".include file saved.")
                self.load_include_patterns()
                self.refresh_files()
                self.include_editor_text_widget = None
                editor_win.destroy()
            except Exception as e:
                messagebox.showerror(
                    "Save Error",
                    f"Could not save .include file:\n{e}",
                    parent=editor_win,
                )

        def cancel_changes():
            self.include_editor_text_widget = None
            editor_win.destroy()

        save_btn = ttk.Button(button_frame, text="Save & Close", command=save_changes)
        save_btn.pack(side=RIGHT, padx=5)
        cancel_btn = ttk.Button(button_frame, text="Cancel", command=cancel_changes)
        cancel_btn.pack(side=RIGHT)
        include_text_widget_editor.focus_set()
        editor_win.protocol(
            "WM_DELETE_WINDOW", cancel_changes
        )  # Ensure cleanup if closed with 'X'
        editor_win.wait_window()  # Wait for editor to close

    # --- File Watching Control (Unchanged) ---
    def start_observer(self):
        if not WATCHDOG_AVAILABLE:
            self.auto_refresh_var.set(False)
            return
        if (
            not self.selected_project
            or not self.auto_refresh_var.get()
            or (self.observer_thread and self.observer_thread.is_alive())
        ):
            return
        self.watch_path = Path(self.selected_project["directory"])
        if not self.watch_path.is_dir():
            self.log_status(f"Cannot watch non-existent directory: {self.watch_path}")
            self.auto_refresh_var.set(False)
            return
        while not self.callback_queue.empty():
            try:
                self.callback_queue.get_nowait()
            except queue.Empty:
                break
        self.observer = Observer()
        event_handler = ProjectChangeHandler(self.callback_queue)
        try:
            self.observer.schedule(event_handler, str(self.watch_path), recursive=True)
            self.observer_thread = threading.Thread(
                target=self.observer.start, daemon=True
            )
            self.observer_thread.start()
            self.log_status(f"File watching started: {self.watch_path}")
        except Exception as e:
            self.log_status(f"Error starting file observer: {e}")
            self.observer = None
            self.auto_refresh_var.set(False)

    def stop_observer(self):
        if self.observer and self.observer.is_alive():
            try:
                self.observer.stop()
                self.log_status("File watching stopped.")
            except Exception as e:
                self.log_status(f"Error stopping observer: {e}")
        self.observer = None
        self.observer_thread = None
        self.watch_path = None

    def toggle_observer(self):
        if self.auto_refresh_var.get():
            if self.selected_project:
                self.start_observer()
        else:
            self.stop_observer()

    def check_queue(self):
        try:
            message = self.callback_queue.get_nowait()
            if (
                message == "refresh"
                and self.selected_project
                and self.auto_refresh_var.get()
            ):
                self.log_status("Auto-refresh triggered...")
                self.refresh_files()
        except queue.Empty:
            pass
        except Exception as e:
            self.log_status(f"Error checking observer queue: {e}")
        finally:
            if self.root.winfo_exists():
                self.root.after(250, self.check_queue)  # Check every 250ms

    # --- App Lifecycle (Unchanged) ---
    def on_closing(self):
        self.log_status("Closing application...")
        self.stop_observer()
        self.root.destroy()

    def run(self):
        self.root.mainloop()


# --- Main Execution Block ---
if __name__ == "__main__":
    app = Progomatter()
    app.run()
