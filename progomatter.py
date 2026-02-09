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
import re
# --- UI and Core Logic Imports ---
import tkinter as tk
from tkinter import ttk
from tkinter import filedialog, messagebox, simpledialog, BooleanVar, Text, Toplevel, Scrollbar
from tkinter.constants import (
    BOTH,
    END,
    WORD,
    VERTICAL,
    HORIZONTAL,
    LEFT,
    RIGHT,
    TOP,
    BOTTOM,
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
        self.root.title("Progomatter v4.0")
        self.root.geometry("600x450")
        # --- Initialize variables ---
        self.projects_file = Path("projects.json")
        self.projects = self.load_projects()
        self.selected_project = None
        self.temp_dir = Path(tempfile.gettempdir()) / self.TEMP_DIR_NAME
        self.temp_dir.mkdir(exist_ok=True)  # Ensure it exists
        self.gitignore_spec = None
        self.include_patterns = []
        self.file_notes = {}  # Dictionary to store notes: {relative_path_str: note_text}
        # --- Tkinter Option Vars ---
        # Controls Markdown file with paths
        self.create_paths_md_var = BooleanVar(value=True)  # Default to True
        # Controls function extraction in MD file
        self.extract_functions_var = BooleanVar(value=False)
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
        edit_context_btn = ttk.Button(
            project_row_frame,
            text="Edit Context",
            command=self.edit_context_file,
            width=11,
        )
        edit_context_btn.pack(side=LEFT, padx=(0, 3))
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
        # --- Row 2: File Notes Editor Button ---
        notes_frame = ttk.Frame(main_frame)
        notes_frame.grid(row=2, column=0, sticky="ew", pady=3)
        edit_notes_btn = ttk.Button(
            notes_frame,
            text="Edit File Notes",
            command=self.edit_file_notes,
            width=15,
        )
        edit_notes_btn.pack(side=LEFT)
        ttk.Label(
            notes_frame,
            text="Add context notes to individual files (saved between refreshes)",
            foreground="gray"
        ).pack(side=LEFT, padx=(10, 0))
        # --- Row 3: Markdown Output Options ---
        options_frame_md = ttk.Frame(main_frame, padding=(0, 5))
        options_frame_md.grid(row=3, column=0, sticky="w", pady=(5, 0))
        
        self.paths_md_cb = ttk.Checkbutton(
            options_frame_md,
            text="Create paths MD (project_file_names_and_locations.md)",
            variable=self.create_paths_md_var,
            command=self.on_paths_md_change,
        )
        self.paths_md_cb.pack(side=LEFT, padx=(0, 10))
        
        self.extract_functions_cb = ttk.Checkbutton(
            options_frame_md,
            text="Extract function summaries",
            variable=self.extract_functions_var,
            command=self.on_option_change,
        )
        self.extract_functions_cb.pack(side=LEFT, padx=(0, 10))
        # --- Row 4: Individual File Output Options ---
        options_frame_files = ttk.Frame(main_frame, padding=(0, 0))
        options_frame_files.grid(row=4, column=0, sticky="w", pady=(0, 0))
        copy_individual_cb = ttk.Checkbutton(
            options_frame_files,
            text="Copy individual files",
            variable=self.copy_individual_files_var,
            command=self.on_copy_individual_change,
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
        action_frame.grid(row=6, column=0, sticky="w", pady=(8, 5))
        refresh_btn = ttk.Button(
            action_frame, text="Refresh Output", command=self.refresh_files, width=15
        )
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
        status_frame.grid(row=7, column=0, sticky="nsew", pady=(5, 0))
        status_frame.rowconfigure(0, weight=1)
        status_frame.columnconfigure(0, weight=1)
        main_frame.rowconfigure(7, weight=1)
        self.status_text = Text(
            status_frame,
            height=8,
            wrap=WORD,
            relief=FLAT,
            borderwidth=1,
            font=("Consolas", 9),
        )
        self.status_text.grid(row=0, column=0, sticky="nsew")
        status_scrollbar = ttk.Scrollbar(
            status_frame, orient=VERTICAL, command=self.status_text.yview
        )
        status_scrollbar.grid(row=0, column=1, sticky="ns")
        self.status_text.config(yscrollcommand=status_scrollbar.set)
        # Set initial states
        if self.auto_refresh_var.get() and self.selected_project:
            self.start_observer()
    # --- Option Callbacks ---
    def on_option_change(self):
        """Called when most checkbox states change."""
        if self.selected_project:
            self.refresh_files()
    def on_paths_md_change(self):
        """Called when 'Create paths MD' checkbox changes."""
        self.update_dependent_checkbox_states()
        self.on_option_change()
    def on_copy_individual_change(self):
        """Called when 'Copy individual files' checkbox changes."""
        self.update_dependent_checkbox_states()
        self.on_option_change()
    def update_dependent_checkbox_states(self):
        """Enable/disable dependent checkboxes based on parent checkbox states."""
        # Convert checkbox depends on Copy checkbox
        if self.copy_individual_files_var.get():
            self.convert_cb.config(state=tk.NORMAL)
        else:
            self.convert_cb.config(state=tk.DISABLED)
        
        # Function extraction depends on MD paths checkbox
        if self.create_paths_md_var.get():
            self.extract_functions_cb.config(state=tk.NORMAL)
        else:
            self.extract_functions_cb.config(state=tk.DISABLED)
    # --- File Notes Management ---
    def load_file_notes(self):
        """Load file notes from project's .progomatter_notes.json file."""
        if not self.selected_project:
            return
        project_dir = Path(self.selected_project["directory"])
        notes_file = project_dir / ".progomatter_notes.json"
        
        if notes_file.exists():
            try:
                with open(notes_file, "r", encoding="utf-8") as f:
                    self.file_notes = json.load(f)
                self.log_status(f"Loaded {len(self.file_notes)} file notes.")
            except Exception as e:
                self.log_status(f"Error loading file notes: {e}")
                self.file_notes = {}
        else:
            self.file_notes = {}
    def save_file_notes(self):
        """Save file notes to project's .progomatter_notes.json file."""
        if not self.selected_project:
            return
        project_dir = Path(self.selected_project["directory"])
        notes_file = project_dir / ".progomatter_notes.json"
        
        try:
            with open(notes_file, "w", encoding="utf-8") as f:
                json.dump(self.file_notes, f, indent=4)
        except Exception as e:
            self.log_status(f"Error saving file notes: {e}")
    def edit_file_notes(self):
        """Open a window to edit file notes for each file in the project."""
        if not self.selected_project:
            return messagebox.showerror(
                "Error", "No project selected.", parent=self.root
            )
        
        # Collect all current files in project
        source_dir = Path(self.selected_project["directory"])
        current_files = []
        
        for root, dirs, files in os.walk(source_dir, topdown=True):
            root_path = Path(root)
            # Filter directories
            original_dir_count = len(dirs)
            filtered_dirs = [
                d for d in dirs if not self.should_ignore(root_path / d, True)
            ]
            dirs[:] = filtered_dirs
            
            for filename in files:
                file_path = root_path / filename
                if self.should_ignore(file_path, False):
                    continue
                if self.include_patterns and not self.should_include(file_path):
                    continue
                
                relative_path = file_path.relative_to(source_dir)
                current_files.append(str(relative_path.as_posix()))
        
        current_files.sort()
        
        # Create editor window
        editor_win = Toplevel(self.root)
        editor_win.title(f"Edit File Notes - {self.selected_project['project_name']}")
        editor_win.geometry("800x600")
        editor_win.transient(self.root)
        editor_win.grab_set()
        
        editor_frame = ttk.Frame(editor_win, padding=10)
        editor_frame.pack(fill=BOTH, expand=True)
        
        # Instructions
        ttk.Label(
            editor_frame,
            text="Add brief notes (1-3 sentences) to help the LLM remember important context about each file.",
            wraplength=750
        ).pack(pady=(0, 10))
        
        # Create frame for file list and note editor
        content_frame = ttk.Frame(editor_frame)
        content_frame.pack(fill=BOTH, expand=True)
        content_frame.columnconfigure(1, weight=1)
        content_frame.rowconfigure(0, weight=1)
        
        # Left: File list
        list_frame = ttk.Frame(content_frame)
        list_frame.grid(row=0, column=0, sticky="nsew", padx=(0, 10))
        
        ttk.Label(list_frame, text="Files:", font=("", 10, "bold")).pack(anchor="w")
        
        listbox_frame = ttk.Frame(list_frame)
        listbox_frame.pack(fill=BOTH, expand=True)
        
        file_listbox = tk.Listbox(listbox_frame, width=40, font=("Consolas", 9))
        file_listbox.pack(side=LEFT, fill=BOTH, expand=True)
        
        list_scrollbar = ttk.Scrollbar(listbox_frame, orient=VERTICAL, command=file_listbox.yview)
        list_scrollbar.pack(side=RIGHT, fill=Y)
        file_listbox.config(yscrollcommand=list_scrollbar.set)
        
        # Populate file list with indicator for files with notes
        for file_path in current_files:
            display_text = file_path
            if file_path in self.file_notes and self.file_notes[file_path].strip():
                display_text = "📝 " + display_text
            file_listbox.insert(END, display_text)
        
        # Right: Note editor
        note_frame = ttk.Frame(content_frame)
        note_frame.grid(row=0, column=1, sticky="nsew")
        note_frame.rowconfigure(1, weight=1)
        note_frame.columnconfigure(0, weight=1)
        
        current_file_label = ttk.Label(note_frame, text="Select a file", font=("", 10, "bold"))
        current_file_label.grid(row=0, column=0, sticky="w", pady=(0, 5))
        
        text_frame = ttk.Frame(note_frame)
        text_frame.grid(row=1, column=0, sticky="nsew")
        text_frame.rowconfigure(0, weight=1)
        text_frame.columnconfigure(0, weight=1)
        
        note_text = Text(text_frame, wrap=WORD, font=("", 10), height=10)
        note_text.grid(row=0, column=0, sticky="nsew")
        
        note_scrollbar = ttk.Scrollbar(text_frame, orient=VERTICAL, command=note_text.yview)
        note_scrollbar.grid(row=0, column=1, sticky="ns")
        note_text.config(yscrollcommand=note_scrollbar.set)
        
        char_count_label = ttk.Label(note_frame, text="0 characters", foreground="gray")
        char_count_label.grid(row=2, column=0, sticky="e", pady=(5, 0))
        
        # Track current selection
        current_selection = [None]  # Use list to allow modification in nested function
        
        def update_char_count(event=None):
            content = note_text.get("1.0", END).strip()
            char_count_label.config(text=f"{len(content)} characters")
        
        def save_current_note():
            """Save the currently edited note."""
            if current_selection[0] is not None:
                file_path = current_files[current_selection[0]]
                note_content = note_text.get("1.0", END).strip()
                if note_content:
                    self.file_notes[file_path] = note_content
                elif file_path in self.file_notes:
                    del self.file_notes[file_path]
        
        def on_file_select(event):
            """Load note for selected file."""
            selection = file_listbox.curselection()
            if not selection:
                return
            
            # Save previous note
            save_current_note()
            
            # Load new note
            idx = selection[0]
            current_selection[0] = idx
            file_path = current_files[idx]
            
            current_file_label.config(text=f"Note for: {file_path}")
            
            note_text.delete("1.0", END)
            if file_path in self.file_notes:
                note_text.insert("1.0", self.file_notes[file_path])
            
            update_char_count()
        
        file_listbox.bind("<<ListboxSelect>>", on_file_select)
        note_text.bind("<KeyRelease>", update_char_count)
        
        # Button frame
        button_frame = ttk.Frame(editor_frame)
        button_frame.pack(fill=X, pady=(10, 0))
        
        def save_and_close():
            save_current_note()
            self.save_file_notes()
            self.log_status("File notes saved.")
            self.refresh_files()  # Refresh to include notes in MD
            editor_win.destroy()
        
        def cancel():
            editor_win.destroy()
        
        ttk.Button(button_frame, text="Save & Close", command=save_and_close).pack(side=RIGHT, padx=5)
        ttk.Button(button_frame, text="Cancel", command=cancel).pack(side=RIGHT)
        
        editor_win.protocol("WM_DELETE_WINDOW", cancel)
        editor_win.wait_window()
    # --- Function Extraction Helpers ---
    def extract_functions_from_file(self, file_path: Path) -> list:
        """
        Extract function definitions from code files.
        Supports: .py, .gd, .rs, .gdshader
        Returns: List of function signature strings
        """
        extension = file_path.suffix.lower()
        functions = []
        
        try:
            with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
                content = f.read()
            
            if extension == '.py':
                # Python: def function_name(params):
                pattern = r'^\s*def\s+(\w+)\s*\((.*?)\):'
                matches = re.finditer(pattern, content, re.MULTILINE)
                for match in matches:
                    func_name = match.group(1)
                    params = match.group(2).strip()
                    functions.append(f"def {func_name}({params})")
            
            elif extension == '.gd':
                # GDScript: func function_name(params):
                pattern = r'^\s*func\s+(\w+)\s*\((.*?)\):'
                matches = re.finditer(pattern, content, re.MULTILINE)
                for match in matches:
                    func_name = match.group(1)
                    params = match.group(2).strip()
                    functions.append(f"func {func_name}({params})")
            
            elif extension == '.rs':
                # Rust: fn function_name(params) -> return_type {
                pattern = r'^\s*(?:pub\s+)?(?:async\s+)?fn\s+(\w+)\s*\((.*?)\)(?:\s*->\s*([^{]+))?'
                matches = re.finditer(pattern, content, re.MULTILINE)
                for match in matches:
                    func_name = match.group(1)
                    params = match.group(2).strip()
                    return_type = match.group(3).strip() if match.group(3) else ""
                    if return_type:
                        functions.append(f"fn {func_name}({params}) -> {return_type}")
                    else:
                        functions.append(f"fn {func_name}({params})")
            
            elif extension == '.gdshader':
                # GLSL/Godot Shader: void/float/vec2/etc function_name(params) {
                pattern = r'^\s*(?:void|float|int|vec[234]|mat[234]|bool)\s+(\w+)\s*\((.*?)\)'
                matches = re.finditer(pattern, content, re.MULTILINE)
                for match in matches:
                    func_name = match.group(1)
                    params = match.group(2).strip()
                    functions.append(f"{func_name}({params})")
        
        except Exception as e:
            self.log_status(f"Error extracting functions from {file_path.name}: {e}")
        
        return functions
    # --- Logging, Project Load/Save ---
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
        print(f"LOG: {message}")
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
    # --- Project Management ---
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
        }
        self.projects.append(new_project)
        self.save_projects()
        include_path = selected_path / ".include"
        if not include_path.exists():
            try:
                with open(include_path, "w", encoding="utf-8") as f:
                    f.write("# Include patterns (*.py, *.html, *.gd, *.rs, *.gdshader)\n")
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
            self.include_patterns = []
            self.gitignore_spec = None
            self.file_notes = {}
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
            self.clear_status()
            self.log_status(f"Loading project: {name}...")
            self.load_gitignore()
            self.load_include_patterns()
            self.load_file_notes()
            self.copy_context_file_to_output()
            self.refresh_files()
            if self.auto_refresh_var.get() and WATCHDOG_AVAILABLE:
                self.start_observer()
        else:
            self.log_status(f"Error: Could not find project data for '{name}'")
            self.selected_project = None
            self.dir_label.config(text="Project not found")
    # --- Context File Management ---
    def edit_context_file(self):
        """Open editor for 'read this first.md' file."""
        if not self.selected_project:
            return messagebox.showerror(
                "Error", "No project selected.", parent=self.root
            )
        
        project_dir = Path(self.selected_project["directory"])
        context_path = project_dir / "read this first.md"
        
        editor_win = Toplevel(self.root)
        editor_win.title(f"Edit Context - {self.selected_project['project_name']}")
        editor_win.geometry("700x500")
        editor_win.transient(self.root)
        editor_win.grab_set()
        
        editor_frame = ttk.Frame(editor_win, padding=10)
        editor_frame.pack(fill=BOTH, expand=True)
        
        # Instructions
        instruction_text = (
            "This file contains important context for the LLM about your project.\n"
            "Include key points from prior conversations, project goals, coding standards, etc.\n"
            "This file will be copied to the output folder and should be read first by the LLM."
        )
        ttk.Label(editor_frame, text=instruction_text, wraplength=650).pack(pady=(0, 10))
        
        text_frame = ttk.Frame(editor_frame)
        text_frame.pack(fill=BOTH, expand=True, pady=(0, 10))
        text_frame.rowconfigure(0, weight=1)
        text_frame.columnconfigure(0, weight=1)
        
        context_text_widget = Text(text_frame, wrap=WORD, undo=True, font=("", 10))
        context_text_widget.grid(row=0, column=0, sticky="nsew")
        
        scrollbar = ttk.Scrollbar(text_frame, orient=VERTICAL, command=context_text_widget.yview)
        scrollbar.grid(row=0, column=1, sticky="ns")
        context_text_widget.config(yscrollcommand=scrollbar.set)
        
        # Load existing content if file exists
        if context_path.exists():
            try:
                with open(context_path, "r", encoding="utf-8") as f:
                    context_text_widget.insert("1.0", f.read())
                context_text_widget.edit_reset()
            except Exception as e:
                messagebox.showerror(
                    "Error", f"Could not read context file:\n{e}", parent=editor_win
                )
        
        button_frame = ttk.Frame(editor_frame)
        button_frame.pack(fill=X)
        
        def save_changes():
            content = context_text_widget.get("1.0", END).strip()
            try:
                if content:
                    with open(context_path, "w", encoding="utf-8") as f:
                        f.write(content + "\n")
                    self.log_status("Context file saved.")
                    self.copy_context_file_to_output()
                elif context_path.exists():
                    # Don't delete if empty, just warn
                    messagebox.showwarning(
                        "Empty File",
                        "Context file is empty. It won't be deleted but won't provide context to the LLM.",
                        parent=editor_win
                    )
                editor_win.destroy()
            except Exception as e:
                messagebox.showerror(
                    "Save Error",
                    f"Could not save context file:\n{e}",
                    parent=editor_win,
                )
        
        def cancel_changes():
            editor_win.destroy()
        
        ttk.Button(button_frame, text="Save & Close", command=save_changes).pack(side=RIGHT, padx=5)
        ttk.Button(button_frame, text="Cancel", command=cancel_changes).pack(side=RIGHT)
        
        context_text_widget.focus_set()
        editor_win.protocol("WM_DELETE_WINDOW", cancel_changes)
        editor_win.wait_window()
    def copy_context_file_to_output(self):
        """Copy 'read this first.md' to output folder if it exists."""
        if not self.selected_project:
            return
        
        project_dir = Path(self.selected_project["directory"])
        context_path = project_dir / "read this first.md"
        
        if context_path.exists():
            try:
                dest_path = self.temp_dir / "read this first.md"
                shutil.copy2(context_path, dest_path)
                self.log_status("Copied 'read this first.md' to output folder.")
            except Exception as e:
                self.log_status(f"Error copying context file: {e}")
    # --- Pattern Loading ---
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
    # --- File Filtering ---
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
                return False
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
                pass
        return False
    # --- Temp Folder ---
    def clear_temp_folder(self):
        if not self.temp_dir.exists():
            try:
                self.temp_dir.mkdir(exist_ok=True)
                return
            except Exception as e:
                self.log_status(f"Error creating temp directory: {e}")
                return
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
        """Refreshes output based on selected options (MD file, individual files)."""
        if not self.selected_project:
            self.log_status("Refresh skipped: No project selected.")
            return
        self.log_status("Refreshing output...")
        start_time = time.time()
        self.clear_temp_folder()
        
        # Copy context file first
        self.copy_context_file_to_output()
        
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
        do_paths_md = self.create_paths_md_var.get()
        do_extract_functions = self.extract_functions_var.get() and do_paths_md
        do_copy = self.copy_individual_files_var.get()
        do_convert = self.convert_copied_files_var.get() and do_copy
        # Initialize collectors
        paths_md_lines = [] if do_paths_md else None
        files_in_temp = set() if do_copy else None
        copied_count, ignored_git_count, ignored_incl_count = 0, 0, 0
        converted_count, read_error_count, collision_skips = 0, 0, 0
        md_entries = 0
        try:
            for root, dirs, files in os.walk(source_dir, topdown=True):
                root_path = Path(root)
                original_dir_count = len(dirs)
                filtered_dirs = [
                    d for d in dirs if not self.should_ignore(root_path / d, True)
                ]
                ignored_git_count += original_dir_count - len(filtered_dirs)
                dirs[:] = filtered_dirs
                for filename in files:
                    file_path = root_path / filename
                    if self.should_ignore(file_path, False):
                        ignored_git_count += 1
                        continue
                    if self.include_patterns and not self.should_include(file_path):
                        ignored_incl_count += 1
                        continue
                    relative_path = file_path.relative_to(source_dir)
                    rel_path_str = str(relative_path.as_posix())
                    
                    # --- Action: Create Paths Markdown ---
                    if do_paths_md:
                        paths_md_lines.append(f"### `{rel_path_str}`")
                        
                        # Add user note if exists
                        if rel_path_str in self.file_notes and self.file_notes[rel_path_str].strip():
                            note_lines = self.file_notes[rel_path_str].strip().split('\n')
                            paths_md_lines.append("")
                            paths_md_lines.append("**Note:**")
                            for line in note_lines:
                                paths_md_lines.append(f"> {line}")
                            paths_md_lines.append("")
                        
                        # Extract functions if enabled and file type is supported
                        if do_extract_functions:
                            ext = file_path.suffix.lower()
                            if ext in ['.py', '.gd', '.rs', '.gdshader']:
                                functions = self.extract_functions_from_file(file_path)
                                if functions:
                                    paths_md_lines.append("")
                                    paths_md_lines.append("**Functions:**")
                                    for func in functions:
                                        paths_md_lines.append(f"- `{func}`")
                        
                        paths_md_lines.append("")  # Blank line between files
                        md_entries += 1
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
                                    if source_copy_path.exists():
                                        copied_count -= 1
                                        try:
                                            source_copy_path.unlink()
                                        except OSError:
                                            pass
                                    continue
                            files_in_temp.add(final_dest_path.name)
                        except Exception as copy_err:
                            self.log_status(
                                f"Error copying '{relative_path}': {copy_err}"
                            )
                            read_error_count += 1
                            copied_count = max(0, copied_count - 1)
                            if source_copy_path.exists():
                                try:
                                    source_copy_path.unlink()
                                except OSError:
                                    pass
            # --- Post-Processing: Write Output Files ---
            output_actions = []
            
            # Write Paths Markdown
            if do_paths_md and paths_md_lines:
                md_output_path = self.temp_dir / "project_file_names_and_locations.md"
                self.log_status(f"Writing paths MD ({md_entries} files)...")
                try:
                    with open(md_output_path, "w", encoding="utf-8") as f:
                        f.write(f"# Project File Locations\n\n")
                        f.write(f"**Project:** {self.selected_project['project_name']}\n\n")
                        f.write(f"**Root:** `{source_dir}`\n\n")
                        f.write("---\n\n")
                        f.write("## Files\n\n")
                        f.write("\n".join(paths_md_lines))
                    output_actions.append(f"Created {md_output_path.name}")
                except Exception as write_err:
                    self.log_status(
                        f"Error writing {md_output_path.name}: {write_err}"
                    )
                    output_actions.append(f"Failed {md_output_path.name}")
            
            # --- Final Logging ---
            duration = time.time() - start_time
            ignored_total = ignored_git_count + ignored_incl_count
            summary = []
            if do_paths_md:
                summary.append(f"PathsMD Entries: {md_entries}")
                if do_extract_functions:
                    summary.append("(with functions)")
                notes_count = len([n for n in self.file_notes.values() if n.strip()])
                if notes_count > 0:
                    summary.append(f"({notes_count} notes)")
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
    # --- .include Editor ---
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
                    f.write("# Include patterns (*.py, *.html, *.gd, *.rs, *.gdshader)\n")
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
        editor_win.protocol("WM_DELETE_WINDOW", cancel_changes)
        editor_win.wait_window()
    # --- File Watching Control ---
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
                self.root.after(250, self.check_queue)
    # --- App Lifecycle ---
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