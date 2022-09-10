import atexit
import grp
import hashlib
import logging
import os
import pwd
import subprocess
import tarfile

from shutil import which
from typing import Generator, IO, Optional, Union, Sequence

from exec.cmd import run_cmd, run_root_cmd

_programs_available = dict[str, bool]()


def programs_available(programs: Union[str, Sequence[str]], lazy: bool = True) -> bool:
    global _programs_available
    if type(programs) is str:
        programs = [programs]
    for program in programs:
        if program not in _programs_available or not lazy:
            avail = bool(which(program))
            _programs_available[program] = avail
        if not _programs_available[program]:
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


def git(cmd: list[str], dir: Optional[str] = None, capture_output=False, user: Optional[str] = None) -> subprocess.CompletedProcess:
    result = run_cmd(['git'] + cmd, cwd=dir, capture_output=capture_output, switch_user=user)
    assert isinstance(result, subprocess.CompletedProcess)
    return result


def log_or_exception(raise_exception: bool, msg: str, exc_class=Exception, log_level=logging.WARNING):
    if raise_exception:
        raise exc_class(msg)
    else:
        logging.log(log_level, msg)


def get_user_name(uid: Union[str, int]) -> str:
    if isinstance(uid, int) or uid.isnumeric():
        return pwd.getpwuid(int(uid)).pw_name
    return uid


def get_group_name(gid: Union[str, int]) -> str:
    if isinstance(gid, int) or gid.isnumeric():
        return grp.getgrgid(int(gid)).gr_name
    return gid


def get_uid(user: Union[int, str]) -> int:
    if isinstance(user, int) or user.isnumeric():
        return int(user)
    return pwd.getpwnam(user).pw_uid


def get_gid(group: Union[int, str]) -> int:
    if isinstance(group, int) or group.isnumeric():
        return int(group)
    return grp.getgrnam(group).gr_gid


def read_files_from_tar(tar_file: str, files: Sequence[str]) -> Generator[tuple[str, IO], None, None]:
    assert os.path.exists(tar_file)
    with tarfile.open(tar_file) as index:
        for path in files:
            fd = index.extractfile(index.getmember(path))
            assert fd
            yield path, fd


# stackoverflow magic from https://stackoverflow.com/a/44873382
def sha256sum(filename):
    h = hashlib.sha256()
    b = bytearray(128 * 1024)
    mv = memoryview(b)
    with open(filename, 'rb', buffering=0) as f:
        while n := f.readinto(mv):
            h.update(mv[:n])
    return h.hexdigest()
