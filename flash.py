import atexit
from constants import FLASH_PARTS, LOCATIONS
from fastboot import fastboot_flash
import shutil
from image import dump_bootimg, dump_lk2nd, dump_qhypstub, get_device_and_flavour, get_image_name, losetup_rootfs_image
import os
import subprocess
import click
import tempfile
from wrapper import enforce_wrap
from image import shrink_fs

BOOTIMG = FLASH_PARTS['BOOTIMG']
LK2ND = FLASH_PARTS['LK2ND']
QHYPSTUB = FLASH_PARTS['QHYPSTUB']
ROOTFS = FLASH_PARTS['ROOTFS']


@click.command(name='flash')
@click.argument('what')
@click.argument('location', required=False)
def cmd_flash(what, location):
    enforce_wrap()
    device, flavour = get_device_and_flavour()
    image_name = get_image_name(device, flavour)

    # TODO: PARSE DEVICE SECTOR SIZE
    sector_size = 4096

    if what not in FLASH_PARTS.values():
        raise Exception(f'Unknown what "{what}", must be one of {", ".join(FLASH_PARTS.values())}')

    if what == ROOTFS:
        if location is None:
            raise Exception(f'You need to specify a location to flash {what} to')

        path = ''
        if location.startswith("/dev/"):
            path = location
        else:
            if location not in LOCATIONS:
                raise Exception(f'Invalid location {location}. Choose one of {", ".join(LOCATIONS)}')

            dir = '/dev/disk/by-id'
            for file in os.listdir(dir):
                sanitized_file = file.replace('-', '').replace('_', '').lower()
                if f'jumpdrive{location.split("-")[0]}' in sanitized_file:
                    path = os.path.realpath(os.path.join(dir, file))
                    result = subprocess.run(['lsblk', path, '-o', 'SIZE'], capture_output=True)
                    if result.returncode != 0:
                        raise Exception(f'Failed to lsblk {path}')
                    if result.stdout == b'SIZE\n  0B\n':
                        raise Exception(
                            f'Disk {path} has a size of 0B. That probably means it is not available (e.g. no microSD inserted or no microSD card slot installed in the device) or corrupt or defect'
                        )
            if path == '':
                raise Exception('Unable to discover Jumpdrive')

        image_dir = tempfile.gettempdir()
        image_path = os.path.join(image_dir, f'minimal-{image_name}')

        def clean_dir():
            shutil.rmtree(image_dir)

        atexit.register(clean_dir)

        shutil.copyfile(os.path.join('/images', image_name), image_path)

        loop_device = losetup_rootfs_image(image_path, sector_size)
        shrink_fs(loop_device, image_path, sector_size)

        result = subprocess.run([
            'dd',
            f'if={image_path}',
            f'of={path}',
            'bs=20M',
            'iflag=direct',
            'oflag=direct',
            'status=progress',
            'conv=sync,noerror',
        ])
        if result.returncode != 0:
            raise Exception(f'Failed to flash {image_path} to {path}')
    else:
        loop_device = losetup_rootfs_image(os.path.join('/images', image_name), sector_size)
        if what == BOOTIMG:
            path = dump_bootimg(f'{loop_device}p1')
            fastboot_flash('boot', path)
        elif what == LK2ND:
            path = dump_lk2nd(f'{loop_device}p1')
            fastboot_flash('lk2nd', path)
        elif what == QHYPSTUB:
            path = dump_qhypstub(f'{loop_device}p1')
            fastboot_flash('qhypstub', path)
        else:
            raise Exception(f'Unknown what "{what}", this must be a bug in kupferbootstrap!')
