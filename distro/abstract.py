from copy import deepcopy
from typing import Optional, Mapping, ChainMap, Any

from .version import compare_package_versions


class PackageInfo:
    name: str
    version: str
    _filename: Optional[str]
    depends: list[str]
    provides: list[str]
    replaces: list[str]

    def __init__(self, name: str, version: str, filename: str = None):
        self.name = name
        self.version = version
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
        assert self._filename
        return self._filename

    def acquire(self) -> Optional[str]:
        """
        Acquires the package through either build or download.
        Returns the downloaded file's path.
        """
        raise NotImplementedError()

    def is_remote(self) -> bool:
        raise NotImplementedError()


class RepoSearchResult:
    """Repo search results split along qualifier. Truthy value is calculated on whether all members are empty"""
    exact_name: list[PackageInfo]
    provides: list[PackageInfo]
    replaces: list[PackageInfo]

    def __init__(self):
        self.exact_name = []
        self.provides = []
        self.replaces = []

    def __bool__(self):
        return self.exact_name and self.provides and self.replaces


ResultSource = Any
ResultSources = Mapping[ResultSource, RepoSearchResult]


class MergedResults:
    results: ResultSources
    exact_name: Mapping[PackageInfo, ResultSource]
    replaces: Mapping[PackageInfo, ResultSource]
    provides: Mapping[PackageInfo, ResultSource]

    def __init__(self, sources: ResultSources = {}):
        self.results = {}
        self.update(sources)

    def update(self, additional_sources: ResultSources = {}):
        assert isinstance(self.results, dict)
        self.results.update(additional_sources)
        self.exact_name = {}
        self.replaces = {}
        self.provides = {}
        for source, results in self.results.items():
            for source_category, target_category in [
                (results.exact_name, self.exact_name),
                (results.replaces, self.replaces),
                (results.provides, self.provides),
            ]:
                for pkg in source_category:
                    target_category[pkg] = source


class RepoInfo:
    name: str
    options: dict[str, str] = {}
    url_template: str
    packages: dict[str, PackageInfo]
    remote: bool

    def __init__(self, name: str, url_template: str, options: dict[str, str] = {}):
        self.name = name
        self.url_template = url_template
        self.options = deepcopy(options)
        self.remote = not url_template.startswith('file://')

    def acquire_package(self, package: PackageInfo) -> Optional[str]:
        if package not in self.packages.values():
            raise NotImplementedError(f'Package {package} did not come from our repo')
        return package.acquire()

    def config_snippet(self) -> str:
        options = {'Server': self.url_template} | self.options
        return ('[%s]\n' % self.name) + '\n'.join([f"{key} = {value}" for key, value in options.items()])

    def scan(self, refresh: bool = False):
        pass

    def get_providers(self, name: str) -> RepoSearchResult:
        results = RepoSearchResult()
        for package in self.packages.values():
            if name == package.name:
                results.exact_name.append(package)
            if name in package.provides:
                results.provides.append(package)
            if name in package.replaces:
                results.replaces.append(package)
        return results


class DistroInfo:
    repos: Mapping[str, RepoInfo]

    def get_packages(self) -> Mapping[str, PackageInfo]:
        """ get packages from all repos, semantically overlaying them"""
        # results = {}
        # for repo in list(self.repos.values())[::-1]: # TODO: figure if the list even needs to be reversed
        #    assert repo.packages is not None
        #    for package in repo.packages.values():
        #        results[package.name] = package
        # return results
        return ChainMap[str, PackageInfo](*[repo.packages for repo in list(self.repos.values())])

    def get_providers(self, name: str, allow_empty: bool = False) -> MergedResults:
        """Returns a mapping from repo.name to RepoSearchResult"""
        return MergedResults({name: repo.get_providers(name) for name, repo in list(self.repos.items())})
