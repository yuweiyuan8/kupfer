from __future__ import annotations

import click
import logging
import multiprocessing
import os
import subprocess

from joblib import Parallel, delayed
from typing import Iterable, Optional

from config import config, ConfigStateHolder
from constants import REPOSITORIES
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


_pkgbuilds_initialised: bool = False


def init_pkgbuilds(interactive=False, lazy: bool = True):
    global _pkgbuilds_initialised
    if lazy and _pkgbuilds_initialised:
        return
    pkgbuilds_dir = config.get_path('pkgbuilds')
    repo_url = config.file.pkgbuilds.git_repo
    branch = config.file.pkgbuilds.git_branch
    clone_pkbuilds(pkgbuilds_dir, repo_url, branch, interactive=interactive, update=False)
    _pkgbuilds_initialised = True


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
    description: str
    sources_refreshed: bool

    def __init__(
        self,
        relative_path: str,
        arches: list[Arch] = [],
        depends: list[str] = [],
        provides: list[str] = [],
        replaces: list[str] = [],
        repo: Optional[str] = None,
        sources_refreshed: bool = False,
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
        self.description = ''
        self.sources_refreshed = sources_refreshed

    def __repr__(self):
        return ','.join([
            'Pkgbuild(' + self.name,
            repr(self.path),
            self.version + ("🔄" if self.sources_refreshed else ""),
            self.mode + ')',
        ])

    def names(self):
        return list(set([self.name] + self.provides + self.replaces))

    def update_version(self):
        """updates `self.version` from `self.pkgver` and `self.pkgrel`"""
        self.version = f'{self.pkgver}-{self.pkgrel}'

    def update(self, pkg: Pkgbuild):
        self.version = pkg.version
        self.arches = list(pkg.arches)
        self.depends = list(pkg.depends)
        self.provides = list(pkg.provides)
        self.replaces = list(pkg.replaces)
        self.local_depends = list(pkg.local_depends)
        self.repo = pkg.repo
        self.mode = pkg.mode
        self.path = pkg.path
        self.pkgver = pkg.pkgver
        self.pkgrel = pkg.pkgrel
        self.description = pkg.description
        self.sources_refreshed = self.sources_refreshed or pkg.sources_refreshed
        self.update_version()

    def refresh_sources(self):
        raise NotImplementedError()

    def get_filename(self, arch: Arch):
        if not self.version:
            self.update_version()
        if self.arches[0] == 'any':
            arch = 'any'
        return f'{self.name}-{self.version}-{arch}.pkg.tar.zst'


class Pkgbase(Pkgbuild):
    subpackages: list[SubPkgbuild]

    def __init__(self, relative_path: str, subpackages: list[SubPkgbuild] = [], **args):
        self.subpackages = list(subpackages)
        super().__init__(relative_path, **args)

    def update(self, pkg: Pkgbuild):
        if not isinstance(pkg, Pkgbase):
            raise Exception(f"Tried to update pkgbase {self.name} with non-base pkg {pkg}")
        Pkgbuild.update(self, pkg)
        sub_dict = {p.name: p for p in self.subpackages}
        self.subpackages.clear()
        for new_pkg in pkg.subpackages:
            name = new_pkg.name
            if name not in sub_dict:
                sub_dict[name] = new_pkg
            else:
                sub_dict[name].update(new_pkg)
            updated = sub_dict[name]
            updated.sources_refreshed = self.sources_refreshed
            self.subpackages.append(updated)

    def refresh_sources(self, lazy: bool = True):
        '''
        Reloads the pkgbuild from disk.
        Does **NOT** actually perform the makepkg action to refresh the pkgver() first!
        '''
        if lazy and self.sources_refreshed:
            return
        parsed = parse_pkgbuild(self.path, sources_refreshed=True)
        basepkgs = [p for p in parsed if isinstance(p, Pkgbase)]
        if not len(basepkgs) == 1:
            raise Exception(f"error refreshing {self.name}: wrong number of base packages found: {basepkgs}")
        self.sources_refreshed = True
        self.update(basepkgs[0])


class SubPkgbuild(Pkgbuild):
    pkgbase: Pkgbase

    def __init__(self, name: str, pkgbase: Pkgbase):

        self.name = name
        self.pkgbase = pkgbase

        self.sources_refreshed = False
        self.update(pkgbase)

        self.provides = []
        self.replaces = []

    def refresh_sources(self, lazy: bool = True):
        assert self.pkgbase
        self.pkgbase.refresh_sources(lazy=lazy)


def parse_pkgbuild(relative_pkg_dir: str, _config: Optional[ConfigStateHolder] = None, sources_refreshed: bool = False) -> list[Pkgbuild]:
    """
    Since function may run in a different subprocess, we need to be passed the config via parameter
    """
    global config
    if _config:
        config = _config
    setup_logging(verbose=config.runtime.verbose, log_setup=False)  # different thread needs log setup.
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

    base_package = Pkgbase(relative_pkg_dir, sources_refreshed=sources_refreshed)
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
        elif line.startswith('pkgname'):
            current = SubPkgbuild(splits[1], base_package)
            assert isinstance(base_package.subpackages, list)
            base_package.subpackages.append(current)
            if current.name != base_package.name:
                multi_pkgs = True
        elif line.startswith('pkgver'):
            current.pkgver = splits[1]
        elif line.startswith('pkgrel'):
            current.pkgrel = splits[1]
        elif line.startswith('pkgdesc'):
            current.description = splits[1]
        elif line.startswith('arch'):
            current.arches.append(splits[1])
        elif line.startswith('provides'):
            current.provides.append(splits[1])
        elif line.startswith('replaces'):
            current.replaces.append(splits[1])
        elif line.startswith('depends') or line.startswith('makedepends') or line.startswith('checkdepends') or line.startswith('optdepends'):
            current.depends.append(splits[1].split('=')[0].split(': ')[0])

    results: list[Pkgbuild] = list(base_package.subpackages)
    if multi_pkgs:
        logging.debug(f" Split package detected: {base_package.name}: {results}")

    base_package.update_version()
    for pkg in results:
        assert isinstance(pkg, Pkgbuild)
        pkg.depends = list(set(pkg.depends))  # deduplicate dependencies
        pkg.update_version()
        if not (pkg.version == base_package.version):
            raise Exception(f'Subpackage malformed! Versions differ! base: {base_package}, subpackage: {pkg}')
    return results


_pkgbuilds_cache = dict[str, Pkgbuild]()
_pkgbuilds_paths = dict[str, list[Pkgbuild]]()
_pkgbuilds_scanned: bool = False


def get_pkgbuild_by_path(relative_path: str, lazy: bool = True, _config: Optional[ConfigStateHolder] = None) -> list[Pkgbuild]:
    global _pkgbuilds_cache, _pkgbuilds_paths
    if lazy and relative_path in _pkgbuilds_paths:
        return _pkgbuilds_paths[relative_path]
    parsed = parse_pkgbuild(relative_path, _config=_config)
    _pkgbuilds_paths[relative_path] = parsed
    for pkg in parsed:
        _pkgbuilds_cache[pkg.name] = pkg
    return parsed


def get_pkgbuild_by_name(name: str, lazy: bool = True):
    if lazy and name in _pkgbuilds_cache:
        return _pkgbuilds_cache[name]
    if _pkgbuilds_scanned and lazy:
        raise Exception(f"couldn't find PKGBUILD for package with name {name}")
    discover_pkgbuilds(lazy=lazy)
    assert _pkgbuilds_scanned
    return get_pkgbuild_by_name(name=name, lazy=lazy)


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
            p = os.path.join(repo, dir)
            if not os.path.exists(os.path.join(pkgbuilds_dir, p, 'PKGBUILD')):
                logging.warning(f"{p} doesn't include a PKGBUILD file; skipping")
                continue
            paths.append(p)

    logging.info("Parsing PKGBUILDs")

    results = []
    if parallel:
        paths_filtered = paths
        if lazy:
            # filter out cached packages as the caches don't cross process boundaries
            paths_filtered = []
            for p in paths:
                if p in _pkgbuilds_paths:
                    # use cache
                    results += _pkgbuilds_paths[p]
                else:
                    paths_filtered += [p]
        chunks = (Parallel(n_jobs=multiprocessing.cpu_count() * 4)(
            delayed(get_pkgbuild_by_path)(path, lazy=lazy, _config=config) for path in paths_filtered))
    else:
        chunks = (get_pkgbuild_by_path(path, lazy=lazy) for path in paths)

    _pkgbuilds_paths.clear()
    # one list of packages per path
    for pkglist in chunks:
        _pkgbuilds_paths[pkglist[0].path] = pkglist
        results += pkglist

    logging.info('Building package dictionary')
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
            for pkg in packages.values():
                if found:
                    break
                if dep in pkg.names():
                    logging.debug(f'Found {pkg.name} that provides {dep}')
                    found = True
                    break
            if not found:
                logging.debug(f'Removing {dep} from dependencies')
                package.local_depends.remove(dep)

    _pkgbuilds_cache.clear()
    _pkgbuilds_cache.update(packages)
    _pkgbuilds_scanned = True
    return packages


def filter_pkgbuilds(
    paths: Iterable[str],
    repo: Optional[dict[str, Pkgbuild]] = None,
    arch: Optional[Arch] = None,
    allow_empty_results=True,
    use_paths=True,
    use_names=True,
) -> Iterable[Pkgbuild]:
    if not (use_names or use_paths):
        raise Exception('Error: filter_packages instructed to match neither by names nor paths; impossible!')
    if not allow_empty_results and not paths:
        raise Exception("Can't search for packages: no query given")
    repo = repo or discover_pkgbuilds()
    if 'all' in paths:
        all_pkgs = list(repo.values())
        if arch:
            all_pkgs = [pkg for pkg in all_pkgs if set([arch, 'any']).intersection(pkg.arches)]
        return all_pkgs
    result = []
    for pkg in repo.values():
        comparison = set()
        if use_paths:
            comparison.add(pkg.path)
        if use_names:
            comparison.add(pkg.name)
        matches = list(comparison.intersection(paths))
        if matches:
            assert pkg.arches
            if arch and not set([arch, 'any']).intersection(pkg.arches):
                logging.warn(f"Pkg {pkg.name} matches query {matches[0]} but isn't available for architecture {arch}: {pkg.arches}")
                continue
            result += [pkg]

    if not allow_empty_results and not result:
        raise Exception('No packages matched by paths: ' + ', '.join([f'"{p}"' for p in paths]))
    return result
