import sys
import subprocess
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
VENV_PYTHON = BASE_DIR / "venv" / "bin" / "python3"
PYTHON_BIN = str(VENV_PYTHON if VENV_PYTHON.exists() else sys.executable)

def print_menu():
    print("""
\033[1;31m╔════════════════════════════════════════════════════════════════════════════╗\033[0m
\033[1;31m║\033[0m   \033[1;31m影の道\033[0m  \033[1;37mAFTERLIGHT\033[0m  \033[1;31m//\033[0m  \033[1;37mSUPERBAN OS v5.0 APEX\033[0m                           \033[1;31m║\033[0m
\033[1;31m║\033[0m   \033[2mWhere stillness reveals the unseen • Precision Telegram Governance\033[0m       \033[1;31m║\033[0m
\033[1;31m╚════════════════════════════════════════════════════════════════════════════╝\033[0m

  \033[1;31m[01]\033[0m  \033[1;37mTERMINAL METHOD\033[0m  \033[2m— High-Velocity Rich Console TUI (Tactical War Room)\033[0m
  \033[1;31m[02]\033[0m  \033[1;37mWEB METHOD\033[0m       \033[2m— Afterlight Obsidian HUD (http://127.0.0.1:8000)\033[0m
  \033[1;31m[00]\033[0m  \033[2mExit\033[0m
""")

def main():
    if len(sys.argv) > 1:
        flag = sys.argv[1].lower()
        if flag in ["--terminal", "-t", "terminal", "1", "01"]:
            subprocess.run([PYTHON_BIN, str(BASE_DIR / "terminal_app.py")])
            return
        elif flag in ["--web", "-w", "web", "2", "02"]:
            subprocess.run([PYTHON_BIN, str(BASE_DIR / "web_app.py")])
            return

    print_menu()
    choice = input("\033[1;31m▌ Select Execution Mode [01/02] (default: 02):\033[0m ").strip()

    if choice in ["1", "01"]:
        subprocess.run([PYTHON_BIN, str(BASE_DIR / "terminal_app.py")])
    elif choice in ["2", "02", ""]:
        subprocess.run([PYTHON_BIN, str(BASE_DIR / "web_app.py")])
    elif choice in ["0", "00", "q", "exit"]:
        print("\033[2mReturning to stillness.\033[0m")
    else:
        print("Invalid choice.")

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n\033[2mReturning to stillness.\033[0m")
