from typing_extensions import TypeAlias

FASTBOOT = 'fastboot'
FLASH_PARTS = {
    'ROOTFS': 'rootfs',
    'ABOOT': 'aboot',
    'LK2ND': 'lk2nd',
    'QHYPSTUB': 'qhypstub',
}
EMMC = 'emmc'
MICROSD = 'microsd'
LOCATIONS = [EMMC, MICROSD]

JUMPDRIVE = 'jumpdrive'
JUMPDRIVE_VERSION = '0.8'

BOOT_STRATEGIES: dict[str, str] = {
    'oneplus-enchilada': FASTBOOT,
    'oneplus-fajita': FASTBOOT,
    'xiaomi-beryllium-ebbg': FASTBOOT,
    'xiaomi-beryllium-tianma': FASTBOOT,
    'bq-paella': FASTBOOT,
}

BASE_PACKAGES: list[str] = [
    'base',
    'base-kupfer',
    'nano',
    'vim',
]

POST_CMDS = ['kupfer-config apply']

REPOSITORIES = [
    'boot',
    'cross',
    'device',
    'firmware',
    'linux',
    'main',
    'phosh',
]

DEFAULT_PACKAGE_BRANCH = 'dev'
KUPFER_HTTPS = 'https://gitlab.com/kupfer/packages/prebuilts/-/raw/%branch%/$arch/$repo'

Arch: TypeAlias = str
ARCHES = [
    'x86_64',
    'aarch64',
    'armv7h',
]

DistroArch: TypeAlias = Arch
TargetArch: TypeAlias = Arch

ALARM_REPOS = {
    'core': 'http://mirror.archlinuxarm.org/$arch/$repo',
    'extra': 'http://mirror.archlinuxarm.org/$arch/$repo',
    'community': 'http://mirror.archlinuxarm.org/$arch/$repo',
    'alarm': 'http://mirror.archlinuxarm.org/$arch/$repo',
    'aur': 'http://mirror.archlinuxarm.org/$arch/$repo',
}

BASE_DISTROS: dict[DistroArch, dict[str, dict[str, str]]] = {
    'x86_64': {
        'repos': {
            'core': 'http://ftp.halifax.rwth-aachen.de/archlinux/$repo/os/$arch',
            'extra': 'http://ftp.halifax.rwth-aachen.de/archlinux/$repo/os/$arch',
            'community': 'http://ftp.halifax.rwth-aachen.de/archlinux/$repo/os/$arch',
        },
    },
    'aarch64': {
        'repos': ALARM_REPOS,
    },
    'armv7h': {
        'repos': ALARM_REPOS,
    },
}

COMPILE_ARCHES: dict[Arch, str] = {
    'x86_64': 'amd64',
    'aarch64': 'arm64',
    'armv7h': 'arm',
}

GCC_HOSTSPECS: dict[DistroArch, dict[TargetArch, str]] = {
    'x86_64': {
        'x86_64': 'x86_64-pc-linux-gnu',
        'aarch64': 'aarch64-linux-gnu',
        'armv7h': 'arm-unknown-linux-gnueabihf'
    },
    'aarch64': {
        'aarch64': 'aarch64-unknown-linux-gnu',
    },
    'armv7h': {
        'armv7h': 'armv7l-unknown-linux-gnueabihf'
    },
}

CFLAGS_GENERAL = ['-O2', '-pipe', '-fstack-protector-strong']
CFLAGS_ALARM = [
    ' -fno-plt',
    '-fexceptions',
    '-Wp,-D_FORTIFY_SOURCE=2',
    '-Wformat',
    '-Werror=format-security',
    '-fstack-clash-protection',
]
CFLAGS_ARCHES: dict[Arch, list[str]] = {
    'x86_64': ['-march=x86-64', '-mtune=generic'],
    'aarch64': [
        '-march=armv8-a',
    ] + CFLAGS_ALARM,
    'armv7h': [
        '-march=armv7-a',
        '-mfloat-abi=hard',
        '-mfpu=neon',
    ] + CFLAGS_ALARM,
}

QEMU_ARCHES: dict[Arch, str] = {
    'x86_64': 'x86_64',
    'aarch64': 'aarch64',
    'armv7h': 'arm',
}

QEMU_BINFMT_PKGS = ['qemu-user-static-bin', 'binfmt-qemu-static']
CROSSDIRECT_PKGS = ['crossdirect'] + QEMU_BINFMT_PKGS

SSH_DEFAULT_HOST = '172.16.42.1'
SSH_DEFAULT_PORT = 22
SSH_COMMON_OPTIONS = [
    '-o',
    'GlobalKnownHostsFile=/dev/null',
    '-o',
    'UserKnownHostsFile=/dev/null',
    '-o',
    'StrictHostKeyChecking=no',
]

CHROOT_PATHS = {
    'chroots': '/chroots',
    'jumpdrive': '/var/cache/jumpdrive',
    'pacman': '/pacman',
    'packages': '/packages',
    'pkgbuilds': '/pkgbuilds',
    'images': '/images',
}

WRAPPER_TYPES = [
    'none',
    'docker',
]

MAKEPKG_CMD = [
    'makepkg',
    '--noconfirm',
    '--ignorearch',
    '--needed',
]

SRCINFO_FILE = 'SRCINFO'
SRCINFO_METADATA_FILE = 'srcinfo_meta.json'

FLAVOUR_INFO_FILE = 'flavourinfo.json'
