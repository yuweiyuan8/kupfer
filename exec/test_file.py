import pytest

import os
import tempfile

from typing import Union, Generator
from dataclasses import dataclass

from .cmd import run_root_cmd
from .file import write_file, chown
from utils import get_gid, get_uid


@dataclass
class TempdirFillInfo():
    path: str
    files: dict[str, str]


def get_tempdir():
    d = tempfile.mkdtemp()
    assert os.path.exists(d)
    return d


def remove_dir(d):
    run_root_cmd(['rm', '-rf', d]).check_returncode()


def create_file(filepath, owner='root', group='root'):
    assert not os.path.exists(filepath)
    run_root_cmd(['touch', filepath]).check_returncode()
    run_root_cmd(['chown', f'{owner}:{group}', filepath]).check_returncode()


@pytest.fixture
def tempdir():
    d = get_tempdir()
    yield d
    # cleanup, gets run after the test since we yield above
    remove_dir(d)


@pytest.fixture
def tempdir_filled() -> Generator[TempdirFillInfo, None, None]:
    d = get_tempdir()
    contents = {
        'rootfile': {
            'owner': 'root',
            'group': 'root',
        },
        'userfile': {
            'owner': 'nobody',
            'group': 'nobody',
        },
    }
    res = TempdirFillInfo(path=d, files={})
    for p, opts in contents.items():
        path = os.path.join(d, p)
        res.files[p] = path
        create_file(path, **opts)
    yield res
    # cleanup, gets run after the test since we yield above
    remove_dir(d)


def verify_ownership(filepath, user: Union[str, int], group: Union[str, int]):
    uid = get_uid(user)
    gid = get_gid(group)
    assert os.path.exists(filepath)
    fstat = os.stat(filepath)
    assert fstat.st_uid == uid
    assert fstat.st_gid == gid


def verify_content(filepath, content):
    assert os.path.exists(filepath)
    with open(filepath, 'r') as f:
        assert f.read().strip() == content.strip()


@pytest.mark.parametrize("user,group", [('root', 'root'), ('nobody', 'nobody')])
def test_chown(tempdir: str, user: str, group: str):
    assert os.path.exists(tempdir)
    target_uid = get_uid(user)
    target_gid = get_gid(group)
    chown(tempdir, target_uid, target_gid)
    verify_ownership(tempdir, target_uid, target_gid)


def test_tempdir_filled_fixture(tempdir_filled: TempdirFillInfo):
    files = tempdir_filled.files
    assert files
    assert 'rootfile' in files
    assert 'userfile' in files
    verify_ownership(files['rootfile'], 'root', 'root')
    verify_ownership(files['userfile'], 'nobody', 'nobody')


def test_write_new_file_naive(tempdir: str):
    assert os.path.exists(tempdir)
    new = os.path.join(tempdir, 'newfiletest')
    content = 'test12345'
    assert not os.path.exists(new)
    write_file(new, content)
    verify_content(new, content)
    verify_ownership(new, user=os.getuid(), group=os.getgid())


def test_write_new_file_root(tempdir: str):
    assert os.path.exists(tempdir)
    new = os.path.join(tempdir, 'newfiletest')
    content = 'test12345'
    assert not os.path.exists(new)
    write_file(new, content, user='root', group='root')
    verify_content(new, content)
    verify_ownership(new, user=0, group=0)


def test_write_new_file_user(tempdir: str):
    user = 'nobody'
    group = 'nobody'
    assert os.path.exists(tempdir)
    new = os.path.join(tempdir, 'newfiletest')
    content = 'test12345'
    assert not os.path.exists(new)
    write_file(new, content, user=user, group=group)
    assert os.path.exists(new)
    verify_content(new, content)
    verify_ownership(new, user=user, group=group)


def test_write_new_file_user_in_root_dir(tempdir: str):
    assert os.path.exists(tempdir)
    chown(tempdir, user='root', group='root')
    run_root_cmd(['chmod', '755', tempdir])
    verify_ownership(tempdir, 'root', 'root')
    test_write_new_file_user(tempdir)


def test_write_rootfile_naive(tempdir_filled: TempdirFillInfo):
    files = tempdir_filled.files
    assert 'rootfile' in files
    p = files['rootfile']
    assert os.path.exists(p)
    verify_ownership(p, 'root', 'root')
    content = 'test123'
    write_file(p, content)
    verify_content(p, 'test123')
    verify_ownership(p, 'root', 'root')


@pytest.mark.parametrize("user,group", [('root', 'root'), ('nobody', 'nobody')])
def test_write_rootfile(tempdir_filled: TempdirFillInfo, user: str, group: str):
    files = tempdir_filled.files
    assert 'rootfile' in files
    p = files['rootfile']
    assert os.path.exists(p)
    verify_ownership(p, 'root', 'root')
    content = 'test123'
    write_file(p, content)
    verify_content(p, 'test123')
    verify_ownership(p, 'root', 'root')
