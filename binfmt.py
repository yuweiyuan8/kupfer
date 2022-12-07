# modifed from pmbootstrap's binfmt.py, Copyright 2018 Oliver Smith, GPL-licensed

import os
import logging

from typing import Optional

from chroot.abstract import Chroot
from constants import Arch, QEMU_ARCHES
from exec.cmd import run_root_cmd
from utils import mount


def binfmt_info(chroot: Optional[Chroot] = None):
    # Parse the info file
    full = {}
    info = "/usr/lib/binfmt.d/qemu-static.conf"
    if chroot:
        info = chroot.get_path(info)
    logging.debug("parsing: " + info)
    with open(info, "r") as handle:
        for line in handle:
            if line.startswith('#') or ":" not in line:
                continue
            splitted = line.split(":")
            result = {
                # _ = splitted[0] # empty
                'name': splitted[1],
                'type': splitted[2],
                'offset': splitted[3],
                'magic': splitted[4],
                'mask': splitted[5],
                'interpreter': splitted[6],
                'flags': splitted[7],
                'line': line,
            }
            if not result['name'].startswith('qemu-'):
                logging.fatal(f'Unknown binfmt handler "{result["name"]}"')
                logging.debug(f'binfmt line: {line}')
                continue
            arch = ''.join(result['name'].split('-')[1:])
            full[arch] = result

    return full


def is_arch_known(arch: Arch, raise_exception: bool = False, action: Optional[str] = None) -> bool:
    if arch not in QEMU_ARCHES:
        if raise_exception:
            raise Exception(f'binfmt{f".{action}()" if action else ""}: unknown arch {arch} (not in QEMU_ARCHES)')
        return False
    return True


def binfmt_is_registered(arch: Arch, chroot: Optional[Chroot] = None) -> bool:
    is_arch_known(arch, True, 'is_registered')
    qemu_arch = QEMU_ARCHES[arch]
    path = "/proc/sys/fs/binfmt_misc/qemu-" + qemu_arch
    binfmt_ensure_mounted(chroot)
    if chroot:
        path = chroot.get_path(path)
    return os.path.exists(path)


def binfmt_ensure_mounted(chroot: Optional[Chroot] = None):
    binfmt_path = '/proc/sys/fs/binfmt_misc'
    register_path = binfmt_path + '/register'
    if chroot:
        binfmt_path = chroot.get_path(binfmt_path)
        register_path = chroot.get_path(register_path)
        chroot.activate()
    if not os.path.exists(register_path):
        logging.info('mounting binfmt_misc')
        result = mount('binfmt_misc', binfmt_path, options=[], fs_type='binfmt_misc')
        if result.returncode != 0:
            raise Exception(f'Failed mounting binfmt_misc to {binfmt_path}')


def register(arch: Arch, chroot: Optional[Chroot] = None):
    binfmt_path = '/proc/sys/fs/binfmt_misc'
    register_path = binfmt_path + '/register'
    is_arch_known(arch, True, 'register')
    qemu_arch = QEMU_ARCHES[arch]
    if binfmt_is_registered(arch):
        return

    lines = binfmt_info()

    _runcmd = run_root_cmd
    if chroot:
        _runcmd = chroot.run_cmd
        chroot.activate()

    binfmt_ensure_mounted(chroot)

    # Build registration string
    # https://en.wikipedia.org/wiki/Binfmt_misc
    # :name:type:offset:magic:mask:interpreter:flags
    info = lines[qemu_arch]
    code = info['line']

    # Register in binfmt_misc
    logging.info(f"Registering qemu binfmt ({arch})")
    _runcmd(f'echo "{code}" > "{register_path}" 2>/dev/null')  # use path without chroot path prefix
    if not binfmt_is_registered(arch):
        logging.debug(f'binfmt line: {code}')
        raise Exception(f'Failed to register qemu-user for {arch} with binfmt_misc, {binfmt_path}/{info["name"]} not found')


def unregister(arch, chroot: Optional[Chroot] = None):
    is_arch_known(arch, True, 'unregister')
    qemu_arch = QEMU_ARCHES[arch]
    binfmt_ensure_mounted(chroot)
    binfmt_file = "/proc/sys/fs/binfmt_misc/qemu-" + qemu_arch
    if chroot:
        binfmt_file = chroot.get_path(binfmt_file)
    if not os.path.exists(binfmt_file):
        return
    logging.info(f"Unregistering qemu binfmt ({arch})")
    run_root_cmd(f"echo -1 > {binfmt_file}")
