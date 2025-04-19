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
import ttkbootstrap as tkb # Using ttkbootstrap for UI
from ttkbootstrap.constants import * # Import constants like BOTH, END, WORD, etc.
from tkinter import filedialog, messagebox, simpledialog, BooleanVar, Text, Toplevel # Still need some core tk parts

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
        self.debounce_delay = 1.0 # Debounce delay in seconds

    def schedule_refresh(self):
        """Schedules a refresh call via the queue with debouncing."""
        current_time = time.time()
        if current_time - self.last_event_time >= self.debounce_delay or self.last_event_time == 0:
            self.queue.put("refresh")
            self.last_event_time = current_time # Record time of this queued event

    def on_any_event(self, event):
        """Called when watchdog detects any file system change."""
        # Ignore directory events and events within the .git directory or our temp dir
        if not event.is_directory:
             # Basic check to avoid common noisy events - might need refinement
             path_str = event.src_path
             if '.git' not in path_str and 'progomatter_files' not in path_str:
                self.schedule_refresh()


# --- Main Application Class ---
class Progomatter:
    """Main application class for Progomatter using ttkbootstrap."""
    def __init__(self):
        # --- Theme Selection (Using ttkbootstrap) ---
        # Choose a light theme like 'cosmo', 'flatly', 'litera', 'lumen', 'minty', 'sandstone', 'yeti'
        self.selected_theme = "cosmo"
        self.root = tkb.Window(themename=self.selected_theme)

        self.root.title("Progomatter v2.7") # Version bump for UI change
        self.root.geometry("480x400") # Adjusted size after removing options

        # --- Initialize variables ---
        self.projects_file = Path("projects.json")
        self.projects = self.load_projects()
        self.selected_project = None
        # Ensure temp_dir uses Path object consistently
        self.temp_dir = Path(tempfile.gettempdir()) / "progomatter_files"
        self.temp_dir.mkdir(exist_ok=True)
        self.gitignore_spec = None
        self.include_patterns = []

        # --- Tkinter Option Vars ---
        self.convert_to_text_var = BooleanVar(value=False)
        self.combine_files_var = BooleanVar(value=False)
        # Auto-refresh defaults to True if watchdog is available
        self.auto_refresh_var = BooleanVar(value=WATCHDOG_AVAILABLE)

        # --- File Watching Setup ---
        self.observer = None
        self.observer_thread = None
        self.watch_path = None
        self.callback_queue = queue.Queue()

        # Reference for the .include editor text widget (if open)
        self.include_editor_text_widget = None

        # --- Build GUI ---
        self.setup_gui()
        self.check_queue() # Start checking the watchdog queue
        self.root.protocol("WM_DELETE_WINDOW", self.on_closing)

    def setup_gui(self):
        """Creates and arranges all the GUI widgets using ttkbootstrap."""
        main_frame = tkb.Frame(self.root, padding=10)
        main_frame.pack(fill=BOTH, expand=True)
        main_frame.columnconfigure(0, weight=1)

        # --- Row 0: Project Management ---
        project_row_frame = tkb.Frame(main_frame)
        project_row_frame.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        project_row_frame.columnconfigure(1, weight=1)
        tkb.Label(project_row_frame, text="Project:").pack(side=LEFT, padx=(0, 5))
        self.project_dropdown = tkb.Combobox(project_row_frame, state="readonly", values=[p["project_name"] for p in self.projects])
        self.project_dropdown.pack(side=LEFT, fill=X, expand=True, padx=(0, 5))
        self.project_dropdown.bind("<<ComboboxSelected>>", self.load_selected_project)
        edit_include_btn = tkb.Button(project_row_frame, text="Edit .include", command=self.edit_include_file, width=11, bootstyle="secondary-outline")
        edit_include_btn.pack(side=LEFT, padx=(0, 3))
        new_project_btn = tkb.Button(project_row_frame, text="Create New", command=self.create_new_project, width=10, bootstyle="success-outline")
        new_project_btn.pack(side=LEFT, padx=(0, 3))
        delete_project_btn = tkb.Button(project_row_frame, text="Delete", command=self.delete_project, width=7, bootstyle="danger-outline")
        delete_project_btn.pack(side=LEFT)

        # --- Row 1: Directory Display ---
        dir_frame = tkb.Frame(main_frame)
        dir_frame.grid(row=1, column=0, sticky="ew", pady=3)
        dir_frame.columnconfigure(1, weight=1)
        tkb.Label(dir_frame, text="Directory:").pack(side=LEFT, padx=(0, 5))
        self.dir_label = tkb.Label(dir_frame, text="No project selected", anchor="w", relief=FLAT, padding=2, borderwidth=1) # Using FLAT relief
        self.dir_label.pack(side=LEFT, fill=X, expand=True)
        # Add a subtle border using ttkbootstrap style if needed, e.g. bootstyle="secondary" on the label

        # --- Row 2: Prompt Rules ---
        prompt_frame = tkb.Frame(main_frame)
        prompt_frame.grid(row=2, column=0, sticky="ew", pady=3)
        prompt_frame.columnconfigure(1, weight=1)
        tkb.Label(prompt_frame, text="Prompt Rules:").pack(side=LEFT, padx=(0, 5))
        self.prompt_rules_input = tkb.Entry(prompt_frame)
        self.prompt_rules_input.pack(side=LEFT, fill=X, expand=True, padx=(0, 5))
        save_prompt_btn = tkb.Button(prompt_frame, text="Save Prompt", command=self.save_prompt_rules, width=11, bootstyle="primary")
        save_prompt_btn.pack(side=LEFT)

        # --- Row 3: Options ---
        options_frame = tkb.Frame(main_frame, padding=(0, 5))
        options_frame.grid(row=3, column=0, sticky="w", pady=5) # West alignment

        convert_cb = tkb.Checkbutton(options_frame, text="Convert all copied files to .txt", variable=self.convert_to_text_var, command=self.on_option_change, bootstyle="primary")
        convert_cb.pack(side=LEFT, padx=(0, 10)) # Pack horizontally

        combine_cb = tkb.Checkbutton(options_frame, text="Combine files into single output", variable=self.combine_files_var, command=self.on_option_change, bootstyle="primary")
        combine_cb.pack(side=LEFT, padx=(0, 10)) # Pack horizontally

        if WATCHDOG_AVAILABLE:
            auto_refresh_cb = tkb.Checkbutton(options_frame, text="Auto Refresh", variable=self.auto_refresh_var, command=self.toggle_observer, bootstyle="info")
            auto_refresh_cb.pack(side=LEFT) # Pack horizontally

        # Removed Pin Window and Transparency options

        # --- Row 4: Action Buttons ---
        action_frame = tkb.Frame(main_frame)
        action_frame.grid(row=4, column=0, sticky="w", pady=(8, 5)) # West alignment

        refresh_btn = tkb.Button(action_frame, text="Refresh Files", command=self.refresh_files, width=15, bootstyle="info")
        refresh_btn.pack(side=LEFT, padx=(0, 10))

        open_btn = tkb.Button(action_frame, text="Open Folder", command=self.open_temp_folder, width=15, bootstyle="secondary")
        open_btn.pack(side=LEFT)

        # --- Row 5: Status Display ---
        status_frame = tkb.Frame(main_frame)
        status_frame.grid(row=5, column=0, sticky="nsew", pady=(5, 0)) # Use remaining space
        status_frame.rowconfigure(0, weight=1)
        status_frame.columnconfigure(0, weight=1)
        main_frame.rowconfigure(5, weight=1) # Allow status area to expand vertically

        # Use ttkbootstrap Text widget
        self.status_text = Text(
            status_frame, height=8, wrap=WORD, # Removed relief/border, let theme handle it
            font=("Consolas", 9) # Keep monospace font for logs
            # ttkbootstrap Text automatically uses theme colors
        )
        self.status_text.grid(row=0, column=0, sticky="nsew")
        status_scrollbar = tkb.Scrollbar(status_frame, orient=VERTICAL, command=self.status_text.yview)
        status_scrollbar.grid(row=0, column=1, sticky="ns")
        self.status_text.config(yscrollcommand=status_scrollbar.set)

        # Set initial states (no longer need toggle_always_on_top or toggle_transparency)
        if self.auto_refresh_var.get() and self.selected_project:
             self.start_observer() # Start observer if auto-refresh is on and project loaded initially


    # --- Option Callbacks ---
    def on_option_change(self):
        """Called when 'Convert' or 'Combine' checkbox state changes."""
        if self.selected_project:
            self.refresh_files()

    # Removed toggle_always_on_top, toggle_transparency_widgets, update_transparency

    # --- Logging, Project Load/Save ---
    def log_status(self, message):
        """Appends a message to the status text area in a thread-safe way."""
        timestamp = time.strftime("%H:%M:%S")
        full_message = f"[{timestamp}] {message}\n"
        def update_widget():
            try:
                # Check if widget exists before modifying
                if self.status_text.winfo_exists():
                    self.status_text.insert(END, full_message)
                    self.status_text.see(END)
            except tkb.TclError:
                pass # Ignore errors during shutdown or if widget destroyed unexpectedly
        try:
            if threading.current_thread() is threading.main_thread():
                update_widget()
            else:
                # Ensure root window exists before scheduling update
                if self.root.winfo_exists():
                    self.root.after(0, update_widget)
        except Exception as e:
            print(f"Error logging status (potentially during shutdown): {e}") # Log to console if GUI fails
        print(f"LOG: {message}") # Keep console log

    def clear_status(self):
        """Clears the status text area."""
        try:
            if self.status_text.winfo_exists():
                self.status_text.delete(1.0, END)
        except tkb.TclError:
             pass # Ignore if widget is already destroyed

    def load_projects(self):
        """Loads project definitions from the JSON file."""
        if not self.projects_file.exists():
            return []
        try:
            with open(self.projects_file, "r", encoding='utf-8') as f:
                return json.load(f)
        except json.JSONDecodeError as e:
            print(f"Error decoding JSON from {self.projects_file}: {e}")
            messagebox.showerror("Load Error", f"Could not load projects file (invalid JSON):\n{self.projects_file}\nError: {e}", parent=self.root if self.root else None)
            return []
        except Exception as e:
            print(f"Error loading projects from {self.projects_file}: {e}")
            messagebox.showerror("Load Error", f"Could not load projects:\n{e}", parent=self.root if self.root else None)
            return []

    def save_projects(self):
        """Saves the current project list to the JSON file."""
        try:
            with open(self.projects_file, "w", encoding='utf-8') as f:
                json.dump(self.projects, f, indent=4)
        except Exception as e:
            self.log_status(f"Error saving projects: {e}")
            messagebox.showerror("Save Error", f"Could not save projects: {e}", parent=self.root)

    # --- Project Management ---
    def create_new_project(self):
        """Handles the creation of a new project."""
        # Use standard simpledialog from tkinter
        project_name = simpledialog.askstring("New Project", "Enter Project Name:", parent=self.root)
        if not project_name or not project_name.strip(): return
        project_name = project_name.strip()

        if any(p["project_name"] == project_name for p in self.projects):
            return messagebox.showerror("Error", f"Project '{project_name}' already exists.", parent=self.root)

        # Use standard filedialog from tkinter
        selected_dir = filedialog.askdirectory(title=f"Select Root Directory for '{project_name}'", parent=self.root)
        if not selected_dir: return

        selected_path = Path(selected_dir).resolve() # Resolve to absolute path
        new_project = { "project_name": project_name, "directory": str(selected_path), "prompt_rules": "" }
        self.projects.append(new_project)
        self.save_projects()

        include_path = selected_path / ".include"
        if not include_path.exists():
            try:
                with open(include_path, "w", encoding='utf-8') as f: f.write("# Include patterns (e.g., *.py, *.html)\n# One pattern per line.\n# Use * as wildcard.\n")
                self.log_status(f"Created default .include in {selected_path}")
            except Exception as e:
                self.log_status(f"Warn: Could not create .include: {e}")

        self.project_dropdown["values"] = [p["project_name"] for p in self.projects]
        self.project_dropdown.set(project_name)
        self.load_selected_project()

    def delete_project(self):
        """Deletes the currently selected project entry."""
        if not self.selected_project:
            return messagebox.showerror("Error", "No project selected.", parent=self.root)

        name = self.selected_project["project_name"]
        if messagebox.askyesno("Confirm Delete", f"Delete project '{name}' entry?\n(This does not delete project files)", parent=self.root):
            self.stop_observer() # Stop watching before modifying project list
            self.projects = [p for p in self.projects if p["project_name"] != name]
            self.save_projects()
            # Reset UI and state
            self.selected_project = None
            self.project_dropdown.set("")
            self.project_dropdown["values"] = [p["project_name"] for p in self.projects]
            self.dir_label.config(text="No project selected")
            self.prompt_rules_input.delete(0, END)
            self.include_patterns = []
            self.gitignore_spec = None
            self.clear_status()
            self.clear_temp_folder() # Clear temp associated with the deleted project
            self.log_status(f"Project '{name}' deleted.")

    def load_selected_project(self, event=None):
        """Loads the project selected in the dropdown."""
        name = self.project_dropdown.get()
        project = next((p for p in self.projects if p["project_name"] == name), None)
        if project:
            self.stop_observer() # Stop any previous observer
            self.selected_project = project
            self.dir_label.config(text=project["directory"])
            self.prompt_rules_input.delete(0, END)
            self.prompt_rules_input.insert(0, project.get("prompt_rules", ""))
            self.clear_status()
            self.log_status(f"Loading project: {name}...")
            # Load patterns and refresh files
            self.load_gitignore()
            self.load_include_patterns()
            # Save current prompt rules (in case they were just loaded)
            self.save_prompt_rules() # Ensures prompt.txt matches loaded rules
            self.refresh_files() # Refresh based on new project
            # Start watching if enabled
            if self.auto_refresh_var.get() and WATCHDOG_AVAILABLE:
                self.start_observer()
        else:
            self.log_status(f"Error: Could not find project data for '{name}'")
            self.selected_project = None # Ensure no project is selected if load fails
            self.dir_label.config(text="Project not found")
            self.prompt_rules_input.delete(0, END)


    # --- Prompt Rules ---
    def save_prompt_rules(self):
        """Saves prompt rules, writes to temp only if not empty."""
        if not self.selected_project:
            # Don't show popup if just loading a project
            # messagebox.showerror("Error", "No project selected.", parent=self.root)
            return

        user_prompt = self.prompt_rules_input.get().strip()
        # Only save if project data is actually loaded
        if self.selected_project.get("project_name"):
             self.selected_project["prompt_rules"] = user_prompt
             self.save_projects() # Save to projects.json

        prompt_file_path = self.temp_dir / "prompt.txt"
        try:
            if user_prompt:
                    with open(prompt_file_path, "w", encoding='utf-8') as f: f.write(user_prompt)
                    self.log_status("Prompt rules saved (and written to temp).")
            elif prompt_file_path.exists(): # Prompt is empty, remove file if it exists
                    prompt_file_path.unlink()
                    self.log_status("Prompt rules cleared (removed temp file).")
            else:
                 # Prompt is empty and file doesn't exist, just log saving to json
                 self.log_status("Prompt rules saved (empty).")
        except Exception as e:
            self.log_status(f"Error writing/deleting prompt file: {e}")


    # --- Pattern Loading ---
    def load_gitignore(self):
        """Loads and parses the .gitignore file."""
        self.gitignore_spec = None
        if not self.selected_project or not PATHSPEC_AVAILABLE: return
        path = Path(self.selected_project["directory"]) / ".gitignore"
        if path.is_file():
            try:
                with open(path, "r", encoding='utf-8', errors='ignore') as f: # Ignore encoding errors
                    # Use GitWildMatchPattern for standard gitignore behavior
                    self.gitignore_spec = pathspec.PathSpec.from_lines(pathspec.patterns.GitWildMatchPattern, f)
                    self.log_status("Loaded .gitignore patterns.")
            except Exception as e:
                self.log_status(f"Error reading .gitignore: {e}")
        else:
             self.log_status(".gitignore not found in project root.")

    def load_include_patterns(self):
        """Loads patterns from the .include file."""
        self.include_patterns = []
        if not self.selected_project: return
        path = Path(self.selected_project["directory"]) / ".include"
        if path.is_file():
            try:
                with open(path, 'r', encoding='utf-8', errors='ignore') as f:
                    self.include_patterns = [ln.strip() for ln in f if ln.strip() and not ln.strip().startswith('#')]
                self.log_status(f"Loaded {len(self.include_patterns)} patterns from .include.")
            except Exception as e:
                self.log_status(f"Error reading .include: {e}")
                self.include_patterns = []
        else:
             self.log_status(".include file not found. All non-ignored files will be processed.")


    # --- File Filtering ---
    def should_ignore(self, path_obj: Path, is_dir: bool) -> bool:
        """Checks if a path should be ignored by .gitignore or is the temp dir itself."""
        if not self.selected_project: return False

        project_dir = Path(self.selected_project["directory"])

        # Basic checks first
        if '.git' in path_obj.parts: return True # Always ignore .git content

        # Check if the path IS the temp directory itself (avoid recursive copying)
        try:
            if path_obj.resolve() == self.temp_dir.resolve():
                return True
        except OSError: # Handle potential resolution errors (e.g., broken symlinks)
            pass

        # Check against .gitignore using pathspec if available
        if self.gitignore_spec:
            try:
                # Pathspec expects relative paths from the gitignore location (project root)
                rel_path = path_obj.relative_to(project_dir)
                # Format path string for pathspec matching (POSIX style, add '/' for dirs)
                path_str = str(rel_path.as_posix()) + ('/' if is_dir else '')
                return self.gitignore_spec.match_file(path_str)
            except ValueError:
                # Path is not relative to project dir (shouldn't happen with os.walk)
                return False
            except Exception as e:
                # Log unexpected errors during matching
                self.log_status(f"Warn: Error matching path '{path_obj}' against .gitignore: {e}")
                return False # Default to not ignoring if error occurs

        return False # No .gitignore spec or basic checks failed

    def should_include(self, path_obj: Path) -> bool:
        """Checks if a path should be included based on .include patterns."""
        if not self.include_patterns: return True # Include if no patterns defined (default allow)
        if not self.selected_project: return False # Should not happen if called correctly

        # Match against the filename part only
        name = path_obj.name
        for pattern in self.include_patterns:
            try:
                if fnmatch.fnmatch(name, pattern): return True # Match found
            except Exception as e:
                 self.log_status(f"Warn: Error matching pattern '{pattern}' against '{name}': {e}")
        return False # No patterns matched


    # --- Temp Folder ---
    def clear_temp_folder(self):
        """Deletes all files and subdirectories within the temp folder."""
        if not self.temp_dir.exists():
            try:
                self.temp_dir.mkdir(exist_ok=True)
                return # Ensure exists and return
            except Exception as e:
                self.log_status(f"Error creating temp directory {self.temp_dir}: {e}")
                return

        self.log_status(f"Clearing temp folder: {self.temp_dir}")
        deleted_count = 0
        error_count = 0
        for item in self.temp_dir.iterdir():
            try:
                if item.is_file() or item.is_symlink():
                    item.unlink()
                    deleted_count += 1
                elif item.is_dir():
                    shutil.rmtree(item)
                    deleted_count += 1
            except Exception as e:
                self.log_status(f"Warn: Error deleting temp item {item.name}: {e}")
                error_count += 1
        if error_count > 0:
             self.log_status(f"Finished clearing temp folder with {error_count} errors.")
        elif deleted_count > 0:
             self.log_status(f"Finished clearing temp folder (deleted {deleted_count} items).")


    def open_temp_folder(self):
        """Opens the temporary folder in the default file explorer."""
        if not self.temp_dir.exists():
             # Attempt to create it if it doesn't exist
             try:
                 self.temp_dir.mkdir(parents=True, exist_ok=True)
                 self.log_status(f"Created missing temp directory: {self.temp_dir}")
             except Exception as e:
                 messagebox.showerror("Error", f"Temp directory missing and could not be created:\n{self.temp_dir}\n{e}", parent=self.root)
                 return

        try:
            path = str(self.temp_dir.resolve()) # Resolve path for robustness
            self.log_status(f"Opening folder: {path}")
            if sys.platform == "win32":
                os.startfile(path)
            elif sys.platform == "darwin": # macOS
                subprocess.run(["open", path], check=True)
            else: # Linux and other POSIX
                subprocess.run(["xdg-open", path], check=True)
        except FileNotFoundError:
             messagebox.showerror("Error", f"Could not find file explorer command (e.g., 'open' or 'xdg-open').\nFolder path: {path}", parent=self.root)
        except subprocess.CalledProcessError as e:
            messagebox.showerror("Error", f"File explorer command failed:\n{e}\nFolder path: {path}", parent=self.root)
        except Exception as e:
            self.log_status(f"Error opening temp folder: {e}")
            messagebox.showerror("Error", f"Could not open temp folder:\n{e}\nPath: {path}", parent=self.root)


    # --- Core File Refresh ---
    def refresh_files(self):
        """Main function to refresh files in the temp directory based on filters."""
        if not self.selected_project:
            self.log_status("Refresh skipped: No project selected.")
            return
        self.log_status("Refreshing files...")
        start_time = time.time()
        self.clear_temp_folder() # Start with a clean temp directory

        source_dir_str = self.selected_project.get("directory")
        if not source_dir_str:
             self.log_status("Error: Project directory not set.")
             return
        source_dir = Path(source_dir_str)

        if not source_dir.is_dir():
            self.log_status(f"Error: Source directory not found or not a directory: {source_dir}")
            messagebox.showerror("Error", f"Project source directory not found:\n{source_dir}", parent=self.root)
            return

        copied_count, ignored_git_count, ignored_incl_count, converted_count = 0, 0, 0, 0
        combined_content = []
        # Use a set to track destination filenames to prevent overwrites during conversion/copying
        files_in_temp = set()
        # Re-write prompt file if needed
        self.save_prompt_rules()

        try:
            # Walk through source directory
            for root, dirs, files in os.walk(source_dir, topdown=True):
                root_path = Path(root)

                # Filter directories first using .gitignore
                original_dir_count = len(dirs)
                dirs[:] = [d for d in dirs if not self.should_ignore(root_path / d, True)]
                ignored_git_count += (original_dir_count - len(dirs))

                # Process files in current directory
                for filename in files:
                    file_path = root_path / filename

                    # 1. Check .gitignore
                    if self.should_ignore(file_path, False):
                        ignored_git_count += 1; continue

                    # 2. Check .include patterns (if any exist)
                    if self.include_patterns and not self.should_include(file_path):
                        ignored_incl_count += 1; continue

                    # --- Passed filters, proceed to copy/process ---
                    dest_filename = filename
                    dest_path = self.temp_dir / dest_filename

                    # Option: Convert to .txt (before checking collision)
                    is_converted = False
                    target_txt_path = None
                    if self.convert_to_text_var.get() and file_path.suffix.lower() != ".txt":
                        target_txt_filename = file_path.stem + ".txt"
                        target_txt_path = self.temp_dir / target_txt_filename
                        is_converted = True # Mark for potential rename later

                    # Check for potential filename collisions in the temp dir
                    target_final_filename = target_txt_filename if is_converted else dest_filename
                    if target_final_filename in files_in_temp:
                         self.log_status(f"Warn: Skipping '{filename}' - target name '{target_final_filename}' already exists in temp dir.")
                         continue # Skip this file to avoid overwrite

                    # Copy the file
                    try:
                        shutil.copy2(file_path, dest_path) # Copy first to original name
                        copied_count += 1
                        final_dest_path = dest_path

                        # Perform rename if conversion was intended
                        if is_converted and target_txt_path:
                            try:
                                dest_path.rename(target_txt_path)
                                converted_count += 1
                                final_dest_path = target_txt_path # Update final path after rename
                            except Exception as rename_err:
                                self.log_status(f"Error renaming {dest_path.name} to {target_txt_path.name}: {rename_err}")
                                final_dest_path.unlink() # Clean up the copied file if rename failed
                                copied_count -= 1 # Decrement copy count
                                continue # Skip processing this file further

                        # Add the *final* filename to the tracking set
                        files_in_temp.add(final_dest_path.name)

                        # Option: Prepare for Combine (Read the *final* destination file)
                        if self.combine_files_var.get():
                            try:
                                # Try reading as UTF-8, ignore errors for binary/other files
                                with open(final_dest_path, 'r', encoding='utf-8', errors='ignore') as f:
                                    content = f.read()
                                combined_content.append((final_dest_path.name, content))
                            except Exception as read_err:
                                self.log_status(f"Warn: Could not read {final_dest_path.name} for combine: {read_err}")

                    except Exception as copy_err:
                        self.log_status(f"Error copying {filename}: {copy_err}")
                        # Ensure inconsistent state isn't tracked
                        if dest_path.exists():
                             try: dest_path.unlink()
                             except: pass


            # --- Post-Processing: Combine and Cleanup ---
            if self.combine_files_var.get() and combined_content:
                self.log_status(f"Combining {len(combined_content)} files...")
                mega_filename = "combined_output.txt"
                prompt_filename = "prompt.txt" # Filename of the prompt file
                mega_filepath = self.temp_dir / mega_filename
                separator = "\n" + "="*20 + f" FILE: {{}} " + "="*20 + "\n\n" # Use f-string correctly later
                try:
                    # Write combined file
                    with open(mega_filepath, 'w', encoding='utf-8') as megafile:
                        # Optional: Sort combined content alphabetically by filename for consistency
                        # combined_content.sort(key=lambda item: item[0])
                        for filename, content in combined_content:
                            megafile.write(separator.format(filename)) # Format the separator
                            megafile.write(content)
                    self.log_status(f"Created {mega_filename}. Cleaning up individual files...")

                    # --- Cleanup Logic ---
                    cleaned_count = 0
                    items_to_keep = {mega_filename, prompt_filename}
                    for item in list(self.temp_dir.iterdir()): # Iterate over a copy of the list
                        # Keep combined file and prompt file, delete others
                        if item.is_file() and item.name not in items_to_keep:
                            try:
                                item.unlink()
                                cleaned_count += 1
                            except Exception as delete_err:
                                self.log_status(f"Error deleting temp file {item.name}: {delete_err}")
                    if cleaned_count > 0:
                        self.log_status(f"Removed {cleaned_count} individual files after combining.")
                    # --- End Cleanup ---

                except Exception as combine_err:
                    self.log_status(f"Error during combine/cleanup: {combine_err}")
                    traceback.print_exc() # Print full traceback for combine errors

            # --- Final Logging ---
            duration = time.time() - start_time
            ignored_total = ignored_git_count + ignored_incl_count
            self.log_status(f"Refresh finished ({duration:.2f}s). Copied: {len(files_in_temp)}, Skipped: {ignored_total} ({ignored_git_count} gitignore, {ignored_incl_count} include), Converted: {converted_count}.")

        except Exception as e:
            self.log_status(f"Critical Error during file refresh walk: {str(e)}")
            traceback.print_exc()


    # --- .include Editor ---
    def edit_include_file(self):
        """Opens a Toplevel window to edit the .include file."""
        if not self.selected_project:
            return messagebox.showerror("Error", "No project selected.", parent=self.root)

        project_dir = Path(self.selected_project["directory"])
        include_path = project_dir / ".include"

        # Attempt to create if missing
        if not include_path.is_file():
            try:
                with open(include_path, "w", encoding='utf-8') as f:
                    f.write("# Include patterns (e.g., *.py, *.html)\n")
                    f.write("# One pattern per line. Use * as wildcard.\n")
                self.log_status(f"Created missing .include file: {include_path}")
            except Exception as e:
                messagebox.showerror("Error", f"Could not create .include file:\n{e}", parent=self.root)
                return

        # --- Editor Window Setup ---
        # Use tkb.Toplevel
        editor_win = Toplevel(self.root)
        editor_win.title(f"Edit .include - {self.selected_project['project_name']}")
        editor_win.geometry("500x450")
        editor_win.transient(self.root) # Keep editor on top of main window
        editor_win.grab_set() # Modal behavior

        # ttkbootstrap Toplevel automatically uses the theme background

        editor_frame = tkb.Frame(editor_win, padding=10)
        editor_frame.pack(fill=BOTH, expand=True)

        text_frame = tkb.Frame(editor_frame)
        text_frame.pack(fill=BOTH, expand=True, pady=(0, 10))
        text_frame.rowconfigure(0, weight=1)
        text_frame.columnconfigure(0, weight=1)

        # Use tkinter Text for simplicity here, styled by theme
        include_text_widget_editor = Text(
            text_frame, wrap=WORD, undo=True, font=("Consolas", 10)
            # Removed manual bg/fg, relies on theme
        )
        include_text_widget_editor.grid(row=0, column=0, sticky="nsew")
        # Use tkb.Scrollbar
        scrollbar = tkb.Scrollbar(text_frame, orient=VERTICAL, command=include_text_widget_editor.yview)
        scrollbar.grid(row=0, column=1, sticky="ns")
        include_text_widget_editor.config(yscrollcommand=scrollbar.set)

        # Store reference (needed for potential bg updates if transparency were kept, but good practice anyway)
        self.include_editor_text_widget = include_text_widget_editor
        # Removed call to update_transparency

        # Load content
        try:
            with open(include_path, "r", encoding='utf-8') as f:
                include_text_widget_editor.insert("1.0", f.read())
            include_text_widget_editor.edit_reset() # Clear undo stack after load
        except Exception as e:
            messagebox.showerror("Error", f"Could not read .include file:\n{e}", parent=editor_win)
            self.include_editor_text_widget = None # Clear reference on error
            editor_win.destroy()
            return

        # --- Buttons ---
        button_frame = tkb.Frame(editor_frame)
        button_frame.pack(fill=X)

        def save_changes():
            content = include_text_widget_editor.get("1.0", END).strip()
            try:
                with open(include_path, "w", encoding='utf-8') as f:
                    f.write(content + ("\n" if content else "")) # Add trailing newline if not empty
                self.log_status(".include file saved.")
                self.load_include_patterns() # Reload patterns immediately
                self.refresh_files()         # Refresh view based on new patterns
                self.include_editor_text_widget = None # Clear reference
                editor_win.destroy()
            except Exception as e:
                messagebox.showerror("Save Error", f"Could not save .include file:\n{e}", parent=editor_win)

        def cancel_changes():
            self.include_editor_text_widget = None # Clear reference
            editor_win.destroy()

        # Use tkb.Button with bootstyles
        save_btn = tkb.Button(button_frame, text="Save & Close", command=save_changes, bootstyle="success")
        save_btn.pack(side=RIGHT, padx=5)
        cancel_btn = tkb.Button(button_frame, text="Cancel", command=cancel_changes, bootstyle="secondary")
        cancel_btn.pack(side=RIGHT)

        # Set focus and define close behavior
        include_text_widget_editor.focus_set()
        editor_win.protocol("WM_DELETE_WINDOW", cancel_changes) # Ensure cleanup if closed with 'X'
        editor_win.wait_window() # Wait for editor to close


    # --- File Watching Control ---
    def start_observer(self):
        """Starts the file system observer if conditions are met."""
        if not WATCHDOG_AVAILABLE:
             self.log_status("Auto-refresh disabled: 'watchdog' library not found.")
             self.auto_refresh_var.set(False) # Ensure checkbox reflects reality
             return
        if not self.selected_project:
             self.log_status("Auto-refresh not started: No project selected.")
             return
        if not self.auto_refresh_var.get():
             # self.log_status("Auto-refresh is disabled.") # Avoid logging if user turned it off
             return
        if self.observer_thread and self.observer_thread.is_alive():
            # self.log_status("Observer already running.") # Usually not necessary to log this
            return # Already running

        self.watch_path = Path(self.selected_project["directory"])
        if not self.watch_path.is_dir():
            self.log_status(f"Cannot watch non-existent directory: {self.watch_path}")
            self.auto_refresh_var.set(False) # Turn off checkbox if path invalid
            return

        # Clear any stale messages from previous runs
        while not self.callback_queue.empty():
            try: self.callback_queue.get_nowait()
            except queue.Empty: break

        self.observer = Observer()
        event_handler = ProjectChangeHandler(self.callback_queue)
        try:
            self.observer.schedule(event_handler, str(self.watch_path), recursive=True)
            # Use daemon thread so it exits when the main app exits
            self.observer_thread = threading.Thread(target=self.observer.start, daemon=True)
            self.observer_thread.start()
            self.log_status(f"File watching started: {self.watch_path}")
        except Exception as e:
            self.log_status(f"Error starting file observer: {e}")
            self.observer = None # Ensure observer is None if scheduling failed
            self.auto_refresh_var.set(False) # Turn off checkbox on error


    def stop_observer(self):
        """Stops the file system observer if it's running."""
        observer_was_running = False
        if self.observer and self.observer.is_alive():
            observer_was_running = True
            try:
                self.observer.stop()
                # Don't join immediately in GUI, let thread finish naturally
                # self.observer_thread.join() # Avoid join in main thread to prevent potential blocking
                self.log_status("File watching stopped.")
            except Exception as e:
                self.log_status(f"Error stopping observer: {e}")
        # Always reset state variables regardless of whether it was running or errored
        self.observer = None
        self.observer_thread = None
        self.watch_path = None
        # if not observer_was_running: # Optionally log if it wasn't running
        #     self.log_status("File watching was not active.")

    def toggle_observer(self):
        """Called when the Auto Refresh checkbox is toggled."""
        if self.auto_refresh_var.get():
            if self.selected_project:
                 self.start_observer()
            else:
                 self.log_status("Select a project to enable auto-refresh.")
                 # Keep checkbox checked, will start when project loads
        else:
            self.stop_observer()

    def check_queue(self):
        """Periodically check the queue for messages from the observer thread."""
        try:
            message = self.callback_queue.get_nowait()
            if message == "refresh" and self.selected_project and self.auto_refresh_var.get():
                self.log_status("Auto-refresh triggered by file change...")
                self.refresh_files()
        except queue.Empty:
            pass # No message is normal
        except Exception as e:
            self.log_status(f"Error checking observer queue: {e}")
        finally:
            # Schedule the next check using root.after for GUI thread safety
            # Check if root still exists before scheduling
            if self.root.winfo_exists():
                self.root.after(250, self.check_queue) # Check every 250ms


    # --- App Lifecycle ---
    def on_closing(self):
        """Handles application closing gracefully."""
        self.log_status("Closing application...")
        self.stop_observer()
        # No need to explicitly clear temp folder on close, OS usually handles temp files
        # self.clear_temp_folder()
        self.root.destroy()

    def run(self):
        """Starts the Tkinter main loop."""
        self.root.mainloop()

# --- Main Execution Block ---
if __name__ == "__main__":
    # Ensure ttkbootstrap is installed
    try:
        import ttkbootstrap
    except ImportError:
        print("Error: ttkbootstrap is not installed.")
        print("Please install it using: pip install ttkbootstrap")
        sys.exit(1)

    app = Progomatter()
    app.run()