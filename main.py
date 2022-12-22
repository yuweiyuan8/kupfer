#!/usr/bin/env python3

import click
import subprocess

from traceback import format_exc, format_exception_only, format_tb
from typing import Optional

from logger import logging, setup_logging, verbose_option
from wrapper import nowrapper_option, enforce_wrap

from binfmt.cli import cmd_binfmt
from config.cli import config, config_option, cmd_config
from packages.cli import cmd_packages
from flavours.cli import cmd_flavours
from devices.cli import cmd_devices
from net.cli import cmd_net
from chroot.cli import cmd_chroot
from cache.cli import cmd_cache
from image.cli import cmd_image


@click.group()
@click.option('--error-shell', '-E', 'error_shell', is_flag=True, default=False, help='Spawn shell after error occurs')
@verbose_option
@config_option
@nowrapper_option
def cli(verbose: bool = False, config_file: Optional[str] = None, wrapper_override: Optional[bool] = None, error_shell: bool = False):
    setup_logging(verbose)
    config.runtime.verbose = verbose
    config.runtime.no_wrap = wrapper_override is False
    config.runtime.error_shell = error_shell
    config.try_load_file(config_file)
    if config.file_state.exception:
        logging.warning(f"Config file couldn't be loaded: {config.file_state.exception}")
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


cli.add_command(cmd_binfmt)
cli.add_command(cmd_cache)
cli.add_command(cmd_chroot)
cli.add_command(cmd_config)
cli.add_command(cmd_devices)
cli.add_command(cmd_flavours)
cli.add_command(cmd_image)
cli.add_command(cmd_net)
cli.add_command(cmd_packages)

if __name__ == '__main__':
    main()
