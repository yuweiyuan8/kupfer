import click
import logging
import os

from typing import Optional

from config.state import config
from wrapper import enforce_wrap
from devices.device import get_profile_device

from .abstract import Chroot
from .base import get_base_chroot
from .build import get_build_chroot, BuildChroot

CHROOT_TYPES = ['base', 'build', 'rootfs']


@click.command('chroot')
@click.argument('type', required=False, type=click.Choice(CHROOT_TYPES), default='build')
@click.argument(
    'name',
    required=False,
    default=None,
)
@click.pass_context
def cmd_chroot(ctx: click.Context, type: str = 'build', name: Optional[str] = None, enable_crossdirect=True):
    """Open a shell in a chroot. For rootfs NAME is a profile name, for others the architecture (e.g. aarch64)."""

    if type not in CHROOT_TYPES:
        raise Exception(f'Unknown chroot type: "{type}"')

    if type == 'rootfs':
        from image.image import cmd_inspect
        assert isinstance(cmd_inspect, click.Command)
        ctx.invoke(cmd_inspect, profile=name, shell=True)
        return

    enforce_wrap()

    chroot: Chroot
    arch = name
    if not arch:
        arch = get_profile_device().arch
    assert arch
    if type == 'base':
        chroot = get_base_chroot(arch)
        if not os.path.exists(chroot.get_path('/bin')):
            chroot.initialize()
        chroot.initialized = True
    elif type == 'build':
        build_chroot: BuildChroot = get_build_chroot(arch, activate=True)
        chroot = build_chroot  # type safety
        if not os.path.exists(build_chroot.get_path('/bin')):
            build_chroot.initialize()
        build_chroot.initialized = True
        build_chroot.mount_pkgbuilds()
        build_chroot.mount_chroots()
        assert arch and config.runtime.arch
        if config.file.build.crossdirect and enable_crossdirect and arch != config.runtime.arch:
            build_chroot.mount_crossdirect()
    else:
        raise Exception('Really weird bug')

    chroot.mount_packages()
    chroot.activate()
    logging.debug(f'Starting shell in {chroot.name}:')
    chroot.run_cmd('bash', attach_tty=True)
