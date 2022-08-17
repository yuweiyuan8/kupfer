import pytest

import os
from tempfile import mktemp, gettempdir as get_system_tempdir
import toml
from typing import Optional

from config import CONFIG_DEFAULTS, ConfigStateHolder, Profile


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
    confpath = configstate.runtime['config_file']
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


def compare_to_defaults(config: dict, defaults: dict = CONFIG_DEFAULTS):
    # assert sections match
    assert config.keys() == defaults.keys()
    for section, section_defaults in defaults.items():
        assert section in config
        assert isinstance(section_defaults, dict)
        # Filter out None values from defaults - they're not written unless set
        section_defaults = dict_filter_out_None(section_defaults)
        section_values_config = config[section]
        if section != 'profiles':
            assert section_values_config == section_defaults
        else:
            CURRENT_KEY = 'current'
            assert CURRENT_KEY in section_defaults.keys()
            assert section_defaults.keys() == section_values_config.keys()
            assert section_defaults[CURRENT_KEY] == section_values_config[CURRENT_KEY]
            for key in set(section_defaults.keys()) - set([CURRENT_KEY]):
                assert dict_filter_out_None(section_defaults[key]) == section_values_config[key]


def test_config_save_nonexistant(configstate_nonexistant: ConfigStateHolder):
    c = configstate_nonexistant
    confpath = c.runtime['config_file']
    assert not os.path.exists(confpath)
    c.write()
    assert confpath
    assert os.path.exists(confpath)
    with open(confpath, 'r') as f:
        text = f.read()
    assert text
    loaded = toml.loads(text)
    # sadly we can't just assert `loaded == CONFIG_DEFAULTS` due to `None` values
    compare_to_defaults(loaded)


def test_profile():
    p = None
    p = Profile()
    assert p is not None
    assert isinstance(p, dict)
