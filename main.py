#!/usr/bin/env python3

import click
import subprocess

from traceback import format_exc, format_exception_only, format_tb
from typing import Optional

from logger import logging, setup_logging, verbose_option
from wrapper import nowrapper_option, enforce_wrap
from config import config, config_option, cmd_config
from forwarding import cmd_forwarding
from packages.cli import cmd_packages
from devices.cli import cmd_devices
from telnet import cmd_telnet
from chroot import cmd_chroot
from cache import cmd_cache
from image import cmd_image
from boot import cmd_boot
from flash import cmd_flash
from ssh import cmd_ssh


@click.group()
@click.option('--error-shell', '-E', 'error_shell', is_flag=True, default=False, help='Spawn shell after error occurs')
@verbose_option
@config_option
@nowrapper_option
def cli(verbose: bool = False, config_file: str = None, wrapper_override: Optional[bool] = None, error_shell: bool = False):
    setup_logging(verbose)
    config.runtime.verbose = verbose
    config.runtime.no_wrap = wrapper_override is False
    config.runtime.error_shell = error_shell
    config.try_load_file(config_file)
    if wrapper_override:
        enforce_wrap()


def main():
    try:
        return cli(prog_name='kupferbootstrap')
    except Exception as ex:
        if config.runtime.verbose:
            msg = format_exc()
        else:
            tb_start = ''.join(format_tb(ex.__traceback__, limit=1)).strip('\n')
            tb_end = ''.join(format_tb(ex.__traceback__, limit=-1)).strip('\n')
            short_tb = [
                'Traceback (most recent call last):',
                tb_start,
                '[...]',
                tb_end,
                format_exception_only(ex)[-1],  # type: ignore[arg-type]
            ]
            msg = '\n'.join(short_tb)
        logging.fatal('\n' + msg)
        if config.runtime.error_shell:
            logging.info('Starting error shell. Type exit to quit.')
            subprocess.call('/bin/bash')
        exit(1)


cli.add_command(cmd_boot)
cli.add_command(cmd_cache)
cli.add_command(cmd_chroot)
cli.add_command(cmd_config)
cli.add_command(cmd_devices)
cli.add_command(cmd_flash)
cli.add_command(cmd_forwarding)
cli.add_command(cmd_image)
cli.add_command(cmd_packages)
cli.add_command(cmd_ssh)
cli.add_command(cmd_telnet)

if __name__ == '__main__':
    main()
