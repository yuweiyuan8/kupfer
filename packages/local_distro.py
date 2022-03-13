import logging
import subprocess
import os
from typing import Optional

from binfmt import register as binfmt_register
from config import config
from chroot.build import setup_build_chroot
from constants import Arch, ARCHES, QEMU_BINFMT_PKGS, REPOSITORIES
from wrapper import enforce_wrap

from distro.distro import Distro
from .local_repo import LocalRepo


class LocalDistro(Distro):
    pass


_local_distros = dict[Arch, LocalDistro]()


def get_local_distro(arch: Arch, repo_names: list[str] = REPOSITORIES) -> LocalDistro:
    global _local_distros
    if arch not in _local_distros or not _local_distros[arch]:
        repos = dict[str, LocalRepo]()
        for name in repo_names:
            repos[name] = LocalRepo(name, arch)
        _local_distros[arch] = LocalDistro(arch, repos)
    return _local_distros[arch]


def get_local_distro_flat(arch: Arch, flat_repo_name: str = "local"):
    return get_local_distro(arch, [flat_repo_name])
