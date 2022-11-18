import click
import sys

from enlighten import Counter, Manager, get_manager as _getmanager
from typing import Hashable, Optional

from config.state import config

BAR_PADDING = 25
DEFAULT_OUTPUT = sys.stderr

managers: dict[Hashable, Manager] = {}

progress_bars_option = click.option(
    '--force-progress-bars/--no-progress-bars',
    is_flag=True,
    default=None,
    help='Force enable/disable progress bars. Defaults to autodetection.',
)


def get_manager(file=DEFAULT_OUTPUT, enabled: Optional[bool] = None) -> Manager:
    global managers
    m = managers.get(file, None)
    if not m:
        kwargs = {}
        if enabled is None or config.runtime.progress_bars is False:
            enabled = config.runtime.progress_bars
        if enabled is not None:
            kwargs = {"enabled": enabled}
        m = _getmanager(file, **kwargs)
        managers[file] = m
    return m


def get_progress_bar(*kargs, file=DEFAULT_OUTPUT, leave=False, **kwargs) -> Counter:
    m = get_manager(file=file)

    kwargs["file"] = file
    kwargs["leave"] = leave
    return m.counter(*kargs, **kwargs)


def get_levels_bar(*kargs, file=DEFAULT_OUTPUT, enable_rate=True, **kwargs):
    kwargs["fields"] = {"name": "None", "level": 1, "levels_total": 1} | (kwargs.get("fields", None) or {})
    f = (u'{desc}: {name}{desc_pad}{percentage:3.0f}%|{bar}| '
         u'{count:{len_total}d}/{total:d} '
         u'[lvl: {level}/{levels_total}] ')
    if enable_rate:
        f += u'[{elapsed}<{eta}, {rate:.2f}{unit_pad}{unit}/s]'
    kwargs["bar_format"] = f
    return get_progress_bar(*kargs, **kwargs)
