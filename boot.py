import os
import urllib.request
import click

from typing import Optional

from config import config
from constants import BOOT_STRATEGIES, FLASH_PARTS, FASTBOOT, JUMPDRIVE, JUMPDRIVE_VERSION
from exec.file import makedir
from fastboot import fastboot_boot, fastboot_erase_dtbo
from image import get_device_name, losetup_rootfs_image, get_image_path, dump_aboot, dump_lk2nd
from packages.device import get_profile_device
from packages.flavour import get_profile_flavour, profile_option
from wrapper import enforce_wrap

LK2ND = FLASH_PARTS['LK2ND']
ABOOT = FLASH_PARTS['ABOOT']

TYPES = [LK2ND, JUMPDRIVE, ABOOT]


@click.command(name='boot')
@profile_option
@click.argument('type', required=False, default=ABOOT, type=click.Choice(TYPES))
def cmd_boot(type: str, profile: Optional[str] = None):
    """Boot JumpDrive or the Kupfer aboot image. Erases Android DTBO in the process."""
    enforce_wrap()
    device = get_profile_device(profile)
    flavour = get_profile_flavour(profile).name
    deviceinfo = device.parse_deviceinfo()
    sector_size = deviceinfo.flash_pagesize
    if not sector_size:
        raise Exception(f"Device {device.name} has no flash_pagesize specified")
    image_path = get_image_path(device, flavour)
    strategy = BOOT_STRATEGIES[device]

    if strategy == FASTBOOT:
        if type == JUMPDRIVE:
            file = f'boot-{get_device_name(device)}.img'
            path = os.path.join(config.get_path('jumpdrive'), file)
            makedir(os.path.dirname(path))
            if not os.path.exists(path):
                urllib.request.urlretrieve(f'https://github.com/dreemurrs-embedded/Jumpdrive/releases/download/{JUMPDRIVE_VERSION}/{file}', path)
        else:
            loop_device = losetup_rootfs_image(image_path, sector_size)
            if type == LK2ND:
                path = dump_lk2nd(loop_device + 'p1')
            elif type == ABOOT:
                path = dump_aboot(loop_device + 'p1')
            else:
                raise Exception(f'Unknown boot image type {type}')
        fastboot_erase_dtbo()
        fastboot_boot(path)
