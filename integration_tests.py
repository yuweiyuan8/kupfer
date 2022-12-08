import click
import os
import pytest

from glob import glob
from subprocess import CompletedProcess

from config.state import config, CONFIG_DEFAULTS
from constants import SRCINFO_METADATA_FILE
from exec.cmd import run_cmd
from exec.file import get_temp_dir
from logger import setup_logging
from packages.cli import SRCINFO_CACHE_FILES, cmd_build, cmd_clean, cmd_update
from utils import git_get_branch

tempdir = None
config.try_load_file()
setup_logging(True)

PKG_TEST_PATH = 'device/device-sdm845-oneplus-enchilada'
PKG_TEST_NAME = 'device-sdm845-xiaomi-beryllium-ebbg'


@pytest.fixture()
def ctx() -> click.Context:
    global tempdir
    if not tempdir:
        tempdir = get_temp_dir()
    if not os.environ.get('INTEGRATION_TESTS_USE_GLOBAL_CONFIG', 'false').lower() == 'true':
        config.file.paths.update(CONFIG_DEFAULTS.paths | {'cache_dir': tempdir})
    config_path = os.path.join(tempdir, 'kupferbootstrap.toml')
    config.runtime.config_file = config_path
    if not os.path.exists(config_path):
        config.write()
    config.try_load_file(config_path)
    print(f'cache_dir: {config.file.paths.cache_dir}')
    return click.Context(click.Command('integration_tests'))


def test_config_load(ctx: click.Context):
    path = config.runtime.config_file
    assert path
    assert path.startswith('/tmp/')
    assert os.path.exists(path)
    config.enforce_config_loaded()


def test_packages_update(ctx: click.Context):
    pkgbuilds_path = config.get_path('pkgbuilds')
    kbs_branch = git_get_branch(config.runtime.script_source_dir)
    # Gitlab CI integration: the CI checks out a detached commit, branch comes back empty.
    if not kbs_branch and os.environ.get('CI', 'false') == 'true':
        kbs_branch = os.environ.get('CI_COMMIT_BRANCH', '')
    branches: dict[str, bool] = {'main': False, 'dev': False}
    if kbs_branch:
        branches[kbs_branch] = True
    for branch, may_fail in branches.items():
        config.file.pkgbuilds.git_branch = branch
        try:
            ctx.invoke(cmd_update, non_interactive=True, switch_branch=True, discard_changes=True, init_caches=False)
        except Exception as ex:
            print(f'may_fail: {may_fail}; Exception: {ex}')
            if not may_fail:
                raise ex
            # check branch really doesn't exist
            res = run_cmd(f"git ls-remote {CONFIG_DEFAULTS.pkgbuilds.git_repo} 'refs/heads/*' | grep 'refs/heads/{branch}'")
            assert isinstance(res, CompletedProcess)
            assert res.returncode != 0
            continue
        assert git_get_branch(pkgbuilds_path) == branch


def test_packages_clean(ctx: click.Context):
    if not glob(os.path.join(config.get_path('pkgbuilds'), '*', '*', SRCINFO_METADATA_FILE)):
        ctx.invoke(cmd_update, non_interactive=True)
    ctx.invoke(cmd_clean, what=['git'], force=True)


def test_packages_cache_init(ctx: click.Context):
    ctx.invoke(cmd_update, non_interactive=True, switch_branch=False, discard_changes=False, init_caches=True)

    for f in SRCINFO_CACHE_FILES:
        assert os.path.exists(os.path.join(config.get_path('pkgbuilds'), PKG_TEST_PATH, f))


def build_pkgs(_ctx: click.Context, query: list[str], arch: str = 'aarch64', **kwargs):
    _ctx.invoke(cmd_build, paths=query, arch=arch, **kwargs)


def test_packages_build_by_path(ctx: click.Context):
    build_pkgs(ctx, [PKG_TEST_PATH], force=True)


def test_split_package_build_by_name(ctx: click.Context):
    build_pkgs(ctx, [PKG_TEST_NAME])
