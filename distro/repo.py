from copy import deepcopy
from io import BufferedReader
import logging
import os
import tarfile

from config import config
from utils import download_file

from .package import PackageInfo, parse_package_desc


def resolve_url(url_template, repo_name: str, arch: str):
    result = url_template
    for template, replacement in {'$repo': repo_name, '$arch': config.runtime['arch']}.items():
        result = result.replace(template, replacement)
    return result


class RepoInfo:
    options: dict[str, str] = {}
    url_template: str
    packages: dict[str, PackageInfo]
    remote: bool

    def __init__(self, url_template: str, options: dict[str, str] = {}):
        self.url_template = url_template
        self.options = deepcopy(options)
        self.remote = not url_template.startswith('file://')

    def acquire_package(self, package: PackageInfo) -> str:
        if package not in self.packages.values():
            raise NotImplementedError(f'Package {package} did not come from our repo')
        return package.acquire()

    def scan(self, refresh: bool = False):
        pass


class Repo(RepoInfo):
    name: str
    resolved_url: str
    arch: str
    scanned: bool = False

    def __init__(self, name: str, url_template: str, arch: str, options: dict[str, str] = {}, scan=False):
        self.packages = {}
        self.name = name
        self.url_template = url_template
        self.arch = arch

        super().__init__(url_template=url_template, options=options)
        if scan:
            self.scan()

    def get_package_from_desc(self, desc_str: str) -> PackageInfo:
        return parse_package_desc(desc_str=desc_str, arch=self.arch, resolved_url=self.resolved_url)

    def scan(self, refresh: bool = False):
        if refresh or not self.scanned:
            self.resolved_url = resolve_url(self.url_template, repo_name=self.name, arch=self.arch)
            self.remote = not self.resolved_url.startswith('file://')
            uri = f'{self.resolved_url}/{self.name}.db'
            path = ''
            if self.remote:
                logging.debug(f'Downloading repo file from {uri}')
                path = download_file(uri)
            else:
                path = uri.split('file://')[1]
            logging.debug(f'Parsing repo file at {path}')
            with tarfile.open(path) as index:
                for node in index.getmembers():
                    if os.path.basename(node.name) == 'desc':
                        logging.debug(f'Parsing desc file for {os.path.dirname(node.name)}')
                        with index.extractfile(node) as reader:  # type: ignore
                            assert isinstance(reader, BufferedReader)
                            desc = reader.read().decode()
                        pkg = self.get_package_from_desc(desc)
                        self.packages[pkg.name] = pkg

        self.scanned = True

    def config_snippet(self) -> str:
        options = {'Server': self.url_template} | self.options
        return ('[%s]\n' % self.name) + '\n'.join([f"{key} = {value}" for key, value in options.items()])

    def get_RepoInfo(self):
        return RepoInfo(url_template=self.url_template, options=self.options)
