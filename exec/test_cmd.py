import logging
import os
import pwd
import subprocess

from typing import Optional

from .cmd import run_cmd, run_root_cmd, generate_cmd_su


def get_username(id: int):
    return pwd.getpwuid(id).pw_name


def run_func(f, expected_user: Optional[str] = None, **kwargs):
    current_uid = os.getuid()
    current_username = get_username(current_uid)
    target_uid = current_uid
    result = f(['id', '-u'], capture_output=True, **kwargs)
    assert isinstance(result, subprocess.CompletedProcess)
    result.check_returncode()
    if expected_user and current_username != expected_user:
        target_uid = pwd.getpwnam(expected_user).pw_uid
    result_uid = result.stdout.decode()
    assert int(result_uid) == target_uid


def run_generate_and_exec(script, generate_args={}, switch_user=None, **kwargs):
    "runs generate_cmd_su() and executes the resulting argv"
    if not switch_user:
        switch_user = get_username(os.getuid())
    cmd = generate_cmd_su(script, switch_user=switch_user, **generate_args)
    logging.debug(f'run_generate_and_exec: running {cmd}')
    return subprocess.run(
        cmd,
        **kwargs,
    )


def test_generate_su_force_su():
    run_func(run_generate_and_exec, generate_args={'force_su': True})


def test_generate_su_force_elevate():
    run_func(run_generate_and_exec, generate_args={'force_elevate': True}, expected_user='root', switch_user='root')


def test_generate_su_nobody_force_su():
    user = 'nobody'
    run_func(run_generate_and_exec, expected_user=user, switch_user=user, generate_args={'force_su': True})


def test_generate_su_nobody_force_su_and_elevate():
    user = 'nobody'
    run_func(run_generate_and_exec, expected_user=user, switch_user=user, generate_args={'force_su': True, 'force_elevate': True})


def test_run_cmd():
    run_func(run_cmd)


def test_run_cmd_su_nobody():
    user = 'nobody'
    run_func(run_cmd, expected_user=user, switch_user=user)


def test_run_cmd_as_root():
    run_func(run_cmd, expected_user='root', switch_user='root')


def test_run_root_cmd():
    run_func(run_root_cmd, expected_user='root')
