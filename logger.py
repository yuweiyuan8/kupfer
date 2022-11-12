import click
import coloredlogs
import logging
import sys

from typing import Optional


def setup_logging(verbose: bool, force_colors: Optional[bool] = None, log_setup: bool = True):
    level_colors = coloredlogs.DEFAULT_LEVEL_STYLES | {'info': {'color': 'magenta', 'bright': True}, 'debug': {'color': 'blue', 'bright': True}}
    field_colors = coloredlogs.DEFAULT_FIELD_STYLES | {'asctime': {'color': 'white', 'faint': True}}
    level = logging.DEBUG if verbose else logging.INFO
    coloredlogs.install(
        stream=sys.stdout,
        fmt='%(asctime)s %(levelname)s: %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S',
        level=level,
        level_styles=level_colors,
        field_styles=field_colors,
        isatty=force_colors,
    )
    if log_setup:
        logging.debug('Logger: Logging set up.')
        if force_colors is not None:
            logging.debug(f'Logger: Force-{"en" if force_colors else "dis"}abled colors')


verbose_option = click.option(
    '-v',
    '--verbose',
    is_flag=True,
    help='Enables verbose logging',
)

color_option = click.option(
    '--force-colors/--no-colors',
    is_flag=True,
    default=None,
    help='Force enable/disable log coloring. Defaults to autodetection.',
)
