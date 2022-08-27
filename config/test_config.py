import pytest

import os
import pickle
import toml

from tempfile import mktemp, gettempdir as get_system_tempdir
from typing import Optional

from config.profile import PROFILE_DEFAULTS
from config.scheme import Config, Profile
from config.state import CONFIG_DEFAULTS, ConfigStateHolder


def get_filename():
    return mktemp() + '_pytest.toml'


@pytest.fixture
def conf_filename():
    f = get_filename()
    yield f


@pytest.fixture
def empty_config():
    f = get_filename()
    with open(f, 'w') as fd:
        fd.write('')
    yield f
    os.unlink(f)


@pytest.fixture
def configstate_nonexistant(conf_filename):
    return ConfigStateHolder(conf_filename)


@pytest.fixture
def configstate_emptyfile(empty_config):
    return ConfigStateHolder(empty_config)


def validate_ConfigStateHolder(c: ConfigStateHolder, should_load: Optional[bool] = None):
    assert isinstance(c, ConfigStateHolder)
    if should_load is not None:
        assert c.file_state.load_finished is True
        assert c.is_loaded() == should_load
    assert c.file


@pytest.mark.parametrize('conf_fixture,exists', [('configstate_emptyfile', True), ('configstate_nonexistant', False)])
def test_fixture_configstate(conf_fixture: str, exists: bool, request):
    configstate = request.getfixturevalue(conf_fixture)
    assert 'config_file' in configstate.runtime
    confpath = configstate.runtime.config_file
    assert isinstance(confpath, str)
    assert confpath
    assert exists == os.path.exists(confpath)
    assert confpath.startswith(get_system_tempdir())


def test_config_load_emptyfile(configstate_emptyfile):
    validate_ConfigStateHolder(configstate_emptyfile, should_load=True)


def test_config_load_nonexistant(configstate_nonexistant):
    validate_ConfigStateHolder(configstate_nonexistant, should_load=False)


@pytest.mark.parametrize('path_fixture,should_load', [('conf_filename', False), ('empty_config', True)])
def test_loadstate_is_loaded(path_fixture: str, should_load: bool, request: pytest.FixtureRequest):
    path = request.getfixturevalue(path_fixture)
    assert os.path.exists(path) == should_load
    c = ConfigStateHolder(path)
    validate_ConfigStateHolder(c, should_load)
    assert c.file_state.load_finished is True
    assert (c.file_state.exception is None) == should_load
    assert c.is_loaded() == should_load


@pytest.mark.parametrize('conf_fixture', ['configstate_emptyfile', 'configstate_nonexistant'])
def test_config_fills_defaults(conf_fixture: str, request):
    c = request.getfixturevalue(conf_fixture)
    assert c.file == CONFIG_DEFAULTS


def dict_filter_out_None(d: dict):
    return {k: v for k, v in d.items() if v is not None}


def compare_to_defaults(config: dict, defaults: dict = CONFIG_DEFAULTS, filter_None_from_defaults: Optional[bool] = None):
    if filter_None_from_defaults is None:
        filter_None_from_defaults = not isinstance(config, Config)
    # assert sections match
    assert config.keys() == defaults.keys()
    for section, section_defaults in defaults.items():
        assert section in config
        assert isinstance(section_defaults, dict)
        # Filter out None values from defaults - they're not written unless set
        if filter_None_from_defaults:
            section_defaults = dict_filter_out_None(section_defaults)
        section_values_config = config[section]
        if section != 'profiles':
            assert section_values_config == section_defaults
        else:
            CURRENT_KEY = 'current'
            assert CURRENT_KEY in section_defaults.keys()
            assert section_defaults.keys() == section_values_config.keys()
            assert section_defaults[CURRENT_KEY] == section_values_config[CURRENT_KEY]
            for profile_name, profile in section_defaults.items():
                if profile_name == CURRENT_KEY:
                    continue  # not a profile
                if filter_None_from_defaults:
                    profile = dict_filter_out_None(profile)
                assert profile == section_values_config[profile_name]


def load_toml_file(path) -> dict:
    with open(path, 'r') as f:
        text = f.read()
    assert text
    return toml.loads(text)


def get_path_from_stateholder(c: ConfigStateHolder):
    return c.runtime.config_file


def test_config_save_nonexistant(configstate_nonexistant: ConfigStateHolder):
    c = configstate_nonexistant
    confpath = c.runtime.config_file
    assert confpath
    assert not os.path.exists(confpath)
    c.write()
    assert confpath
    assert os.path.exists(confpath)
    loaded = load_toml_file(confpath)
    assert loaded
    # sadly we can't just assert `loaded == CONFIG_DEFAULTS` due to `None` values
    compare_to_defaults(loaded)


def test_config_save_modified(configstate_emptyfile: ConfigStateHolder):
    c = configstate_emptyfile
    WRAPPER_KEY = 'wrapper'
    TYPE_KEY = 'type'
    assert WRAPPER_KEY in c.file
    assert TYPE_KEY in c.file[WRAPPER_KEY]
    wrapper_section = CONFIG_DEFAULTS[WRAPPER_KEY] | {TYPE_KEY: 'none'}
    c.file[WRAPPER_KEY] |= wrapper_section
    c.write()
    defaults_modified = CONFIG_DEFAULTS | {WRAPPER_KEY: wrapper_section}
    compare_to_defaults(load_toml_file(get_path_from_stateholder(c)), defaults_modified)


def test_config_scheme_defaults():
    c = Config.fromDict(CONFIG_DEFAULTS, validate=True, allow_incomplete=False)
    assert c
    compare_to_defaults(c)


def test_config_scheme_modified():
    modifications = {'wrapper': {'type': 'none'}, 'build': {'crossdirect': False}}
    assert set(modifications.keys()).issubset(CONFIG_DEFAULTS.keys())
    d = {section_name: (section | modifications.get(section_name, {})) for section_name, section in CONFIG_DEFAULTS.items()}
    c = Config.fromDict(d, validate=True, allow_incomplete=False)
    assert c
    assert c.build.crossdirect is False
    assert c.wrapper.type == 'none'


def test_configstate_profile_pickle():
    c = ConfigStateHolder()
    assert c.file.wrapper
    assert c.file.profiles
    # add new profile to check it doesn't error out due to unknown keys
    c.file.profiles['graphical'] = {'username': 'kupfer123', 'hostname': 'test123'}
    p = pickle.dumps(c)
    unpickled = pickle.loads(p)
    assert c.file == unpickled.file


def test_profile():
    p = None
    p = Profile.fromDict(PROFILE_DEFAULTS)
    assert p is not None
    assert isinstance(p, Profile)


def test_get_profile():
    c = ConfigStateHolder()
    d = {'username': 'kupfer123', 'hostname': 'test123'}
    c.file.profiles['testprofile'] = d
    p = c.get_profile('testprofile')
    assert p
    assert isinstance(p, Profile)


def test_get_profile_from_disk(configstate_emptyfile):
    profile_name = 'testprofile'
    device = 'sdm845-oneplus-enchilada'
    c = configstate_emptyfile
    c.file.profiles.default.device = device
    d = {'parent': 'default', 'username': 'kupfer123', 'hostname': 'test123'}
    c.file.profiles[profile_name] = d
    filepath = c.runtime.config_file
    assert filepath
    c.write()
    del c
    c = ConfigStateHolder(filepath)
    c.try_load_file(filepath)
    c.enforce_config_loaded()
    p: Profile = c.get_profile(profile_name)
    assert isinstance(p, Profile)
    assert 'device' in p
    assert p.device == device
