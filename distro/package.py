import logging
import os

from shutil import copyfileobj
from typing import Optional
from urllib.request import urlopen

from exec.file import get_temp_dir, makedir


class PackageInfo:
    name: str
    version: str


class BinaryPackage(PackageInfo):
    arch: str
    filename: str
    resolved_url: Optional[str]

    def __init__(
        self,
        name: str,
        version: str,
        arch: str,
        filename: str,
        resolved_url: Optional[str] = None,
    ):
        self.name = name
        self.version = version
        self.arch = arch
        self.filename = filename
        self.resolved_url = resolved_url

    def __repr__(self):
        return f'{self.name}@{self.version}'

    @classmethod
    def parse_desc(clss, desc_str: str, resolved_repo_url=None):
        """Parses a desc file, returning a PackageInfo"""

        pruned_lines = ([line.strip() for line in desc_str.split('%') if line.strip()])
        desc = {}
        for key, value in zip(pruned_lines[0::2], pruned_lines[1::2]):
            desc[key.strip()] = value.strip()
        return clss(name=desc['NAME'], version=desc['VERSION'], arch=desc['ARCH'], filename=desc['FILENAME'], resolved_url='/'.join([resolved_repo_url, desc['FILENAME']]))

    def acquire(self) -> str:
        raise NotImplementedError()


class LocalPackage(BinaryPackage):

    def acquire(self) -> str:
        assert self.resolved_url and self.filename and self.filename in self.resolved_url
        path = f'{self.resolved_url.split("file://")[1]}'
        assert os.path.exists(path) or print(path)
        return path


class RemotePackage(BinaryPackage):

    def acquire(self, dest_dir: Optional[str] = None) -> str:
        assert self.resolved_url and '.pkg.tar.' in self.resolved_url
        url = f"{self.resolved_url}"
        assert url

        dest_dir = dest_dir or get_temp_dir()
        makedir(dest_dir)
        dest_file_path = os.path.join(dest_dir, self.filename)

        logging.info(f"Trying to download package {url}")
        with urlopen(url) as fsrc, open(dest_file_path, 'wb') as fdst:
            copyfileobj(fsrc, fdst)
        logging.info(f"{self.filename} downloaded from repos")
        return dest_file_path
