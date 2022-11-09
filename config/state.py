import appdirs
import logging
import os
import toml
from copy import deepcopy
from typing import Mapping, Optional

from constants import DEFAULT_PACKAGE_BRANCH

from .scheme import Config, ConfigLoadState, DataClass, Profile, RuntimeConfiguration
from .profile import PROFILE_DEFAULTS, PROFILE_DEFAULTS_DICT, resolve_profile

CONFIG_DIR = appdirs.user_config_dir('kupfer')
CACHE_DIR = appdirs.user_cache_dir('kupfer')
CONFIG_DEFAULT_PATH = os.path.join(CONFIG_DIR, 'kupferbootstrap.toml')

CONFIG_DEFAULTS_DICT = {
    'wrapper': {
        'type': 'docker',
    },
    'build': {
        'ccache': True,
        'clean_mode': True,
        'crosscompile': True,
        'crossdirect': True,
        'threads': 0,
    },
    'pkgbuilds': {
        'git_repo': 'https://gitlab.com/kupfer/packages/pkgbuilds.git',
        'git_branch': DEFAULT_PACKAGE_BRANCH,
    },
    'pacman': {
        'parallel_downloads': 4,
        'check_space': False,  # TODO: investigate why True causes issues
        'repo_branch': DEFAULT_PACKAGE_BRANCH,
    },
    'paths': {
        'cache_dir': CACHE_DIR,
        'chroots': os.path.join('%cache_dir%', 'chroots'),
        'pacman': os.path.join('%cache_dir%', 'pacman'),
        'packages': os.path.join('%cache_dir%', 'packages'),
        'pkgbuilds': os.path.join('%cache_dir%', 'pkgbuilds'),
        'jumpdrive': os.path.join('%cache_dir%', 'jumpdrive'),
        'images': os.path.join('%cache_dir%', 'images'),
        'ccache': os.path.join('%cache_dir%', 'ccache'),
        'rust': os.path.join('%cache_dir%', 'rust'),
    },
    'profiles': {
        'current': 'default',
        'default': deepcopy(PROFILE_DEFAULTS_DICT),
    },
}
CONFIG_DEFAULTS: Config = Config.fromDict(CONFIG_DEFAULTS_DICT)
CONFIG_SECTIONS = list(CONFIG_DEFAULTS.keys())

CONFIG_RUNTIME_DEFAULTS: RuntimeConfiguration = RuntimeConfiguration.fromDict({
    'verbose': False,
    'no_wrap': False,
    'error_shell': False,
    'config_file': None,
    'script_source_dir': None,
    'arch': None,
    'uid': None,
})


def resolve_path_template(path_template: str, paths: dict[str, str]) -> str:
    terminator = '%'  # i'll be back
    result = path_template
    for path_name, path in paths.items():
        result = result.replace(terminator + path_name + terminator, path)
    return result


def sanitize_config(conf: dict[str, dict], warn_missing_defaultprofile=True) -> dict[str, dict]:
    """checks the input config dict for unknown keys and returns only the known parts"""
    return merge_configs(conf_new=conf, conf_base={}, warn_missing_defaultprofile=warn_missing_defaultprofile)


def merge_configs(conf_new: Mapping[str, dict], conf_base={}, warn_missing_defaultprofile=True) -> dict[str, dict]:
    """
    Returns `conf_new` semantically merged into `conf_base`, after validating
    `conf_new` keys against `CONFIG_DEFAULTS` and `PROFILE_DEFAULTS`.
    Pass `conf_base={}` to get a sanitized version of `conf_new`.
    NOTE: `conf_base` is NOT checked for invalid keys. Sanitize beforehand.
    """
    parsed = deepcopy(dict(conf_base))

    for outer_name, outer_conf in deepcopy(conf_new).items():
        # only handle known config sections
        if outer_name not in CONFIG_SECTIONS:
            logging.warning(f'Skipped unknown config section "{outer_name}"')
            continue
        logging.debug(f'Parsing config section "{outer_name}"')
        # check if outer_conf is a dict
        if not (isinstance(outer_conf, (dict, DataClass))):
            parsed[outer_name] = outer_conf
        else:
            # init section
            if outer_name not in parsed:
                parsed[outer_name] = {}

            # profiles need special handling:
            # 1. profile names are unknown keys by definition, but we want 'default' to exist
            # 2. A profile's subkeys must be compared against PROFILE_DEFAULTS.keys()
            if outer_name == 'profiles':
                if warn_missing_defaultprofile and 'default' not in outer_conf.keys():
                    logging.warning('Default profile is not defined in config file')

                update = dict[str, dict]()
                for profile_name, profile_conf in outer_conf.items():
                    if not isinstance(profile_conf, (dict, Profile)):
                        if profile_name == 'current':
                            parsed[outer_name][profile_name] = profile_conf
                        else:
                            logging.warning(f'Skipped key "{profile_name}" in profile section: only subsections and "current" allowed')
                        continue

                    #  init profile
                    if profile_name in parsed[outer_name]:
                        profile = parsed[outer_name][profile_name]
                    else:
                        profile = {}

                    for key, val in profile_conf.items():
                        if key not in PROFILE_DEFAULTS:
                            logging.warning(f'Skipped unknown config item "{key}" in profile "{profile_name}"')
                            continue
                        profile[key] = val
                    update |= {profile_name: profile}
                parsed[outer_name].update(update)

            else:
                # handle generic inner config dict
                for inner_name, inner_conf in outer_conf.items():
                    if inner_name not in CONFIG_DEFAULTS[outer_name].keys():
                        logging.warning(f'Skipped unknown config item "{inner_name}" in section "{outer_name}"')
                        continue
                    parsed[outer_name][inner_name] = inner_conf

    return parsed


def dump_toml(conf) -> str:
    return toml.dumps(conf)


def dump_file(file_path: str, config: dict, file_mode: int = 0o600):

    def _opener(path, flags):
        return os.open(path, flags, file_mode)

    conf_dir = os.path.dirname(file_path)
    if not os.path.exists(conf_dir):
        os.makedirs(conf_dir)
    old_umask = os.umask(0)
    with open(file_path, 'w', opener=_opener) as f:
        f.write(dump_toml(conf=config))
    os.umask(old_umask)


def parse_file(config_file: str, base: dict = CONFIG_DEFAULTS) -> dict:
    """
    Parse the toml contents of `config_file`, validating keys against `CONFIG_DEFAULTS`.
    The parsed results are semantically merged into `base` before returning.
    `base` itself is NOT checked for invalid keys.
    """
    _conf_file = config_file if config_file is not None else CONFIG_DEFAULT_PATH
    logging.debug(f'Trying to load config file: {_conf_file}')
    loaded_conf = toml.load(_conf_file)
    return merge_configs(conf_new=loaded_conf, conf_base=base)


class ConfigLoadException(Exception):
    inner = None

    def __init__(self, extra_msg='', inner_exception: Optional[Exception] = None):
        msg: list[str] = ['Config load failed!']
        if extra_msg:
            msg.append(extra_msg)
        if inner_exception:
            self.inner = inner_exception
            msg.append(str(inner_exception))
        super().__init__(self, ' '.join(msg))


class ConfigStateHolder:
    # config options that are persisted to file
    file: Config
    # runtime config not persisted anywhere
    runtime: RuntimeConfiguration
    file_state: ConfigLoadState
    _profile_cache: Optional[dict[str, Profile]]

    def __init__(self, file_conf_path: Optional[str] = None, runtime_conf={}, file_conf_base: dict = {}):
        """init a stateholder, optionally loading `file_conf_path`"""
        self.file = Config.fromDict(merge_configs(conf_new=file_conf_base, conf_base=CONFIG_DEFAULTS))
        self.file_state = ConfigLoadState()
        self.runtime = RuntimeConfiguration.fromDict(CONFIG_RUNTIME_DEFAULTS | runtime_conf)
        self.runtime.arch = os.uname().machine
        self.runtime.script_source_dir = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
        self.runtime.uid = os.getuid()
        self._profile_cache = {}
        if file_conf_path:
            self.try_load_file(file_conf_path)

    def try_load_file(self, config_file=None, base=CONFIG_DEFAULTS_DICT):
        config_file = config_file or CONFIG_DEFAULT_PATH
        self.runtime.config_file = config_file
        self._profile_cache = None
        try:
            self.file = Config.fromDict(parse_file(config_file=config_file, base=base), validate=True)
        except Exception as ex:
            self.file_state.exception = ex
        self.file_state.load_finished = True

    def is_loaded(self) -> bool:
        "returns True if a file was **sucessfully** loaded"
        return self.file_state.load_finished and self.file_state.exception is None

    def enforce_config_loaded(self):
        if not self.file_state.load_finished:
            m = "Config file wasn't even parsed yet. This is probably a bug in kupferbootstrap :O"
            raise ConfigLoadException(Exception(m))
        ex = self.file_state.exception
        if ex:
            if type(ex) == FileNotFoundError:
                ex = Exception("Config file doesn't exist. Try running `kupferbootstrap config init` first?")
            raise ex

    def get_profile(self, name: Optional[str] = None) -> Profile:
        name = name or self.file.profiles.current
        self._profile_cache = resolve_profile(name=name, sparse_profiles=self.file.profiles, resolved=self._profile_cache)
        return self._profile_cache[name]

    def _enforce_profile_field(self, field: str, profile_name: Optional[str] = None, hint_or_set_arch: bool = False) -> Profile:
        # TODO: device
        profile_name = profile_name if profile_name is not None else self.file.profiles.current
        arch_hint = ''
        if not hint_or_set_arch:
            self.enforce_config_loaded()
        else:
            arch_hint = (' or specifiy the target architecture by passing `--arch` to the current command,\n'
                         'e.g. `kupferbootstrap packages build --arch x86_64`')
            if not self.is_loaded():
                if not self.file_state.exception:
                    raise Exception(f'Error enforcing config profile {field}: config hadn\'t even been loaded yet.\n'
                                    'This is a bug in kupferbootstrap!')
                raise Exception(f"Profile {field} couldn't be resolved because the config file couldn't be loaded.\n"
                                "If the config doesn't exist, try running `kupferbootstrap config init`.\n"
                                f"Error: {self.file_state.exception}")
        if profile_name and profile_name not in self.file.profiles:
            raise Exception(f'Unknown profile "{profile_name}". Please run `kupferbootstrap config profile init`{arch_hint}')
        profile = self.get_profile(profile_name)
        if field not in profile or not profile[field]:
            m = (f'Profile "{profile_name}" has no {field.upper()} configured.\n'
                 f'Please run `kupferbootstrap config profile init {field}`{arch_hint}')
            raise Exception(m)
        return profile

    def enforce_profile_device_set(self, **kwargs) -> Profile:
        return self._enforce_profile_field(field='device', **kwargs)

    def enforce_profile_flavour_set(self, **kwargs) -> Profile:
        return self._enforce_profile_field(field='flavour', **kwargs)

    def get_path(self, path_name: str) -> str:
        paths = self.file.paths
        return resolve_path_template(paths[path_name], paths)

    def get_package_dir(self, arch: str):
        return os.path.join(self.get_path('packages'), arch)

    def dump(self) -> str:
        """dump toml representation of `self.file`"""
        return dump_toml(self.file)

    def write(self, path=None):
        """write toml representation of `self.file` to `path`"""
        if path is None:
            path = self.runtime.config_file
        assert path
        os.makedirs(os.path.dirname(path), exist_ok=True)
        new = not os.path.exists(path)
        dump_file(path, self.file)
        logging.info(f'{"Created" if new else "Written changes to"} config file at {path}')

    def invalidate_profile_cache(self):
        """Clear the profile cache (usually after modification)"""
        self._profile_cache = None

    def update(self, config_fragment: dict[str, dict], warn_missing_defaultprofile: bool = True) -> bool:
        """Update `self.file` with `config_fragment`. Returns `True` if the config was changed"""
        merged = merge_configs(config_fragment, conf_base=self.file, warn_missing_defaultprofile=warn_missing_defaultprofile)
        changed = self.file.toDict() != merged
        self.file.update(merged)
        if changed and 'profiles' in config_fragment and self.file.profiles.toDict() != config_fragment['profiles']:
            self.invalidate_profile_cache()
        return changed

    def update_profile(self, name: str, profile: Profile, merge: bool = False, create: bool = True, prune: bool = True):
        new = {}
        if name not in self.file.profiles:
            if not create:
                raise Exception(f'Unknown profile: {name}')
        else:
            if merge:
                new = deepcopy(self.file.profiles[name])

        logging.debug(f'new: {new}')
        logging.debug(f'profile: {profile}')
        new |= profile

        if prune:
            new = {key: val for key, val in new.items() if val is not None}
        self.file.profiles[name] = new
        self.invalidate_profile_cache()


config: ConfigStateHolder = ConfigStateHolder(file_conf_base=CONFIG_DEFAULTS)
