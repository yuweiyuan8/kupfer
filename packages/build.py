import logging
import multiprocessing
import os
import shutil
import subprocess

from copy import deepcopy
from urllib.error import HTTPError
from typing import Iterable, Iterator, Optional

from binfmt import register as binfmt_register, QEMU_ARCHES
from constants import REPOSITORIES, CROSSDIRECT_PKGS, QEMU_BINFMT_PKGS, GCC_HOSTSPECS, ARCHES, Arch, CHROOT_PATHS, MAKEPKG_CMD
from config import config
from exec.cmd import run_cmd, run_root_cmd
from exec.file import makedir, remove_file, symlink
from chroot.build import get_build_chroot, BuildChroot
from distro.distro import get_kupfer_https, get_kupfer_local
from distro.package import RemotePackage
from wrapper import check_programs_wrap, wrap_if_foreign_arch

from .pkgbuild import discover_pkgbuilds, filter_pkgbuilds, Pkgbuild

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
    env = {key: val for key, val in os.environ.items() if not key.split('_', maxsplit=1)[0] in ['CI', 'GITLAB', 'FF']}
    env |= {
        'LANG': 'C',
        'CARGO_BUILD_JOBS': str(threads),
        'MAKEFLAGS': f"-j{threads}",
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
        logging.debug("Removing cached package file {cache_file}")
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
    result = run_cmd(cmd)
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
    logging.debug(f"checking if we can download {package.name}")
    filename = os.path.basename(dest_file_path)
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


def check_package_version_built(package: Pkgbuild, arch: Arch, try_download: bool = False) -> bool:
    missing = True
    filename = package.get_filename(arch)
    filename_stripped = strip_compression_extension(filename)
    logging.debug(f'Checking if {filename_stripped} is built')
    any_arch = filename_stripped.endswith('any.pkg.tar')
    if any_arch:
        logging.debug("any-arch pkg detected")
    for ext in ['xz', 'zst']:
        file = os.path.join(config.get_package_dir(arch), package.repo, f'{filename_stripped}.{ext}')
        if not filename_stripped.endswith('.pkg.tar'):
            raise Exception(f'stripped filename has unknown extension. {filename}')
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
                    missing = False
        if os.path.exists(file):
            missing = False
            add_file_to_repo(file, repo_name=package.repo, arch=arch, remove_original=False)
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
        if not missing:
            return True
    return False


def setup_build_chroot(
    arch: Arch,
    extra_packages: list[str] = [],
    add_kupfer_repos: bool = True,
    clean_chroot: bool = False,
) -> BuildChroot:
    assert config.runtime.arch
    if arch != config.runtime.arch:
        wrap_if_foreign_arch(arch)
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


def setup_sources(package: Pkgbuild, chroot: BuildChroot, makepkg_conf_path='/etc/makepkg.conf', switch_user: str = 'kupfer'):
    makepkg_setup_args = [
        '--config',
        makepkg_conf_path,
        '--nobuild',
        '--holdver',
        '--nodeps',
        '--skippgpcheck',
    ]

    logging.info(f'Setting up sources for {package.path} in {chroot.name}')
    setup_git_insecure_paths(chroot)
    result = chroot.run_cmd(
        MAKEPKG_CMD + makepkg_setup_args,
        cwd=os.path.join(CHROOT_PATHS['pkgbuilds'], package.path),
        inner_env=get_makepkg_env(chroot.arch),
        switch_user=switch_user,
    )
    assert isinstance(result, subprocess.CompletedProcess)
    if result.returncode != 0:
        raise Exception(f'Failed to check sources for {package.path}')


def build_package(
    package: Pkgbuild,
    arch: Arch,
    repo_dir: str = None,
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
    deps = (list(set(package.depends) - set(package.names())))
    needs_rust = 'rust' in deps
    build_root: BuildChroot
    target_chroot = setup_build_chroot(
        arch=arch,
        extra_packages=deps,
        clean_chroot=clean_chroot,
    )
    assert config.runtime.arch
    native_chroot = target_chroot if not foreign_arch else setup_build_chroot(
        arch=config.runtime.arch,
        extra_packages=['base-devel'] + CROSSDIRECT_PKGS,
        clean_chroot=clean_chroot,
    )
    cross = foreign_arch and package.mode == 'cross' and enable_crosscompile

    target_chroot.initialize()

    if cross:
        logging.info(f'Cross-compiling {package.path}')
        build_root = native_chroot
        makepkg_compile_opts += ['--nodeps']
        env = deepcopy(get_makepkg_env(arch))
        if enable_ccache:
            env['PATH'] = f"/usr/lib/ccache:{env['PATH']}"
            native_chroot.mount_ccache(user=build_user)
        logging.info('Setting up dependencies for cross-compilation')
        # include crossdirect for ccache symlinks and qemu-user
        results = native_chroot.try_install_packages(package.depends + CROSSDIRECT_PKGS + [f"{GCC_HOSTSPECS[native_chroot.arch][arch]}-gcc"])
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
        makepkg_compile_opts += ['--syncdeps']
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
        dep_install = target_chroot.try_install_packages(deps, allow_fail=False)
        failed_deps = [name for name, res in dep_install.items() if res.returncode != 0]  # type: ignore[union-attr]
        if failed_deps:
            raise Exception(f'Dependencies failed to install: {failed_deps}')

    if enable_ccache:
        build_root.mount_ccache(user=build_user)
    if needs_rust:
        build_root.mount_rust(user=build_user)
    setup_git_insecure_paths(build_root)
    makepkg_conf_absolute = os.path.join('/', makepkg_conf_path)
    setup_sources(package, build_root, makepkg_conf_path=makepkg_conf_absolute)

    build_cmd = f'makepkg --config {makepkg_conf_absolute} --skippgpcheck --needed --noconfirm --ignorearch {" ".join(makepkg_compile_opts)}'
    logging.debug(f'Building: Running {build_cmd}')
    result = build_root.run_cmd(
        build_cmd,
        inner_env=env,
        cwd=os.path.join(CHROOT_PATHS['pkgbuilds'], package.path),
        switch_user=build_user,
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


def get_unbuilt_package_levels(
    packages: Iterable[Pkgbuild],
    arch: Arch,
    repo: Optional[dict[str, Pkgbuild]] = None,
    force: bool = False,
    rebuild_dependants: bool = False,
    try_download: bool = False,
) -> list[set[Pkgbuild]]:
    repo = repo or discover_pkgbuilds()
    dependants = set[Pkgbuild]()
    if rebuild_dependants:
        dependants = get_dependants(repo, packages, arch=arch)
    package_levels = generate_dependency_chain(repo, set(packages).union(dependants))
    build_names = set[str]()
    build_levels = list[set[Pkgbuild]]()
    i = 0
    for level_packages in package_levels:
        level = set[Pkgbuild]()
        for package in level_packages:
            if ((force and package in packages) or (rebuild_dependants and package in dependants) or
                    not check_package_version_built(package, arch, try_download)):
                level.add(package)
                build_names.update(package.names())
        if level:
            build_levels.append(level)
            logging.debug(f'Adding to level {i}:' + '\n' + ('\n'.join([p.name for p in level])))
            i += 1
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

    files = []
    for level, need_build in enumerate(build_levels):
        logging.info(f"(Level {level}) Building {', '.join([x.name for x in need_build])}")
        for package in need_build:
            build_package(
                package,
                arch=arch,
                enable_crosscompile=enable_crosscompile,
                enable_crossdirect=enable_crossdirect,
                enable_ccache=enable_ccache,
                clean_chroot=clean_chroot,
            )
            files += add_package_to_repo(package, arch)
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


def build_enable_qemu_binfmt(arch: Arch, repo: Optional[dict[str, Pkgbuild]] = None, lazy: bool = True):
    if arch not in ARCHES:
        raise Exception(f'Unknown architecture "{arch}". Choices: {", ".join(ARCHES)}')
    logging.info('Installing qemu-user (building if necessary)')
    if lazy and _qemu_enabled[arch]:
        return
    native = config.runtime.arch
    assert native
    if arch == native:
        return
    check_programs_wrap([f'qemu-{QEMU_ARCHES[arch]}-static', 'pacman', 'makepkg'])
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
    run_root_cmd(['pacman', '-U', '--noconfirm', '--needed'] + pkgfiles)
    if arch != native:
        binfmt_register(arch)
    _qemu_enabled[arch] = True
