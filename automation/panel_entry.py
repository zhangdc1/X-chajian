from __future__ import annotations

import tkinter as tk

from newtkmain import AppGUI


def main() -> None:
    root = tk.Tk()
    root.withdraw()
    app = AppGUI(root)
    app.mainloop()


if __name__ == "__main__":
    main()
