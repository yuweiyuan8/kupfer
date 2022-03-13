import logging
import subprocess
import os
from typing import Iterable, Iterator, Mapping, Optional

from binfmt import register as binfmt_register
from config import config
from chroot.build import setup_build_chroot
from distro.abstract import DistroInfo, PackageInfo
#from distro.distro import Distro
from constants import Arch, ARCHES, QEMU_BINFMT_PKGS
from wrapper import enforce_wrap

from .pkgbuild import Pkgbuild
from .local_distro import LocalDistro
from .source_distro import SourceDistro
from .meta_package import MetaPackage


class MetaDistro(DistroInfo):

    def __init__(
        self,
        source_distro: SourceDistro,
        remote_distro: DistroInfo,
        local_distro: LocalDistro,
    ):
        pass

    def get_unbuilt_package_levels(self, packages: Iterable[PackageInfo], arch: Arch, force: bool = False) -> list[set[Pkgbuild]]:
        package_levels = self.pkgbuilds.generate_dependency_chain(packages)
        build_names = set[str]()
        build_levels = list[set[Pkgbuild]]()
        i = 0
        for level_packages in package_levels:
            level = set[Pkgbuild]()
            for package in level_packages:
                if ((not self.check_package_version_built(package, arch)) or set.intersection(set(package.depends), set(build_names)) or
                    (force and package in packages)):
                    level.add(package)
                    build_names.update(package.names())
            if level:
                build_levels.append(level)
                logging.debug(f'Adding to level {i}:' + '\n' + ('\n'.join([p.name for p in level])))
                i += 1
        return build_levels

    def generate_dependency_chain(self, to_build: Iterable[MetaPackage]) -> list[set[Pkgbuild]]:
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
        build_levels = self.get_unbuilt_package_levels(packages, arch, force=force)
        if not build_levels:
            logging.info('Everything built already')
            return
        self.pkgbuilds.build_package_levels(
            build_levels,
            arch=arch,
            force=force,
            enable_crosscompile=enable_crosscompile,
            enable_crossdirect=enable_crossdirect,
            enable_ccache=enable_ccache,
            clean_chroot=clean_chroot,
        )

    def get_packages(self) -> Mapping[str, MetaPackage]:
        return super().get_packages()

    def build_enable_qemu_binfmt(self, foreign_arch: Arch):
        if foreign_arch not in ARCHES:
            raise Exception(f'Unknown architecture "{foreign_arch}". Choices: {", ".join(ARCHES)}')
        enforce_wrap()
        native = config.runtime['arch']
        assert self.arch == native
        self.init()
        # build qemu-user, binfmt, crossdirect
        chroot = setup_build_chroot(native)
        logging.info('Installing qemu-user (building if necessary)')
        qemu_pkgs = [pkg for pkgname, pkg in self.get_packages().items() if pkgname in QEMU_BINFMT_PKGS]
        self.build_packages(
            qemu_pkgs,
            native,
            enable_crosscompile=False,
            enable_crossdirect=False,
            enable_ccache=False,
        )
        subprocess.run(['pacman', '-Syy', '--noconfirm', '--needed', '--config', os.path.join(chroot.path, 'etc/pacman.conf')] + QEMU_BINFMT_PKGS)
        if foreign_arch != native:
            binfmt_register(foreign_arch)
