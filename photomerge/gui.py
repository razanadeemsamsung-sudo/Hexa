"""Minimal Tkinter desktop interface."""

from __future__ import annotations

import threading

from .merger import (
    COMPRESSIONS, LAYOUTS, MAX_PHOTOS, MergeError, collect_images, merge_photos,
)


def run_gui() -> int:
    try:
        import tkinter as tk
        from tkinter import filedialog, messagebox, ttk
    except ImportError:
        print("Tkinter is not installed; use the command line instead (see --help).")
        return 1

    root = tk.Tk()
    root.title(f"Photo Merge - up to {MAX_PHOTOS} photos to TIFF")
    root.minsize(560, 420)

    files: list[str] = []
    layout = tk.StringVar(value="pages")
    columns = tk.StringVar(value="")
    spacing = tk.StringVar(value="0")
    compression = tk.StringVar(value="deflate")
    status = tk.StringVar(value="Add photos to begin.")

    frame = ttk.Frame(root, padding=10)
    frame.pack(fill="both", expand=True)

    listbox = tk.Listbox(frame, selectmode="extended")
    listbox.grid(row=0, column=0, columnspan=4, sticky="nsew")
    frame.rowconfigure(0, weight=1)
    frame.columnconfigure(1, weight=1)

    def refresh():
        listbox.delete(0, "end")
        for f in files:
            listbox.insert("end", f)
        status.set(f"{len(files)} / {MAX_PHOTOS} photos selected")

    def add():
        chosen = filedialog.askopenfilenames(
            title="Choose photos",
            filetypes=[("Images", "*.jpg *.jpeg *.png *.tif *.tiff *.bmp *.gif *.webp"),
                       ("All files", "*.*")],
        )
        add_paths(list(chosen))

    def add_paths(chosen):
        room = MAX_PHOTOS - len(files)
        if len(chosen) > room:
            messagebox.showwarning("Too many photos",
                                   f"Only {MAX_PHOTOS} photos allowed; extra ones were skipped.")
        files.extend(chosen[:room])
        refresh()

    def add_folder():
        folder = filedialog.askdirectory(title="Choose a folder of photos")
        if folder:
            add_paths([str(p) for p in collect_images([folder])])

    def clear():
        files.clear()
        refresh()

    def remove():
        for i in reversed(listbox.curselection()):
            del files[i]
        refresh()

    def move(delta):
        sel = listbox.curselection()
        if len(sel) != 1:
            return
        i, j = sel[0], sel[0] + delta
        if 0 <= j < len(files):
            files[i], files[j] = files[j], files[i]
            refresh()
            listbox.selection_set(j)

    buttons = ttk.Frame(frame)
    buttons.grid(row=1, column=0, columnspan=4, sticky="w", pady=6)
    ttk.Button(buttons, text="Add photos...", command=add).pack(side="left")
    ttk.Button(buttons, text="Add folder...", command=add_folder).pack(side="left", padx=4)
    ttk.Button(buttons, text="Remove", command=remove).pack(side="left")
    ttk.Button(buttons, text="Clear all", command=clear).pack(side="left", padx=4)
    ttk.Button(buttons, text="Move up", command=lambda: move(-1)).pack(side="left")
    ttk.Button(buttons, text="Move down", command=lambda: move(1)).pack(side="left", padx=4)

    ttk.Label(frame, text="Layout:").grid(row=2, column=0, sticky="w")
    ttk.Combobox(frame, textvariable=layout, values=LAYOUTS, state="readonly").grid(
        row=2, column=1, sticky="w")
    ttk.Label(frame, text="Grid columns (blank = auto):").grid(row=3, column=0, sticky="w")
    ttk.Entry(frame, textvariable=columns, width=8).grid(row=3, column=1, sticky="w")
    ttk.Label(frame, text="Spacing (px):").grid(row=4, column=0, sticky="w")
    ttk.Entry(frame, textvariable=spacing, width=8).grid(row=4, column=1, sticky="w")
    ttk.Label(frame, text="Compression:").grid(row=5, column=0, sticky="w")
    ttk.Combobox(frame, textvariable=compression, values=COMPRESSIONS, state="readonly").grid(
        row=5, column=1, sticky="w")

    merge_btn = ttk.Button(frame, text="Merge to TIFF...")
    merge_btn.grid(row=6, column=0, columnspan=4, pady=8)
    progress_bar = ttk.Progressbar(frame, mode="determinate")
    progress_bar.grid(row=7, column=0, columnspan=4, sticky="ew")
    ttk.Label(frame, textvariable=status).grid(row=8, column=0, columnspan=4, sticky="w")

    def on_progress(done, total):
        def update():
            progress_bar.configure(maximum=total, value=done)
            status.set(f"Merging photo {done} of {total}...")
        root.after(0, update)

    def do_merge():
        if not files:
            messagebox.showerror("No photos", "Add at least one photo first.")
            return
        out = filedialog.asksaveasfilename(defaultextension=".tiff",
                                           filetypes=[("TIFF", "*.tiff *.tif")])
        if not out:
            return
        try:
            cols = int(columns.get()) if columns.get().strip() else None
            gap = int(spacing.get() or 0)
        except ValueError:
            messagebox.showerror("Invalid value", "Columns and spacing must be whole numbers.")
            return

        merge_btn.state(["disabled"])
        status.set("Checking photos... this can take a moment for large batches.")
        progress_bar.configure(value=0)

        def work():
            try:
                res = merge_photos(list(files), out, layout=layout.get(), columns=cols,
                                   spacing=gap, compression=compression.get(),
                                   progress=on_progress)
                msg = f"Saved {res.output}"
                root.after(0, lambda: messagebox.showinfo("Done", msg))
            except Exception as exc:  # show any failure instead of dying silently
                err = str(exc)
                root.after(0, lambda: messagebox.showerror("Error", err))
            finally:
                root.after(0, lambda: (merge_btn.state(["!disabled"]), refresh()))

        threading.Thread(target=work, daemon=True).start()

    merge_btn.configure(command=do_merge)
    refresh()
    root.mainloop()
    return 0
