from io import BufferedReader
import logging
import os
import tarfile

from config import config
from utils import download_file

from .abstract import RepoInfo
from .package import Package


def resolve_url(url_template, repo_name: str, arch: str):
    result = url_template
    for template, replacement in {'$repo': repo_name, '$arch': config.runtime['arch']}.items():
        result = result.replace(template, replacement)
    return result


class Repo(RepoInfo):
    resolved_url: str
    arch: str
    scanned: bool

    def __init__(self, name: str, url_template: str, arch: str, options: dict[str, str] = {}, scan=False):
        self.scanned = False
        self.packages = {}
        self.url_template = url_template
        self.arch = arch

        super().__init__(name, url_template=url_template, options=options)
        if scan:
            self.scan()

    def acquire_index(self) -> str:
        """[Download and] return local file path to repo .db file"""
        self.resolved_url = resolve_url(self.url_template, repo_name=self.name, arch=self.arch)
        self.remote = not self.resolved_url.startswith('file://')
        uri = f'{self.resolved_url}/{self.name}.db'
        if self.remote:
            logging.debug(f'Downloading repo file from {uri}')
            path = download_file(uri)
        else:
            path = uri.split('file://')[1]
        return path

    def scan(self, refresh: bool = False):
        if refresh or not self.scanned:
            path = self.acquire_index()
            logging.debug(f'Parsing repo file at {path}')
            with tarfile.open(path) as index:
                for node in index.getmembers():
                    if os.path.basename(node.name) == 'desc':
                        logging.debug(f'Parsing desc file for {os.path.dirname(node.name)}')
                        with index.extractfile(node) as reader:  # type: ignore
                            assert isinstance(reader, BufferedReader)
                            desc = reader.read().decode()
                        pkg = Package.parse_desc(desc, repo_name=self.name, resolved_url=self.resolved_url)
                        self.packages[pkg.name] = pkg
        self.scanned = True
