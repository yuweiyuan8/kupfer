from hashlib import md5
import logging
import os
import shutil
import subprocess

from typing import Iterable

from config import config
from constants import Arch
from distro.repo import RepoInfo

from .pkgbuild import Pkgbuild
from .source_repo import SourceRepo, get_repo as get_source_repo
from .local_distro import get_local_distro, LocalRepo
from .helpers import setup_build_chroot


class MetaRepo(LocalRepo):

    def __init__(self, name, local_repo: LocalRepo):
        self.name = name
        self.local_repo = local_repo
        self.arch = local_repo.arch

    def init(self, discover_packages: bool = True, parallel: bool = True):
        self.pkgbuilds.init()
        if discover_packages:
            self.pkgbuilds.discover_packages(refresh=False, parallel=parallel)
        self.local_repo.init()

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
                self.local_repo.add_file_to_repo(os.path.join(pkgbuild_dir, file), repo_name, arch)
        if files and self.local_repo.scanned:
            self.scan(refresh=True)
        return files

    def check_package_version_built(self, package: Pkgbuild) -> bool:
        native_chroot = setup_build_chroot(config.runtime['arch'])

        missing = False
        for line in package.get_pkg_filenames(self.arch, native_chroot):
            if not line:
                continue
            assert package.repo and package.repo.name
            file = os.path.join(self.repo_dir, self.arch, package.repo.name, os.path.basename(line))
            logging.debug(f'Checking if {file} is built')
            if os.path.exists(file):
                self.add_file_to_repo(file, repo_name=package.repo.name, arch=self.arch)
            else:
                missing = True

        return not missing
