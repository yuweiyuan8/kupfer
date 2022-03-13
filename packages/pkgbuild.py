from __future__ import annotations

import logging
import os
import subprocess

from copy import deepcopy
from typing import Any, Iterable, Optional, Sequence

from chroot.build import BuildChroot
from config import config
from constants import Arch, CHROOT_PATHS, MAKEPKG_CMD, CROSSDIRECT_PKGS, GCC_HOSTSPECS
from distro.abstract import PackageInfo

from .helpers import setup_build_chroot, get_makepkg_env


class Pkgbuild(PackageInfo):
    name: str
    version: str
    arches: list[Arch]
    depends: list[str]
    provides: list[str]
    replaces: list[str]
    local_depends: list[str]
    mode = ''
    path = ''
    pkgver = ''
    pkgrel = ''
    source_packages: dict[Arch, SourcePackage]

    def __init__(
        self,
        relative_path: str,
        arches: list[Arch] = [],
        depends: list[str] = [],
        provides: list[str] = [],
        replaces: list[str] = [],
    ) -> None:
        """Create new Pkgbuild representation for file located at `relative_path/PKGBUILD`. `relative_path` will be written to `self.path`"""
        self.name = os.path.basename(relative_path)
        self.version = ''
        self.path = relative_path
        self.depends = deepcopy(depends)
        self.provides = deepcopy(provides)
        self.replaces = deepcopy(replaces)
        self.arches = deepcopy(arches)
        self.source_packages = {}

    def __repr__(self):
        return f'Pkgbuild({self.name},{repr(self.path)},{self.version},{self.mode})'

    def names(self):
        return list(set([self.name] + self.provides + self.replaces))

    def get_pkg_filenames(self, arch: Arch, native_chroot: BuildChroot) -> Iterable[str]:
        config_path = '/' + native_chroot.write_makepkg_conf(
            target_arch=arch,
            cross_chroot_relative=os.path.join('chroot', f'base_{arch}'),
            cross=True,
        )

        cmd = ['cd', os.path.join(CHROOT_PATHS['pkgbuilds'], self.path), '&&'] + MAKEPKG_CMD + [
            '--config',
            config_path,
            '--nobuild',
            '--noprepare',
            '--skippgpcheck',
            '--packagelist',
        ]
        result: Any = native_chroot.run_cmd(
            cmd,
            capture_output=True,
        )
        if result.returncode != 0:
            raise Exception(f'Failed to get package list for {self.path}:' + '\n' + result.stdout.decode() + '\n' + result.stderr.decode())

        return result.stdout.decode('utf-8').split('\n')

    def setup_sources(self, chroot: BuildChroot, makepkg_conf_path='/etc/makepkg.conf'):
        makepkg_setup_args = [
            '--config',
            makepkg_conf_path,
            '--nobuild',
            '--holdver',
            '--nodeps',
            '--skippgpcheck',
        ]

        logging.info(f'Setting up sources for {self.path} in {chroot.name}')
        result = chroot.run_cmd(MAKEPKG_CMD + makepkg_setup_args, cwd=os.path.join(CHROOT_PATHS['pkgbuilds'], self.path))
        assert isinstance(result, subprocess.CompletedProcess)
        if result.returncode != 0:
            raise Exception(f'Failed to check sources for {self.path}')

    def build(
        self,
        arch: Arch,
        enable_crosscompile: bool = True,
        enable_crossdirect: bool = True,
        enable_ccache: bool = True,
        clean_chroot: bool = False,
        repo_dir: str = None,
    ):
        """build the PKGBUILD for the given Architecture. Returns the directory in which the PKGBUILD and the resulting packages reside"""
        makepkg_compile_opts = ['--holdver']
        makepkg_conf_path = 'etc/makepkg.conf'
        repo_dir = repo_dir or config.get_path('pkgbuilds')
        foreign_arch = config.runtime['arch'] != arch
        deps = (list(set(self.depends) - set(self.names())))
        target_chroot = setup_build_chroot(
            arch=arch,
            extra_packages=deps,
            clean_chroot=clean_chroot,
        )
        native_chroot = target_chroot if not foreign_arch else setup_build_chroot(
            arch=config.runtime['arch'],
            extra_packages=['base-devel'] + CROSSDIRECT_PKGS,
            clean_chroot=clean_chroot,
        )
        cross = foreign_arch and self.mode == 'cross' and enable_crosscompile

        target_chroot.initialize()

        if cross:
            logging.info(f'Cross-compiling {self.path}')
            build_root = native_chroot
            makepkg_compile_opts += ['--nodeps']
            env = deepcopy(get_makepkg_env())
            if enable_ccache:
                env['PATH'] = f"/usr/lib/ccache:{env['PATH']}"
            logging.info('Setting up dependencies for cross-compilation')
            # include crossdirect for ccache symlinks and qemu-user
            results = native_chroot.try_install_packages(self.depends + CROSSDIRECT_PKGS + [f"{GCC_HOSTSPECS[native_chroot.arch][arch]}-gcc"])
            res_crossdirect = results['crossdirect']
            assert isinstance(res_crossdirect, subprocess.CompletedProcess)
            if res_crossdirect.returncode != 0:
                raise Exception('Unable to install crossdirect')
            # mount foreign arch chroot inside native chroot
            chroot_relative = os.path.join(CHROOT_PATHS['chroots'], target_chroot.name)
            makepkg_path_absolute = native_chroot.write_makepkg_conf(target_arch=arch, cross_chroot_relative=chroot_relative, cross=True)
            makepkg_conf_path = os.path.join('etc', os.path.basename(makepkg_path_absolute))
            native_chroot.mount_crosscompile(target_chroot)
        else:
            logging.info(f'Host-compiling {self.path}')
            build_root = target_chroot
            makepkg_compile_opts += ['--syncdeps']
            env = deepcopy(get_makepkg_env())
            if foreign_arch and enable_crossdirect and self.name not in CROSSDIRECT_PKGS:
                env['PATH'] = f"/native/usr/lib/crossdirect/{arch}:{env['PATH']}"
                target_chroot.mount_crossdirect(native_chroot)
            else:
                if enable_ccache:
                    logging.debug('ccache enabled')
                    env['PATH'] = f"/usr/lib/ccache:{env['PATH']}"
                    deps += ['ccache']
                logging.debug(('Building for native arch. ' if not foreign_arch else '') + 'Skipping crossdirect.')
            dep_install = target_chroot.try_install_packages(deps, allow_fail=False)
            failed_deps = [name for name, res in dep_install.items() if res.returncode != 0]  # type: ignore[union-attr]
            if failed_deps:
                raise Exception(f'Dependencies failed to install: {failed_deps}')

        makepkg_conf_absolute = os.path.join('/', makepkg_conf_path)
        self.setup_sources(build_root, makepkg_conf_path=makepkg_conf_absolute)

        build_cmd = f'makepkg --config {makepkg_conf_absolute} --skippgpcheck --needed --noconfirm --ignorearch {" ".join(makepkg_compile_opts)}'
        logging.debug(f'Building: Running {build_cmd}')
        pkgbuild_dir = os.path.join(CHROOT_PATHS['pkgbuilds'], self.path)
        result = build_root.run_cmd(build_cmd, inner_env=env, cwd=pkgbuild_dir)
        assert isinstance(result, subprocess.CompletedProcess)
        if result.returncode != 0:
            raise Exception(f'Failed to compile package {self.path}')
        return pkgbuild_dir

    def update_version(self):
        """updates `self.version` from `self.pkgver` and `self.pkgrel`"""
        self.version = f'{self.pkgver}-{self.pkgrel}'

    def update(self, pkgbuild: Pkgbuild):
        self.depends = pkgbuild.depends
        self.provides = pkgbuild.provides
        self.replaces = pkgbuild.replaces
        self.pkgver = pkgbuild.pkgver
        self.pkgrel = pkgbuild.pkgrel
        self.local_depends = pkgbuild.local_depends
        self.path = pkgbuild.path
        self.mode = pkgbuild.mode
        self.update_version()
        for arch, package in self.source_packages.items():
            if package.pkgbuild is not self:
                self.source_packages.pop(arch)
                logging.warning(
                    f'Pkgbuild {self.name} held reference package {package.name} for arch {arch} that references Pkgbuild {package.pkgbuild} instead')
                continue
            package.update()

    def get_source_repo(self, arch: Arch) -> 'SourcePackage':
        if not self.source_packages.get(arch, None):
            self.source_packages[arch] = SourcePackage(arch=arch, pkgbuild=self)
        return self.source_packages[arch]


class Pkgbase(Pkgbuild):
    subpackages: Sequence[SubPkgbuild]

    def __init__(self, relative_path: str, subpackages: Sequence[SubPkgbuild] = [], **args):
        self.subpackages = list(subpackages)
        super().__init__(relative_path, **args)


class SubPkgbuild(Pkgbuild):
    pkgbase: Pkgbase

    def __init__(self, name: str, pkgbase: Pkgbase):
        self.depends = []
        self.provides = []
        self.replaces = []
        self.local_depends = []

        self.name = name
        self.pkgbase = pkgbase

        self.arches = pkgbase.arches
        self.version = pkgbase.version
        self.mode = pkgbase.mode
        self.path = pkgbase.path
        self.pkgver = pkgbase.pkgver
        self.pkgrel = pkgbase.pkgrel


def parse_pkgbuild(relative_pkg_dir: str, native_chroot: BuildChroot) -> Sequence[Pkgbuild]:
    mode = None
    with open(os.path.join(native_chroot.get_path(CHROOT_PATHS['pkgbuilds']), relative_pkg_dir, 'PKGBUILD'), 'r') as file:
        for line in file.read().split('\n'):
            if line.startswith('_mode='):
                mode = line.split('=')[1]
                break
    if mode not in ['host', 'cross']:
        raise Exception((f'{relative_pkg_dir}/PKGBUILD has {"no" if mode is None else "an invalid"} mode configured') +
                        (f': "{mode}"' if mode is not None else ''))

    base_package = Pkgbase(relative_pkg_dir)
    base_package.mode = mode
    #base_package.repo = relative_pkg_dir.split('/')[0]
    srcinfo = native_chroot.run_cmd(
        MAKEPKG_CMD + ['--printsrcinfo'],
        cwd=os.path.join(CHROOT_PATHS['pkgbuilds'], base_package.path),
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

    results: Sequence[Pkgbuild] = list(base_package.subpackages) or [base_package]
    for pkg in results:
        assert isinstance(pkg, Pkgbuild)
        pkg.depends = list(set(pkg.depends))
        pkg.update_version()
        if not (pkg.pkgver == base_package.pkgver and pkg.pkgrel == base_package.pkgrel):
            raise Exception('subpackage malformed! pkgver differs!')

    return results


class SourcePackage(PackageInfo):
    pkgbuild: Pkgbuild

    def __init__(self, arch: Arch, pkgbuild: Pkgbuild):
        self.arch = arch
        self.pkgbuild = pkgbuild
        self.update()

    def update(self):
        self.name = self.pkgbuild.name
        self.depends = self.pkgbuild.depends
        self.provides = self.pkgbuild.provides
        self.replaces = self.pkgbuild.replaces
        self.local_depends = self.pkgbuild.local_depends
        self.path = self.pkgbuild.path
        self.pkgbuild.update_version()
        self.version = self.pkgbuild.version

    def acquire(self):
        return os.path.join(self.pkgbuild.build(arch=self.arch), self.get_filename())
