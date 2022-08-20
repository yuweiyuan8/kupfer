from __future__ import annotations

import click
import logging
import multiprocessing
import os
import subprocess

from constants import REPOSITORIES
from joblib import Parallel, delayed
from typing import Optional, Sequence

from config import config, ConfigStateHolder
from exec.cmd import run_cmd
from constants import Arch, MAKEPKG_CMD
from distro.package import PackageInfo
from logger import setup_logging
from utils import git
from wrapper import check_programs_wrap


def clone_pkbuilds(pkgbuilds_dir: str, repo_url: str, branch: str, interactive=False, update=True):
    check_programs_wrap(['git'])
    git_dir = os.path.join(pkgbuilds_dir, '.git')
    if not os.path.exists(git_dir):
        logging.info('Cloning branch {branch} from {repo}')
        result = git(['clone', '-b', branch, repo_url, pkgbuilds_dir])
        if result.returncode != 0:
            raise Exception('Error cloning pkgbuilds')
    else:
        result = git(['--git-dir', git_dir, 'branch', '--show-current'], capture_output=True)
        current_branch = result.stdout.decode().strip()
        if current_branch != branch:
            logging.warning(f'pkgbuilds repository is on the wrong branch: {current_branch}, requested: {branch}')
            if interactive and click.confirm('Would you like to switch branches?', default=False):
                result = git(['switch', branch], dir=pkgbuilds_dir)
                if result.returncode != 0:
                    raise Exception('failed switching branches')
        if update:
            if interactive:
                if not click.confirm('Would you like to try updating the PKGBUILDs repo?'):
                    return
            result = git(['pull'], pkgbuilds_dir)
            if result.returncode != 0:
                raise Exception('failed to update pkgbuilds')


def init_pkgbuilds(interactive=False):
    pkgbuilds_dir = config.get_path('pkgbuilds')
    repo_url = config.file['pkgbuilds']['git_repo']
    branch = config.file['pkgbuilds']['git_branch']
    clone_pkbuilds(pkgbuilds_dir, repo_url, branch, interactive=interactive, update=False)


class Pkgbuild(PackageInfo):
    name: str
    version: str
    arches: list[Arch]
    depends: list[str]
    provides: list[str]
    replaces: list[str]
    local_depends: list[str]
    repo: str
    mode: str
    path: str
    pkgver: str
    pkgrel: str

    def __init__(
        self,
        relative_path: str,
        arches: list[Arch] = [],
        depends: list[str] = [],
        provides: list[str] = [],
        replaces: list[str] = [],
        repo: Optional[str] = None,
    ) -> None:
        """
        Create new Pkgbuild representation for file located at `{relative_path}/PKGBUILD`.
        `relative_path` will be stored in `self.path`.
        """
        self.name = os.path.basename(relative_path)
        self.version = ''
        self.arches = list(arches)
        self.depends = list(depends)
        self.provides = list(provides)
        self.replaces = list(replaces)
        self.local_depends = []
        self.repo = repo or ''
        self.mode = ''
        self.path = relative_path
        self.pkgver = ''
        self.pkgrel = ''

    def __repr__(self):
        return f'Pkgbuild({self.name},{repr(self.path)},{self.version},{self.mode})'

    def names(self):
        return list(set([self.name] + self.provides + self.replaces))

    def update_version(self):
        """updates `self.version` from `self.pkgver` and `self.pkgrel`"""
        self.version = f'{self.pkgver}-{self.pkgrel}'


class Pkgbase(Pkgbuild):
    subpackages: Sequence[SubPkgbuild]

    def __init__(self, relative_path: str, subpackages: Sequence[SubPkgbuild] = [], **args):
        self.subpackages = list(subpackages)
        super().__init__(relative_path, **args)


class SubPkgbuild(Pkgbuild):
    pkgbase: Pkgbase

    def __init__(self, name: str, pkgbase: Pkgbase):

        self.name = name
        self.pkgbase = pkgbase

        self.version = pkgbase.version
        self.arches = pkgbase.arches
        self.depends = list(pkgbase.depends)
        self.provides = []
        self.replaces = []
        self.local_depends = list(pkgbase.local_depends)
        self.repo = pkgbase.repo
        self.mode = pkgbase.mode
        self.path = pkgbase.path
        self.pkgver = pkgbase.pkgver
        self.pkgrel = pkgbase.pkgrel
        self.update_version()


def parse_pkgbuild(relative_pkg_dir: str, _config: Optional[ConfigStateHolder] = None) -> Sequence[Pkgbuild]:
    """
    Since function may run in a different subprocess, we need to be passed the config via parameter
    """
    global config
    if _config:
        config = _config
    setup_logging(verbose=config.runtime['verbose'], log_setup=False)  # different thread needs log setup.
    logging.info(f"Parsing PKGBUILD for {relative_pkg_dir}")
    pkgbuilds_dir = config.get_path('pkgbuilds')
    pkgdir = os.path.join(pkgbuilds_dir, relative_pkg_dir)
    filename = os.path.join(pkgdir, 'PKGBUILD')
    logging.debug(f"Parsing {filename}")
    mode = None
    with open(filename, 'r') as file:
        for line in file.read().split('\n'):
            if line.startswith('_mode='):
                mode = line.split('=')[1]
                break
    if mode not in ['host', 'cross']:
        raise Exception((f'{relative_pkg_dir}/PKGBUILD has {"no" if mode is None else "an invalid"} mode configured') +
                        (f': "{mode}"' if mode is not None else ''))

    base_package = Pkgbase(relative_pkg_dir)
    base_package.mode = mode
    base_package.repo = relative_pkg_dir.split('/')[0]
    srcinfo = run_cmd(
        MAKEPKG_CMD + ['--printsrcinfo'],
        cwd=pkgdir,
        stdout=subprocess.PIPE,
    )
    assert (isinstance(srcinfo, subprocess.CompletedProcess))
    lines = srcinfo.stdout.decode('utf-8').split('\n')

    current: Pkgbuild = base_package
    multi_pkgs = False
    for line_raw in lines:
        line = line_raw.strip()
        if not line:
            continue
        splits = line.split(' = ')
        if line.startswith('pkgbase'):
            base_package.name = splits[1]
            multi_pkgs = True
        elif line.startswith('pkgname'):
            if multi_pkgs:
                current = SubPkgbuild(splits[1], base_package)
                assert isinstance(base_package.subpackages, list)
                base_package.subpackages.append(current)
            else:
                current.name = splits[1]
        elif line.startswith('pkgver'):
            current.pkgver = splits[1]
        elif line.startswith('pkgrel'):
            current.pkgrel = splits[1]
        elif line.startswith('arch'):
            current.arches.append(splits[1])
        elif line.startswith('provides'):
            current.provides.append(splits[1])
        elif line.startswith('replaces'):
            current.replaces.append(splits[1])
        elif line.startswith('depends') or line.startswith('makedepends') or line.startswith('checkdepends') or line.startswith('optdepends'):
            current.depends.append(splits[1].split('=')[0].split(': ')[0])

    results: Sequence[Pkgbuild] = list(base_package.subpackages)
    if len(results) > 1:
        logging.debug(f" Split package detected: {base_package.name}: {results}")
        base_package.update_version()
    else:
        results = [base_package]

    for pkg in results:
        assert isinstance(pkg, Pkgbuild)
        pkg.depends = list(set(pkg.depends))  # deduplicate dependencies
        pkg.update_version()
        if not (pkg.version == base_package.version):
            raise Exception(f'Subpackage malformed! Versions differ! base: {base_package}, subpackage: {pkg}')
    return results


_pkgbuilds_cache = dict[str, Pkgbuild]()
_pkgbuilds_scanned: bool = False


def discover_pkgbuilds(parallel: bool = True, lazy: bool = True) -> dict[str, Pkgbuild]:
    global _pkgbuilds_cache, _pkgbuilds_scanned
    if lazy and _pkgbuilds_scanned:
        logging.debug("Reusing cached pkgbuilds repo")
        return _pkgbuilds_cache.copy()
    pkgbuilds_dir = config.get_path('pkgbuilds')
    packages: dict[str, Pkgbuild] = {}
    paths = []
    init_pkgbuilds(interactive=False)
    for repo in REPOSITORIES:
        for dir in os.listdir(os.path.join(pkgbuilds_dir, repo)):
            paths.append(os.path.join(repo, dir))

    results = []

    logging.info("Parsing PKGBUILDs")

    logging.debug(f"About to parse pkgbuilds. verbosity: {config.runtime['verbose']}")
    if parallel:
        chunks = (Parallel(n_jobs=multiprocessing.cpu_count() * 4)(delayed(parse_pkgbuild)(path, config) for path in paths))
    else:
        chunks = (parse_pkgbuild(path) for path in paths)

    for pkglist in chunks:
        results += pkglist

    logging.debug('Building package dictionary!')
    for package in results:
        for name in [package.name] + package.replaces:
            if name in packages:
                logging.warning(f'Overriding {packages[package.name]} with {package}')
            packages[name] = package

    # This filters the deps to only include the ones that are provided in this repo
    for package in packages.values():
        package.local_depends = package.depends.copy()
        for dep in package.depends.copy():
            found = dep in packages
            for p in packages.values():
                if found:
                    break
                if dep in p.names():
                    logging.debug(f'Found {p.name} that provides {dep}')
                    found = True
                    break
            if not found:
                logging.debug(f'Removing {dep} from dependencies')
                package.local_depends.remove(dep)

    _pkgbuilds_cache.clear()
    _pkgbuilds_cache.update(packages)
    _pkgbuilds_scanned = True
    return packages
