import atexit
import logging
import os
import stat
import subprocess

from shutil import rmtree
from tempfile import mkdtemp
from typing import Optional, Union

from .cmd import run_root_cmd, elevation_noop, generate_cmd_su, wrap_in_bash, shell_quote
from utils import get_user_name, get_group_name


def try_native_filewrite(path: str, content: Union[str, bytes], chmod: Optional[str] = None) -> Optional[Exception]:
    "try writing with python open(), return None on success, return(!) Exception on failure"
    bflag = 'b' if isinstance(content, bytes) else ''
    try:
        kwargs = {}
        if chmod:
            kwargs['mode'] = chmod
        descriptor = os.open(path, **kwargs)  # type: ignore
        with open(descriptor, 'w' + bflag) as f:
            f.write(content)
    except Exception as ex:
        return ex
    return None


def chown(path: str, user: Optional[Union[str, int]] = None, group: Optional[Union[str, int]] = None, recursive: bool = False):
    owner = ''
    if user is not None:
        owner += get_user_name(user)
    if group is not None:
        owner += f':{get_group_name(group)}'
    if owner:
        cmd = ["chown"] + (['-R'] if recursive else [])
        result = run_root_cmd(cmd + [owner, path])
        assert isinstance(result, subprocess.CompletedProcess)
        if result.returncode:
            raise Exception(f"Failed to change owner of '{path}' to '{owner}'")


def chmod(path, mode: Union[int, str] = 0o0755, force_sticky=True):
    if not isinstance(mode, str):
        octal = oct(mode)[2:]
    else:
        octal = mode
    assert octal.isnumeric()
    octal = octal.rjust(3, '0')
    if force_sticky:
        octal = octal.rjust(4, '0')
    try:
        os.chmod(path, mode=octal)  # type: ignore
    except:
        cmd = ["chmod", octal, path]
        result = run_root_cmd(cmd)
        assert isinstance(result, subprocess.CompletedProcess)
        if result.returncode:
            raise Exception(f"Failed to set mode of '{path}' to '{chmod}'")


def root_check_exists(path):
    return os.path.exists(path) or run_root_cmd(['[', '-e', path, ']']).returncode == 0


def root_check_is_dir(path):
    return os.path.isdir(path) or run_root_cmd(['[', '-d', path, ']'])


def write_file(
    path: str,
    content: Union[str, bytes],
    lazy: bool = True,
    mode: Optional[str] = None,
    user: Optional[str] = None,
    group: Optional[str] = None,
):
    chmod_mode = ''
    chown_user = get_user_name(user) if user else None
    chown_group = get_group_name(group) if group else None
    fstat: os.stat_result
    exists = root_check_exists(path)
    dirname = os.path.dirname(path)
    failed = False
    if exists:
        try:
            fstat = os.stat(path)
        except PermissionError:
            failed = True
    else:
        chown_user = chown_user or get_user_name(os.getuid())
        chown_group = chown_group or get_group_name(os.getgid())
        dir_exists = root_check_exists(dirname)
        if not dir_exists or not root_check_is_dir(dirname):
            reason = "is not a directory" if dir_exists else "does not exist"
            raise Exception(f"Error writing file {path}, parent dir {reason}")
    if mode:
        if not mode.isnumeric():
            raise Exception(f"Unknown file mode '{mode}' (must be numeric): {path}")
        if not exists or failed or stat.filemode(int(mode, 8)) != stat.filemode(fstat.st_mode):
            chmod_mode = mode
    if not failed:
        failed = try_native_filewrite(path, content, chmod_mode) is not None
    if exists or failed:
        if failed:
            try:
                elevation_noop(attach_tty=True)  # avoid password prompt while writing file
                logging.debug(f"Writing to {path} using elevated /bin/tee")
                cmd: list[str] = generate_cmd_su(wrap_in_bash(f'tee {shell_quote(path)} >/dev/null', flatten_result=False), 'root')  # type: ignore
                assert isinstance(cmd, list)
                s = subprocess.Popen(
                    cmd,
                    text=(not isinstance(content, bytes)),
                    stdin=subprocess.PIPE,
                )
                s.communicate(content)
                s.wait(300)  # 5 minute timeout
                if s.returncode:
                    raise Exception(f"Write command excited non-zero: {s.returncode}")
            except Exception as ex:
                logging.fatal(f"Writing to file '{path}' with elevated privileges failed")
                raise ex
        if chmod_mode:
            chmod(path, chmod_mode)

    chown(path, chown_user, chown_group)


def root_write_file(*args, **kwargs):
    kwargs['user'] = 'root'
    kwargs['group'] = 'root'
    return write_file(*args, **kwargs)


def remove_file(path: str, recursive=False):
    try:
        rm = rmtree if recursive else os.unlink
        rm(path)  # type: ignore
    except:
        cmd = ['rm'] + (['-r'] if recursive else []) + [path]
        rc = run_root_cmd(cmd).returncode
        if rc:
            raise Exception(f"Unable to remove {path}: cmd returned {rc}")


def makedir(path, user: Optional[Union[str, int]] = None, group: Optional[Union[str, int]] = None, parents: bool = True):
    if not root_check_exists(path):
        try:
            if parents:
                os.makedirs(path, exist_ok=True)
            else:
                os.mkdir(path)
        except:
            run_root_cmd(['mkdir'] + (['-p'] if parents else []) + [path])
    chown(path, user, group)


def root_makedir(path, parents: bool = True):
    return makedir(path, user='root', group='root', parents=parents)


def symlink(source, target):
    try:
        os.symlink(source, target)
    except:
        run_root_cmd(['ln', '-s', source, target])


def get_temp_dir(register_cleanup=True, mode: int = 0o0755):
    "create a new tempdir and sanitize ownership so root can access user files as god intended"
    t = mkdtemp()
    chmod(t, mode)
    if register_cleanup:
        atexit.register(remove_file, t, recursive=True)
    return t
