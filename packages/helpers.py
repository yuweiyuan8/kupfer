import logging
import os
import multiprocessing

from config import config
from chroot.build import get_build_chroot, BuildChroot
from constants import Arch


def setup_build_chroot(
    arch: Arch,
    extra_packages: list[str] = [],
    add_kupfer_repos: bool = True,
    clean_chroot: bool = False,
) -> BuildChroot:
    chroot = get_build_chroot(arch, add_kupfer_repos=add_kupfer_repos)
    chroot.mount_packages()
    logging.info(f'Initializing {arch} build chroot')
    chroot.initialize(reset=clean_chroot)
    chroot.write_pacman_conf()  # in case it was initialized with different repos
    chroot.activate()
    chroot.mount_pacman_cache()
    chroot.mount_pkgbuilds()
    if extra_packages:
        chroot.try_install_packages(extra_packages, allow_fail=False)
    return chroot


def get_makepkg_env():
    # has to be a function because calls to `config` must be done after config file was read
    threads = config.file['build']['threads'] or multiprocessing.cpu_count()
    return os.environ.copy() | {
        'LANG': 'C',
        'CARGO_BUILD_JOBS': str(threads),
        'MAKEFLAGS': f"-j{threads}",
        'QEMU_LD_PREFIX': '/usr/aarch64-unknown-linux-gnu',
    }
