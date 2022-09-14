import click
import logging
import os

from copy import deepcopy
from typing import Any, Iterable, Optional, Union

from .scheme import Profile
from .profile import PROFILE_EMPTY, PROFILE_DEFAULTS
from .state import ConfigStateHolder, CONFIG_DEFAULTS, CONFIG_SECTIONS, merge_configs


def list_to_comma_str(str_list: list[str], default='') -> str:
    if str_list is None:
        return default
    return ','.join(str_list)


def comma_str_to_list(s: str, default=None) -> list[str]:
    if not s:
        return default
    return [a for a in s.split(',') if a]


def prompt_config(
    text: str,
    default: Any,
    field_type: Union[type, click.Choice] = str,
    bold: bool = True,
    echo_changes: bool = True,
    show_choices: bool = False,
) -> tuple[Any, bool]:
    """
    prompts for a new value for a config key. returns the result and a boolean that indicates
    whether the result is different, considering empty strings and None equal to each other.
    """

    original_default = default

    def true_or_zero(to_check) -> bool:
        """returns true if the value is truthy or int(0)"""
        zero = 0  # compiler complains about 'is with literal' otherwise
        return to_check or to_check is zero  # can't do == due to boolean<->int casting

    if type(None) == field_type:
        field_type = str

    if field_type == dict:
        raise Exception('Dictionaries not supported by config_prompt, this is likely a bug in kupferbootstrap')
    elif field_type == list:
        default = list_to_comma_str(default)
        value_conv = comma_str_to_list
    else:
        value_conv = None
        default = '' if default is None else default

    if bold:
        text = click.style(text, bold=True)

    result = click.prompt(
        text,
        type=field_type,  # type: ignore
        default=default,
        value_proc=value_conv,
        show_default=True,
        show_choices=show_choices,
    )  # type: ignore
    changed = result != (original_default if field_type == list else default) and (true_or_zero(default) or true_or_zero(result))
    if changed and echo_changes:
        print(f'value changed: "{text}" = "{result}"')
    return result, changed


def prompt_profile(
    name: str,
    create: bool = True,
    defaults: Union[Profile, dict] = {},
    no_parse: bool = True,
) -> tuple[Profile, bool]:
    """Prompts the user for every field in `defaults`. Set values to None for an empty profile."""
    PARSEABLE_FIELDS = ['device', 'flavour']
    profile: Any = PROFILE_EMPTY | defaults
    if name == 'current':
        raise Exception("profile name 'current' not allowed")
    # don't use get_profile() here because we need the sparse profile
    if name in config.file.profiles:
        profile |= config.file.profiles[name]
    elif create:
        logging.info(f"Profile {name} doesn't exist yet, creating new profile.")
    else:
        raise Exception(f'Unknown profile "{name}"')
    logging.info(f'Configuring profile "{name}"')
    changed = False
    if not (no_parse or os.path.exists(os.path.join(config.get_path('pkgbuilds'), 'device'))):
        logging.warning("PKGBUILDS NOT INITIALISED:\n"
                        "Usually we'd present you with detailed lists of choices for devices and flavours in this dialogue,\n"
                        "but your pkgbuilds.git seem to not have been cloned yet.\n\n"
                        "You can:\n1. complete the dialogue with default values for now\n"
                        "2. run `kupferbootstrap packages update` afterwards\n"
                        f"3. then get back to this dialogue by running `kupferbootstrap config profile init {name}`\n\n"
                        "You can also use `kupferbootstrap packages flavours` and `kupferbootstrap packages devices` to list them.")
        no_parse = True
    for key, current in profile.items():
        current = profile[key]
        text = f'{name}.{key}'
        if not no_parse and key in PARSEABLE_FIELDS:
            parse_prompt = None
            if key == 'device':
                parse_prompt = prompt_profile_device
            elif key == 'flavour':
                parse_prompt = prompt_profile_flavour
            else:
                raise Exception(f'config: Unhandled parseable field {key}, this is a bug in kupferbootstrap.')
            result, _changed = parse_prompt(current, name)  # type: ignore
        else:
            result, _changed = prompt_config(text=text, default=current, field_type=type(PROFILE_DEFAULTS[key]))  # type: ignore
        if _changed:
            profile[key] = result
            changed = True
    return profile, changed


def prompt_choice(current: Optional[Any], key: str, choices: Iterable[Any], allow_none: bool = True, show_choices: bool = False) -> tuple[Any, bool]:
    choices = list(choices) + ([''] if allow_none else [])
    res, _ = prompt_config(text=key, default=current, field_type=click.Choice(choices), show_choices=show_choices)
    if allow_none and res == '':
        res = None
    return res, res == current


def prompt_profile_device(current: Optional[str], profile_name: str) -> tuple[str, bool]:
    from packages.device import get_devices
    devices = get_devices()
    print(click.style("Pick your device!\nThese are the available devices:", bold=True))
    for dev in sorted(devices.keys()):
        print(devices[dev])
    return prompt_choice(current, f'profiles.{profile_name}.device', devices.keys())


def prompt_profile_flavour(current: Optional[str], profile_name: str) -> tuple[str, bool]:
    from packages.flavour import get_flavours
    flavours = get_flavours()
    print(click.style("Pick your flavour!\nThese are the available flavours:", bold=True))
    for f in sorted(flavours.keys()):
        print(flavours[f])
    return prompt_choice(current, f'profiles.{profile_name}.flavour', flavours.keys())


def config_dot_name_get(name: str, config: dict[str, Any], prefix: str = '') -> Any:
    if not isinstance(config, dict):
        raise Exception(f"Couldn't resolve config name: passed config is not a dict: {repr(config)}")
    split_name = name.split('.')
    name = split_name[0]
    if name not in config:
        raise Exception(f"Couldn't resolve config name: key {prefix + name} not found")
    value = config[name]
    if len(split_name) == 1:
        return value
    else:
        rest_name = '.'.join(split_name[1:])
        return config_dot_name_get(name=rest_name, config=value, prefix=prefix + name + '.')


def config_dot_name_set(name: str, value: Any, config: dict[str, Any]):
    split_name = name.split('.')
    if len(split_name) > 1:
        config = config_dot_name_get('.'.join(split_name[:-1]), config)
    config[split_name[-1]] = value


def prompt_for_save(retry_ctx: Optional[click.Context] = None):
    """
    Prompt whether to save the config file. If no is answered, `False` is returned.

    If `retry_ctx` is passed, the context's command will be reexecuted with the same arguments if the user chooses to retry.
    False will still be returned as the retry is expected to either save, perform another retry or arbort.
    """
    if click.confirm(f'Do you want to save your changes to {config.runtime.config_file}?', default=True):
        return True
    if retry_ctx:
        if click.confirm('Retry? ("n" to quit without saving)', default=True):
            retry_ctx.forward(retry_ctx.command)
    return False


config: ConfigStateHolder = ConfigStateHolder(file_conf_base=CONFIG_DEFAULTS)

config_option = click.option(
    '-C',
    '--config',
    'config_file',
    help='Override path to config file',
)


@click.group(name='config')
def cmd_config():
    """Manage the configuration and -profiles"""


noninteractive_flag = click.option('-N', '--non-interactive', is_flag=True)
noop_flag = click.option('--noop', '-n', help="Don't write changes to file", is_flag=True)
noparse_flag = click.option('--no-parse', help="Don't search PKGBUILDs for devices and flavours", is_flag=True)


@cmd_config.command(name='init')
@noninteractive_flag
@noop_flag
@noparse_flag
@click.option(
    '--sections',
    '-s',
    multiple=True,
    type=click.Choice(CONFIG_SECTIONS),
    default=CONFIG_SECTIONS,
    show_choices=True,
)
@click.pass_context
def cmd_config_init(
    ctx,
    sections: list[str] = CONFIG_SECTIONS,
    non_interactive: bool = False,
    noop: bool = False,
    no_parse: bool = False,
):
    """Initialize the config file"""
    if not non_interactive:
        results: dict[str, dict] = {}
        for section in sections:
            if section not in CONFIG_SECTIONS:
                raise Exception(f'Unknown section: {section}')
            if section == 'profiles':
                continue

            results[section] = {}
            for key, current in config.file[section].items():
                text = f'{section}.{key}'
                result, changed = prompt_config(text=text, default=current, field_type=type(CONFIG_DEFAULTS[section][key]))
                if changed:
                    results[section][key] = result

        config.update(results)
        if 'profiles' in sections:
            current_profile = 'default' if 'current' not in config.file.profiles else config.file.profiles.current
            new_current, _ = prompt_config('profiles.current', default=current_profile, field_type=str)
            profile, changed = prompt_profile(new_current, create=True, no_parse=no_parse)
            config.update_profile(new_current, profile)
        if not noop:
            if not prompt_for_save(ctx):
                return

    if not noop:
        config.write()
    else:
        logging.info(f'--noop passed, not writing to {config.runtime.config_file}!')


@cmd_config.command(name='set')
@noninteractive_flag
@noop_flag
@noparse_flag
@click.argument('key_vals', nargs=-1)
@click.pass_context
def cmd_config_set(ctx, key_vals: list[str], non_interactive: bool = False, noop: bool = False, no_parse: bool = False):
    """
    Set config entries. Pass entries as `key=value` pairs, with keys as dot-separated identifiers,
    like `build.clean_mode=false` or alternatively just keys to get prompted if run interactively.
    """
    config.enforce_config_loaded()
    config_copy = deepcopy(config.file)
    for pair in key_vals:
        split_pair = pair.split('=')
        if len(split_pair) == 2:
            key: str = split_pair[0]
            value: Any = split_pair[1]
            value_type = type(config_dot_name_get(key, CONFIG_DEFAULTS))
            if value_type != list:
                value = click.types.convert_type(value_type)(value)
            else:
                value = comma_str_to_list(value, default=[])
        elif len(split_pair) == 1 and not non_interactive:
            key = split_pair[0]
            value_type = type(config_dot_name_get(key, CONFIG_DEFAULTS))
            current = config_dot_name_get(key, config.file)
            value, _ = prompt_config(text=key, default=current, field_type=value_type, echo_changes=False)
        else:
            raise Exception(f'Invalid key=value pair "{pair}"')
        print('%s = %s' % (key, value))
        config_dot_name_set(key, value, config_copy)
        if merge_configs(config_copy, warn_missing_defaultprofile=False) != config_copy:
            raise Exception('Config "{key}" = "{value}" failed to evaluate')
    if not noop:
        if not non_interactive and not prompt_for_save(ctx):
            return
        config.update(config_copy)
        config.write()


@cmd_config.command(name='get')
@click.argument('keys', nargs=-1)
def cmd_config_get(keys: list[str]):
    """Get config entries.
    Get entries for keys passed as dot-separated identifiers, like `build.clean_mode`"""
    if len(keys) == 1:
        print(config_dot_name_get(keys[0], config.file))
        return
    for key in keys:
        print('%s = %s' % (key, config_dot_name_get(key, config.file)))


@cmd_config.group(name='profile')
def cmd_profile():
    """Manage config profiles"""


@cmd_profile.command(name='init')
@noninteractive_flag
@noop_flag
@noparse_flag
@click.argument('name', required=False)
@click.pass_context
def cmd_profile_init(ctx, name: Optional[str] = None, non_interactive: bool = False, noop: bool = False, no_parse: bool = False):
    """Create or edit a profile"""
    profile = deepcopy(PROFILE_EMPTY)
    if name == 'current':
        raise Exception("profile name 'current' not allowed")
    name = name or config.file.profiles.current
    if name in config.file.profiles:
        profile |= config.file.profiles[name]

    if not non_interactive:
        profile, _changed = prompt_profile(name, create=True, no_parse=no_parse)

    config.update_profile(name, profile)
    if not noop:
        if not prompt_for_save(ctx):
            return
        config.write()
    else:
        logging.info(f'--noop passed, not writing to {config.runtime.config_file}!')
