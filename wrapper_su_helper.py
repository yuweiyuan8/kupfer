#!/bin/python3

import click
import pwd
import os

from logger import logging, setup_logging

from constants import WRAPPER_ENV_VAR
from exec.cmd import run_cmd, flatten_shell_script
from exec.file import chown


@click.command('kupferbootstrap_su')
@click.option('--username', default='kupfer', help="The user's name. If --uid is provided, the user's uid will be changed to this in passwd")
@click.option('--uid', default=1000, type=int, help='uid to change $username to and run as')
@click.argument('cmd', type=str, nargs=-1)
def kupferbootstrap_su(cmd: list[str], uid: int = 1000, username: str = 'kupfer'):
    "Changes `username`'s uid to `uid` and executes kupferbootstrap as that user"
    cmd = list(cmd)
    user = pwd.getpwnam(username)
    home = user.pw_dir
    if uid != user.pw_uid:
        run_cmd(['usermod', '-u', str(uid), username]).check_returncode()  # type: ignore[union-attr]
        chown(home, username, recursive=False)
    logging.debug(f'wrapper_su_helper: running {cmd} as {repr(username)}')
    env_inject = ['env', f'{WRAPPER_ENV_VAR}={os.environ[WRAPPER_ENV_VAR]}'] if WRAPPER_ENV_VAR in os.environ else []
    su_cmd = ['sudo', *env_inject, 'su', '-P', username, '-c', flatten_shell_script(cmd, wrap_in_shell_quote=True, shell_quote_items=True)]
    result = run_cmd(su_cmd, attach_tty=True)
    assert isinstance(result, int)
    exit(result)


if __name__ == '__main__':
    setup_logging(True)
    kupferbootstrap_su(prog_name='kupferbootstrap_su_helper')
