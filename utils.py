import atexit
import logging
import subprocess
from shutil import which
from typing import Optional, Union, Sequence

from exec import run_cmd, run_root_cmd


def programs_available(programs: Union[str, Sequence[str]]) -> bool:
    if type(programs) is str:
        programs = [programs]
    for program in programs:
        if not which(program):
            return False
    return True


def umount(dest: str, lazy=False):
    return run_root_cmd(
        [
            'umount',
            '-c' + ('l' if lazy else ''),
            dest,
        ],
        capture_output=True,
    )


def mount(src: str, dest: str, options: list[str] = ['bind'], fs_type: Optional[str] = None, register_unmount=True) -> subprocess.CompletedProcess:
    opts = []
    for opt in options:
        opts += ['-o', opt]

    if fs_type:
        opts += ['-t', fs_type]

    result = run_root_cmd(
        ['mount'] + opts + [
            src,
            dest,
        ],
        capture_output=False,
    )
    if result.returncode == 0 and register_unmount:
        atexit.register(umount, dest)
    return result


def check_findmnt(path: str):
    result = run_root_cmd(
        [
            'findmnt',
            '-n',
            '-o',
            'source',
            path,
        ],
        capture_output=True,
    )
    return result.stdout.decode().strip()


def git(cmd: list[str], dir='.', capture_output=False, user: Optional[str] = None) -> subprocess.CompletedProcess:
    result = run_cmd(['git'] + cmd, cwd=dir, capture_output=capture_output, switch_user=user)
    assert isinstance(result, subprocess.CompletedProcess)
    return result


def log_or_exception(raise_exception: bool, msg: str, exc_class=Exception, log_level=logging.WARNING):
    if raise_exception:
        raise exc_class(msg)
    else:
        logging.log(log_level, msg)
