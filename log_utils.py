from __future__ import annotations

import builtins
import logging
import sys
from datetime import datetime
from pathlib import Path

from rich.console import Console


_ORIGINAL_PRINT = builtins.print
_ORIGINAL_CONSOLE_PRINT = Console.print
_INITIALIZED = False


class _TeeStream:
    def __init__(self, original, log_file):
        self._original = original
        self._log_file = log_file

    def write(self, data):
        self._original.write(data)
        self._log_file.write(data)
        return len(data)

    def flush(self):
        self._original.flush()
        self._log_file.flush()

    def isatty(self):
        return self._original.isatty()

    def __getattr__(self, name):
        return getattr(self._original, name)


def _prefix_for_frame(frame) -> str:
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    path = Path(frame.f_code.co_filename).name
    return f"{now} {path}:{frame.f_lineno}"


def _wrap_print():
    def wrapped_print(*args, **kwargs):
        frame = sys._getframe(1)
        prefix = _prefix_for_frame(frame)
        _ORIGINAL_PRINT(prefix, *args, **kwargs)

    builtins.print = wrapped_print


def _wrap_console_print():
    def wrapped_console_print(self, *args, **kwargs):
        frame = sys._getframe(1)
        prefix = _prefix_for_frame(frame)
        if args and all(isinstance(arg, str) for arg in args):
            _ORIGINAL_CONSOLE_PRINT(self, prefix, *args, **kwargs)
            return
        _ORIGINAL_CONSOLE_PRINT(self, prefix, markup=False)
        if args:
            _ORIGINAL_CONSOLE_PRINT(self, *args, **kwargs)

    Console.print = wrapped_console_print


def setup_file_logging(log_name: str | None = None):
    global _INITIALIZED
    if _INITIALIZED:
        return

    base_dir = Path(__file__).resolve().parent
    log_dir = base_dir / "logs"
    log_dir.mkdir(exist_ok=True)

    script_name = log_name or Path(sys.argv[0]).stem or "app"
    log_path = log_dir / f"{script_name}.log"
    log_file = log_path.open("a", encoding="utf-8", buffering=1)

    sys.stdout = _TeeStream(sys.stdout, log_file)
    sys.stderr = _TeeStream(sys.stderr, log_file)

    _wrap_print()
    _wrap_console_print()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(filename)s:%(lineno)d] %(levelname)s %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        handlers=[logging.StreamHandler(sys.stderr)],
        force=True,
    )

    _INITIALIZED = True
