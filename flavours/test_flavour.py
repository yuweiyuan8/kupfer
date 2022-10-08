import pytest

from .flavour import Flavour, get_flavour, get_flavours

FLAVOUR_NAME = 'phosh'


@pytest.fixture()
def flavour(name=FLAVOUR_NAME) -> Flavour:
    return get_flavour(name)


def test_get_flavour(flavour: Flavour):
    assert isinstance(flavour, Flavour)
    assert flavour.name
    assert flavour.pkgbuild


def test_parse_flavourinfo(flavour: Flavour):
    info = flavour.parse_flavourinfo()
    assert isinstance(info.rootfs_size, int)
    # rootfs_size should not be zero
    assert info.rootfs_size


def test_get_flavours():
    flavours = get_flavours()
    assert flavours
    assert FLAVOUR_NAME in flavours
