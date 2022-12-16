import logging
import multiprocessing
import os
import shutil
import subprocess
import sys

from copy import deepcopy
from urllib.error import HTTPError
from typing import Iterable, Iterator, Optional

from binfmt import register as binfmt_register, binfmt_is_registered
from constants import REPOSITORIES, CROSSDIRECT_PKGS, QEMU_BINFMT_PKGS, GCC_HOSTSPECS, ARCHES, Arch, CHROOT_PATHS, MAKEPKG_CMD
from config.state import config
from exec.cmd import run_cmd, run_root_cmd
from exec.file import makedir, remove_file, symlink
from chroot.build import get_build_chroot, BuildChroot
from distro.distro import get_kupfer_https, get_kupfer_local
from distro.package import RemotePackage, LocalPackage
from distro.repo import LocalRepo
from progressbar import BAR_PADDING, get_levels_bar
from wrapper import check_programs_wrap, is_wrapped
from utils import ellipsize, sha256sum

from .pkgbuild import discover_pkgbuilds, filter_pkgbuilds, Pkgbase, Pkgbuild, SubPkgbuild

pacman_cmd = [
    'pacman',
    '-Syuu',
    '--noconfirm',
    '--overwrite=*',
    '--needed',
]


def get_makepkg_env(arch: Optional[Arch] = None):
    # has to be a function because calls to `config` must be done after config file was read
    threads = config.file.build.threads or multiprocessing.cpu_count()
    # env = {key: val for key, val in os.environ.items() if not key.split('_', maxsplit=1)[0] in ['CI', 'GITLAB', 'FF']}
    env = {
        'LANG': 'C',
        'CARGO_BUILD_JOBS': str(threads),
        'MAKEFLAGS': f"-j{threads}",
        'PATH': '/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin',
    }
    native = config.runtime.arch
    assert native
    if arch and arch != native:
        env |= {'QEMU_LD_PREFIX': f'/usr/{GCC_HOSTSPECS[native][arch]}'}
    return env


def init_local_repo(repo: str, arch: Arch):
    repo_dir = os.path.join(config.get_package_dir(arch), repo)
    if not os.path.exists(repo_dir):
        logging.info(f"Creating local repo {repo} ({arch})")
        makedir(repo_dir)
    for ext in ['db', 'files']:
        filename_stripped = f'{repo}.{ext}'
        filename = f'{filename_stripped}.tar.xz'
        if not os.path.exists(os.path.join(repo_dir, filename)):
            logging.info(f"Initialising local repo {f'{ext} ' if ext != 'db' else ''}db for repo {repo} ({arch})")
            result = run_cmd(
                [
                    'tar',
                    '-czf',
                    filename,
                    '-T',
                    '/dev/null',
                ],
                cwd=os.path.join(repo_dir),
            )
            assert isinstance(result, subprocess.CompletedProcess)
            if result.returncode != 0:
                raise Exception(f'Failed to create local repo {repo}')
        symlink_path = os.path.join(repo_dir, filename_stripped)
        if not os.path.islink(symlink_path):
            if os.path.exists(symlink_path):
                remove_file(symlink_path)
            symlink(filename, symlink_path)


def init_prebuilts(arch: Arch):
    """Ensure that all `constants.REPOSITORIES` inside `dir` exist"""
    prebuilts_dir = config.get_path('packages')
    makedir(prebuilts_dir)
    for repo in REPOSITORIES:
        init_local_repo(repo, arch)


def generate_dependency_chain(package_repo: dict[str, Pkgbuild], to_build: Iterable[Pkgbuild]) -> list[set[Pkgbuild]]:
    """
    This figures out all dependencies and their sub-dependencies for the selection and adds those packages to the selection.
    First the top-level packages get selected by searching the paths.
    Then their dependencies and sub-dependencies and so on get added to the selection.
    """
    visited = set[Pkgbuild]()
    visited_names = set[str]()
    dep_levels: list[set[Pkgbuild]] = [set(), set()]

    def visit(package: Pkgbuild, visited=visited, visited_names=visited_names):
        visited.add(package)
        visited_names.update(package.names())

    def join_levels(levels: list[set[Pkgbuild]]) -> dict[Pkgbuild, int]:
        result = dict[Pkgbuild, int]()
        for i, level in enumerate(levels):
            for pkg in level:
                result[pkg] = i
        return result

    def get_dependencies(package: Pkgbuild, package_repo: dict[str, Pkgbuild] = package_repo) -> Iterator[Pkgbuild]:
        for dep_name in package.depends:
            if dep_name in visited_names:
                continue
            elif dep_name in package_repo:
                dep_pkg = package_repo[dep_name]
                visit(dep_pkg)
                yield dep_pkg

    def get_recursive_dependencies(package: Pkgbuild, package_repo: dict[str, Pkgbuild] = package_repo) -> Iterator[Pkgbuild]:
        for pkg in get_dependencies(package, package_repo):
            yield pkg
            for sub_pkg in get_recursive_dependencies(pkg, package_repo):
                yield sub_pkg

    logging.debug('Generating dependency chain:')
    # init level 0
    for package in to_build:
        visit(package)
        dep_levels[0].add(package)
        logging.debug(f'Adding requested package {package.name}')
        # add dependencies of our requested builds to level 0
        for dep_pkg in get_recursive_dependencies(package):
            logging.debug(f"Adding {package.name}'s dependency {dep_pkg.name} to level 0")
            dep_levels[0].add(dep_pkg)
            visit(dep_pkg)
    """
    Starting with `level` = 0, iterate over the packages in `dep_levels[level]`:
    1. Moving packages that are dependencies of other packages up to `level`+1
    2. Adding yet unadded local dependencies of all pkgs on `level` to `level`+1
    3. increment level
    """
    level = 0
    # protect against dependency cycles
    repeat_count = 0
    _last_level: Optional[set[Pkgbuild]] = None
    while dep_levels[level]:
        level_copy = dep_levels[level].copy()
        modified = False
        logging.debug(f'Scanning dependency level {level}')
        if level > 100:
            raise Exception('Dependency chain reached 100 levels depth, this is probably a bug. Aborting!')

        for pkg in level_copy:
            pkg_done = False
            if pkg not in dep_levels[level]:
                # pkg has been moved, move on
                continue
            # move pkg to level+1 if something else depends on it
            for other_pkg in level_copy:
                if pkg == other_pkg:
                    continue
                if pkg_done:
                    break
                if not issubclass(type(other_pkg), Pkgbuild):
                    raise Exception('Not a Pkgbuild object:' + repr(other_pkg))
                for dep_name in other_pkg.depends:
                    if dep_name in pkg.names():
                        dep_levels[level].remove(pkg)
                        dep_levels[level + 1].add(pkg)
                        logging.debug(f'Moving {pkg.name} to level {level+1} because {other_pkg.name} depends on it as {dep_name}')
                        modified = True
                        pkg_done = True
                        break
            for dep_name in pkg.depends:
                if dep_name in visited_names:
                    continue
                elif dep_name in package_repo:
                    dep_pkg = package_repo[dep_name]
                    logging.debug(f"Adding {pkg.name}'s dependency {dep_name} to level {level}")
                    dep_levels[level].add(dep_pkg)
                    visit(dep_pkg)
                    modified = True

        if _last_level == dep_levels[level]:
            repeat_count += 1
        else:
            repeat_count = 0
        if repeat_count > 10:
            raise Exception(f'Probable dependency cycle detected: Level has been passed on unmodifed multiple times: #{level}: {_last_level}')
        _last_level = dep_levels[level].copy()
        if not modified:  # if the level was modified, make another pass.
            level += 1
            dep_levels.append(set[Pkgbuild]())
    # reverse level list into buildorder (deps first!), prune empty levels
    return list([lvl for lvl in dep_levels[::-1] if lvl])


def add_file_to_repo(file_path: str, repo_name: str, arch: Arch, remove_original: bool = True):
    check_programs_wrap(['repo-add'])
    repo_dir = os.path.join(config.get_package_dir(arch), repo_name)
    pacman_cache_dir = os.path.join(config.get_path('pacman'), arch)
    file_name = os.path.basename(file_path)
    target_file = os.path.join(repo_dir, file_name)

    init_local_repo(repo_name, arch)
    if file_path != target_file:
        logging.debug(f'moving {file_path} to {target_file} ({repo_dir})')
        shutil.copy(
            file_path,
            repo_dir,
        )
        if remove_original:
            remove_file(file_path)

    # clean up same name package from pacman cache
    cache_file = os.path.join(pacman_cache_dir, file_name)
    if os.path.exists(cache_file):
        logging.debug(f"Removing cached package file {cache_file}")
        remove_file(cache_file)
    cmd = [
        'repo-add',
        '--remove',
        os.path.join(
            repo_dir,
            f'{repo_name}.db.tar.xz',
        ),
        target_file,
    ]
    logging.debug(f'repo: running cmd: {cmd}')
    result = run_cmd(cmd, stderr=sys.stdout)
    assert isinstance(result, subprocess.CompletedProcess)
    if result.returncode != 0:
        raise Exception(f'Failed add package {target_file} to repo {repo_name}')
    for ext in ['db', 'files']:
        old = os.path.join(repo_dir, f'{repo_name}.{ext}.tar.xz.old')
        if os.path.exists(old):
            remove_file(old)


def strip_compression_extension(filename: str):
    for ext in ['zst', 'xz', 'gz', 'bz2']:
        if filename.endswith(f'.pkg.tar.{ext}'):
            return filename[:-(len(ext) + 1)]
    logging.debug(f"file {filename} matches no known package extension")
    return filename


def add_package_to_repo(package: Pkgbuild, arch: Arch):
    logging.info(f'Adding {package.path} to repo {package.repo}')
    pkgbuild_dir = os.path.join(config.get_path('pkgbuilds'), package.path)

    files = []
    for file in os.listdir(pkgbuild_dir):
        # Forced extension by makepkg.conf
        pkgext = '.pkg.tar'
        if pkgext not in file:
            continue
        stripped_name = strip_compression_extension(file)
        if not stripped_name.endswith(pkgext):
            continue

        repo_file = os.path.join(config.get_package_dir(arch), package.repo, file)
        files.append(repo_file)
        add_file_to_repo(os.path.join(pkgbuild_dir, file), package.repo, arch)

        # copy any-arch packages to other repos as well
        if stripped_name.endswith(f'-any{pkgext}'):
            for repo_arch in ARCHES:
                if repo_arch == arch:
                    continue  # done already
                add_file_to_repo(repo_file, package.repo, repo_arch, remove_original=False)

    return files


def try_download_package(dest_file_path: str, package: Pkgbuild, arch: Arch) -> Optional[str]:
    filename = os.path.basename(dest_file_path)
    logging.debug(f"checking if we can download {filename}")
    pkgname = package.name
    repo_name = package.repo
    repos = get_kupfer_https(arch, scan=True).repos
    if repo_name not in repos:
        logging.warning(f"Repository {repo_name} is not a known HTTPS repo")
        return None
    repo = repos[repo_name]
    if pkgname not in repo.packages:
        logging.warning(f"Package {pkgname} not found in remote repos, building instead.")
        return None
    repo_pkg: RemotePackage = repo.packages[pkgname]
    if repo_pkg.version != package.version:
        logging.debug(f"Package {pkgname} versions differ: local: {package.version}, remote: {repo_pkg.version}. Building instead.")
        return None
    if repo_pkg.filename != filename:
        versions_str = f"local: {filename}, remote: {repo_pkg.filename}"
        if strip_compression_extension(repo_pkg.filename) != strip_compression_extension(filename):
            logging.debug(f"package filenames don't match: {versions_str}")
            return None
        logging.debug(f"ignoring compression extension difference: {versions_str}")
    url = repo_pkg.resolved_url
    assert url
    try:
        path = repo_pkg.acquire()
        assert os.path.exists(path)
        return path
    except HTTPError as e:
        if e.code == 404:
            logging.debug(f"remote package {filename} missing on server: {url}")
        else:
            logging.error(f"remote package {filename} failed to download ({e.code}): {url}: {e}")
        return None


def check_package_version_built(
    package: Pkgbuild,
    arch: Arch,
    try_download: bool = False,
    refresh_sources: bool = False,
) -> bool:
    logging.info(f"Checking if {package.name} is built for architecture {arch}")

    if refresh_sources:
        setup_sources(package)

    missing = True
    filename = package.get_filename(arch)
    filename_stripped = strip_compression_extension(filename)
    local_repo: Optional[LocalRepo] = None
    if not filename_stripped.endswith('.pkg.tar'):
        raise Exception(f'{package.name}: stripped filename has unknown extension. {filename}')
    logging.debug(f'Checking if {filename_stripped} is built')

    any_arch = filename_stripped.endswith('any.pkg.tar')
    if any_arch:
        logging.debug(f"{package.name}: any-arch pkg detected")

    init_prebuilts(arch)
    # check if DB entry exists and matches PKGBUILD
    try:
        local_distro = get_kupfer_local(arch, in_chroot=False, scan=True)
        if package.repo not in local_distro.repos:
            raise Exception(f"Repo {package.repo} not found locally")
        local_repo = local_distro.repos[package.repo]
        if not local_repo.scanned:
            local_repo.scan()
        if package.name not in local_repo.packages:
            raise Exception(f"Package '{package.name}' not found")
        binpkg: LocalPackage = local_repo.packages[package.name]
        if package.version != binpkg.version:
            raise Exception(f"Versions differ: PKGBUILD: {package.version}, Repo: {binpkg.version}")
        if binpkg.arch not in (['any'] if package.arches == ['any'] else [arch]):
            raise Exception(f"Wrong Architecture: {binpkg.arch}, requested: {arch}")
        assert binpkg.resolved_url
        filepath = binpkg.resolved_url.split('file://')[1]
        if filename_stripped != strip_compression_extension(binpkg.filename):
            raise Exception(f"Repo entry exists but the filename {binpkg.filename} doesn't match expected {filename_stripped}")
        if not os.path.exists(filepath):
            raise Exception(f"Repo entry exists but file {filepath} is missing from disk")
        assert binpkg._desc
        if 'SHA256SUM' not in binpkg._desc or not binpkg._desc['SHA256SUM']:
            raise Exception("Repo entry exists but has no checksum")
        if sha256sum(filepath) != binpkg._desc['SHA256SUM']:
            raise Exception("Repo entry exists but checksum doesn't match")
        missing = False
        file = filepath
        filename = binpkg.filename
        logging.debug(f"{filename} found in {package.repo}.db ({arch}) and checksum matches")
    except Exception as ex:
        logging.debug(f"Failed to search local repos for package {package.name}: {ex}")

    # file might be in repo directory but not in DB or checksum mismatch
    for ext in ['xz', 'zst']:
        if not missing:
            break
        file = os.path.join(config.get_package_dir(arch), package.repo, f'{filename_stripped}.{ext}')
        if not os.path.exists(file):
            # look for 'any' arch packages in other repos
            if any_arch:
                target_repo_file = os.path.join(config.get_package_dir(arch), package.repo, filename)
                if os.path.exists(target_repo_file):
                    file = target_repo_file
                    missing = False
                else:
                    # we have to check if another arch's repo holds our any-arch pkg
                    for repo_arch in ARCHES:
                        if repo_arch == arch:
                            continue  # we already checked that
                        other_repo_file = os.path.join(config.get_package_dir(repo_arch), package.repo, filename)
                        if os.path.exists(other_repo_file):
                            logging.info(f"package {file} found in {repo_arch} repo, copying to {arch}")
                            file = other_repo_file
                            missing = False
            if try_download and missing:
                downloaded = try_download_package(file, package, arch)
                if downloaded:
                    file = downloaded
                    filename = os.path.basename(file)
                    missing = False
                    logging.info(f"Successfully downloaded {filename} from HTTPS mirror")
        if os.path.exists(file):
            missing = False
            add_file_to_repo(file, repo_name=package.repo, arch=arch, remove_original=False)
            assert local_repo
            local_repo.scan()
    # copy arch=(any) packages to all arches
    if any_arch and not missing:
        # copy to other arches if they don't have it
        for repo_arch in ARCHES:
            if repo_arch == arch:
                continue  # we already have that
            copy_target = os.path.join(config.get_package_dir(repo_arch), package.repo, filename)
            if not os.path.exists(copy_target):
                logging.info(f"copying any-arch package {package.name} to {repo_arch} repo: {copy_target}")
                add_file_to_repo(file, package.repo, repo_arch, remove_original=False)
                other_repo = get_kupfer_local(repo_arch, in_chroot=False, scan=False).repos.get(package.repo, None)
                if other_repo and other_repo.scanned:
                    other_repo.scan()
    return not missing


def setup_build_chroot(
    arch: Arch,
    extra_packages: list[str] = [],
    add_kupfer_repos: bool = True,
    clean_chroot: bool = False,
) -> BuildChroot:
    assert config.runtime.arch
    if arch != config.runtime.arch:
        build_enable_qemu_binfmt(arch)
    init_prebuilts(arch)
    chroot = get_build_chroot(arch, add_kupfer_repos=add_kupfer_repos)
    chroot.mount_packages()
    logging.debug(f'Initializing {arch} build chroot')
    chroot.initialize(reset=clean_chroot)
    chroot.write_pacman_conf()  # in case it was initialized with different repos
    chroot.activate()
    chroot.mount_pacman_cache()
    chroot.mount_pkgbuilds()
    if extra_packages:
        chroot.try_install_packages(extra_packages, allow_fail=False)
    assert config.runtime.uid is not None
    chroot.create_user('kupfer', password='12345678', uid=config.runtime.uid, non_unique=True)
    if not os.path.exists(chroot.get_path('/etc/sudoers.d/kupfer_nopw')):
        chroot.add_sudo_config('kupfer_nopw', 'kupfer', password_required=False)

    return chroot


def setup_git_insecure_paths(chroot: BuildChroot, username: str = 'kupfer'):
    chroot.run_cmd(
        ["git", "config", "--global", "--add", "safe.directory", "'*'"],
        switch_user=username,
    ).check_returncode()  # type: ignore[union-attr]


def setup_sources(package: Pkgbuild, lazy: bool = True):
    cache = package.srcinfo_cache
    assert cache
    # catch cache._changed: if the PKGBUILD changed whatsoever, that's an indicator the sources might be changed
    if lazy and not cache._changed and cache.is_src_initialised():
        if cache.validate_checksums():
            logging.info(f"{package.path}: Sources already set up.")
            return
    makepkg_setup = MAKEPKG_CMD + [
        '--nodeps',
        '--nobuild',
        '--noprepare',
        '--skippgpcheck',
    ]

    logging.info(f'{package.path}: Getting build chroot for source setup')
    # we need to use a chroot here because makepkg symlinks sources into src/ via an absolute path
    dir = os.path.join(CHROOT_PATHS['pkgbuilds'], package.path)
    assert config.runtime.arch
    chroot = setup_build_chroot(config.runtime.arch)
    logging.info(f'{package.path}: Setting up sources with makepkg')
    result = chroot.run_cmd(makepkg_setup, cwd=dir, switch_user='kupfer')
    assert isinstance(result, subprocess.CompletedProcess)
    if result.returncode != 0:
        raise Exception(f'{package.path}: Failed to setup sources, exit code: {result.returncode}')
    cache.refresh_all(write=True)
    cache.write_src_initialised()
    old_version = package.version
    package.refresh_sources()
    if package.version != old_version:
        logging.info(f"{package.path}: version refreshed from {old_version} to {package.version}")


def build_package(
    package: Pkgbuild,
    arch: Arch,
    repo_dir: Optional[str] = None,
    enable_crosscompile: bool = True,
    enable_crossdirect: bool = True,
    enable_ccache: bool = True,
    clean_chroot: bool = False,
    build_user: str = 'kupfer',
):
    makepkg_compile_opts = ['--holdver']
    makepkg_conf_path = 'etc/makepkg.conf'
    repo_dir = repo_dir if repo_dir else config.get_path('pkgbuilds')
    foreign_arch = config.runtime.arch != arch
    deps = list(package.makedepends)
    names = set(package.names())
    if isinstance(package, SubPkgbuild):
        names |= set(package.pkgbase.names())
    if not package.nodeps:
        deps += list(package.depends)
    deps = list(set(deps) - names)
    needs_rust = 'rust' in deps
    logging.info(f"{package.path}: Preparing to build: getting native arch build chroot")
    build_root: BuildChroot
    target_chroot = setup_build_chroot(
        arch=arch,
        extra_packages=deps,
        clean_chroot=clean_chroot,
    )
    assert config.runtime.arch
    native_chroot = target_chroot
    if foreign_arch:
        logging.info(f"{package.path}: Preparing to build: getting {arch} build chroot")
        native_chroot = setup_build_chroot(
            arch=config.runtime.arch,
            extra_packages=['base-devel'] + CROSSDIRECT_PKGS,
            clean_chroot=clean_chroot,
        )
    if not package.mode:
        logging.warning(f'Package {package.path} has no _mode set, assuming "host"')
    cross = foreign_arch and package.mode == 'cross' and enable_crosscompile

    if cross:
        logging.info(f'Cross-compiling {package.path}')
        build_root = native_chroot
        makepkg_compile_opts += ['--nodeps']
        env = deepcopy(get_makepkg_env(arch))
        if enable_ccache:
            env['PATH'] = f"/usr/lib/ccache:{env['PATH']}"
            native_chroot.mount_ccache(user=build_user)
        logging.info(f'{package.path}: Setting up dependencies for cross-compilation')
        # include crossdirect for ccache symlinks and qemu-user
        cross_deps = list(package.makedepends) if package.nodeps else (deps + CROSSDIRECT_PKGS + [f"{GCC_HOSTSPECS[native_chroot.arch][arch]}-gcc"])
        results = native_chroot.try_install_packages(cross_deps)
        if not package.nodeps:
            res_crossdirect = results['crossdirect']
            assert isinstance(res_crossdirect, subprocess.CompletedProcess)
            if res_crossdirect.returncode != 0:
                raise Exception('Unable to install crossdirect')
        # mount foreign arch chroot inside native chroot
        chroot_relative = os.path.join(CHROOT_PATHS['chroots'], target_chroot.name)
        makepkg_path_absolute = native_chroot.write_makepkg_conf(target_arch=arch, cross_chroot_relative=chroot_relative, cross=True)
        makepkg_conf_path = os.path.join('etc', os.path.basename(makepkg_path_absolute))
        native_chroot.mount_crosscompile(target_chroot)
    else:
        logging.info(f'Host-compiling {package.path}')
        build_root = target_chroot
        makepkg_compile_opts += ['--nodeps' if package.nodeps else '--syncdeps']
        env = deepcopy(get_makepkg_env(arch))
        if foreign_arch and enable_crossdirect and package.name not in CROSSDIRECT_PKGS:
            env['PATH'] = f"/native/usr/lib/crossdirect/{arch}:{env['PATH']}"
            target_chroot.mount_crossdirect(native_chroot)
        else:
            if enable_ccache:
                logging.debug('ccache enabled')
                env['PATH'] = f"/usr/lib/ccache:{env['PATH']}"
                deps += ['ccache']
            logging.debug(('Building for native arch. ' if not foreign_arch else '') + 'Skipping crossdirect.')
        if not package.nodeps:
            dep_install = target_chroot.try_install_packages(deps, allow_fail=False)
            failed_deps = [name for name, res in dep_install.items() if res.returncode != 0]  # type: ignore[union-attr]
            if failed_deps:
                raise Exception(f'{package.path}: Dependencies failed to install: {failed_deps}')

    if enable_ccache:
        build_root.mount_ccache(user=build_user)
    if needs_rust:
        build_root.mount_rust(user=build_user)
    setup_git_insecure_paths(build_root)
    makepkg_conf_absolute = os.path.join('/', makepkg_conf_path)

    build_cmd = MAKEPKG_CMD + ['--config', makepkg_conf_absolute, '--skippgpcheck'] + makepkg_compile_opts
    logging.debug(f'Building: Running {build_cmd}')
    result = build_root.run_cmd(
        build_cmd,
        inner_env=env,
        cwd=os.path.join(CHROOT_PATHS['pkgbuilds'], package.path),
        switch_user=build_user,
        stderr=sys.stdout,
    )
    assert isinstance(result, subprocess.CompletedProcess)
    if result.returncode != 0:
        raise Exception(f'Failed to compile package {package.path}')


def get_dependants(
    repo: dict[str, Pkgbuild],
    packages: Iterable[Pkgbuild],
    arch: Arch,
    recursive: bool = True,
) -> set[Pkgbuild]:
    names = set([pkg.name for pkg in packages])
    to_add = set[Pkgbuild]()
    for pkg in repo.values():
        if set.intersection(names, set(pkg.depends)):
            if not set([arch, 'any']).intersection(pkg.arches):
                logging.warn(f'get_dependants: skipping matched pkg {pkg.name} due to wrong arch: {pkg.arches}')
                continue
            to_add.add(pkg)
    if recursive and to_add:
        to_add.update(get_dependants(repo, to_add, arch=arch))
    return to_add


def get_pkg_names_str(pkgs: Iterable[Pkgbuild]) -> str:
    return ', '.join(x.name for x in pkgs)


def get_pkg_levels_str(pkg_levels: Iterable[Iterable[Pkgbuild]]):
    return '\n'.join(f'{i}: {get_pkg_names_str(level)}' for i, level in enumerate(pkg_levels))


def get_unbuilt_package_levels(
    packages: Iterable[Pkgbuild],
    arch: Arch,
    repo: Optional[dict[str, Pkgbuild]] = None,
    force: bool = False,
    rebuild_dependants: bool = False,
    try_download: bool = False,
    refresh_sources: bool = True,
) -> list[set[Pkgbuild]]:
    repo = repo or discover_pkgbuilds()
    dependants = set[Pkgbuild]()
    if rebuild_dependants:
        dependants = get_dependants(repo, packages, arch=arch)
    package_levels = generate_dependency_chain(repo, set(packages).union(dependants))
    build_names = set[str]()
    build_levels = list[set[Pkgbuild]]()
    includes_dependants = " (includes dependants)" if rebuild_dependants else ""
    logging.info(f"Checking for unbuilt packages ({arch}) in dependency order{includes_dependants}:\n{get_pkg_levels_str(package_levels)}")
    i = 0
    total_levels = len(package_levels)
    package_bar = get_levels_bar(
        total=sum([len(lev) for lev in package_levels]),
        desc=f"Checking pkgs ({arch})",
        unit='pkgs',
        fields={"levels_total": total_levels},
        enable_rate=False,
    )
    counter_built = package_bar.add_subcounter('green')
    counter_unbuilt = package_bar.add_subcounter('blue')
    for level_num, level_packages in enumerate(package_levels):
        level_num = level_num + 1
        package_bar.update(0, name=" " * BAR_PADDING, level=level_num)
        level = set[Pkgbuild]()
        if not level_packages:
            continue

        def add_to_level(pkg, level, reason=''):
            if reason:
                reason = f': {reason}'
            counter_unbuilt.update()
            logging.info(f"Level {level}/{total_levels} ({arch}): Adding {package.path}{reason}")
            level.add(package)
            build_names.update(package.names())

        for package in level_packages:
            package_bar.update(0, name=ellipsize(package.name, padding=" ", length=BAR_PADDING))
            if (force and package in packages):
                add_to_level(package, level, 'query match and force=True')
            elif rebuild_dependants and package in dependants:
                add_to_level(package, level, 'package is a dependant, dependant-rebuilds requested')
            elif not check_package_version_built(package, arch, try_download=try_download, refresh_sources=refresh_sources):
                add_to_level(package, level, 'package unbuilt')
            else:
                logging.info(f"Level {level_num}/{total_levels} ({arch}): {package.path}: Package doesn't need [re]building")
                counter_built.update()

        logging.debug(f'Finished checking level {level_num}/{total_levels} ({arch}). Adding unbuilt pkgs: {get_pkg_names_str(level)}')
        if level:
            build_levels.append(level)
            i += 1
    package_bar.close(clear=True)
    return build_levels


def build_packages(
    packages: Iterable[Pkgbuild],
    arch: Arch,
    repo: Optional[dict[str, Pkgbuild]] = None,
    force: bool = False,
    rebuild_dependants: bool = False,
    try_download: bool = False,
    enable_crosscompile: bool = True,
    enable_crossdirect: bool = True,
    enable_ccache: bool = True,
    clean_chroot: bool = False,
):
    check_programs_wrap(['makepkg', 'pacman', 'pacstrap'])
    init_prebuilts(arch)
    build_levels = get_unbuilt_package_levels(
        packages,
        arch,
        repo=repo,
        force=force,
        rebuild_dependants=rebuild_dependants,
        try_download=try_download,
    )

    if not build_levels:
        logging.info('Everything built already')
        return

    logging.info(f"Build plan made:\n{get_pkg_levels_str(build_levels)}")

    total_levels = len(build_levels)
    package_bar = get_levels_bar(
        desc=f'Building pkgs ({arch})',
        color='purple',
        unit='pkgs',
        total=sum([len(lev) for lev in build_levels]),
        fields={"levels_total": total_levels},
        enable_rate=False,
    )
    files = []
    updated_repos: set[str] = set()
    package_bar.update(-1)
    for level, need_build in enumerate(build_levels):
        level = level + 1
        package_bar.update(incr=0, force=True, name=" " * BAR_PADDING, level=level)
        logging.info(f"(Level {level}/{total_levels}) Building {get_pkg_names_str(need_build)}")
        for package in need_build:
            package_bar.update(force=True, name=ellipsize(package.name, padding=" ", length=BAR_PADDING))
            base = package.pkgbase if isinstance(package, SubPkgbuild) else package
            assert isinstance(base, Pkgbase)
            if package.is_built(arch):
                logging.info(f"Skipping building {package.name} since it was already built this run as part of pkgbase {base.name}")
                continue
            build_package(
                package,
                arch=arch,
                enable_crosscompile=enable_crosscompile,
                enable_crossdirect=enable_crossdirect,
                enable_ccache=enable_ccache,
                clean_chroot=clean_chroot,
            )
            files += add_package_to_repo(package, arch)
            updated_repos.add(package.repo)
            for _arch in ['any', arch]:
                if _arch in base.arches:
                    base._built_for.add(_arch)
            package_bar.update()
    # rescan affected repos
    local_repos = get_kupfer_local(arch, in_chroot=False, scan=False)
    for repo_name in updated_repos:
        assert repo_name in local_repos.repos
        local_repos.repos[repo_name].scan()

    package_bar.close(clear=True)
    return files


def build_packages_by_paths(
    paths: Iterable[str],
    arch: Arch,
    repo: Optional[dict[str, Pkgbuild]] = None,
    force=False,
    rebuild_dependants: bool = False,
    try_download: bool = False,
    enable_crosscompile: bool = True,
    enable_crossdirect: bool = True,
    enable_ccache: bool = True,
    clean_chroot: bool = False,
):
    if isinstance(paths, str):
        paths = [paths]

    check_programs_wrap(['makepkg', 'pacman', 'pacstrap'])
    assert config.runtime.arch
    for _arch in set([arch, config.runtime.arch]):
        init_prebuilts(_arch)
    packages = filter_pkgbuilds(paths, arch=arch, repo=repo, allow_empty_results=False)
    return build_packages(
        packages,
        arch,
        repo=repo,
        force=force,
        rebuild_dependants=rebuild_dependants,
        try_download=try_download,
        enable_crosscompile=enable_crosscompile,
        enable_crossdirect=enable_crossdirect,
        enable_ccache=enable_ccache,
        clean_chroot=clean_chroot,
    )


_qemu_enabled: dict[Arch, bool] = {arch: False for arch in ARCHES}


def build_enable_qemu_binfmt(arch: Arch, repo: Optional[dict[str, Pkgbuild]] = None, lazy: bool = True, native_chroot: Optional[BuildChroot] = None):
    if arch not in ARCHES:
        raise Exception(f'Unknown architecture "{arch}". Choices: {", ".join(ARCHES)}')
    logging.info('Installing qemu-user (building if necessary)')
    if lazy and _qemu_enabled[arch] and binfmt_is_registered(arch):
        _qemu_enabled[arch] = True
        return
    native = config.runtime.arch
    assert native
    if arch == native:
        return
    check_programs_wrap(['pacman', 'makepkg', 'pacstrap'])
    # build qemu-user, binfmt, crossdirect
    build_packages_by_paths(
        CROSSDIRECT_PKGS,
        native,
        repo=repo,
        try_download=True,
        enable_crosscompile=False,
        enable_crossdirect=False,
        enable_ccache=False,
    )
    crossrepo = get_kupfer_local(native, in_chroot=False, scan=True).repos['cross'].packages
    pkgfiles = [os.path.join(crossrepo[pkg].resolved_url.split('file://')[1]) for pkg in QEMU_BINFMT_PKGS]  # type: ignore
    runcmd = run_root_cmd
    if native_chroot or not is_wrapped():
        native_chroot = native_chroot or setup_build_chroot(native)
        runcmd = native_chroot.run_cmd
        hostdir = config.get_path('packages')
        _files = []
        # convert host paths to in-chroot paths
        for p in pkgfiles:
            assert p.startswith(hostdir)
            _files.append(os.path.join(CHROOT_PATHS['packages'], p[len(hostdir):].lstrip('/')))
        pkgfiles = _files
    runcmd(['pacman', '-U', '--noconfirm', '--needed'] + pkgfiles, stderr=sys.stdout)
    binfmt_register(arch, chroot=native_chroot)
    _qemu_enabled[arch] = True
