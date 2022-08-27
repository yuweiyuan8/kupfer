import click
import logging

from typing import Sequence, Union

from config import config
from constants import Arch
from utils import programs_available
from .docker import DockerWrapper
from .wrapper import Wrapper

wrapper_impls: dict[str, Wrapper] = {
    'docker': DockerWrapper(),
}


def get_wrapper_type(wrapper_type: str = None):
    return wrapper_type or config.file['wrapper']['type']


def get_wrapper_impl(wrapper_type: str = None) -> Wrapper:
    return wrapper_impls[get_wrapper_type(wrapper_type)]


def wrap(wrapper_type: str = None):
    wrapper_type = get_wrapper_type(wrapper_type)
    if wrapper_type != 'none':
        get_wrapper_impl(wrapper_type).wrap()


def is_wrapped(wrapper_type: str = None):
    return get_wrapper_impl(wrapper_type).is_wrapped()


def enforce_wrap(no_wrapper=False):
    wrapper_type = get_wrapper_type()
    if wrapper_type != 'none' and not is_wrapped(wrapper_type) and not config.runtime['no_wrap'] and not no_wrapper:
        logging.info(f'Wrapping in {wrapper_type}')
        wrap()


def check_programs_wrap(programs: Union[str, Sequence[str]]):
    if not programs_available(programs):
        logging.debug(f"Wrapping because one of {[programs] if isinstance(programs, str) else programs} isn't available.")
        enforce_wrap()


def wrap_if_foreign_arch(arch: Arch):
    if arch != config.runtime.arch:
        enforce_wrap()


nowrapper_option = click.option(
    '-w/-W',
    '--force-wrapper/--no-wrapper',
    'wrapper_override',
    is_flag=True,
    default=None,
    help='Force or disable the docker wrapper. Defaults to autodetection.',
)
