import tkinter as tk
from tkinter import filedialog, messagebox
import os
import shutil
import fnmatch
import tempfile
from pathlib import Path
import subprocess


class Progomatter:
    def __init__(self):
        self.root = tk.Tk()
        self.root.title("Progomatter")
        self.root.geometry("600x400")

        # Initialize variables
        self.source_dir = None
        self.temp_dir = os.path.join(tempfile.gettempdir(), "progomatter_files")
        self.progress_var = tk.DoubleVar()

        # Create temp directory if it doesn't exist
        os.makedirs(self.temp_dir, exist_ok=True)

        self.setup_gui()

    def setup_gui(self):
        # Create main frame with padding
        main_frame = tk.Frame(self.root, padx=20, pady=20)
        main_frame.pack(fill=tk.BOTH, expand=True)

        # Directory selection
        dir_frame = tk.Frame(main_frame)
        dir_frame.pack(fill=tk.X, pady=(0, 20))

        self.dir_label = tk.Label(dir_frame, text="No folder selected", wraplength=400)
        self.dir_label.pack(side=tk.LEFT, fill=tk.X, expand=True)

        select_btn = tk.Button(
            dir_frame, text="Select Folder", command=self.select_directory
        )
        select_btn.pack(side=tk.RIGHT, padx=(10, 0))

        # Status display
        self.status_text = tk.Text(main_frame, height=10, wrap=tk.WORD)
        self.status_text.pack(fill=tk.BOTH, expand=True, pady=(0, 20))

        # Action buttons
        btn_frame = tk.Frame(main_frame)
        btn_frame.pack(fill=tk.X)

        refresh_btn = tk.Button(
            btn_frame, text="Refresh Files", command=self.refresh_files
        )
        refresh_btn.pack(side=tk.LEFT, padx=(0, 10))

        open_btn = tk.Button(
            btn_frame, text="Open Folder", command=self.open_temp_folder
        )
        open_btn.pack(side=tk.LEFT)

    def select_directory(self):
        self.source_dir = filedialog.askdirectory()
        if self.source_dir:
            self.dir_label.config(text=self.source_dir)
            self.refresh_files()

    def read_patterns_file(self, filename):
        patterns = []
        try:
            pattern_file = os.path.join(self.source_dir, filename)
            if os.path.exists(pattern_file):
                with open(pattern_file, "r") as f:
                    patterns = [
                        line.strip()
                        for line in f
                        if line.strip() and not line.startswith("#")
                    ]
                self.log_status(f"Found patterns in {filename}: {patterns}")
            else:
                self.log_status(f"Warning: {filename} not found in {self.source_dir}")
        except Exception as e:
            self.log_status(f"Error reading {filename}: {str(e)}")
        return patterns

    def should_include_file(self, file_name, include_patterns, ignore_patterns):
        # First check if file should be ignored by name
        for pattern in ignore_patterns:
            if not pattern.endswith("/") and fnmatch.fnmatch(file_name, pattern):
                self.log_status(
                    f"Ignoring file {file_name} - matches ignore pattern {pattern}"
                )
                return False

        # Then check if file matches include patterns
        for pattern in include_patterns:
            if fnmatch.fnmatch(file_name, pattern):
                return True

        return False

    def should_ignore_directory(self, dir_path, ignore_patterns):
        # Handle directory-specific ignore patterns
        for pattern in ignore_patterns:
            # If pattern ends with /, it's a directory pattern
            if pattern.endswith("/"):
                pattern = pattern[:-1]  # Remove trailing slash for matching
                if fnmatch.fnmatch(dir_path, pattern) or fnmatch.fnmatch(
                    os.path.basename(dir_path), pattern
                ):
                    self.log_status(
                        f"Ignoring directory: {dir_path} - matches pattern {pattern}"
                    )
                    return True
            # Also check non-slash patterns against directory names
            elif fnmatch.fnmatch(os.path.basename(dir_path), pattern):
                self.log_status(
                    f"Ignoring directory: {dir_path} - matches pattern {pattern}"
                )
                return True
        return False

    def refresh_files(self):
        if not self.source_dir:
            messagebox.showerror("Error", "Please select a source directory first")
            return

        self.log_status("Starting refresh process...")
        self.progress_var.set(0)

        # Close existing Explorer window if on Windows
        if os.name == "nt":
            try:
                os.system(
                    f'taskkill /F /IM explorer.exe /FI "WINDOWTITLE eq {os.path.basename(self.temp_dir)}*"'
                )
            except Exception:
                pass

        # Clear temp directory
        for file in os.listdir(self.temp_dir):
            file_path = os.path.join(self.temp_dir, file)
            try:
                if os.path.isfile(file_path):
                    os.unlink(file_path)
            except Exception as e:
                self.log_status(f"Error removing {file}: {str(e)}")

        # Read include and ignore patterns
        include_patterns = self.read_patterns_file(".include")
        ignore_patterns = self.read_patterns_file(".ignore")

        if not include_patterns:
            self.log_status("Warning: No include patterns found in .include file")
            return

        # First count total files to process for progress bar
        total_files = 0
        matching_files = []
        for root, dirs, files in os.walk(self.source_dir):
            # Check if current directory should be ignored
            rel_path = os.path.relpath(root, self.source_dir)

            # Remove directories that match ignore patterns
            dirs[:] = [
                d
                for d in dirs
                if not self.should_ignore_directory(
                    os.path.join(rel_path, d), ignore_patterns
                )
            ]

            for file in files:
                if self.should_include_file(file, include_patterns, ignore_patterns):
                    total_files += 1
                    matching_files.append((root, file))

        if total_files == 0:
            self.log_status("No matching files found")
            return

        # Copy matching files with progress updates
        copied_count = 0
        for root, file in matching_files:
            src_path = os.path.join(root, file)
            try:
                shutil.copy2(src_path, self.temp_dir)
                copied_count += 1
                # Update progress bar
                progress = (copied_count / total_files) * 100
                self.progress_var.set(progress)
                self.root.update_idletasks()
            except Exception as e:
                self.log_status(f"Error copying {file}: {str(e)}")

        self.log_status(f"Refresh complete. Copied {copied_count} files.")

        # Reset progress bar
        self.progress_var.set(100)

        # Automatically open the folder after refresh
        self.open_temp_folder()

    def open_temp_folder(self):
        if os.name == "nt":  # Windows
            os.startfile(self.temp_dir)
        elif os.name == "posix":  # macOS and Linux
            subprocess.run(
                ["xdg-open" if os.name == "posix" else "open", self.temp_dir]
            )

    def log_status(self, message):
        self.status_text.insert(tk.END, message + "\n")
        self.status_text.see(tk.END)

    def run(self):
        self.root.mainloop()


if __name__ == "__main__":
    app = Progomatter()
    app.run()
