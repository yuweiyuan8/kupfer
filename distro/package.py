from __future__ import annotations
from typing import Optional
import logging

from constants import Arch
from utils import download_file

from .version import compare_package_versions


class PackageInfo:
    name: str
    version: str
    arch: Arch
    _filename: Optional[str]
    depends: list[str]
    provides: list[str]
    replaces: list[str]

    def __init__(self, name: str, version: str, arch: Arch, filename: str = None):
        self.name = name
        self.version = version
        self.arch = arch
        self._filename = filename
        self.depends = []
        self.provides = []
        self.replaces = []

    def __repr__(self):
        return f'{self.name}@{self.version}'

    def compare_version(self, other: str) -> int:
        """Returns -1 if `other` is newer than `self`, 0 if `self == other`, 1 if `self` is newer than `other`"""
        return compare_package_versions(self.version, other)

    def get_filename(self, ext='.zst') -> str:
        return self._filename or f'{self.name}-{self.version}-{self.arch}.pkg.tar{ext}'

    def acquire(self) -> Optional[str]:
        """
        Acquires the package through either build or download.
        Returns the downloaded file's path.
        """
        raise NotImplementedError()

    def is_remote(self) -> bool:
        raise NotImplementedError()


class RemotePackage(PackageInfo):
    resolved_url: Optional[str] = None
    repo_name: str

    def __init__(self, repo_name: str, *args, resolved_url: Optional[str] = None, **kwargs):
        self.repo_name = repo_name
        self.resolved_url = resolved_url
        super().__init__(*args, **kwargs)

    def acquire(self):
        assert self.resolved_url
        assert self.is_remote()
        return download_file(f'{self.resolved_url}/{self.get_filename()}')

    def is_remote(self) -> bool:
        return bool(self.resolved_url and not self.resolved_url.startswith('file://'))


def parse_package_desc(desc_str: str, arch: Arch, repo_name: str, resolved_url=None) -> PackageInfo:
    """Parses a desc file, returning a PackageInfo"""

    pruned_lines = ([line.strip() for line in desc_str.split('%') if line.strip()])
    desc = {}
    for key, value in zip(pruned_lines[0::2], pruned_lines[1::2]):
        desc[key.strip()] = value.strip()
    return RemotePackage(name=desc['NAME'],
                         version=desc['VERSION'],
                         arch=arch,
                         filename=desc['FILENAME'],
                         resolved_url=resolved_url,
                         repo_name=repo_name)


def split_version_str(version_str) -> tuple[str, str]:
    pkgver, pkgrel = version_str.rsplit('-', maxsplit=1)
    logging.debug('Split versions: pkgver: {pkgver}; pkgrel: {pkgrel}')
    return pkgver, pkgrel
