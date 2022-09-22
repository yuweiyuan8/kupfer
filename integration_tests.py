import click
import os
import pytest

from glob import glob

from config import config, CONFIG_DEFAULTS
from constants import SRCINFO_METADATA_FILE
from exec.file import get_temp_dir
from logger import setup_logging
from packages.cli import cmd_clean, cmd_update
from utils import git_get_branch

tempdir = None
config.try_load_file()
setup_logging(True)


@pytest.fixture()
def ctx() -> click.Context:
    global tempdir
    if not os.environ.get('INTEGRATION_TESTS_USE_GLOBAL_CONFIG', 'false').lower() == 'true':
        if not tempdir:
            tempdir = get_temp_dir()
        config.file.paths.update(CONFIG_DEFAULTS.paths | {'cache_dir': tempdir})
    print(f'cache_dir: {config.file.paths.cache_dir}')
    return click.Context(click.Command('integration_tests'))


def test_packages_update(ctx: click.Context):
    kbs_branch = git_get_branch(config.runtime.script_source_dir)
    pkgbuilds_path = config.get_path('pkgbuilds')
    for branch, may_fail in {'main': False, 'dev': False, kbs_branch: True}.items():
        config.file.pkgbuilds.git_branch = branch
        try:
            ctx.invoke(cmd_update, non_interactive=True, switch_branch=True)
        except Exception as ex:
            print(f'may_fail: {may_fail}; Exception: {ex}')
            if not may_fail:
                raise ex
            continue
        assert git_get_branch(pkgbuilds_path) == branch


def test_packages_clean(ctx: click.Context):
    if not glob(os.path.join(config.get_path('pkgbuilds'), '*', '*', SRCINFO_METADATA_FILE)):
        ctx.invoke(cmd_update, non_interactive=True)
    ctx.invoke(cmd_clean, what=['git'], force=True)
