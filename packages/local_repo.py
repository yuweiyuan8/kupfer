from hashlib import md5
import logging
import os
import shutil
import subprocess

from typing import Iterable

from binfmt import register as binfmt_register
from config import config
from constants import REPOSITORIES, QEMU_BINFMT_PKGS, ARCHES, Arch
from distro.distro import Distro
from distro.repo import Repo
from wrapper import enforce_wrap
from utils import md5sum_file

from .pkgbuild import Pkgbuild
from .source_repo import SourceRepo, get_repo as get_source_repo
from .helpers import setup_build_chroot


class LocalRepo:
    initialized: bool = False
    pkgbuilds: SourceRepo
    repo_dir: str

    def __init__(self, repo_dir: str = None):
        self.repo_dir = repo_dir or config.get_path('packages')
        self.pkgbuilds = get_source_repo()

    def init(self, arch: Arch, discover_packages: bool = True, parallel: bool = True):
        """Ensure that all `constants.REPOSITORIES` inside `self.repo_dir` exist"""
        self.pkgbuilds.init()
        if discover_packages:
            self.pkgbuilds.discover_packages(parallel=parallel)
        if not self.initialized:
            for _arch in set([arch, config.runtime['arch']]):
                for repo in REPOSITORIES:
                    repo_dir = os.path.join(self.repo_dir, arch, repo)
                    os.makedirs(repo_dir, exist_ok=True)
                    for ext1 in ['db', 'files']:
                        for ext2 in ['', '.tar.xz']:
                            if not os.path.exists(os.path.join(repo_dir, f'{repo}.{ext1}{ext2}')):
                                result = subprocess.run(
                                    [
                                        'tar',
                                        '-czf',
                                        f'{repo}.{ext1}{ext2}',
                                        '-T',
                                        '/dev/null',
                                    ],
                                    cwd=repo_dir,
                                )
                                if result.returncode != 0:
                                    raise Exception('Failed to create prebuilt repos')
        self.initialized = True

    def add_file_to_repo(self, file_path: str, repo_name: str, arch: Arch):
        repo_dir = os.path.join(self.repo_dir, arch, repo_name)
        pacman_cache_dir = os.path.join(config.get_path('pacman'), arch)
        file_name = os.path.basename(file_path)
        target_file = os.path.join(repo_dir, file_name)

        os.makedirs(repo_dir, exist_ok=True)
        md5sum = md5sum_file(file_path)

        if file_path != target_file:
            if md5sum_file(target_file) != md5sum:
                logging.debug(f'moving {file_path} to {target_file} ({repo_dir})')
                shutil.copy(
                    file_path,
                    repo_dir,
                )
            else:
                logging.warning('Exact package file (confirmed by hash) was already in the repo. Skipped and deleted.')
            os.unlink(file_path)

        # clean up same name package from pacman cache
        cache_file = os.path.join(pacman_cache_dir, file_name)
        if os.path.exists(cache_file) and md5sum_file(cache_file) != md5sum:
            logging.debug(f'Removing stale cache file (checksum mismatch): {cache_file}')
            os.unlink(cache_file)
        cmd = [
            'repo-add',
            '--remove',
            os.path.join(
                repo_dir,
                f'{repo_name}.db.tar.xz',
            ),
            target_file,
        ]
        logging.debug(f'repo: running cmd: {cmd}')
        result = subprocess.run(cmd)
        if result.returncode != 0:
            raise Exception(f'Failed to add package {target_file} to repo {repo_name}')
        for ext in ['db', 'files']:
            file = os.path.join(repo_dir, f'{repo_name}.{ext}')
            if os.path.exists(file + '.tar.xz'):
                os.unlink(file)
                shutil.copyfile(file + '.tar.xz', file)
            old = file + '.tar.xz.old'
            if os.path.exists(old):
                os.unlink(old)

    def add_package_to_repo(self, package: Pkgbuild, arch: Arch):
        logging.info(f'Adding {package.path} to repo {package.repo}')
        pkgbuild_dir = self.pkgbuilds.pkgbuilds_dir

        files = []
        for file in os.listdir(pkgbuild_dir):
            # Forced extension by makepkg.conf
            if file.endswith('.pkg.tar.xz') or file.endswith('.pkg.tar.zst'):
                assert package.repo and package.repo.name
                repo_name = package.repo.name
                repo_dir = os.path.join(self.repo_dir, arch, repo_name)
                files.append(os.path.join(repo_dir, file))
                self.add_file_to_repo(os.path.join(pkgbuild_dir, file), repo_name, arch)
        return files

    def check_package_version_built(self, package: Pkgbuild, arch: Arch) -> bool:
        native_chroot = setup_build_chroot(config.runtime['arch'])

        missing = False
        for line in package.get_pkg_filenames(arch, native_chroot):
            if line != "":
                assert package.repo and package.repo.name
                file = os.path.join(self.repo_dir, arch, package.repo.name, os.path.basename(line))
                logging.debug(f'Checking if {file} is built')
                if os.path.exists(file):
                    self.add_file_to_repo(file, repo_name=package.repo.name, arch=arch)
                else:
                    missing = True

        return not missing

    def get_unbuilt_package_levels(self, packages: Iterable[Pkgbuild], arch: Arch, force: bool = False) -> list[set[Pkgbuild]]:
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

    def build_enable_qemu_binfmt(self, arch: Arch):
        if arch not in ARCHES:
            raise Exception(f'Unknown architecture "{arch}". Choices: {", ".join(ARCHES)}')
        enforce_wrap()
        self.pkgbuilds.discover_packages()
        native = config.runtime['arch']
        # build qemu-user, binfmt, crossdirect
        chroot = setup_build_chroot(native)
        logging.info('Installing qemu-user (building if necessary)')
        qemu_pkgs = [self.pkgbuilds.pkgbuilds[pkg] for pkg in QEMU_BINFMT_PKGS]
        self.build_packages(
            qemu_pkgs,
            native,
            enable_crosscompile=False,
            enable_crossdirect=False,
            enable_ccache=False,
        )
        subprocess.run(['pacman', '-Syy', '--noconfirm', '--needed', '--config', os.path.join(chroot.path, 'etc/pacman.conf')] + QEMU_BINFMT_PKGS)
        if arch != native:
            binfmt_register(arch)


_local_repo: LocalRepo


def get_repo() -> LocalRepo:
    global _local_repo
    if not _local_repo:
        _local_repo = LocalRepo()
    return _local_repo
