import click
import logging
import multiprocessing
import os

from joblib import Parallel, delayed
from typing import Iterable, Optional, Iterator

from config import config
from constants import Arch, REPOSITORIES
from utils import git

from .pkgbuild import Pkgbuild, parse_pkgbuild
from .helpers import setup_build_chroot

pacman_cmd = [
    'pacman',
    '-Syuu',
    '--noconfirm',
    '--overwrite=*',
    '--needed',
]


class SourceRepo:
    pkgbuilds_dir: str
    pkgbuilds: dict[str, Pkgbuild]
    initialized: bool = False

    def __init__(self, pkgbuilds_dir: Optional[str] = None):
        self.pkgbuilds_dir = pkgbuilds_dir or config.get_path('pkgbuilds')

    def git_get_pkgbuilds(self, repo_url: str, branch: str, interactive=False, update=True):
        git_dir = os.path.join(self.pkgbuilds_dir, '.git')
        if not os.path.exists(git_dir):
            logging.info('Cloning branch {branch} from {repo}')
            result = git(['clone', '-b', branch, repo_url, self.pkgbuilds_dir])
            if result.returncode != 0:
                raise Exception('Error cloning pkgbuilds')
        else:
            result = git(['--git-dir', git_dir, 'branch', '--show-current'], capture_output=True)
            current_branch = result.stdout.decode().strip()
            if current_branch != branch:
                logging.warning(f'pkgbuilds repository is on the wrong branch: {current_branch}, requested: {branch}')
                if interactive and click.confirm('Would you like to switch branches?', default=False):
                    result = git(['switch', branch], dir=self.pkgbuilds_dir)
                    if result.returncode != 0:
                        raise Exception('failed switching branches')
            if update:
                if interactive:
                    if not click.confirm('Would you like to try updating the PKGBUILDs repo?'):
                        return
                result = git(['pull'], self.pkgbuilds_dir)
                if result.returncode != 0:
                    raise Exception('failed to update pkgbuilds')

    def init(self, interactive=False):
        if (not self.initialized) or interactive:
            pkgbuilds_dir = self.pkgbuilds_dir
            repo_url = config.file['pkgbuilds']['git_repo']
            branch = config.file['pkgbuilds']['git_branch']
            self.git_get_pkgbuilds(
                repo_url,
                branch,
                interactive=interactive,
                update=False,
            )

    def discover_packages(self, parallel: bool = True, refresh: bool = False) -> dict[str, Pkgbuild]:
        pkgbuilds_dir = self.pkgbuilds_dir
        packages: dict[str, Pkgbuild] = {}
        paths = []
        self.init(interactive=False)
        if self.pkgbuilds and not refresh:
            return self.pkgbuilds.copy()
        for repo in REPOSITORIES:
            for dir in os.listdir(os.path.join(pkgbuilds_dir, repo)):
                paths.append(os.path.join(repo, dir))

        native_chroot = setup_build_chroot(config.runtime['arch'], add_kupfer_repos=False)
        results = []

        if parallel:
            chunks = (Parallel(n_jobs=multiprocessing.cpu_count() * 4)(delayed(parse_pkgbuild)(path, native_chroot) for path in paths))
        else:
            chunks = (parse_pkgbuild(path, native_chroot) for path in paths)

        for pkglist in chunks:
            results += pkglist

        logging.debug('Building package dictionary!')
        for package in results:
            for name in [package.name] + package.replaces:
                if name in packages:
                    logging.warn(f'Overriding {packages[package.name]} with {package}')
                packages[name] = package

        # This filters the deps to only include the ones that are provided in this repo
        for package in packages.values():
            package.local_depends = package.depends.copy()
            for dep in package.depends.copy():
                found = dep in packages
                for p in packages.values():
                    if found:
                        break
                    for name in p.names():
                        if dep == name:
                            logging.debug(f'Found {p.name} that provides {dep}')
                            found = True
                            break
                if not found:
                    logging.debug(f'Removing {dep} from dependencies')
                    package.local_depends.remove(dep)

        self.pkgbuilds = packages.copy()
        return packages

    def filter_packages_by_paths(self, paths: Iterable[str], allow_empty_results=True) -> Iterable[Pkgbuild]:
        if 'all' in paths:
            return list(self.pkgbuilds.values())
        result = []
        for pkg in self.pkgbuilds.values():
            if pkg.path in paths:
                result += [pkg]

        if not allow_empty_results and not result:
            raise Exception('No packages matched by paths: ' + ', '.join([f'"{p}"' for p in paths]))
        return result

    def generate_dependency_chain(self, to_build: Iterable[Pkgbuild]) -> list[set[Pkgbuild]]:
        """
        This figures out all dependencies and their sub-dependencies for the selection and adds those packages to the selection.
        First the top-level packages get selected by searching the paths.
        Then their dependencies and sub-dependencies and so on get added to the selection.
        """
        visited = set[Pkgbuild]()
        visited_names = set[str]()
        dep_levels: list[set[Pkgbuild]] = [set(), set()]
        package_repo = self.pkgbuilds

        def visit(package: Pkgbuild, visited=visited, visited_names=visited_names):
            visited.add(package)
            visited_names.update(package.names())

        def join_levels(levels: list[set[Pkgbuild]]) -> dict[Pkgbuild, int]:
            result = dict[Pkgbuild, int]()
            for i, level in enumerate(levels):
                for pkg in level:
                    result[pkg] = i
            return result

        def get_dependencies(package: Pkgbuild, package_repo: dict[str, Pkgbuild] = package_repo) -> Iterator[Pkgbuild]:
            for dep_name in package.depends:
                if dep_name in visited_names:
                    continue
                elif dep_name in package_repo:
                    dep_pkg = package_repo[dep_name]
                    visit(dep_pkg)
                    yield dep_pkg

        def get_recursive_dependencies(package: Pkgbuild, package_repo: dict[str, Pkgbuild] = package_repo) -> Iterator[Pkgbuild]:
            for pkg in get_dependencies(package, package_repo):
                yield pkg
                for sub_pkg in get_recursive_dependencies(pkg, package_repo):
                    yield sub_pkg

        logging.debug('Generating dependency chain:')
        # init level 0
        for package in to_build:
            visit(package)
            dep_levels[0].add(package)
            logging.debug(f'Adding requested package {package.name}')
            # add dependencies of our requested builds to level 0
            for dep_pkg in get_recursive_dependencies(package):
                logging.debug(f"Adding {package.name}'s dependency {dep_pkg.name} to level 0")
                dep_levels[0].add(dep_pkg)
                visit(dep_pkg)
        """
        Starting with `level` = 0, iterate over the packages in `dep_levels[level]`:
        1. Moving packages that are dependencies of other packages up to `level`+1
        2. Adding yet unadded local dependencies of all pkgs on `level` to `level`+1
        3. increment level
        """
        level = 0
        # protect against dependency cycles
        repeat_count = 0
        _last_level: Optional[set[Pkgbuild]] = None
        while dep_levels[level]:
            level_copy = dep_levels[level].copy()
            modified = False
            logging.debug(f'Scanning dependency level {level}')
            if level > 100:
                raise Exception('Dependency chain reached 100 levels depth, this is probably a bug. Aborting!')

            for pkg in level_copy:
                pkg_done = False
                if pkg not in dep_levels[level]:
                    # pkg has been moved, move on
                    continue
                # move pkg to level+1 if something else depends on it
                for other_pkg in level_copy:
                    if pkg == other_pkg:
                        continue
                    if pkg_done:
                        break
                    if not issubclass(type(other_pkg), Pkgbuild):
                        raise Exception('Not a Pkgbuild object:' + repr(other_pkg))
                    for dep_name in other_pkg.depends:
                        if dep_name in pkg.names():
                            dep_levels[level].remove(pkg)
                            dep_levels[level + 1].add(pkg)
                            logging.debug(f'Moving {pkg.name} to level {level+1} because {other_pkg.name} depends on it as {dep_name}')
                            modified = True
                            pkg_done = True
                            break
                for dep_name in pkg.depends:
                    if dep_name in visited_names:
                        continue
                    elif dep_name in package_repo:
                        dep_pkg = package_repo[dep_name]
                        logging.debug(f"Adding {pkg.name}'s dependency {dep_name} to level {level}")
                        dep_levels[level].add(dep_pkg)
                        visit(dep_pkg)
                        modified = True

            if _last_level == dep_levels[level]:
                repeat_count += 1
            else:
                repeat_count = 0
            if repeat_count > 10:
                raise Exception(f'Probable dependency cycle detected: Level has been passed on unmodifed multiple times: #{level}: {_last_level}')
            _last_level = dep_levels[level].copy()
            if not modified:  # if the level was modified, make another pass.
                level += 1
                dep_levels.append(set[Pkgbuild]())
        # reverse level list into buildorder (deps first!), prune empty levels
        return list([lvl for lvl in dep_levels[::-1] if lvl])

    def build_package_levels(
        self,
        build_levels: list[set[Pkgbuild]],
        arch: Arch,
        force: bool = False,
        enable_crosscompile: bool = True,
        enable_crossdirect: bool = True,
        enable_ccache: bool = True,
        clean_chroot: bool = False,
    ):
        for level, need_build in enumerate(build_levels):
            logging.info(f"(Level {level}) Building {', '.join([x.name for x in need_build])}")
            for package in need_build:
                package.build(
                    arch=arch,
                    enable_crosscompile=enable_crosscompile,
                    enable_crossdirect=enable_crossdirect,
                    enable_ccache=enable_ccache,
                    clean_chroot=clean_chroot,
                )

    def build_packages(
        self,
        packages: Iterable[Pkgbuild],
        arch: Arch,
        force: bool = False,
        enable_crosscompile: bool = True,
        enable_crossdirect: bool = True,
        enable_ccache: bool = True,
        clean_chroot: bool = False,
    ):
        self.build_package_levels(
            [set(packages)],
            arch=arch,
            force=force,
            enable_crosscompile=enable_crosscompile,
            enable_crossdirect=enable_crossdirect,
            enable_ccache=enable_ccache,
            clean_chroot=clean_chroot,
        )

    def build_packages_by_paths(
        self,
        paths: Iterable[str],
        arch: Arch,
        force=False,
        enable_crosscompile: bool = True,
        enable_crossdirect: bool = True,
        enable_ccache: bool = True,
        clean_chroot: bool = False,
    ):
        if isinstance(paths, str):
            paths = [paths]

        packages = self.filter_packages_by_paths(paths, allow_empty_results=False)
        return self.build_packages(
            packages,
            arch,
            force=force,
            enable_crosscompile=enable_crosscompile,
            enable_crossdirect=enable_crossdirect,
            enable_ccache=enable_ccache,
            clean_chroot=clean_chroot,
        )


_src_repo: SourceRepo


def get_repo():
    global _src_repo
    if not _src_repo:
        _src_repo = SourceRepo()
    return _src_repo
