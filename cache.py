import click
import os
import logging

from config.state import config
from constants import CHROOT_PATHS
from exec.file import remove_file
from packages.cli import cmd_clean as cmd_clean_pkgbuilds
from wrapper import enforce_wrap

PATHS = list(CHROOT_PATHS.keys())


@click.group(name='cache')
def cmd_cache():
    """Clean various working directories"""


@cmd_cache.command(name='clean')
@click.option('--force', is_flag=True, default=False, help="Don't ask for any confirmation")
@click.option('-n', '--noop', is_flag=True, default=False, help="Print what would be removed but dont execute")
@click.argument('paths', nargs=-1, type=click.Choice(['all'] + PATHS), required=False)
@click.pass_context
def cmd_clean(ctx: click.Context, paths: list[str], force: bool = False, noop: bool = False):
    """Clean various working directories"""
    if unknown_paths := (set(paths) - set(PATHS + ['all'])):
        raise Exception(f"Unknown paths: {' ,'.join(unknown_paths)}")
    if 'all' in paths or (not paths and force):
        paths = PATHS.copy()

    enforce_wrap()

    clear = {path: (path in paths) for path in PATHS}
    query = not paths
    if not query and not force:
        click.confirm(f'Really clear {", ".join(paths)}?', abort=True)
    for path_name in PATHS:
        if query and not force:
            clear[path_name] = click.confirm(f'{"(Noop) " if noop else ""}Clear {path_name}?')
        if clear[path_name]:
            logging.info(f'Clearing {path_name}')
            if path_name == 'pkgbuilds':
                ctx.invoke(cmd_clean_pkgbuilds, force=force, noop=noop)
                continue
            dir = config.get_path(path_name)
            for file in os.listdir(dir):
                path = os.path.join(dir, file)
                log = logging.info if noop else logging.debug
                log(f'{"Would remove" if noop else "Removing"} "{path_name}/{file}"')
                if not noop:
                    remove_file(path, recursive=True)
