"""Native folder dialog (tkinter on Windows/Linux, AppleScript on macOS) for the 8081 console. A browser never reveals a folder's absolute path, so the local
companion server runs this helper and reads the choice from stdout: one JSON line {"path": "..."} ("" = cancelled)."""
import json
import sys


def _pick_macos(initial: str) -> int:
    """macOS: the CommandLineTools Python ships Tk 8.5, which aborts on macOS 26 ("macOS 26 (2601) or later required,
    have instead 16 (1601)"). The native AppleScript folder dialog needs no Tk and works with any Python."""
    import subprocess
    script = ['on run argv', 'activate']
    if initial:
        script.append('set p to POSIX path of (choose folder with prompt "AI DEV DIRECTOR - 작업할 폴더 선택" '
                      'default location (POSIX file (item 1 of argv)))')
    else:
        script.append('set p to POSIX path of (choose folder with prompt "AI DEV DIRECTOR - 작업할 폴더 선택")')
    script += ['return p', 'end run']
    cmd = ['osascript']
    for line in script:
        cmd += ['-e', line]
    if initial:
        cmd.append(initial)
    out = subprocess.run(cmd, capture_output=True)
    if out.returncode != 0:
        if b'-128' in out.stderr:                 # user pressed Cancel
            chosen = ''
        else:
            sys.stderr.write(out.stderr.decode('utf-8', 'replace'))
            return 1
    else:
        chosen = out.stdout.decode('utf-8').strip()
        if len(chosen) > 1:
            chosen = chosen.rstrip('/')
    sys.stdout.buffer.write(json.dumps({"path": chosen}).encode("ascii"))
    return 0


def main() -> int:
    if sys.platform == 'darwin':
        return _pick_macos(sys.argv[1] if len(sys.argv) > 1 else "")
    try:
        import tkinter
        from tkinter import filedialog
    except ImportError:          # e.g. Homebrew python on macOS without python-tk: type the path in the console instead
        sys.stderr.write("tkinter unavailable\n")
        return 1
    initial = sys.argv[1] if len(sys.argv) > 1 else ""
    root = tkinter.Tk()
    root.withdraw()
    root.attributes("-topmost", True)          # the dialog must not open behind the browser
    root.update()
    chosen = filedialog.askdirectory(parent=root, initialdir=initial or None, mustexist=True,
                                     title="AI DEV DIRECTOR - 작업할 폴더 선택")
    root.destroy()
    sys.stdout.buffer.write(json.dumps({"path": chosen or ""}).encode("ascii"))   # ensure_ascii: safe on any codepage
    return 0


if __name__ == "__main__":
    sys.exit(main())
