from __future__ import annotations

from . import logging
import os
import subprocess

from typing import Optional, Sequence

from chroot import Chroot
from constants import Arch, CHROOT_PATHS, MAKEPKG_CMD
from distro.package import PackageInfo


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


def parse_pkgbuild(relative_pkg_dir: str, native_chroot: Chroot) -> Sequence[Pkgbuild]:
    filename = os.path.join(native_chroot.get_path(CHROOT_PATHS['pkgbuilds']), relative_pkg_dir, 'PKGBUILD')
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
