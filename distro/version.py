from enum import IntEnum
from typing import NamedTuple, Sequence, Union

# free-form python port of https://gitlab.archlinux.org/pacman/pacman/-/blob/master/lib/libalpm/version.c

Version = Union[str, int]


class VerComp(IntEnum):
    RIGHT_NEWER = -1
    EQUAL = 0
    RIGHT_OLDER = 1


class EVR(NamedTuple):
    epoch: int
    version: str
    release: int
    subrelease: int


def parseEVR(input: str) -> EVR:
    """Parse `Epoch`, `Version` and `Release` from version-string `[Epoch:]Version[-Release]`"""
    epoch = 0
    version = ''
    release = 1
    subrelease = 0

    rest = input
    if ':' in rest:
        split, rest = rest.split(':', maxsplit=1)
        if split.isdigit():
            epoch = int(split)

    version = rest
    if '-' in rest:
        version, _release = rest.rsplit('-', maxsplit=1)
        if _release.isnumeric():
            release = int(_release)
        else:
            splits = _release.split('.')
            assert len(splits) == 2
            for split in splits:
                assert split.isnumeric()
            release, subrelease = (int(i) for i in splits)

    return EVR(epoch, version, release, subrelease)


def int_compare(a: int, b: int) -> VerComp:
    if b > a:
        return VerComp.RIGHT_NEWER
    if a > b:
        return VerComp.RIGHT_OLDER
    return VerComp.EQUAL


def rpm_version_compare(a: Version, b: Version) -> VerComp:
    """return -1: `b` is newer than `a`, 0: `a == b`, +1: `a` is newer than `b`"""
    if a == b:
        return VerComp.EQUAL

    if isinstance(a, int) and isinstance(b, int):
        return int_compare(a, b)

    a = str(a)
    b = str(b)
    is_num: bool
    one = 0
    two = 0
    offset1 = 0
    offset2 = 0

    def is_valid(index: int, sequence: Sequence) -> bool:
        """checks whether `index` is in range for `sequence`"""
        return index < len(sequence)

    def valid_one():
        return is_valid(one, a)

    def valid_two():
        return is_valid(two, b)

    # loop through each version segment of `a` and `b` and compare them
    while valid_one() and valid_two():
        while valid_one() and not a[one].isalnum():
            one += 1
        while valid_two() and not b[two].isalnum():
            two += 1

        # If we ran to the end of either, we are finished with the loop
        if not (valid_one() and valid_two()):
            break

        # If the separator lengths were different, we are also finished
        if (one - offset1) != (two - offset2):
            return VerComp.RIGHT_NEWER if (one - offset1) < (two - offset2) else VerComp.RIGHT_OLDER

        offset1 = one
        offset2 = two

        # grab first completely alpha or completely numeric segment
        # leave `one` and `two` pointing to the start of the alpha or numeric
        # segment and walk `offset1` and `offset2` to end of segment
        if (a[offset1].isdigit()):
            is_num = True
            str_function = str.isdigit
        else:
            is_num = False
            str_function = str.isalpha

        while is_valid(offset1, a) and str_function(a[offset1]):
            offset1 += 1
        while is_valid(offset2, b) and str_function(b[offset2]):
            offset2 += 1

        # this cannot happen, as we previously tested to make sure that
        # the first string has a non-empty segment
        assert one != offset1

        one_cut = a[one:offset1]
        two_cut = b[two:offset2]

        # take care of the case where the two version segments are
        # different types: one numeric, the other alpha (i.e. empty)
        # numeric segments are always newer than alpha segments
        if two == offset2:
            return VerComp.RIGHT_OLDER if is_num else VerComp.RIGHT_NEWER

        if is_num:
            # throw away any leading zeros - it's a number, right?
            one_cut.lstrip('0')
            two_cut.lstrip('0')

            # whichever number has more digits wins
            len_one, len_two = len(one_cut), len(two_cut)
            if len_one != len_two:
                return VerComp.RIGHT_OLDER if len_one > len_two else VerComp.RIGHT_NEWER

        if two_cut > one_cut:
            return VerComp.RIGHT_NEWER
        if one_cut > two_cut:
            return VerComp.RIGHT_OLDER

        one = offset1
        two = offset2

    # this catches the case where all numeric and alpha segments have compared
    # identically but the segment separating characters were different
    if not valid_one() and not valid_two():
        return VerComp.EQUAL

    # the final showdown. we never want a remaining alpha string to beat an empty string.
    # the logic is a bit weird, but:
    # - if one is empty and two is not an alpha, two is newer.
    # - if one is an alpha, two is newer.
    # - otherwise one is newer.
    if a[one].isalpha() or (not valid_one() and not b[two].isalpha()):
        return VerComp.RIGHT_NEWER
    else:
        return VerComp.RIGHT_OLDER


def compare_package_versions(ver_a: str, ver_b: str) -> VerComp:
    """return -1: `b` is newer than `a`, 0: `a == b`, +1: `a` is newer than `b`"""

    parsed_a, parsed_b = parseEVR(ver_a), parseEVR(ver_b)

    for a, b in zip(parsed_a, parsed_b):
        assert isinstance(a, (str, int))
        assert isinstance(b, (str, int))
        result = rpm_version_compare(a, b)
        if result != VerComp.EQUAL:
            return result

    return VerComp.EQUAL
