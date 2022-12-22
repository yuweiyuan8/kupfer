import click

from constants import Arch, ARCHES

from .binfmt import binfmt_unregister

cmd_binfmt = click.Group('binfmt', help='Manage qemu binfmt for executing foreign architecture binaries')
arch_arg = click.argument('arch', type=click.Choice(ARCHES))


@cmd_binfmt.command('register', help='Register a binfmt handler with the kernel')
@arch_arg
def cmd_register(arch: Arch, disable_chroot: bool = False):
    from packages.build import build_enable_qemu_binfmt
    build_enable_qemu_binfmt(arch)


@cmd_binfmt.command('unregister', help='Unregister a binfmt handler from the kernel')
@arch_arg
def cmd_unregister(arch: Arch):
    binfmt_unregister(arch)
