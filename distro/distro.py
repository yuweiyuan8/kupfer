from typing import Optional, Mapping

from constants import Arch, ARCHES, BASE_DISTROS, REPOSITORIES, KUPFER_HTTPS, CHROOT_PATHS
from generator import generate_pacman_conf_body
from config import config

from .abstract import RepoInfo, DistroInfo
from .repo import Repo


class Distro(DistroInfo):
    arch: str
    repos: Mapping[str, Repo]

    def __init__(self, arch: str, repo_infos: Mapping[str, RepoInfo], scan=False):
        assert (arch in ARCHES)
        self.arch = arch
        self.repos = dict[str, Repo]()
        for repo_name, repo_info in repo_infos.items():
            self.repos[repo_name] = Repo(
                name=repo_name,
                arch=arch,
                url_template=repo_info.url_template,
                options=repo_info.options,
                scan=scan,
            )

    def repos_config_snippet(self, extra_repos: Mapping[str, RepoInfo] = {}) -> str:
        extras = [Repo(name, url_template=info.url_template, arch=self.arch, options=info.options, scan=False) for name, info in extra_repos.items()]
        return '\n\n'.join(repo.config_snippet() for repo in (list(self.repos.values()) + extras))

    def get_pacman_conf(self, extra_repos: Mapping[str, RepoInfo] = {}, check_space: bool = True):
        body = generate_pacman_conf_body(self.arch, check_space=check_space)
        return body + self.repos_config_snippet(extra_repos)


def get_base_distro(arch: str) -> Distro:
    repos = {name: RepoInfo(name, url_template=url) for name, url in BASE_DISTROS[arch]['repos'].items()}
    return Distro(arch=arch, repo_infos=repos, scan=False)


def get_kupfer(arch: str, url_template: str) -> Distro:
    repos: Mapping[str, Repo] = {name: Repo(name, url_template=url_template, arch=arch, options={'SigLevel': 'Never'}) for name in REPOSITORIES}
    return Distro(
        arch=arch,
        repo_infos=repos,
    )


kupfer_https: dict[Arch, Distro]
kupfer_local: dict[Arch, dict[bool, Distro]]


def get_kupfer_https(arch: Arch) -> Distro:
    global kupfer_https
    if arch not in kupfer_https or not kupfer_https[arch]:
        kupfer_https[arch] = get_kupfer(arch, KUPFER_HTTPS)
    return kupfer_https[arch]


def get_kupfer_local(arch: Optional[Arch] = None, in_chroot: bool = True) -> Distro:
    global kupfer_local
    arch = arch or config.runtime['arch']
    dir = CHROOT_PATHS['packages'] if in_chroot else config.get_path('packages')
    if arch not in kupfer_local:
        kupfer_local[arch] = {}
    locals = kupfer_local[arch]
    if in_chroot not in locals or not locals[in_chroot]:
        locals[in_chroot] = get_kupfer(arch, f"file://{dir}/$arch/$repo")
    return locals[in_chroot]
