import logging
import os
import shutil
import subprocess
from typing import Optional

from config import config
from constants import Arch, CHROOT_PATHS
from distro.repo import Repo
from distro.abstract import PackageInfo
from utils import md5sum_file

from .pkgbuild import Pkgbuild, Pkgbase, SubPkgbuild


class LocalRepo(Repo):
    initialized: bool = False
    repo_dir: str

    def __init__(self, name: str, arch: Arch, repo_dir: Optional[str] = None, options: dict[str, str] = {'SigLevel': 'Never'}, scan=False):
        self.repo_dir = repo_dir or config.get_path('packages')
        self.full_path = os.path.join(self.repo_dir, arch, name)
        super().__init__(name=name, url_template=f'file://{CHROOT_PATHS["packages"]}/$arch/$repo', arch=arch, options=options, scan=scan)

    def init(self):
        """Create repo database files"""
        if not self.initialized:
            repo = self.name
            repo_dir = os.path.join(self.repo_dir, self.arch, repo)
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

    def scan(self, refresh: bool = False):
        if not self.initialized:
            self.init()
        super().scan(refresh=refresh)

    def copy_file_to_repo(self, file_path: str) -> str:
        file_name = os.path.basename(file_path)
        repo_dir = self.full_path
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
        return os.path.join(repo_dir, file_name), md5sum

    def run_repo_add(self, target_file: str):
        cmd = [
            'repo-add',
            '--remove',
            os.path.join(
                self.full_path,
                f'{self.name}.db.tar.xz',
            ),
            target_file,
        ]
        logging.debug(f'repo: running cmd: {cmd}')
        result = subprocess.run(cmd)
        if result.returncode != 0:
            raise Exception(f'Failed to add package {target_file} to repo {self.name}')
        for ext in ['db', 'files']:
            file = os.path.join(self.full_path, f'{self.name}.{ext}')
            if os.path.exists(file + '.tar.xz'):
                os.unlink(file)
                shutil.copyfile(file + '.tar.xz', file)
            old = file + '.tar.xz.old'
            if os.path.exists(old):
                os.unlink(old)

    def add_file_to_repo(self, file_path: str):
        pacman_cache_dir = os.path.join(config.get_path('pacman'), self.arch)
        file_name = os.path.basename(file_path)

        # copy file to repo dir
        target_file, md5sum = self.copy_file_to_repo(file_path)

        # clean up same name package from pacman cache
        cache_file = os.path.join(pacman_cache_dir, file_name)
        if os.path.exists(cache_file) and md5sum_file(cache_file) != md5sum:
            logging.debug(f'Removing stale cache file (checksum mismatch): {cache_file}')
            os.unlink(cache_file)
        self.run_repo_add(target_file)
        return target_file

    def add_package_to_repo(self, package: Pkgbuild):
        logging.info(f'Adding {package.name} at {package.path} to repo {self.name}')
        pkgbuild_dir = package.path
        assert package.path

        files = []
        for file in os.listdir(pkgbuild_dir):
            # Forced extension by makepkg.conf
            if file.endswith('.pkg.tar.xz') or file.endswith('.pkg.tar.zst'):
                files.append(self.add_file_to_repo(os.path.join(pkgbuild_dir, file)))
        return files
