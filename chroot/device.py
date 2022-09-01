import atexit
import os

from typing import Optional

from config import config
from constants import Arch, BASE_PACKAGES
from distro.repo import RepoInfo
from distro.distro import get_kupfer_local, get_kupfer_https
from exec.file import get_temp_dir, makedir, root_makedir
from utils import check_findmnt

from .base import BaseChroot
from .build import BuildChroot
from .abstract import get_chroot


class DeviceChroot(BuildChroot):

    copy_base: bool = False

    def create_rootfs(self, reset, pacman_conf_target, active_previously):
        clss = BuildChroot if self.copy_base else BaseChroot

        makedir(config.get_path('chroots'))
        root_makedir(self.get_path())
        if not self.copy_base:
            pacman_conf_target = os.path.join(get_temp_dir(register_cleanup=True), f'pacman-{self.name}.conf')
            self.write_pacman_conf(in_chroot=False, absolute_path=pacman_conf_target)

        clss.create_rootfs(self, reset, pacman_conf_target, active_previously)

    def mount_rootfs(self, source_path: str, fs_type: str = None, options: list[str] = [], allow_overlay: bool = False):
        if self.active:
            raise Exception(f'{self.name}: Chroot is marked as active, not mounting a rootfs over it.')
        if not os.path.exists(source_path):
            raise Exception('Source does not exist')
        if not allow_overlay:
            really_active = []
            for mnt in self.active_mounts:
                if check_findmnt(self.get_path(mnt)):
                    really_active.append(mnt)
            if really_active:
                raise Exception(f'{self.name}: Chroot has submounts active: {really_active}')
            if os.path.ismount(self.path):
                raise Exception(f'{self.name}: There is already something mounted at {self.path}, not mounting over it.')
            if os.path.exists(os.path.join(self.path, 'usr/bin')):
                raise Exception(f'{self.name}: {self.path}/usr/bin exists, not mounting over existing rootfs.')
        makedir(self.path)
        atexit.register(self.deactivate)
        self.mount(source_path, '/', fs_type=fs_type, options=options)


def get_device_chroot(
    device: str,
    flavour: str,
    arch: Arch,
    packages: list[str] = BASE_PACKAGES,
    use_local_repos: bool = True,
    extra_repos: Optional[dict[str, RepoInfo]] = None,
    **kwargs,
) -> DeviceChroot:
    name = f'rootfs_{device}-{flavour}'
    repos: dict[str, RepoInfo] = get_kupfer_local(arch).repos if use_local_repos else get_kupfer_https(arch).repos  # type: ignore

    repos.update(extra_repos or {})

    default = DeviceChroot(name, arch, initialize=False, copy_base=False, base_packages=packages, extra_repos=repos)
    chroot = get_chroot(name, **kwargs, extra_repos=repos, default=default)
    assert isinstance(chroot, DeviceChroot)
    return chroot
