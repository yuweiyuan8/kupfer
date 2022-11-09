import logging

from copy import deepcopy
from typing import Optional

from .scheme import Profile, SparseProfile

PROFILE_DEFAULTS_DICT = {
    'parent': '',
    'device': '',
    'flavour': '',
    'pkgs_include': [],
    'pkgs_exclude': [],
    'hostname': 'kupfer',
    'username': 'kupfer',
    'password': None,
    'size_extra_mb': "0",
}
PROFILE_DEFAULTS = Profile.fromDict(PROFILE_DEFAULTS_DICT)

PROFILE_EMPTY: Profile = {key: None for key in PROFILE_DEFAULTS.keys()}  # type: ignore


def resolve_profile(
    name: str,
    sparse_profiles: dict[str, SparseProfile],
    resolved: Optional[dict[str, Profile]] = None,
    _visited=None,
) -> dict[str, Profile]:
    """
    Recursively resolves the specified profile by `name` and its parents to merge the config semantically,
    applying include and exclude overrides along the hierarchy.
    If `resolved` is passed `None`, a fresh dictionary will be created.
    `resolved` will be modified in-place during parsing and also returned.
    A sanitized `sparse_profiles` dict is assumed, no checking for unknown keys or incorrect data types is performed.
    `_visited` should not be passed by users.
    """
    if _visited is None:
        _visited = list[str]()
    if resolved is None:
        resolved = dict[str, Profile]()
    if name in _visited:
        loop = list(_visited)
        raise Exception(f'Dependency loop detected in profiles: {" -> ".join(loop+[loop[0]])}')
    if name in resolved:
        return resolved

    logging.debug(f'Resolving profile {name}')
    _visited.append(name)
    sparse = sparse_profiles[name].copy()
    full = deepcopy(sparse)
    if name != 'default' and 'parent' not in sparse:
        sparse['parent'] = 'default'
    if 'parent' in sparse and (parent_name := sparse['parent']):
        parent = resolve_profile(name=parent_name, sparse_profiles=sparse_profiles, resolved=resolved, _visited=_visited)[parent_name]
        full = parent | sparse
        # add up size_extra_mb
        if 'size_extra_mb' in sparse:
            size = sparse['size_extra_mb']
            if isinstance(size, str) and size.startswith('+'):
                full['size_extra_mb'] = int(parent.get('size_extra_mb', 0)) + int(size.lstrip('+'))
            else:
                full['size_extra_mb'] = int(sparse['size_extra_mb'])
        # join our includes with parent's
        includes = set(parent.get('pkgs_include', []) + sparse.get('pkgs_include', []))
        if 'pkgs_exclude' in sparse:
            includes -= set(sparse['pkgs_exclude'])
        full['pkgs_include'] = list(includes)

        # join our includes with parent's
        excludes = set(parent.get('pkgs_exclude', []) + sparse.get('pkgs_exclude', []))
        # our includes override parent excludes
        if 'pkgs_include' in sparse:
            excludes -= set(sparse['pkgs_include'])
        full['pkgs_exclude'] = list(excludes)

    # now init missing keys
    for key, value in PROFILE_DEFAULTS_DICT.items():
        if key not in full.keys():
            full[key] = value  # type: ignore[literal-required]
            if type(value) == list:
                full[key] = []  # type: ignore[literal-required]

    full['size_extra_mb'] = int(full['size_extra_mb'] or 0)

    resolved[name] = Profile.fromDict(full)
    return resolved
