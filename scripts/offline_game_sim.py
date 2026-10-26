"""
UGM Offline Strategy Game Simulator (Self-contained Live Test Target)
A lightweight offline GUI window built with Tkinter for testing Universal Game MCP (UGM)
without requiring Hearts of Iron IV or any external commercial game installation.
"""

import sys
import time
import tkinter as tk
from tkinter import ttk


class Tooltip:
    """Helper to display tooltips on hover."""

    def __init__(self, widget, text):
        self.widget = widget
        self.text = text
        self.tip_window = None
        self.widget.bind("<Enter>", self.show_tip)
        self.widget.bind("<Leave>", self.hide_tip)

    def show_tip(self, event=None):
        if self.tip_window or not self.text:
            return
        x, y, _cx, cy = self.widget.bbox("insert") or (0, 0, 0, 0)
        x = x + self.widget.winfo_rootx() + 25
        y = y + cy + self.widget.winfo_rooty() + 25
        self.tip_window = tw = tk.Toplevel(self.widget)
        tw.wm_overrideredirect(True)
        tw.wm_geometry(f"+{x}+{y}")
        label = tk.Label(
            tw,
            text=self.text,
            justify=tk.LEFT,
            background="#ffffe0",
            foreground="#000000",
            relief=tk.SOLID,
            borderwidth=1,
            font=("Segoe UI", 10, "normal"),
            padx=6,
            pady=4
        )
        label.pack(ipadx=1)

    def hide_tip(self, event=None):
        if self.tip_window:
            self.tip_window.destroy()
            self.tip_window = None


class OfflineGameSimulator:
    """
    Simulates a strategy game UI with topbar statistics, interactive buttons,
    tooltips, hotkeys, and dynamic text state changes.
    """

    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("UGM Offline Strategy Simulator")
        self.root.geometry("900x550")
        self.root.configure(bg="#1e1e24")
        self.root.resizable(False, False)

        # Game State
        self.gold = 500
        self.wood = 320
        self.mana = 100
        self.farms = 0
        self.soldiers = 0
        self.is_paused = False

        self._build_ui()
        self._bind_keys()

    def _build_ui(self):
        # 1. Top Statistics Bar
        top_bar = tk.Frame(self.root, bg="#2b2d42", height=50)
        top_bar.pack(side=tk.TOP, fill=tk.X)

        self.lbl_title = tk.Label(
            top_bar, text="UGM STRATEGY SIM", font=("Segoe UI", 12, "bold"),
            bg="#2b2d42", fg="#edf2f4", padx=15, pady=10
        )
        self.lbl_title.pack(side=tk.LEFT)

        self.lbl_gold = tk.Label(
            top_bar, text=f"GOLD: {self.gold}", font=("Segoe UI", 11, "bold"),
            bg="#2b2d42", fg="#ffd166", padx=10
        )
        self.lbl_gold.pack(side=tk.LEFT)

        self.lbl_wood = tk.Label(
            top_bar, text=f"WOOD: {self.wood}", font=("Segoe UI", 11, "bold"),
            bg="#2b2d42", fg="#06d6a0", padx=10
        )
        self.lbl_wood.pack(side=tk.LEFT)

        self.lbl_mana = tk.Label(
            top_bar, text=f"MANA: {self.mana}", font=("Segoe UI", 11, "bold"),
            bg="#2b2d42", fg="#118ab2", padx=10
        )
        self.lbl_mana.pack(side=tk.LEFT)

        self.lbl_status = tk.Label(
            top_bar, text="STATUS: RUNNING", font=("Segoe UI", 11, "bold"),
            bg="#2b2d42", fg="#06d6a0", padx=15
        )
        self.lbl_status.pack(side=tk.RIGHT)

        # 2. Main Playfield Center Canvas
        self.canvas = tk.Canvas(self.root, bg="#121216", width=860, height=330, highlightthickness=1, highlightbackground="#3d405b")
        self.canvas.pack(pady=15)

        # Draw decorative castle and territory
        self.canvas.create_rectangle(50, 50, 250, 250, fill="#242633", outline="#474a63", width=2)
        self.canvas.create_text(150, 80, text="KINGDOM FORTRESS", fill="#ffffff", font=("Segoe UI", 11, "bold"))
        self.fortress_text = self.canvas.create_text(
            150, 150,
            text=f"Farms: {self.farms}\nSoldiers: {self.soldiers}",
            fill="#a5a5b5",
            font=("Segoe UI", 11)
        )

        # Log box inside canvas
        self.canvas.create_rectangle(320, 50, 810, 280, fill="#181920", outline="#333544")
        self.canvas.create_text(400, 75, text="ACTION LOG:", fill="#edf2f4", font=("Segoe UI", 10, "bold"))
        self.log_text = self.canvas.create_text(
            560, 170,
            text="Game started.\nWaiting for AI or user commands...\nUse buttons below or hotkeys:\n[Space] = Pause | [B] = Build | [T] = Train",
            fill="#8d99ae",
            font=("Consolas", 10),
            width=460
        )

        # 3. Bottom Action Buttons Bar
        btn_bar = tk.Frame(self.root, bg="#1e1e24")
        btn_bar.pack(side=tk.BOTTOM, fill=tk.X, pady=10)

        # Button 1: BUILD FARM
        self.btn_farm = tk.Button(
            btn_bar, text="BUILD FARM (-50 Gold)", font=("Segoe UI", 10, "bold"),
            bg="#457b9d", fg="#ffffff", activebackground="#1d3557", activeforeground="#ffffff",
            padx=10, pady=6, command=self.build_farm
        )
        self.btn_farm.pack(side=tk.LEFT, padx=15)
        Tooltip(self.btn_farm, "Constructs a new Farm. Increases gold generation.")

        # Button 2: TRAIN SOLDIER
        self.btn_soldier = tk.Button(
            btn_bar, text="TRAIN SOLDIER (-40 Wood)", font=("Segoe UI", 10, "bold"),
            bg="#2a9d8f", fg="#ffffff", activebackground="#264653", activeforeground="#ffffff",
            padx=10, pady=6, command=self.train_soldier
        )
        self.btn_soldier.pack(side=tk.LEFT, padx=10)
        Tooltip(self.btn_soldier, "Recruits a heavy infantry soldier for defense.")

        # Button 3: TOGGLE PAUSE
        self.btn_pause = tk.Button(
            btn_bar, text="PAUSE GAME [Space]", font=("Segoe UI", 10, "bold"),
            bg="#e76f51", fg="#ffffff", activebackground="#f4a261", activeforeground="#ffffff",
            padx=10, pady=6, command=self.toggle_pause
        )
        self.btn_pause.pack(side=tk.LEFT, padx=10)
        Tooltip(self.btn_pause, "Pauses or unpauses game execution.")

        # Button 4: RESET
        self.btn_reset = tk.Button(
            btn_bar, text="RESET GAME", font=("Segoe UI", 10, "bold"),
            bg="#6c757d", fg="#ffffff", padx=10, pady=6, command=self.reset_game
        )
        self.btn_reset.pack(side=tk.RIGHT, padx=15)

    def _bind_keys(self):
        self.root.bind("<space>", lambda e: self.toggle_pause())
        self.root.bind("b", lambda e: self.build_farm())
        self.root.bind("B", lambda e: self.build_farm())
        self.root.bind("t", lambda e: self.train_soldier())
        self.root.bind("T", lambda e: self.train_soldier())

    def update_displays(self):
        self.lbl_gold.config(text=f"GOLD: {self.gold}")
        self.lbl_wood.config(text=f"WOOD: {self.wood}")
        self.lbl_mana.config(text=f"MANA: {self.mana}")
        self.canvas.itemconfig(
            self.fortress_text,
            text=f"Farms: {self.farms}\nSoldiers: {self.soldiers}"
        )

    def log(self, msg: str):
        now = time.strftime("%H:%M:%S")
        curr = self.canvas.itemcget(self.log_text, "text")
        lines = curr.split("\n")
        lines.append(f"[{now}] {msg}")
        if len(lines) > 8:
            lines = lines[-8:]
        self.canvas.itemconfig(self.log_text, text="\n".join(lines))

    def build_farm(self):
        if self.is_paused:
            self.log("Action rejected: Game is PAUSED.")
            return
        if self.gold >= 50:
            self.gold -= 50
            self.farms += 1
            self.update_displays()
            self.log(f"Farm constructed! Total farms: {self.farms} (Remaining Gold: {self.gold})")
        else:
            self.log("Not enough gold to build farm!")

    def train_soldier(self):
        if self.is_paused:
            self.log("Action rejected: Game is PAUSED.")
            return
        if self.wood >= 40:
            self.wood -= 40
            self.soldiers += 1
            self.update_displays()
            self.log(f"Soldier trained! Army size: {self.soldiers} (Remaining Wood: {self.wood})")
        else:
            self.log("Not enough wood to train soldier!")

    def toggle_pause(self):
        self.is_paused = not self.is_paused
        if self.is_paused:
            self.lbl_status.config(text="STATUS: PAUSED", fg="#e63946")
            self.btn_pause.config(text="RESUME GAME [Space]", bg="#2a9d8f")
            self.log("Game PAUSED.")
        else:
            self.lbl_status.config(text="STATUS: RUNNING", fg="#06d6a0")
            self.btn_pause.config(text="PAUSE GAME [Space]", bg="#e76f51")
            self.log("Game RESUMED.")

    def reset_game(self):
        self.gold = 500
        self.wood = 320
        self.mana = 100
        self.farms = 0
        self.soldiers = 0
        self.is_paused = False
        self.lbl_status.config(text="STATUS: RUNNING", fg="#06d6a0")
        self.btn_pause.config(text="PAUSE GAME [Space]", bg="#e76f51")
        self.update_displays()
        self.log("Game has been reset to initial state.")


def main():
    root = tk.Tk()
    app = OfflineGameSimulator(root)
    root.mainloop()


if __name__ == "__main__":
    main()
