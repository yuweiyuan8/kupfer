import click
import logging
import os

from glob import glob
from typing import Iterable, Optional

from config import config
from constants import Arch, ARCHES, REPOSITORIES
from exec.file import remove_file
from distro.distro import get_kupfer_local
from distro.package import LocalPackage
from ssh import run_ssh_command, scp_put_files
from utils import git
from wrapper import check_programs_wrap, enforce_wrap

from .build import build_packages_by_paths
from .pkgbuild import discover_pkgbuilds, filter_pkgbuilds, init_pkgbuilds
from .device import cmd_devices_list, get_profile_device
from .flavour import cmd_flavours_list


def build(
    paths: Iterable[str],
    force: bool,
    arch: Optional[Arch] = None,
    rebuild_dependants: bool = False,
    try_download: bool = False,
):
    config.enforce_profile_device_set(hint_or_set_arch=True)
    enforce_wrap()
    arch = arch or get_profile_device(hint_or_set_arch=True).arch

    if arch not in ARCHES:
        raise Exception(f'Unknown architecture "{arch}". Choices: {", ".join(ARCHES)}')

    return build_packages_by_paths(
        paths,
        arch,
        force=force,
        rebuild_dependants=rebuild_dependants,
        try_download=try_download,
        enable_crosscompile=config.file.build.crosscompile,
        enable_crossdirect=config.file.build.crossdirect,
        enable_ccache=config.file.build.ccache,
        clean_chroot=config.file.build.clean_mode,
    )


@click.group(name='packages')
def cmd_packages():
    """Build and manage packages and PKGBUILDs"""


cmd_packages.add_command(cmd_flavours_list, 'flavours')
cmd_packages.add_command(cmd_devices_list, 'devices')


@cmd_packages.command(name='update')
@click.option('--non-interactive', is_flag=True)
def cmd_update(non_interactive: bool = False):
    """Update PKGBUILDs git repo"""
    init_pkgbuilds(interactive=not non_interactive)
    logging.info("Refreshing SRCINFO caches")
    discover_pkgbuilds()


# alias "update" to "init"
cmd_packages.add_command(cmd_update, 'init')


@cmd_packages.command(name='build')
@click.option('--force', is_flag=True, default=False, help='Rebuild even if package is already built')
@click.option('--arch', default=None, required=False, type=click.Choice(ARCHES), help="The CPU architecture to build for")
@click.option('--rebuild-dependants', is_flag=True, default=False, help='Rebuild packages that depend on packages that will be [re]built')
@click.option('--no-download', is_flag=True, default=False, help="Don't try downloading packages from online repos before building")
@click.argument('paths', nargs=-1)
def cmd_build(paths: list[str], force=False, arch: Optional[Arch] = None, rebuild_dependants: bool = False, no_download: bool = False):
    """
    Build packages (and dependencies) by paths as required.

    The paths are specified relative to the PKGBUILDs dir, eg. "cross/crossdirect".

    Multiple paths may be specified as separate arguments.

    Packages that aren't built already will be downloaded from HTTPS repos unless --no-download is passed,
    if an exact version match exists on the server.
    """
    build(paths, force, arch=arch, rebuild_dependants=rebuild_dependants, try_download=not no_download)


@cmd_packages.command(name='sideload')
@click.argument('paths', nargs=-1)
@click.option('--arch', default=None, required=False, type=click.Choice(ARCHES), help="The CPU architecture to build for")
@click.option('-B', '--no-build', is_flag=True, default=False, help="Don't try to build packages, just copy and install")
def cmd_sideload(paths: Iterable[str], arch: Optional[Arch] = None, no_build: bool = False):
    """Build packages, copy to the device via SSH and install them"""
    if not paths:
        raise Exception("No packages specified")
    arch = arch or get_profile_device(hint_or_set_arch=True).arch
    if not no_build:
        build(paths, False, arch=arch, try_download=True)
    repo: dict[str, LocalPackage] = get_kupfer_local(arch=arch, scan=True, in_chroot=False).get_packages()
    files = [pkg.resolved_url.split('file://')[1] for pkg in repo.values() if pkg.resolved_url and pkg.name in paths]
    logging.debug(f"Sideload: Found package files: {files}")
    if not files:
        logging.fatal("No packages matched")
        return
    scp_put_files(files, '/tmp').check_returncode()
    run_ssh_command([
        'sudo',
        'pacman',
        '-U',
    ] + [os.path.join('/tmp', os.path.basename(file)) for file in files] + [
        '--noconfirm',
        "'--overwrite=\\*'",
    ],
                    alloc_tty=True).check_returncode()


@cmd_packages.command(name='clean')
@click.option('-f', '--force', is_flag=True, default=False, help="Don't prompt for confirmation")
@click.option('-n', '--noop', is_flag=True, default=False, help="Print what would be removed but dont execute")
@click.argument('what', type=click.Choice(['all', 'src', 'pkg']), nargs=-1)
def cmd_clean(what: Iterable[str] = ['all'], force: bool = False, noop: bool = False):
    """Remove files and directories not tracked in PKGBUILDs.git. Passing in an empty `what` defaults it to `['all']`"""
    if noop:
        logging.debug('Running in noop mode!')
    if force:
        logging.debug('Running in FORCE mode!')
    what = what or ['all']
    logging.debug(f'Clearing {what} from PKGBUILDs')
    pkgbuilds = config.get_path('pkgbuilds')
    if 'all' in what:
        check_programs_wrap(['git'])
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
        verb = 'Would remove' if noop else 'Removing'
        logging.info(verb + ' directories:\n' + dir_lines)

        if not (noop or force):
            if not click.confirm("Really remove all of these?", default=True):
                return

        for dir in dirs:
            if not noop:
                remove_file(dir, recursive=True)


@cmd_packages.command(name='list')
def cmd_list():
    "List information about available source packages (PKGBUILDs)"
    logging.info('Discovering packages.')
    check_programs_wrap(['makepkg', 'pacman'])
    packages = discover_pkgbuilds()
    logging.info(f'Done! {len(packages)} Pkgbuilds:')
    for p in set(packages.values()):
        print(
            f'name: {p.name}; ver: {p.version}; provides: {p.provides}; replaces: {p.replaces}; local_depends: {p.local_depends}; depends: {p.depends}'
        )


@cmd_packages.command(name='check')
@click.argument('paths', nargs=-1)
def cmd_check(paths):
    """Check that specified PKGBUILDs are formatted correctly"""
    check_programs_wrap(['makepkg'])

    def check_quoteworthy(s: str) -> bool:
        quoteworthy = ['"', "'", "$", " ", ";", "&", "<", ">", "*", "?"]
        for symbol in quoteworthy:
            if symbol in s:
                return True
        return False

    paths = list(paths) or ['all']
    packages = filter_pkgbuilds(paths, allow_empty_results=False)

    for package in packages:
        name = package.name

        is_git_package = False
        if name.endswith('-git'):
            is_git_package = True

        required_arches = ''
        provided_arches = []

        mode_key = '_mode'
        nodeps_key = '_nodeps'
        pkgbase_key = 'pkgbase'
        pkgname_key = 'pkgname'
        arches_key = '_arches'
        arch_key = 'arch'
        commit_key = '_commit'
        source_key = 'source'
        sha256sums_key = 'sha256sums'
        required = {
            mode_key: True,
            nodeps_key: False,
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

            if line.startswith('_') and line.split('=', 1)[0] not in [mode_key, nodeps_key, arches_key, commit_key]:
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

            if '"' in line and not check_quoteworthy(line):
                formatted = False
                reason = 'Found literal " although no special character was found in the line to justify the usage of a literal "'

            if "'" in line and not '"' in line:
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
                logging.warning(f'Missing {arch} in arches list in {path}, because _arches hint is `all`')
