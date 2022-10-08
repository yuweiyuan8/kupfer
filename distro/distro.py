from typing import Generic, Mapping, Optional, TypeVar

from constants import Arch, ARCHES, BASE_DISTROS, REPOSITORIES, KUPFER_HTTPS, CHROOT_PATHS
from generator import generate_pacman_conf_body
from config.state import config

from .repo import BinaryPackageType, RepoInfo, Repo, LocalRepo, RemoteRepo

RepoType = TypeVar('RepoType', bound=Repo)


class Distro(Generic[RepoType]):
    repos: Mapping[str, RepoType]
    arch: str

    def __init__(self, arch: Arch, repo_infos: dict[str, RepoInfo], scan=False):
        assert (arch in ARCHES)
        self.arch = arch
        self.repos = dict[str, RepoType]()
        for repo_name, repo_info in repo_infos.items():
            self.repos[repo_name] = self._create_repo(
                name=repo_name,
                arch=arch,
                url_template=repo_info.url_template,
                options=repo_info.options,
                scan=scan,
            )

    def _create_repo(self, **kwargs) -> RepoType:
        raise NotImplementedError()
        Repo(**kwargs)

    def get_packages(self) -> dict[str, BinaryPackageType]:
        """ get packages from all repos, semantically overlaying them"""
        results = dict[str, BinaryPackageType]()
        for repo in list(self.repos.values())[::-1]:
            assert repo.packages is not None
            results.update(repo.packages)
        return results

    def repos_config_snippet(self, extra_repos: Mapping[str, RepoInfo] = {}) -> str:
        extras: list[Repo] = [
            Repo(name, url_template=info.url_template, arch=self.arch, options=info.options, scan=False) for name, info in extra_repos.items()
        ]
        return '\n\n'.join(repo.config_snippet() for repo in (extras + list(self.repos.values())))

    def get_pacman_conf(self, extra_repos: Mapping[str, RepoInfo] = {}, check_space: bool = True, in_chroot: bool = True):
        body = generate_pacman_conf_body(self.arch, check_space=check_space)
        return body + self.repos_config_snippet(extra_repos)

    def scan(self, lazy=True):
        for repo in self.repos.values():
            if not (lazy and repo.scanned):
                repo.scan()

    def is_scanned(self):
        for repo in self.repos.values():
            if not repo.scanned:
                return False
        return True


class LocalDistro(Distro[LocalRepo]):

    def _create_repo(self, **kwargs) -> LocalRepo:
        return LocalRepo(**kwargs)


class RemoteDistro(Distro[RemoteRepo]):

    def _create_repo(self, **kwargs) -> RemoteRepo:
        return RemoteRepo(**kwargs)


def get_base_distro(arch: str) -> RemoteDistro:
    repos = {name: RepoInfo(url_template=url) for name, url in BASE_DISTROS[arch]['repos'].items()}
    return RemoteDistro(arch=arch, repo_infos=repos, scan=False)


def get_kupfer(arch: str, url_template: str, scan: bool = False) -> Distro:
    repos = {name: RepoInfo(url_template=url_template, options={'SigLevel': 'Never'}) for name in REPOSITORIES}
    remote = not url_template.startswith('file://')
    clss = RemoteDistro if remote else LocalDistro
    distro = clss(
        arch=arch,
        repo_infos=repos,
        scan=scan,
    )
    assert isinstance(distro, (LocalDistro, RemoteDistro))
    return distro


_kupfer_https = dict[Arch, RemoteDistro]()
_kupfer_local = dict[Arch, LocalDistro]()
_kupfer_local_chroots = dict[Arch, LocalDistro]()


def get_kupfer_https(arch: Arch, scan: bool = False) -> RemoteDistro:
    global _kupfer_https
    if arch not in _kupfer_https or not _kupfer_https[arch]:
        kupfer = get_kupfer(arch, KUPFER_HTTPS.replace('%branch%', config.file.pacman.repo_branch), scan)
        assert isinstance(kupfer, RemoteDistro)
        _kupfer_https[arch] = kupfer
    item = _kupfer_https[arch]
    if scan and not item.is_scanned():
        item.scan()
    return item


def get_kupfer_local(arch: Optional[Arch] = None, in_chroot: bool = True, scan: bool = False) -> LocalDistro:
    global _kupfer_local, _kupfer_local_chroots
    cache = _kupfer_local_chroots if in_chroot else _kupfer_local
    arch = arch or config.runtime.arch
    assert arch
    if arch not in cache or not cache[arch]:
        dir = CHROOT_PATHS['packages'] if in_chroot else config.get_path('packages')
        kupfer = get_kupfer(arch, f"file://{dir}/$arch/$repo")
        assert isinstance(kupfer, LocalDistro)
        cache[arch] = kupfer
    item = cache[arch]
    if scan and not item.is_scanned():
        item.scan()
    return item
