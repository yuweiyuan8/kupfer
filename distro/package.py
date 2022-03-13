from __future__ import annotations
from typing import Optional
import logging

from constants import Arch

from .abstract import PackageInfo


class Package(PackageInfo):
    arch: Arch
    resolved_url: Optional[str] = None
    repo_name: str
    md5sum: Optional[str]

    def __init__(self, arch: Arch, repo_name: str, *args, resolved_url: Optional[str] = None, **kwargs):
        self.repo_name = repo_name
        self.resolved_url = resolved_url
        super().__init__(*args, **kwargs)

    def get_filename(self, ext='.zst') -> str:
        return self._filename or f'{self.name}-{self.version}-{self.arch}.pkg.tar{ext}'

    def is_remote(self) -> bool:
        return bool(self.resolved_url and not self.resolved_url.startswith('file://'))

    @staticmethod
    def parse_desc(desc_str: str, repo_name: str, resolved_url=None) -> Package:
        """Parses a desc file, returning a Package"""

        pruned_lines = ([line.strip() for line in desc_str.split('%') if line.strip()])
        desc = {}
        for key, value in zip(pruned_lines[0::2], pruned_lines[1::2]):
            desc[key.strip()] = value.strip()
        package = Package(name=desc['NAME'],
                          version=desc['VERSION'],
                          arch=desc['ARCH'],
                          filename=desc['FILENAME'],
                          resolved_url=resolved_url,
                          repo_name=repo_name)
        package.md5sum = desc.get('MD5SUM', None)
        return package


def split_version_str(version_str) -> tuple[str, str]:
    pkgver, pkgrel = version_str.rsplit('-', maxsplit=1)
    logging.debug('Split versions: pkgver: {pkgver}; pkgrel: {pkgrel}')
    return pkgver, pkgrel
