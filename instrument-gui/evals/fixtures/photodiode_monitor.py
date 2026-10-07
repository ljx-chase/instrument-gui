"""
Photodiode monitor - reads a photodiode through a DAQ and plots the trace.

Works fine at 200 ms per reading but the window stops responding when I set
the interval shorter, and the saved files take ages to write.
"""

import time
import tkinter as tk
from tkinter import ttk
from datetime import datetime
from pathlib import Path

import numpy as np
import matplotlib
matplotlib.use("TkAgg")
from matplotlib.figure import Figure
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg

N_PIX = 2048
OUT_DIR = Path("photodiode_data")


class DAQ:
    """Blocking read from the card."""

    def __init__(self):
        self.integration_s = 0.2

    def read(self):
        time.sleep(self.integration_s)          # the card blocks for this long
        x = np.linspace(0, 1000, N_PIX)
        y = 500 + 200 * np.exp(-0.5 * ((x - 520) / 30) ** 2)
        return x, y + np.random.normal(0, 8, N_PIX)


class MonitorApp:
    def __init__(self, root):
        self.root = root
        self.root.title("Photodiode monitor")
        self.daq = DAQ()
        self.running = False
        self.count = 0
        OUT_DIR.mkdir(exist_ok=True)

        bar = ttk.Frame(root)
        bar.pack(side=tk.TOP, fill=tk.X)
        ttk.Label(bar, text="Interval (s):").pack(side=tk.LEFT)
        self.interval = tk.StringVar(value="0.2")
        ttk.Entry(bar, textvariable=self.interval, width=8).pack(side=tk.LEFT)
        ttk.Button(bar, text="Start", command=self.start).pack(side=tk.LEFT)
        ttk.Button(bar, text="Stop", command=self.stop).pack(side=tk.LEFT)
        self.status = tk.StringVar(value="idle")
        ttk.Label(bar, textvariable=self.status).pack(side=tk.LEFT, padx=8)

        self.fig = Figure(figsize=(7, 4))
        self.ax = self.fig.add_subplot(111)
        self.canvas = FigureCanvasTkAgg(self.fig, master=root)
        self.canvas.get_tk_widget().pack(fill=tk.BOTH, expand=True)

    def start(self):
        self.running = True
        self.daq.integration_s = float(self.interval.get())
        self.tick()

    def stop(self):
        self.running = False

    def tick(self):
        if not self.running:
            return
        x, y = self.daq.read()
        self.count += 1

        np.savez_compressed(
            OUT_DIR / f"trace_{self.count:05d}.npz",
            x=x, y=y, timestamp=datetime.now().isoformat())

        self.ax.clear()
        self.ax.plot(x, y)
        self.ax.set_xlabel("wavelength")
        self.ax.set_ylabel("counts")
        self.canvas.draw()
        self.status.set(f"{self.count} traces")

        self.root.after(10, self.tick)


if __name__ == "__main__":
    root = tk.Tk()
    MonitorApp(root)
    root.mainloop()
