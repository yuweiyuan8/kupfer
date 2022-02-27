import logging
from typing import Optional
from __future__ import annotations

from constants import Arch


class PackageInfo:
    name: str
    version: str
    arch: str
    filename: str
    resolved_url: Optional[str]

    def __init__(
        self,
        name: str,
        version: str,
        filename: str,
        arch: str,
        resolved_url: str = None,
    ):
        self.name = name
        self.version = version
        self.filename = filename
        self.resolved_url = resolved_url

    def __repr__(self):
        return f'{self.name}@{self.version}'

    def compare_version(self, other: PackageInfo):
        return self.version == other.version

    def acquire(self):
        assert self.resolved_url
        raise NotImplementedError()


def parse_package_desc(desc_str: str, arch: Arch, resolved_url=None) -> PackageInfo:
    """Parses a desc file, returning a PackageInfo"""

    pruned_lines = ([line.strip() for line in desc_str.split('%') if line.strip()])
    desc = {}
    for key, value in zip(pruned_lines[0::2], pruned_lines[1::2]):
        desc[key.strip()] = value.strip()
    return PackageInfo(desc['NAME'], desc['VERSION'], desc['FILENAME'], arch, resolved_url=resolved_url)


def split_version_str(version_str) -> tuple[str, str]:
    pkgver, pkgrel = version_str.rsplit('-', maxsplit=1)
    logging.debug('Split versions: pkgver: {pkgver}; pkgrel: {pkgrel}')
    return pkgver, pkgrel
