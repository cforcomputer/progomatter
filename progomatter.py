# Imports remain the same
import tkinter as tk
from tkinter import ttk, filedialog, messagebox, Text, BooleanVar, Frame, Button, Label, Toplevel, Scrollbar, DoubleVar
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

# --- Optional Dependency Imports ---
try:
    from ttkthemes import ThemedTk
    TTKTHEMES_AVAILABLE = True
except ImportError:
    ThemedTk = tk.Tk # Fallback to standard Tk
    TTKTHEMES_AVAILABLE = False
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

# --- File System Event Handler ---
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
        if not event.is_directory: # Focus on file changes
            self.schedule_refresh()

# --- Main Application Class ---
class Progomatter:
    """Main application class for Progomatter."""
    def __init__(self):
        # --- Theme Selection ---
        self.selected_theme = "black" # Explicitly choose dark theme
        if TTKTHEMES_AVAILABLE:
            try:
                self.root = ThemedTk(theme=self.selected_theme)
                print(f"Using theme: {self.selected_theme}")
            except tk.TclError:
                print(f"Theme '{self.selected_theme}' not found, falling back to default.")
                self.root = tk.Tk() # Fallback if theme fails
                self.selected_theme = "default" # Update theme name
        else:
            self.root = tk.Tk()
            self.selected_theme = "default"

        self.root.title("Progomatter v2.6") # Version bump
        self.root.geometry("460x450") # Adjusted height slightly for new layout

        # --- Style Configuration ---
        self.style = ttk.Style()
        if self.selected_theme != "default":
            try:
                self.style.theme_use(self.selected_theme)
            except tk.TclError as e:
                print(f"Warning: Failed to apply theme '{self.selected_theme}': {e}")
                self.selected_theme = "default"

        # Define colors for dark theme consistency
        self.dark_bg = "#2E2E2E"
        self.dark_fg = "#EAEAEA"
        self.dark_entry_bg = "#3C3C3C"
        self.opaque_text_bg = "#1C1C1C"
        self.button_fg = "#FFFFFF"
        self.button_active_bg = "#555555"

        try:
            self.root.config(bg=self.dark_bg)
            self.style.configure('.', background=self.dark_bg, foreground=self.dark_fg)
            self.style.configure('TFrame', background=self.dark_bg)
            self.style.configure('TLabel', background=self.dark_bg, foreground=self.dark_fg)
            self.style.configure('TCheckbutton', background=self.dark_bg, foreground=self.dark_fg)
            self.style.configure('TCombobox', fieldbackground=self.dark_entry_bg, background=self.dark_entry_bg, foreground=self.dark_fg, insertcolor=self.dark_fg)
            self.style.map('TCombobox', fieldbackground=[('readonly', self.dark_entry_bg)])
            self.style.configure('TEntry', fieldbackground=self.dark_entry_bg, foreground=self.dark_fg, insertcolor=self.dark_fg)
            self.style.configure('TScale', background=self.dark_bg)
            self.style.configure('Action.TButton', padding=5, font=('Helvetica', 9, 'bold'), foreground=self.button_fg, relief="raised", borderwidth=1)
            self.style.map('Action.TButton', background=[('active', self.button_active_bg)])
        except tk.TclError as e:
            print(f"Warning: Could not configure some styles (TclError): {e}")

        # --- Initialize variables ---
        self.projects_file = Path("projects.json")
        self.projects = self.load_projects()
        self.selected_project = None
        self.temp_dir = Path(tempfile.gettempdir()) / "progomatter_files"
        self.temp_dir.mkdir(exist_ok=True)
        self.gitignore_spec = None
        self.include_patterns = []

        # --- Tkinter Option Vars ---
        self.convert_to_text_var = BooleanVar(value=False)
        self.combine_files_var = BooleanVar(value=False)
        self.always_on_top_var = BooleanVar(value=False)
        self.auto_refresh_var = BooleanVar(value=WATCHDOG_AVAILABLE)
        self.transparency_var = BooleanVar(value=False)
        self.transparency_level = DoubleVar(value=1.0)

        # --- File Watching Setup ---
        self.observer = None
        self.observer_thread = None
        self.watch_path = None
        self.callback_queue = queue.Queue()

        self.include_editor_text_widget = None

        # --- Build GUI ---
        self.setup_gui()
        self.check_queue()
        self.root.protocol("WM_DELETE_WINDOW", self.on_closing)

    def setup_gui(self):
        """Creates and arranges all the GUI widgets."""
        main_frame = ttk.Frame(self.root, padding="10")
        main_frame.pack(fill=tk.BOTH, expand=True)
        main_frame.columnconfigure(0, weight=1)

        # --- Row 0: Project Management ---
        project_row_frame = ttk.Frame(main_frame)
        project_row_frame.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        project_row_frame.columnconfigure(1, weight=1)
        ttk.Label(project_row_frame, text="Project:").pack(side=tk.LEFT, padx=(0, 5))
        self.project_dropdown = ttk.Combobox(project_row_frame, state="readonly", values=[p["project_name"] for p in self.projects])
        self.project_dropdown.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 5))
        self.project_dropdown.bind("<<ComboboxSelected>>", self.load_selected_project)
        edit_include_btn = ttk.Button(project_row_frame, text="Edit .include", command=self.edit_include_file, width=11, style='Action.TButton')
        edit_include_btn.pack(side=tk.LEFT, padx=(0, 3))
        new_project_btn = ttk.Button(project_row_frame, text="Create New", command=self.create_new_project, width=10, style='Action.TButton')
        new_project_btn.pack(side=tk.LEFT, padx=(0, 3))
        delete_project_btn = ttk.Button(project_row_frame, text="Delete", command=self.delete_project, width=7, style='Action.TButton')
        delete_project_btn.pack(side=tk.LEFT)

        # --- Row 1: Directory Display ---
        dir_frame = ttk.Frame(main_frame)
        dir_frame.grid(row=1, column=0, sticky="ew", pady=3)
        ttk.Label(dir_frame, text="Directory:").pack(side=tk.LEFT, padx=(0, 5))
        self.dir_label = ttk.Label(dir_frame, text="No project selected", anchor="w", relief="groove", padding=2)
        self.dir_label.pack(side=tk.LEFT, fill=tk.X, expand=True)

        # --- Row 2: Prompt Rules ---
        prompt_frame = ttk.Frame(main_frame)
        prompt_frame.grid(row=2, column=0, sticky="ew", pady=3)
        prompt_frame.columnconfigure(1, weight=1)
        ttk.Label(prompt_frame, text="Prompt Rules:").pack(side=tk.LEFT, padx=(0, 5))
        self.prompt_rules_input = ttk.Entry(prompt_frame)
        self.prompt_rules_input.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 5))
        save_prompt_btn = ttk.Button(prompt_frame, text="Save Prompt", command=self.save_prompt_rules, width=11, style='Action.TButton')
        save_prompt_btn.pack(side=tk.LEFT)

        # --- Row 3: Options (Using Grid for layout) ---
        options_frame = ttk.Frame(main_frame, padding=(0, 5))
        options_frame.grid(row=3, column=0, sticky="ew", pady=5)
        options_frame.columnconfigure(1, weight=0) # Column for slider, don't expand

        # Place each option on its own row using grid
        convert_cb = ttk.Checkbutton(options_frame, text="Convert all copied files to .txt", variable=self.convert_to_text_var, command=self.on_option_change)
        convert_cb.grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, 2)) # Span 2 cols

        combine_cb = ttk.Checkbutton(options_frame, text="Combine files into single output (removes others)", variable=self.combine_files_var, command=self.on_option_change)
        combine_cb.grid(row=1, column=0, columnspan=2, sticky="w", pady=(0, 2)) # Span 2 cols

        if WATCHDOG_AVAILABLE:
            auto_refresh_cb = ttk.Checkbutton(options_frame, text="Auto Refresh on file change", variable=self.auto_refresh_var, command=self.toggle_observer)
            auto_refresh_cb.grid(row=2, column=0, columnspan=2, sticky="w", pady=(0, 2)) # Span 2 cols

        pin_cb = ttk.Checkbutton(options_frame, text="Pin Window (Always on Top)", variable=self.always_on_top_var, command=self.toggle_always_on_top)
        pin_cb.grid(row=3, column=0, columnspan=2, sticky="w", pady=(0, 2)) # Span 2 cols

        transparency_cb = ttk.Checkbutton(options_frame, text="Transparency", variable=self.transparency_var, command=self.toggle_transparency_widgets)
        transparency_cb.grid(row=4, column=0, sticky="w", pady=(0, 2)) # Checkbox in col 0
        self.transparency_slider = ttk.Scale(options_frame, from_=0.1, to=1.0, orient=tk.HORIZONTAL, variable=self.transparency_level, command=self.update_transparency, length=100)
        # Slider will be placed in grid column 1 by toggle_transparency_widgets

        # --- Row 4: Action Buttons ---
        action_frame = ttk.Frame(main_frame)
        action_frame.grid(row=4, column=0, sticky="w", pady=(8, 5)) # Increased top padding

        refresh_btn = ttk.Button(action_frame, text="Refresh Files", command=self.refresh_files, width=18, style='Action.TButton')
        refresh_btn.pack(side=tk.LEFT, padx=(0, 10)) # Add manual refresh button

        open_btn = ttk.Button(action_frame, text="Open Folder", command=self.open_temp_folder, width=18, style='Action.TButton')
        open_btn.pack(side=tk.LEFT) # Keep open button

        # --- Row 5: Status Display ---
        status_frame = ttk.Frame(main_frame)
        status_frame.grid(row=5, column=0, sticky="nsew", pady=(0, 5)) # Adjusted row index
        status_frame.rowconfigure(0, weight=1)
        status_frame.columnconfigure(0, weight=1)
        main_frame.rowconfigure(5, weight=1) # Status area expands vertically

        self.status_text = Text(
            status_frame, height=10, wrap=tk.WORD, relief="sunken", borderwidth=1, # Increased height slightly
            font=("Consolas", 9),
            bg=self.dark_entry_bg, fg=self.dark_fg, insertbackground=self.dark_fg
        )
        self.status_text.grid(row=0, column=0, sticky="nsew")
        status_scrollbar = ttk.Scrollbar(status_frame, orient=tk.VERTICAL, command=self.status_text.yview)
        status_scrollbar.grid(row=0, column=1, sticky="ns")
        self.status_text.config(yscrollcommand=status_scrollbar.set)

        # Set initial states
        self.toggle_always_on_top()
        self.toggle_transparency_widgets() # Sets initial slider visibility

    # --- Option Callbacks ---
    def on_option_change(self):
        """Called when 'Convert' or 'Combine' checkbox state changes."""
        if self.selected_project:
            self.refresh_files()

    def toggle_always_on_top(self):
        """Toggles the window's always-on-top state."""
        try:
            self.root.attributes('-topmost', self.always_on_top_var.get())
        except tk.TclError:
            pass # Ignore if not supported

    def toggle_transparency_widgets(self):
        """Shows/hides slider and enables/disables transparency."""
        is_transparent = self.transparency_var.get()
        # Use grid_info() which is more reliable for grid geometry manager
        slider_visible = bool(self.transparency_slider.grid_info())

        if is_transparent:
            if not slider_visible:
                # Place slider in grid next to checkbox
                self.transparency_slider.grid(row=4, column=1, sticky="w", padx=(5, 0))
            self.update_transparency() # Apply current slider value
        else:
            if slider_visible:
                self.transparency_slider.grid_remove() # Hide slider using grid_remove
            try:
                self.root.attributes('-alpha', 1.0)
                self.status_text.config(bg=self.dark_entry_bg)
                if self.include_editor_text_widget and self.include_editor_text_widget.winfo_exists():
                    self.include_editor_text_widget.config(bg=self.dark_entry_bg)
            except tk.TclError:
                if not hasattr(self, "_alpha_error_logged"):
                    self.log_status("Transparency (-alpha) not supported.")
                    self._alpha_error_logged = True

    def update_transparency(self, event=None):
        """Applies the transparency level and adjusts text background."""
        if not self.transparency_var.get(): return

        level = self.transparency_level.get()
        text_alpha_threshold = 0.5

        try:
            self.root.attributes('-alpha', level)
            if hasattr(self, "_alpha_error_logged"): del self._alpha_error_logged
        except tk.TclError:
            if not hasattr(self, "_alpha_error_logged"):
                self.log_status("Transparency (-alpha) not supported.")
                self._alpha_error_logged = True
            return

        text_bg_color = self.opaque_text_bg if level < text_alpha_threshold else self.dark_entry_bg

        try:
            self.status_text.config(bg=text_bg_color)
            if self.include_editor_text_widget and self.include_editor_text_widget.winfo_exists():
                self.include_editor_text_widget.config(bg=text_bg_color)
        except tk.TclError:
             pass # Widget might be destroyed

    # --- Logging, Project Load/Save ---
    def log_status(self, message):
        """Appends a message to the status text area in a thread-safe way."""
        timestamp = time.strftime("%H:%M:%S")
        full_message = f"[{timestamp}] {message}\n"
        def update_widget():
            try:
                self.status_text.insert(tk.END, full_message)
                self.status_text.see(tk.END)
            except tk.TclError:
                pass # Ignore errors during shutdown
        try:
            if threading.current_thread() is threading.main_thread():
                update_widget()
            else:
                self.root.after(0, update_widget)
        except Exception:
            pass # Ignore errors if root is destroyed
        print(f"LOG: {message}")

    def clear_status(self):
        """Clears the status text area."""
        self.status_text.delete(1.0, tk.END)

    def load_projects(self):
        """Loads project definitions from the JSON file."""
        if not self.projects_file.exists():
            return []
        try:
            with open(self.projects_file, "r", encoding='utf-8') as f:
                return json.load(f)
        except Exception as e:
            print(f"Error loading projects from {self.projects_file}: {e}")
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
        project_name = tk.simpledialog.askstring("New Project", "Enter Project Name:", parent=self.root)
        if not project_name or not project_name.strip(): return
        project_name = project_name.strip()

        if any(p["project_name"] == project_name for p in self.projects):
            return messagebox.showerror("Error", f"Project '{project_name}' already exists.", parent=self.root)

        selected_dir = filedialog.askdirectory(title=f"Select Root Directory for '{project_name}'", parent=self.root)
        if not selected_dir: return

        selected_path = Path(selected_dir)
        new_project = { "project_name": project_name, "directory": str(selected_path), "prompt_rules": "" }
        self.projects.append(new_project)
        self.save_projects()

        include_path = selected_path / ".include"
        if not include_path.exists():
            try:
                with open(include_path, "w", encoding='utf-8') as f: f.write("# Include patterns\n")
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
        if messagebox.askyesno("Confirm Delete", f"Delete project '{name}' entry?", parent=self.root):
            self.stop_observer()
            self.projects = [p for p in self.projects if p["project_name"] != name]
            self.save_projects()
            # Reset UI and state
            self.selected_project = None
            self.project_dropdown.set("")
            self.project_dropdown["values"] = [p["project_name"] for p in self.projects]
            self.dir_label.config(text="No project selected")
            self.prompt_rules_input.delete(0, tk.END)
            self.include_patterns = []
            self.gitignore_spec = None
            self.clear_status()
            self.clear_temp_folder()
            self.log_status(f"Project '{name}' deleted.")

    def load_selected_project(self, event=None):
        """Loads the project selected in the dropdown."""
        name = self.project_dropdown.get()
        project = next((p for p in self.projects if p["project_name"] == name), None)
        if project:
            self.stop_observer()
            self.selected_project = project
            self.dir_label.config(text=project["directory"])
            self.prompt_rules_input.delete(0, tk.END)
            self.prompt_rules_input.insert(0, project.get("prompt_rules", ""))
            self.clear_status()
            self.log_status(f"Loading project: {name}...")
            # Load patterns and refresh files
            self.load_gitignore()
            self.load_include_patterns()
            self.refresh_files()
            # Start watching if enabled
            if self.auto_refresh_var.get() and WATCHDOG_AVAILABLE:
                self.start_observer()
        else:
            self.log_status(f"Error: Could not find project data for '{name}'")

    # --- Prompt Rules ---
    def save_prompt_rules(self):
        """Saves prompt rules, writes to temp only if not empty."""
        if not self.selected_project:
            return messagebox.showerror("Error", "No project selected.", parent=self.root)

        user_prompt = self.prompt_rules_input.get().strip()
        self.selected_project["prompt_rules"] = user_prompt
        self.save_projects()

        prompt_file_path = self.temp_dir / "prompt.txt"
        if user_prompt:
            try:
                with open(prompt_file_path, "w", encoding='utf-8') as f: f.write(user_prompt)
                self.log_status("Prompt rules saved (and written).")
            except Exception as e:
                self.log_status(f"Error writing prompt: {e}")
        elif prompt_file_path.exists(): # Prompt is empty, remove file
            try:
                prompt_file_path.unlink()
                self.log_status("Prompt rules cleared (removed temp file).")
            except Exception as e:
                self.log_status(f"Warn: Could not remove prompt file: {e}")
        else:
            self.log_status("Prompt rules saved (empty).")

    # --- Pattern Loading ---
    def load_gitignore(self):
        """Loads and parses the .gitignore file."""
        self.gitignore_spec = None
        if not self.selected_project or not PATHSPEC_AVAILABLE: return
        path = Path(self.selected_project["directory"]) / ".gitignore"
        if path.is_file():
            try:
                with open(path, "r", encoding='utf-8') as f:
                    self.gitignore_spec = pathspec.PathSpec.from_lines(pathspec.patterns.GitWildMatchPattern, f)
            except Exception as e:
                self.log_status(f"Error reading .gitignore: {e}")

    def load_include_patterns(self):
        """Loads patterns from the .include file."""
        self.include_patterns = []
        if not self.selected_project: return
        path = Path(self.selected_project["directory"]) / ".include"
        if path.is_file():
            try:
                with open(path, 'r', encoding='utf-8') as f:
                    self.include_patterns = [ln.strip() for ln in f if ln.strip() and not ln.startswith('#')]
            except Exception as e:
                self.log_status(f"Error reading .include: {e}")
                self.include_patterns = []

    # --- File Filtering ---
    def should_ignore(self, path_obj: Path, is_dir: bool) -> bool:
        """Checks if a path should be ignored by .gitignore."""
        if not self.selected_project or not self.gitignore_spec: return False
        try:
            rel_path = path_obj.relative_to(self.selected_project["directory"])
            path_str = str(rel_path.as_posix()) + ('/' if is_dir else '')
        except Exception:
             return False # Cannot determine relative path

        if rel_path.parts and rel_path.parts[0] == '.git': return True # Always ignore .git
        try: # Avoid temp dir itself if it happens to be inside project
            if path_obj.resolve() == self.temp_dir.resolve(): return True
        except Exception: pass

        return self.gitignore_spec.match_file(path_str)

    def should_include(self, path_obj: Path) -> bool:
        """Checks if a path should be included based on .include patterns."""
        if not self.include_patterns: return True # Include if no patterns defined
        name = path_obj.name
        for pattern in self.include_patterns:
            if fnmatch.fnmatch(name, pattern): return True # Match found
        return False # No patterns matched

    # --- Temp Folder ---
    def clear_temp_folder(self):
        """Deletes all files and subdirectories within the temp folder."""
        if not self.temp_dir.exists():
            self.temp_dir.mkdir(exist_ok=True); return # Ensure exists
        for item in self.temp_dir.iterdir():
            try:
                if item.is_file() or item.is_symlink(): item.unlink()
                elif item.is_dir(): shutil.rmtree(item)
            except Exception as e:
                self.log_status(f"Error deleting temp item {item.name}: {e}")

    def open_temp_folder(self):
        """Opens the temporary folder in the default file explorer."""
        if not self.temp_dir.exists():
            return messagebox.showwarning("Not Found", "Temp directory missing.", parent=self.root)
        try:
            path = str(self.temp_dir.resolve()) # Resolve path
            if os.name == "nt": os.startfile(path)
            elif sys.platform == "darwin": subprocess.run(["open", path], check=True)
            else: subprocess.run(["xdg-open", path], check=True)
            self.log_status(f"Opened temp folder: {path}")
        except Exception as e:
            self.log_status(f"Error opening temp folder: {e}")
            messagebox.showerror("Error", f"Could not open temp folder:\n{e}", parent=self.root)

    # --- Core File Refresh ---
    def refresh_files(self):
        """Main function to refresh files in the temp directory."""
        if not self.selected_project: return
        self.log_status("Refreshing files...")
        start_time = time.time()
        self.clear_temp_folder() # Start clean

        source_dir = Path(self.selected_project["directory"])
        if not source_dir.is_dir():
            return self.log_status(f"Error: Source dir not found: {source_dir}")

        copied_count, ignored_count, converted_count = 0, 0, 0
        combined_content = []
        files_in_temp = set() # Track filenames actually copied to temp

        try:
            # Walk through source directory
            for root, dirs, files in os.walk(source_dir, topdown=True):
                root_path = Path(root)

                # Filter directories based on .gitignore
                original_dir_count = len(dirs)
                dirs[:] = [d for d in dirs if not self.should_ignore(root_path / d, True)]
                ignored_count += (original_dir_count - len(dirs))

                # Process files in current directory
                for filename in files:
                    file_path = root_path / filename

                    # Apply filters
                    if self.should_ignore(file_path, False):
                        ignored_count += 1; continue
                    if not self.should_include(file_path): # Check include patterns
                        ignored_count += 1; continue

                    # Copy if passed filters
                    dest_path = self.temp_dir / filename
                    try:
                        shutil.copy2(file_path, dest_path)
                        copied_count += 1
                        files_in_temp.add(filename) # Track successful copies
                        final_dest_path = dest_path

                        # Option: Convert to .txt
                        if self.convert_to_text_var.get() and dest_path.suffix != ".txt":
                            target_txt_path = dest_path.with_suffix(".txt")
                            # Check if target name already exists from another source file
                            if target_txt_path.name in files_in_temp:
                                self.log_status(f"Skip Convert: Target '{target_txt_path.name}' exists.")
                            else:
                                try:
                                    dest_path.rename(target_txt_path)
                                    final_dest_path = target_txt_path # Update path after rename
                                    converted_count += 1
                                    # Update tracking set
                                    files_in_temp.remove(filename)
                                    files_in_temp.add(target_txt_path.name)
                                except Exception as rename_err:
                                    self.log_status(f"Error renaming {dest_path.name}: {rename_err}")

                        # Option: Prepare for Combine
                        if self.combine_files_var.get():
                            try:
                                with open(final_dest_path, 'r', encoding='utf-8', errors='ignore') as f:
                                    content = f.read()
                                combined_content.append((final_dest_path.name, content))
                            except Exception as read_err:
                                self.log_status(f"Warn: Could not read {final_dest_path.name} for combine: {read_err}")

                    except Exception as copy_err:
                        self.log_status(f"Error copying {filename}: {copy_err}")

            # --- Post-Processing: Combine and Cleanup ---
            if self.combine_files_var.get() and combined_content:
                self.log_status(f"Combining {len(combined_content)} files...")
                mega_filename = "combined_output.txt"
                prompt_filename = "prompt.txt" # Filename of the prompt file
                mega_filepath = self.temp_dir / mega_filename
                separator = "\n" + "="*20 + " FILE: {} " + "="*20 + "\n\n"
                try:
                    # Write combined file
                    with open(mega_filepath, 'w', encoding='utf-8') as megafile:
                        # Sort combined content alphabetically by filename? Optional, but consistent.
                        # combined_content.sort(key=lambda item: item[0])
                        for filename, content in combined_content:
                            megafile.write(separator.format(filename))
                            megafile.write(content)
                    self.log_status(f"Created {mega_filename}. Cleaning up other files...")

                    # --- Cleanup Logic ---
                    cleaned_count = 0
                    for item in self.temp_dir.iterdir():
                        # Keep combined file and prompt file, delete others
                        if item.is_file() and item.name not in [mega_filename, prompt_filename]:
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

            # --- Final Logging ---
            duration = time.time() - start_time
            self.log_status(f"Refresh finished ({duration:.2f}s). Copied: {copied_count}, Skipped: {ignored_count}, Converted: {converted_count}.")

        except Exception as e:
            self.log_status(f"Critical Error during file refresh: {str(e)}")
            traceback.print_exc()

    # --- .include Editor ---
    def edit_include_file(self):
        """Opens a Toplevel window to edit the .include file."""
        if not self.selected_project:
            return messagebox.showerror("Error", "No project selected.", parent=self.root)

        include_path = Path(self.selected_project["directory"]) / ".include"
        if not include_path.is_file(): # Attempt to create if missing
            try:
                with open(include_path, "w", encoding='utf-8') as f: f.write("# Include patterns\n")
                self.log_status(f"Created missing .include file: {include_path}")
            except Exception as e:
                return messagebox.showerror("Error", f"Could not create .include file:\n{e}", parent=self.root)

        # --- Editor Window Setup ---
        editor_win = Toplevel(self.root)
        editor_win.title(f"Edit .include - {self.selected_project['project_name']}")
        editor_win.geometry("500x450")
        editor_win.transient(self.root)
        editor_win.grab_set()
        editor_win.config(bg=self.dark_bg) # Set editor background

        editor_frame = ttk.Frame(editor_win, padding=10)
        editor_frame.pack(fill=tk.BOTH, expand=True)

        text_frame = ttk.Frame(editor_frame)
        text_frame.pack(fill=tk.BOTH, expand=True, pady=(0, 10))
        text_frame.rowconfigure(0, weight=1)
        text_frame.columnconfigure(0, weight=1)

        # Create editor text widget
        include_text_widget_editor = Text(
            text_frame, wrap=tk.WORD, undo=True, font=("Consolas", 10), relief="groove", borderwidth=1,
            bg=self.dark_entry_bg, fg=self.dark_fg, insertbackground=self.dark_fg # Apply dark theme colors
        )
        include_text_widget_editor.grid(row=0, column=0, sticky="nsew")
        scrollbar = ttk.Scrollbar(text_frame, orient=tk.VERTICAL, command=include_text_widget_editor.yview)
        scrollbar.grid(row=0, column=1, sticky="ns")
        include_text_widget_editor.config(yscrollcommand=scrollbar.set)

        # Store reference and apply current transparency logic
        self.include_editor_text_widget = include_text_widget_editor
        self.update_transparency() # Ensure bg matches current alpha state

        # Load content
        try:
            with open(include_path, "r", encoding='utf-8') as f: include_text_widget_editor.insert("1.0", f.read())
            include_text_widget_editor.edit_reset() # Clear undo stack after load
        except Exception as e:
            messagebox.showerror("Error", f"Could not read .include file:\n{e}", parent=editor_win)
            self.include_editor_text_widget = None # Clear reference on error
            editor_win.destroy(); return

        # Buttons
        button_frame = ttk.Frame(editor_frame)
        button_frame.pack(fill=tk.X)

        def save_changes():
            content = include_text_widget_editor.get("1.0", tk.END).strip()
            try:
                with open(include_path, "w", encoding='utf-8') as f: f.write(content + ("\n" if content else ""))
                self.log_status(".include file saved.")
                self.load_include_patterns() # Reload patterns
                self.refresh_files()         # Refresh view
                self.include_editor_text_widget = None # Clear reference on close
                editor_win.destroy()
            except Exception as e:
                messagebox.showerror("Save Error", f"Could not save .include file:\n{e}", parent=editor_win)

        def cancel_changes():
            self.include_editor_text_widget = None # Clear reference on close
            editor_win.destroy()

        save_btn = ttk.Button(button_frame, text="Save & Close", command=save_changes, style='Action.TButton')
        save_btn.pack(side=tk.RIGHT, padx=5)
        cancel_btn = ttk.Button(button_frame, text="Cancel", command=cancel_changes) # Standard cancel button
        cancel_btn.pack(side=tk.RIGHT)

        # Set focus and define close behavior
        include_text_widget_editor.focus_set()
        editor_win.protocol("WM_DELETE_WINDOW", cancel_changes) # Ensure reference is cleared if closed with 'X'
        editor_win.wait_window() # Wait for editor to close

    # --- File Watching Control ---
    def start_observer(self):
        """Starts the file system observer."""
        if not WATCHDOG_AVAILABLE or not self.selected_project or not self.auto_refresh_var.get(): return
        if self.observer_thread and self.observer_thread.is_alive(): return # Already running

        self.watch_path = Path(self.selected_project["directory"])
        if not self.watch_path.is_dir():
            return self.log_status(f"Cannot watch non-existent directory: {self.watch_path}")

        # Clear any pending messages
        while not self.callback_queue.empty():
            try: self.callback_queue.get_nowait()
            except queue.Empty: break

        self.observer = Observer()
        event_handler = ProjectChangeHandler(self.callback_queue)
        try:
            self.observer.schedule(event_handler, str(self.watch_path), recursive=True)
            self.observer_thread = threading.Thread(target=self.observer.start, daemon=True) # Daemon exits with app
            self.observer_thread.start()
            self.log_status(f"File watching started: {self.watch_path}")
        except Exception as e:
            self.log_status(f"Error starting file observer: {e}")
            self.observer = None # Ensure observer is None if failed

    def stop_observer(self):
        """Stops the file system observer."""
        if self.observer and self.observer.is_alive():
            try:
                self.observer.stop()
            except Exception as e:
                self.log_status(f"Error stopping observer: {e}")
        # Always reset state variables
        self.observer = None
        self.observer_thread = None
        self.watch_path = None

    def toggle_observer(self):
        """Called when the Auto Refresh checkbox is toggled."""
        if self.auto_refresh_var.get():
            if self.selected_project: self.start_observer()
        else:
            self.stop_observer()

    def check_queue(self):
        """Periodically check the queue for messages from the observer thread."""
        try:
            message = self.callback_queue.get_nowait()
            if message == "refresh" and self.selected_project:
                self.log_status("Auto-refresh triggered by file change...")
                self.refresh_files()
        except queue.Empty:
            pass # No message is normal
        except Exception as e:
            self.log_status(f"Error checking observer queue: {e}")
        finally:
            # Schedule the next check using root.after for GUI thread safety
            self.root.after(250, self.check_queue) # Check every 250ms

    # --- App Lifecycle ---
    def on_closing(self):
        """Handles application closing gracefully."""
        self.log_status("Closing application...")
        self.stop_observer()
        self.root.destroy()

    def run(self):
        """Starts the Tkinter main loop."""
        self.root.mainloop()

# --- Main Execution Block ---
if __name__ == "__main__":
    app = Progomatter()
    app.run()