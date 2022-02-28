from __future__ import annotations
from typing import Optional
import logging

from constants import Arch
from utils import download_file

from .version import compare_package_versions


class PackageInfo:
    name: str
    version: str
    arch: str
    resolved_url: Optional[str]
    _filename: Optional[str]

    def __init__(self, name: str, version: str, arch: str, filename: str = None, resolved_url: str = None):
        self.name = name
        self.version = version
        self.resolved_url = resolved_url
        self.arch = arch
        self._filename = filename

    def __repr__(self):
        return f'{self.name}@{self.version}'

    def compare_version(self, other: PackageInfo) -> int:
        """Returns -1 if `other` is newer than `self`, 0 if `self == other`, 1 if `self` is newer than `other`"""
        return compare_package_versions(self.version, other.version)

    def get_filename(self, ext='.zst') -> str:
        return self._filename or f'{self.name}-{self.version}-{self.arch}.pkg.tar{ext}'

    def acquire(self) -> str:
        """
        Acquires the package through either build or download.
        Returns the downloaded file's name.
        """
        assert self.resolved_url
        raise NotImplementedError()

    def is_remote(self) -> bool:
        return bool(self.resolved_url and not self.resolved_url.startswith('file://'))


class RemotePackage(PackageInfo):

    def acquire(self):
        assert self.resolved_url
        assert self.is_remote()
        download_file(f'{self.resolved_url}/{self.get_filename()}')


def parse_package_desc(desc_str: str, arch: Arch, resolved_url=None) -> PackageInfo:
    """Parses a desc file, returning a PackageInfo"""

    pruned_lines = ([line.strip() for line in desc_str.split('%') if line.strip()])
    desc = {}
    for key, value in zip(pruned_lines[0::2], pruned_lines[1::2]):
        desc[key.strip()] = value.strip()
    return RemotePackage(name=desc['NAME'], version=desc['VERSION'], arch=arch, filename=desc['FILENAME'], resolved_url=resolved_url)


def split_version_str(version_str) -> tuple[str, str]:
    pkgver, pkgrel = version_str.rsplit('-', maxsplit=1)
    logging.debug('Split versions: pkgver: {pkgver}; pkgrel: {pkgrel}')
    return pkgver, pkgrel
