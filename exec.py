import logging
import os
import pwd
import subprocess

from shlex import quote as shell_quote
from typing import Optional, Union, TypeAlias

ElevationMethod: TypeAlias = str

# as long as **only** sudo is supported, hardcode the default into ELEVATION_METHOD_DEFAULT.
# when other methods are added, all mentions of ELEVATION_METHOD_DEFAULT should be replaced by a config key.

ELEVATION_METHOD_DEFAULT = "sudo"

ELEVATION_METHODS: dict[ElevationMethod, list[str]] = {
    "sudo": ['sudo', '--'],
}


def generate_env_cmd(env: dict[str, str]):
    return ['/usr/bin/env'] + [f'{key}={value}' for key, value in env.items()]


def flatten_shell_script(script: Union[list[str], str], shell_quote_items: bool = False, wrap_in_shell_quote=False) -> str:
    """
    takes a shell-script and returns a flattened string for consumption with `sh -c`.

    `shell_quote_items` should only be used on `script` arrays that have no shell magic anymore,
    e.g. `['bash', '-c', 'echo $USER']`, which would return the string `'bash' '-c' 'echo user'`,
    which is suited for consumption by another bash -c process.
    """
    if not isinstance(script, str) and isinstance(script, list):
        cmds = script
        if shell_quote_items:
            cmds = [shell_quote(i) for i in cmds]
        script = " ".join(cmds)
    if wrap_in_shell_quote:
        script = shell_quote(script)
    return script


def wrap_in_bash(cmd: Union[list[str], str], flatten_result=True) -> Union[str, list[str]]:
    res: Union[str, list[str]] = ['/bin/bash', '-c', flatten_shell_script(cmd, shell_quote_items=False, wrap_in_shell_quote=False)]
    if flatten_result:
        res = flatten_shell_script(res, shell_quote_items=True, wrap_in_shell_quote=False)
    return res


def generate_cmd_elevated(cmd: list[str], elevation_method: ElevationMethod):
    "wraps `cmd` in the necessary commands to escalate, e.g. `['sudo', '--', cmd]`."
    if elevation_method not in ELEVATION_METHODS:
        raise Exception(f"Unknown elevation method {elevation_method}")
    return ELEVATION_METHODS[elevation_method] + cmd


def generate_cmd_su(cmd: list[str], switch_user: str, elevation_method: Optional[ElevationMethod] = None):
    """
    returns cmd to escalate (e.g. sudo) and switch users (su) to run `cmd` as `switch_user` as necessary.
    If `switch_user` is neither the current user nor root, cmd will have to be flattened into a single string.
    A result might look like `['sudo', '--', 'su', '-s', '/bin/bash', '-c', cmd_as_a_string]`.
    """
    current_uid = os.getuid()
    if pwd.getpwuid(current_uid).pw_name != switch_user:
        if switch_user != 'root':
            cmd = ['/bin/su', switch_user, '-s', '/bin/bash', '-c', flatten_shell_script(cmd, shell_quote_items=True)]
        if current_uid != 0:  # in order to use `/bin/su`, we have to be root first.
            cmd = generate_cmd_elevated(cmd, elevation_method or ELEVATION_METHOD_DEFAULT)

    return cmd


def run_cmd(
    script: Union[str, list[str]],
    env: dict[str, str] = {},
    attach_tty: bool = False,
    capture_output: bool = False,
    cwd: Optional[str] = None,
    stdout: Optional[int] = None,
    switch_user: Optional[str] = None,
    elevation_method: Optional[ElevationMethod] = None,
) -> Union[int, subprocess.CompletedProcess]:
    "execute `script` as `switch_user`, elevating and su'ing as necessary"
    kwargs: dict = {}
    env_cmd = []
    if env:
        env_cmd = generate_env_cmd(env)
        kwargs['env'] = env
    if not attach_tty:
        kwargs |= {'stdout': stdout} if stdout else {'capture_output': capture_output}

    script = flatten_shell_script(script)
    if cwd:
        kwargs['cwd'] = cwd
    wrapped_script: list[str] = wrap_in_bash(script, flatten_result=False)  # type: ignore
    cmd = env_cmd + wrapped_script
    if switch_user:
        cmd = generate_cmd_su(cmd, switch_user, elevation_method=elevation_method)
    logging.debug(f'Running cmd: "{cmd}"')
    if attach_tty:
        return subprocess.call(cmd, **kwargs)
    else:
        return subprocess.run(cmd, **kwargs)


def run_root_cmd(*kargs, **kwargs):
    kwargs['switch_user'] = 'root'
    return run_cmd(*kargs, **kwargs)
