import atexit
import json
import os
import re
import subprocess
import click
from logger import logging
from chroot import create_chroot, create_chroot_user, get_chroot_path, run_chroot_cmd
from constants import BASE_PACKAGES, DEVICES, FLAVOURS
from config import config
from distro import get_base_distro, get_kupfer_https, get_kupfer_local
from ssh import copy_ssh_keys
from wrapper import enforce_wrap
from signal import pause


def shrink_fs(loop_device: str, file: str, sector_size: int):
    # 8: 512 bytes sectors
    # 1: 4096 bytes sectors
    sectors_blocks_factor = 4096 // sector_size

    logging.debug(f"Checking filesystem at {loop_device}p2")
    result = subprocess.run(['e2fsck', '-fy', f'{loop_device}p2'])
    if result.returncode > 2:
        # https://man7.org/linux/man-pages/man8/e2fsck.8.html#EXIT_CODE
        raise Exception(f'Failed to e2fsck {loop_device}p2 with exit code {result.returncode}')

    logging.debug(f'Shrinking filesystem at {loop_device}p2')
    result = subprocess.run(['resize2fs', '-M', f'{loop_device}p2'], capture_output=True)
    if result.returncode != 0:
        print(result.stdout)
        print(result.stderr)
        raise Exception(f'Failed to resize2fs {loop_device}p2')

    logging.debug(f'Finding end block of shrunken filesystem on {loop_device}p2')
    blocks = int(re.search('is now [0-9]+', result.stdout.decode('utf-8')).group(0).split(' ')[2])
    sectors = blocks * sectors_blocks_factor  #+ 157812 - 25600

    logging.debug(f'Shrinking partition at {loop_device}p2 to {sectors} sectors')
    child_proccess = subprocess.Popen(
        ['fdisk', '-b', str(sector_size), loop_device],
        stdin=subprocess.PIPE,
    )
    child_proccess.stdin.write('\n'.join([
        'd',
        '2',
        'n',
        'p',
        '2',
        '',
        f'+{sectors}',
        'w',
        'q',
    ]).encode('utf-8'))

    child_proccess.communicate()

    returncode = child_proccess.wait()
    if returncode == 1:
        # For some reason re-reading the partition table fails, but that is not a problem
        subprocess.run(['partprobe'])
    if returncode > 1:
        raise Exception(f'Failed to shrink partition size of {loop_device}p2 with fdisk')

    logging.debug(f'Finding end sector of partition at {loop_device}p2')
    result = subprocess.run(['fdisk', '-b', str(sector_size), '-l', loop_device], capture_output=True)
    if result.returncode != 0:
        print(result.stdout)
        print(result.stderr)
        raise Exception(f'Failed to fdisk -l {loop_device}')

    end_sector = 0
    for line in result.stdout.decode('utf-8').split('\n'):
        if line.startswith(f'{loop_device}p2'):
            parts = list(filter(lambda part: part != '', line.split(' ')))
            end_sector = int(parts[2])

    if end_sector == 0:
        raise Exception(f'Failed to find end sector of {loop_device}p2')

    end_block = end_sector // sectors_blocks_factor

    logging.debug(f'Truncating {file} to {end_block} blocks')
    result = subprocess.run(['truncate', '-o', '-s', str(end_block), file])
    if result.returncode != 0:
        raise Exception(f'Failed to truncate {file}')


def get_device_and_flavour(profile: str = None) -> tuple[str, str]:
    #config.enforce_config_loaded()
    profile = config.get_profile(profile)
    if not profile['device']:
        raise Exception("Please set the device using 'kupferbootstrap config init ...'")

    if not profile['flavour']:
        raise Exception("Please set the flavour using 'kupferbootstrap config init ...'")

    return (profile['device'], profile['flavour'])


def get_image_name(device, flavour) -> str:
    return f'{device}-{flavour}-rootfs.img'


def losetup_rootfs_image(image_path: str, sector_size: int) -> str:
    logging.debug(f'Creating loop device for {image_path}')
    result = subprocess.run([
        'losetup',
        '-f',
        '-b',
        str(sector_size),
        image_path,
    ])
    if result.returncode != 0:
        logging.fatal(f'Failed create loop device for {image_path}')
        exit(1)

    logging.debug(f'Finding loop device for {image_path}')

    result = subprocess.run(['losetup', '-J'], capture_output=True)
    if result.returncode != 0:
        print(result.stdout)
        print(result.stderr)
        logging.fatal('Failed to list loop devices')
        exit(1)

    data = json.loads(result.stdout.decode('utf-8'))
    loop_device = ''
    for d in data['loopdevices']:
        if d['back-file'] == image_path:
            loop_device = d['name']
            break

    if loop_device == '':
        raise Exception(f'Failed to find loop device for {image_path}')

    def losetup_destroy():
        logging.debug(f'Destroying loop device {loop_device} for {image_path}')
        subprocess.run(
            [
                'losetup',
                '-d',
                loop_device,
            ],
            stderr=subprocess.DEVNULL,
        )

    atexit.register(losetup_destroy)

    return loop_device


def mount_rootfs_loop_device(loop_device, mount_path):

    def umount():
        subprocess.run(
            [
                'umount',
                '-lc',
                f'{mount_path}/boot',
            ],
            stderr=subprocess.DEVNULL,
        )
        subprocess.run(
            [
                'umount',
                '-lc',
                mount_path,
            ],
            stderr=subprocess.DEVNULL,
        )

    atexit.register(umount)

    if not os.path.exists(mount_path):
        os.makedirs(mount_path)

    logging.debug(f'Mounting {loop_device}p2 at {mount_path}')

    result = subprocess.run([
        'mount',
        '-o',
        'loop',
        f'{loop_device}p2',
        mount_path,
    ])
    if result.returncode != 0:
        logging.fatal(f'Failed to loop mount {loop_device}p2 to {mount_path}')
        exit(1)

    if not os.path.exists(f'{mount_path}/boot'):
        os.makedirs(f'{mount_path}/boot')

    logging.debug(f'Mounting {loop_device}p1 at {mount_path}/boot')

    result = subprocess.run([
        'mount',
        '-o',
        'loop',
        f'{loop_device}p1',
        f'{mount_path}/boot',
    ])
    if result.returncode != 0:
        logging.fatal(f'Failed to loop mount {loop_device}p1 to {mount_path}/boot')
        exit(1)


def dump_bootimg(image_name: str) -> str:
    path = '/tmp/boot.img'
    result = subprocess.run([
        'debugfs',
        image_name,
        '-R',
        f'dump /boot.img {path}',
    ])
    if result.returncode != 0:
        logging.fatal('Failed to dump boot.img')
        exit(1)
    return path


def dump_lk2nd(image_name: str) -> str:
    """
    This doesn't append the image with the appended DTB which is needed for some devices, so it should get added in the future.
    """
    path = '/tmp/lk2nd.img'
    result = subprocess.run([
        'debugfs',
        image_name,
        '-R',
        f'dump /lk2nd.img {path}',
    ])
    if result.returncode != 0:
        logging.fatal('Failed to dump lk2nd.img')
        exit(1)
    return path


def dump_qhypstub(image_name: str) -> str:
    path = '/tmp/qhypstub.bin'
    result = subprocess.run([
        'debugfs',
        image_name,
        '-R',
        f'dump /qhypstub.bin {path}',
    ])
    if result.returncode != 0:
        logging.fatal('Failed to dump qhypstub.bin')
        exit(1)
    return path


@click.group(name='image')
def cmd_image():
    pass


@cmd_image.command(name='build')
def cmd_build():
    enforce_wrap()
    profile = config.get_profile()
    device, flavour = get_device_and_flavour()
    post_cmds = FLAVOURS[flavour].get('post_cmds', [])
    image_name = os.path.join('/images', get_image_name(device, flavour))

    # TODO: PARSE DEVICE ARCH AND SECTOR SIZE
    arch = 'aarch64'
    sector_size = 4096

    new_image = not os.path.exists(image_name)
    if new_image:
        result = subprocess.run([
            'truncate',
            '-s',
            f"{FLAVOURS[flavour].get('size',2)}G",
            image_name,
        ])
        if result.returncode != 0:
            raise Exception(f'Failed to allocate {image_name}')

    loop_device = losetup_rootfs_image(image_name, sector_size)

    if new_image:
        boot_partition_size = '100MiB'
        create_partition_table = ['mklabel', 'msdos']
        create_boot_partition = ['mkpart', 'primary', 'ext2', '0%', boot_partition_size]
        create_root_partition = ['mkpart', 'primary', boot_partition_size, '100%']
        enable_boot = ['set', '1', 'boot', 'on']
        result = subprocess.run([
            'parted',
            '--script',
            loop_device,
        ] + create_partition_table + create_boot_partition + create_root_partition + enable_boot)
        if result.returncode != 0:
            raise Exception(f'Failed to create partitions on {loop_device}')

        result = subprocess.run([
            'mkfs.ext2',
            '-F',
            '-L',
            'kupfer_boot',
            f'{loop_device}p1',
        ])
        if result.returncode != 0:
            raise Exception(f'Failed to create ext2 filesystem on {loop_device}p1')

        result = subprocess.run([
            'mkfs.ext4',
            '-O',
            '^metadata_csum',
            '-F',
            '-L',
            'kupfer_root',
            '-N',
            '100000',
            f'{loop_device}p2',
        ])
        if result.returncode != 0:
            raise Exception(f'Failed to create ext4 filesystem on {loop_device}p2')

    chroot_name = f'rootfs_{device}-{flavour}'
    rootfs_mount = get_chroot_path(chroot_name)
    mount_rootfs_loop_device(loop_device, rootfs_mount)

    packages_dir = config.get_package_dir(arch)
    if os.path.exists(os.path.join(packages_dir, 'main')):
        extra_repos = get_kupfer_local(arch).repos
    else:
        extra_repos = get_kupfer_https(arch).repos
    packages = BASE_PACKAGES + DEVICES[device] + FLAVOURS[flavour]['packages'] + profile['pkgs_include']
    create_chroot(
        chroot_name,
        arch=arch,
        packages=packages,
        extra_repos=extra_repos,
        bind_mounts={},
        chroot_base_path='/chroot',
    )
    create_chroot_user(
        rootfs_mount,
        user=profile['username'],
        password=profile['password'],
    )

    copy_ssh_keys(
        rootfs_mount,
        user=profile['username'],
    )
    with open(os.path.join(rootfs_mount, 'etc', 'pacman.conf'), 'w') as file:
        file.write(get_base_distro(arch).get_pacman_conf(check_space=True, extra_repos=get_kupfer_https(arch).repos))
    if post_cmds:
        result = run_chroot_cmd(' && '.join(post_cmds), rootfs_mount)
        if result.returncode != 0:
            raise Exception('Error running post_cmds')


@cmd_image.command(name='inspect')
def cmd_inspect():
    device, flavour = get_device_and_flavour()
    image_name = get_image_name(device, flavour)

    # TODO: PARSE DEVICE SECTOR SIZE
    sector_size = 4096

    rootfs_mount = get_chroot_path(f'rootfs_{device}-{flavour}')
    loop_device = losetup_rootfs_image(image_name, sector_size)
    mount_rootfs_loop_device(loop_device, rootfs_mount)

    logging.info(f'Inspect the rootfs image at {rootfs_mount}')

    pause()
