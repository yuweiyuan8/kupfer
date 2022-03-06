import click
import logging
import os
from glob import glob
from shutil import rmtree
from typing import Iterable, Optional

from config import config
from constants import REPOSITORIES, ARCHES, Arch
from ssh import run_ssh_command, scp_put_files
from wrapper import enforce_wrap
from utils import git

#from .pkgbuild import Pkgbuild
from .local_repo import LocalRepo, get_repo


def build(paths: Iterable[str], force: bool, arch: Optional[Arch]):
    # TODO: arch = config.get_profile()...
    arch = arch or 'aarch64'

    if arch not in ARCHES:
        raise Exception(f'Unknown architecture "{arch}". Choices: {", ".join(ARCHES)}')
    enforce_wrap()
    config.enforce_config_loaded()
    local_repo = get_repo()
    local_repo.init(arch)
    # repo: dict[str, Pkgbuild] = local_repo.discover_packages()
    if arch != config.runtime['arch']:
        local_repo.build_enable_qemu_binfmt(arch)

    return local_repo.pkgbuilds.build_packages_by_paths(
        paths,
        arch,
        force=force,
        enable_crosscompile=config.file['build']['crosscompile'],
        enable_crossdirect=config.file['build']['crossdirect'],
        enable_ccache=config.file['build']['ccache'],
        clean_chroot=config.file['build']['clean_mode'],
    )


@click.group(name='packages')
def cmd_packages():
    """Build and manage packages and PKGBUILDs"""


@cmd_packages.command(name='update')
@click.option('--non-interactive', is_flag=True)
def cmd_update(non_interactive: bool = False):
    """Update PKGBUILDs git repo"""
    enforce_wrap()
    get_repo().pkgbuilds.init(interactive=not non_interactive)


@cmd_packages.command(name='build')
@click.option('--force', is_flag=True, default=False, help='Rebuild even if package is already built')
@click.option('--arch', default=None, help="The CPU architecture to build for")
@click.argument('paths', nargs=-1)
def cmd_build(paths: list[str], force=False, arch=None):
    """
    Build packages by paths.

    The paths are specified relative to the PKGBUILDs dir, eg. "cross/crossdirect".

    Multiple paths may be specified as separate arguments.
    """
    build(paths, force, arch)


@cmd_packages.command(name='sideload')
@click.argument('paths', nargs=-1)
def cmd_sideload(paths: Iterable[str]):
    """Build packages, copy to the device via SSH and install them"""
    files = build(paths, True, None)
    scp_put_files(files, '/tmp')
    run_ssh_command([
        'sudo',
        '-S',
        'pacman',
        '-U',
    ] + [os.path.join('/tmp', os.path.basename(file)) for file in files] + [
        '--noconfirm',
        '--overwrite=*',
    ])


@cmd_packages.command(name='clean')
@click.option('-f', '--force', is_flag=True, default=False, help="Don't prompt for confirmation")
@click.option('-n', '--noop', is_flag=True, default=False, help="Print what would be removed but dont execute")
@click.argument('what', type=click.Choice(['all', 'src', 'pkg']), nargs=-1)
def cmd_clean(what: Iterable[str] = ['all'], force: bool = False, noop: bool = False):
    """Remove files and directories not tracked in PKGBUILDs.git"""
    enforce_wrap()
    if noop:
        logging.debug('Running in noop mode!')
    if force:
        logging.debug('Running in FORCE mode!')
    pkgbuilds = config.get_path('pkgbuilds')
    if 'all' in what:
        warning = "Really reset PKGBUILDs to git state completely?\nThis will erase any untracked changes to your PKGBUILDs directory."
        if not (noop or force or click.confirm(warning)):
            return
        result = git(
            [
                'clean',
                '-dffX' + ('n' if noop else ''),
            ] + REPOSITORIES,
            dir=pkgbuilds,
        )
        if result.returncode != 0:
            logging.fatal('Failed to git clean')
            exit(1)
    else:
        what = set(what)
        dirs = []
        for loc in ['pkg', 'src']:
            if loc in what:
                logging.info(f'gathering {loc} directories')
                dirs += glob(os.path.join(pkgbuilds, '*', '*', loc))

        dir_lines = '\n'.join(dirs)
        verb = 'Would remove' if noop or force else 'Removing'
        logging.info(verb + ' directories:\n' + dir_lines)

        if not (noop or force):
            if not click.confirm("Really remove all of these?", default=True):
                return

        for dir in dirs:
            if not noop:
                rmtree(dir)


@cmd_packages.command(name='list')
def cmd_list():
    enforce_wrap()
    repo = get_repo()
    logging.info('Discovering packages.')
    packages = repo.discover_packages()
    logging.info('Done! Pkgbuilds:')
    for p in set(packages.values()):
        print(
            f'name: {p.name}; ver: {p.version}; provides: {p.provides}; replaces: {p.replaces}; local_depends: {p.local_depends}; depends: {p.depends}'
        )


@cmd_packages.command(name='check')
@click.argument('paths', nargs=-1)
def cmd_check(paths: list[str]):
    """Check that specified PKGBUILDs are formatted correctly"""
    enforce_wrap()
    paths = list(paths)
    repo = get_repo()
    packages = repo.pkgbuilds.filter_packages_by_paths(paths, allow_empty_results=False)

    for package in packages:
        name = package.name

        is_git_package = False
        if name.endswith('-git'):
            is_git_package = True

        required_arches = ''
        provided_arches: list[str] = []

        mode_key = '_mode'
        pkgbase_key = 'pkgbase'
        pkgname_key = 'pkgname'
        arches_key = '_arches'
        arch_key = 'arch'
        commit_key = '_commit'
        source_key = 'source'
        sha256sums_key = 'sha256sums'
        required = {
            mode_key: True,
            pkgbase_key: False,
            pkgname_key: True,
            'pkgdesc': False,
            'pkgver': True,
            'pkgrel': True,
            arches_key: True,
            arch_key: True,
            'license': True,
            'url': False,
            'provides': is_git_package,
            'conflicts': False,
            'depends': False,
            'optdepends': False,
            'makedepends': False,
            'backup': False,
            'install': False,
            'options': False,
            commit_key: is_git_package,
            source_key: False,
            sha256sums_key: False,
        }
        pkgbuild_path = os.path.join(config.get_path('pkgbuilds'), package.path, 'PKGBUILD')
        with open(pkgbuild_path, 'r') as file:
            content = file.read()
            if '\t' in content:
                logging.fatal(f'\\t is not allowed in {pkgbuild_path}')
                exit(1)
            lines = content.split('\n')
            if len(lines) == 0:
                logging.fatal(f'Empty {pkgbuild_path}')
                exit(1)
            line_index = 0
            key_index = 0
            hold_key = False
            key = ""
            while True:
                line = lines[line_index]

                if line.startswith('#'):
                    line_index += 1
                    continue

                if line.startswith('_') and not line.startswith(mode_key) and not line.startswith(arches_key) and not line.startswith(commit_key):
                    line_index += 1
                    continue

                formatted = True
                next_key = False
                next_line = False
                reason = ""

                if hold_key:
                    next_line = True
                else:
                    if key_index < len(required):
                        key = list(required)[key_index]
                        if line.startswith(key):
                            if key == pkgbase_key:
                                required[pkgname_key] = False
                            if key == source_key:
                                required[sha256sums_key] = True
                            next_key = True
                            next_line = True
                        elif key in required and not required[key]:
                            next_key = True

                if line == ')':
                    hold_key = False
                    next_key = True

                if key == arches_key:
                    required_arches = line.split('=')[1]

                if line.endswith('=('):
                    hold_key = True

                if line.startswith('    ') or line == ')':
                    next_line = True

                if line.startswith('  ') and not line.startswith('    '):
                    formatted = False
                    reason = 'Multiline variables should be indented with 4 spaces'

                if '"' in line and '$' not in line and ' ' not in line and ';' not in line:
                    formatted = False
                    reason = 'Found literal " although no "$", " " or ";" was found in the line justifying the usage of a literal "'

                if '\'' in line:
                    formatted = False
                    reason = 'Found literal \' although either a literal " or no qoutes should be used'

                if ('=(' in line and ' ' in line and '"' not in line and not line.endswith('=(')) or (hold_key and line.endswith(')')):
                    formatted = False
                    reason = 'Multiple elements in a list need to be in separate lines'

                if formatted and not next_key and not next_line:
                    if key_index == len(required):
                        if lines[line_index] == '':
                            break
                        else:
                            formatted = False
                            reason = 'Expected final emtpy line after all variables'
                    else:
                        formatted = False
                        reason = f'Expected to find "{key}"'

                if not formatted:
                    logging.fatal(f'Formatting error in {pkgbuild_path}: Line {line_index+1}: "{line}"')
                    if reason != "":
                        logging.fatal(reason)
                    exit(1)

                if key == arch_key:
                    if line.endswith(')'):
                        if line.startswith(f'{arch_key}=('):
                            check_arches_hint(pkgbuild_path, required_arches, [line[6:-1]])
                        else:
                            check_arches_hint(pkgbuild_path, required_arches, provided_arches)
                    elif line.startswith('    '):
                        provided_arches.append(line[4:])

                if next_key and not hold_key:
                    key_index += 1
                if next_line:
                    line_index += 1

        logging.info(f'{package.path} nicely formatted!')


def check_arches_hint(path: str, required: str, provided: list[str]):
    if required == 'all':
        for arch in ARCHES:
            if arch not in provided:
                logging.warning(f'Missing {arch} in arches list in {path}, because hint is `all`')
