from __future__ import annotations

import click
import logging
import multiprocessing
import os

from joblib import Parallel, delayed
from typing import Iterable, Optional, TypeAlias

from config.state import config, ConfigStateHolder
from constants import REPOSITORIES
from constants import Arch
from distro.package import PackageInfo
from exec.file import remove_file
from logger import setup_logging
from utils import git, git_get_branch
from wrapper import check_programs_wrap

from .srcinfo_cache import SrcinfoMetaFile


def clone_pkgbuilds(
    pkgbuilds_dir: str,
    repo_url: str,
    branch: str,
    interactive=False,
    update=True,
    switch_branch: bool = False,
    discard_changes: bool = False,
):
    check_programs_wrap(['git'])
    git_dir = os.path.join(pkgbuilds_dir, '.git')
    if not os.path.exists(git_dir):
        logging.info(f'Cloning branch {branch} from {repo_url}')
        result = git(['clone', '-b', branch, repo_url, pkgbuilds_dir])
        if result.returncode != 0:
            raise Exception('Error cloning pkgbuilds')
    else:
        current_branch = git_get_branch(pkgbuilds_dir)
        if current_branch != branch:
            logging.warning(f'pkgbuilds repository is on the wrong branch: {current_branch}, requested: {branch}')
            if switch_branch or (interactive and click.confirm('Would you like to switch branches?', default=False)):
                result = git(['remote', 'update'], dir=pkgbuilds_dir)
                if result.returncode != 0:
                    raise Exception('failed updating PKGBUILDs branches')
                result = git(['switch', *(['-f'] if discard_changes else []), branch], dir=pkgbuilds_dir)
                if result.returncode != 0:
                    raise Exception('failed switching PKGBUILDs branches')
            logging.warning('Hint: you can use `kupferbootstrap packages update` to switch branches')

        if update:
            if interactive:
                if not click.confirm('Would you like to try updating the PKGBUILDs repo?', default=True):
                    return
            result = git(['fetch'], dir=pkgbuilds_dir)
            if result.returncode != 0:
                raise Exception("Failed to fetch updates with git")

            pull_cmd = ['pull', '--ff-only']
            result = git(pull_cmd, dir=pkgbuilds_dir)
            if result.returncode != 0:
                if discard_changes:
                    logging.info("git pull failed, detecting conflicting changes")
                    # '@{u}' is a git placeholder for the latest upstream commit
                    result = git(['diff', '--name-only', '--diff-filter=UD', '@{u}'], capture_output=True, dir=pkgbuilds_dir)
                    result.check_returncode()
                    if result.stdout:
                        logging.info("Discarding conflicting changes")
                        for f in result.stdout.decode().split('\n'):
                            path = os.path.join(pkgbuilds_dir, f)
                            if not os.path.exists(path):
                                continue
                            result = git(['checkout', '--', f], dir=pkgbuilds_dir, capture_output=True)
                            if result.returncode != 0:
                                logging.debug(f'git checkout of file "{f}" failed; removing.')
                                remove_file(path)
                        logging.info("Retrying git pull")
                        result = git(pull_cmd, dir=pkgbuilds_dir)
                        if result.returncode != 0:
                            logging.info("Last resort: git reset --hard")
                            result = git(['reset', '--hard', '@{u}'], capture_output=True, dir=pkgbuilds_dir)
                        if result.returncode == 0:
                            return
                raise Exception('`git pull` failed to update pkgbuilds')


_pkgbuilds_initialised: bool = False


def init_pkgbuilds(
    interactive=False,
    lazy: bool = True,
    update: bool = False,
    switch_branch: bool = False,
    discard_changes: bool = False,
):
    global _pkgbuilds_initialised
    if lazy and _pkgbuilds_initialised:
        return
    pkgbuilds_dir = config.get_path('pkgbuilds')
    repo_url = config.file.pkgbuilds.git_repo
    branch = config.file.pkgbuilds.git_branch
    clone_pkgbuilds(
        pkgbuilds_dir,
        repo_url,
        branch,
        interactive=interactive,
        update=update,
        switch_branch=switch_branch,
        discard_changes=discard_changes,
    )
    _pkgbuilds_initialised = True


VersionSpec: TypeAlias = Optional[str]
VersionSpecs: TypeAlias = dict[str, Optional[list[VersionSpec]]]


def parse_version_spec(spec: str) -> tuple[str, VersionSpec]:
    for op in ['<', '>', '=']:
        if op in spec:
            name, ver = spec.split(op, 1)
            assert name and ver
            ver = op + ver
            if name[-1] == '=':
                assert op != '='
                name = name[:-1]
                ver = '=' + ver
            return name, ver
    return spec.strip(), None


def get_version_specs(spec: str, existing_specs: Optional[VersionSpecs] = None) -> VersionSpecs:
    specs = existing_specs or {}
    name, ver = parse_version_spec(spec)
    _specs = specs.get(name, None)
    if ver:
        _specs = _specs or []
        if ver not in _specs:
            _specs.append(ver)
    specs[name] = _specs
    return specs


class Pkgbuild(PackageInfo):
    name: str
    version: str
    arches: list[Arch]
    depends: VersionSpecs
    makedepends: VersionSpecs
    provides: VersionSpecs
    replaces: list[str]
    local_depends: list[str]
    repo: str
    mode: str
    nodeps: bool
    path: str
    pkgver: str
    pkgrel: str
    description: str
    sources_refreshed: bool
    srcinfo_cache: Optional[SrcinfoMetaFile]

    def __init__(
        self,
        relative_path: str,
        arches: list[Arch] = [],
        depends: VersionSpecs = {},
        makedepends: VersionSpecs = {},
        provides: VersionSpecs = {},
        replaces: list[str] = [],
        repo: Optional[str] = None,
        sources_refreshed: bool = False,
        srcinfo_cache: Optional[SrcinfoMetaFile] = None,
    ) -> None:
        """
        Create new Pkgbuild representation for file located at `{relative_path}/PKGBUILD`.
        `relative_path` will be stored in `self.path`.
        """
        self.name = os.path.basename(relative_path)
        self.version = ''
        self.arches = list(arches)
        self.depends = dict(depends)
        self.makedepends = dict(makedepends)
        self.provides = dict(provides)
        self.replaces = list(replaces)
        self.local_depends = []
        self.repo = repo or ''
        self.mode = ''
        self.nodeps = False
        self.path = relative_path
        self.pkgver = ''
        self.pkgrel = ''
        self.description = ''
        self.sources_refreshed = sources_refreshed
        self.srcinfo_cache = srcinfo_cache

    def __repr__(self):
        return ','.join([
            'Pkgbuild(' + self.name,
            repr(self.path),
            self.version + ("🔄" if self.sources_refreshed else ""),
            self.mode + ')',
        ])

    def names(self) -> list[str]:
        return list({self.name, *self.provides, *self.replaces})

    def update_version(self):
        """updates `self.version` from `self.pkgver` and `self.pkgrel`"""
        self.version = f'{self.pkgver}-{self.pkgrel}'

    def update(self, pkg: Pkgbuild):
        self.version = pkg.version
        self.arches = list(pkg.arches)
        self.depends = dict(pkg.depends)
        self.makedepends = dict(pkg.makedepends)
        self.provides = dict(pkg.provides)
        self.replaces = list(pkg.replaces)
        self.local_depends = list(pkg.local_depends)
        self.repo = pkg.repo
        self.mode = pkg.mode
        self.nodeps = pkg.nodeps
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

    def is_built(self) -> bool:
        raise NotImplementedError()


class Pkgbase(Pkgbuild):
    subpackages: list[SubPkgbuild]
    _is_built: bool

    def __init__(self, relative_path: str, subpackages: list[SubPkgbuild] = [], **args):
        self._is_built = False
        self.subpackages = list(subpackages)
        super().__init__(relative_path, **args)

    def update(self, pkg: Pkgbuild):
        if not isinstance(pkg, Pkgbase):
            raise Exception(f"Tried to update pkgbase {self.name} with non-base pkg {pkg}")
        Pkgbuild.update(self, pkg)
        self._is_built = pkg._is_built or self._is_built
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
        basepkg = parsed[0]
        assert isinstance(basepkg, (Pkgbase, SubPkgbuild))
        if isinstance(basepkg, SubPkgbuild):
            basepkg = basepkg.pkgbase
        self.sources_refreshed = True
        self.update(basepkg)

    def names(self) -> list[str]:
        names = set(Pkgbuild.names(self))
        for pkg in self.subpackages:
            names.update(pkg.names())
        return list(names)

    def is_built(self) -> bool:
        return self._is_built


class SubPkgbuild(Pkgbuild):
    pkgbase: Pkgbase

    def __init__(self, name: str, pkgbase: Pkgbase):

        self.name = name
        self.pkgbase = pkgbase
        self.srcinfo_cache = pkgbase.srcinfo_cache

        self.sources_refreshed = False
        self.update(pkgbase)

        self.provides = {}
        self.replaces = []

    def refresh_sources(self, lazy: bool = True):
        assert self.pkgbase
        self.pkgbase.refresh_sources(lazy=lazy)

    def is_built(self) -> bool:
        return self.pkgbase.is_built()


def parse_pkgbuild(
    relative_pkg_dir: str,
    _config: Optional[ConfigStateHolder] = None,
    force_refresh_srcinfo: bool = False,
    sources_refreshed: bool = False,
) -> list[Pkgbuild]:
    """
    Since function may run in a different subprocess, we need to be passed the config via parameter
    """
    global config
    if _config:
        config = _config
        setup_logging(verbose=config.runtime.verbose, log_setup=False)  # different subprocess needs log setup.
    logging.info(f"Discovering PKGBUILD for {relative_pkg_dir}")

    if force_refresh_srcinfo:
        logging.info('force-refreshing SRCINFOs')
    # parse SRCINFO cache metadata and get correct SRCINFO lines
    srcinfo_cache, lines = SrcinfoMetaFile.handle_directory(relative_pkg_dir, force_refresh=force_refresh_srcinfo, write=True)
    assert lines and srcinfo_cache
    assert 'build_mode' in srcinfo_cache
    mode = srcinfo_cache.build_mode
    assert 'build_nodeps' in srcinfo_cache
    nodeps = srcinfo_cache.build_nodeps
    if mode not in ['host', 'cross']:
        err = 'an invalid' if mode is not None else 'no'
        err_end = f": {repr(mode)}" if mode is not None else "."
        msg = f'{relative_pkg_dir}/PKGBUILD has {err} mode configured{err_end}'
        if mode is None:
            logging.warning(msg)
        else:
            raise Exception(msg)

    base_package = Pkgbase(relative_pkg_dir, sources_refreshed=sources_refreshed, srcinfo_cache=srcinfo_cache)
    base_package.mode = mode
    base_package.nodeps = nodeps
    base_package.repo = relative_pkg_dir.split('/')[0]

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
            current.provides = get_version_specs(splits[1], current.provides)
        elif line.startswith('replaces'):
            current.replaces.append(splits[1])
        elif splits[0] in ['depends', 'makedepends', 'checkdepends', 'optdepends']:
            spec = splits[1].split(': ', 1)[0]
            current.depends = get_version_specs(spec, current.depends)
            if splits[0] == 'makedepends':
                current.makedepends = get_version_specs(spec, current.makedepends)

    results: list[Pkgbuild] = list(base_package.subpackages)
    if multi_pkgs:
        logging.debug(f" Split package detected: {base_package.name}: {results}")

    base_package.update_version()
    for pkg in results:
        assert isinstance(pkg, Pkgbuild)
        pkg.update_version()
        if not (pkg.version == base_package.version):
            raise Exception(f'Subpackage malformed! Versions differ! base: {base_package}, subpackage: {pkg}')
    return results


_pkgbuilds_cache = dict[str, Pkgbuild]()
_pkgbuilds_paths = dict[str, list[Pkgbuild]]()
_pkgbuilds_scanned: bool = False


def get_pkgbuild_by_path(
    relative_path: str,
    force_refresh_srcinfo: bool = False,
    lazy: bool = True,
    _config: Optional[ConfigStateHolder] = None,
) -> list[Pkgbuild]:
    global _pkgbuilds_cache, _pkgbuilds_paths
    if lazy and not force_refresh_srcinfo and relative_path in _pkgbuilds_paths:
        return _pkgbuilds_paths[relative_path]
    parsed = parse_pkgbuild(relative_path, force_refresh_srcinfo=force_refresh_srcinfo, _config=_config)
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


def get_pkgbuild_dirs(quiet: bool = True, repositories: Optional[list[str]] = None) -> list[str]:
    """Gets the relative paths to directories containing PKGBUILDs, optionally warns about dirs without a PKGBUILD"""
    pkgbuilds_dir = config.get_path('pkgbuilds')
    paths = []
    for repo in repositories or REPOSITORIES:
        for dir in os.listdir(os.path.join(pkgbuilds_dir, repo)):
            p = os.path.join(repo, dir)
            if not os.path.exists(os.path.join(pkgbuilds_dir, p, 'PKGBUILD')):
                if not quiet:
                    logging.warning(f"{p} doesn't include a PKGBUILD file; skipping")
                continue
            paths.append(p)
    return paths


def discover_pkgbuilds(parallel: bool = True, lazy: bool = True, repositories: Optional[list[str]] = None) -> dict[str, Pkgbuild]:
    global _pkgbuilds_cache, _pkgbuilds_scanned
    if lazy and _pkgbuilds_scanned:
        logging.debug("Reusing cached pkgbuilds repo")
        return _pkgbuilds_cache.copy()
    check_programs_wrap(['makepkg'])
    packages: dict[str, Pkgbuild] = {}
    init_pkgbuilds(interactive=False)
    paths = get_pkgbuild_dirs(quiet=False, repositories=repositories)
    logging.info(f"Discovering PKGBUILDs{f' in repositories: {repositories}' if repositories else ''}")

    results = []
    if parallel:
        paths_filtered = paths
        backend = 'threading'
        pass_config = config if backend != 'threading' else None
        chunks = (Parallel(n_jobs=multiprocessing.cpu_count() * 4,
                           backend=backend)(delayed(get_pkgbuild_by_path)(path, lazy=lazy, _config=pass_config) for path in paths_filtered))
    else:
        chunks = (get_pkgbuild_by_path(path, lazy=lazy) for path in paths)

    if repositories is None:
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

    if repositories is None:
        # partial scans (specific repos) don't count as truly scanned
        _pkgbuilds_cache.clear()
        _pkgbuilds_scanned = True
    _pkgbuilds_cache.update(packages)

    # This filters local_depends to only include the ones that are provided by local PKGBUILDs
    # we need to iterate over the entire cache in case partial scans happened
    for package in _pkgbuilds_cache.values():
        package.local_depends = list(package.depends.keys())
        for dep in package.depends.copy():
            found = dep in _pkgbuilds_cache
            for pkg in _pkgbuilds_cache.values():
                if found:
                    break
                if dep in pkg.names():
                    logging.debug(f'{package.path}: Found {pkg.name} that provides {dep}')
                    found = True
                    break
            if not found:
                logging.debug(f'{package.path}: Removing {dep} from local dependencies')
                package.local_depends.remove(dep)

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
    paths = list(paths)
    plural = 's' if len(paths) > 1 else ''
    fields = []
    if use_names:
        fields.append('name' + plural)
    if use_paths:
        fields.append('path' + plural)
    fields_err = ' or '.join(fields)
    if not allow_empty_results and not paths:
        raise Exception(f"Can't search for packages: no {fields_err} given")
    repo = repo or discover_pkgbuilds()
    if 'all' in paths:
        all_pkgs = list(repo.values())
        if arch:
            all_pkgs = [pkg for pkg in all_pkgs if set([arch, 'any']).intersection(pkg.arches)]
        return all_pkgs
    result = []
    to_find = list(paths)
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
            for m in set(matches).intersection(to_find):
                to_find.remove(m)

    if not allow_empty_results:
        if not result:
            raise Exception(f'No packages matched by {fields_err}: ' + ', '.join([f'"{p}"' for p in paths]))
        if to_find:
            raise Exception(f"No packagages matched by {fields_err}: " + ', '.join([f'"{p}"' for p in to_find]))

    return result
