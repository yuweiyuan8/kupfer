import pytest

import os
from tempfile import mktemp
from typing import Optional

from config import ConfigStateHolder, Profile


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


def validate_ConfigStateHolder(c: ConfigStateHolder, should_load: Optional[bool] = None):
    assert isinstance(c, ConfigStateHolder)
    if should_load is not None:
        assert c.file_state.load_finished is True
        assert c.is_loaded() == should_load
    assert c.file


@pytest.mark.parametrize('path_fixture,should_load', [('conf_filename', False), ('empty_config', True)])
def test_loadstate_is_loaded(path_fixture: str, should_load: bool, request: pytest.FixtureRequest):
    path = request.getfixturevalue(path_fixture)
    assert os.path.exists(path) == should_load
    c = ConfigStateHolder(path)
    validate_ConfigStateHolder(c, should_load)
    assert c.file_state.load_finished is True
    assert (c.file_state.exception is None) == should_load
    assert c.is_loaded() == should_load


def test_config_empty(empty_config: str):
    c = ConfigStateHolder(empty_config)
    validate_ConfigStateHolder(c, True)


def test_config_nonexistant(conf_filename):
    assert not os.path.exists(conf_filename)
    c = ConfigStateHolder(conf_filename)
    validate_ConfigStateHolder(c, should_load=False)


def test_profile():
    p = None
    p = Profile()
    assert p is not None
    assert isinstance(p, dict)
