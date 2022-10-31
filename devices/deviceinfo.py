# Copyright 2022 Oliver Smith
# SPDX-License-Identifier: GPL-3.0-or-later
# Taken from postmarketOS/pmbootstrap, modified for kupferbootstrap by Prawn
import copy
import logging
import os

from typing import Mapping

from config.state import config
from constants import Arch
from dataclass import DataClass

PMOS_ARCHES_OVERRIDES: dict[str, Arch] = {
    "armv7": 'armv7h',
}


class DeviceInfo(DataClass):
    arch: Arch
    name: str
    manufacturer: str
    codename: str
    chassis: str
    flash_pagesize: int
    flash_method: str

    @classmethod
    def transform(cls, values: Mapping[str, str], validate: bool = True, allow_extra: bool = True):
        return super().transform(values, validate=validate, allow_extra=allow_extra)


# Variables from deviceinfo. Reference: <https://postmarketos.org/deviceinfo>
deviceinfo_attributes = [
    # general
    "format_version",
    "name",
    "manufacturer",
    "codename",
    "year",
    "dtb",
    "modules_initfs",
    "arch",

    # device
    "chassis",
    "keyboard",
    "external_storage",
    "screen_width",
    "screen_height",
    "dev_touchscreen",
    "dev_touchscreen_calibration",
    "append_dtb",

    # bootloader
    "flash_method",
    "boot_filesystem",

    # flash
    "flash_heimdall_partition_kernel",
    "flash_heimdall_partition_initfs",
    "flash_heimdall_partition_system",
    "flash_heimdall_partition_vbmeta",
    "flash_heimdall_partition_dtbo",
    "flash_fastboot_partition_kernel",
    "flash_fastboot_partition_system",
    "flash_fastboot_partition_vbmeta",
    "flash_fastboot_partition_dtbo",
    "generate_legacy_uboot_initfs",
    "kernel_cmdline",
    "generate_bootimg",
    "bootimg_qcdt",
    "bootimg_mtk_mkimage",
    "bootimg_dtb_second",
    "flash_offset_base",
    "flash_offset_kernel",
    "flash_offset_ramdisk",
    "flash_offset_second",
    "flash_offset_tags",
    "flash_pagesize",
    "flash_fastboot_max_size",
    "flash_sparse",
    "flash_sparse_samsung_format",
    "rootfs_image_sector_size",
    "sd_embed_firmware",
    "sd_embed_firmware_step_size",
    "partition_blacklist",
    "boot_part_start",
    "partition_type",
    "root_filesystem",
    "flash_kernel_on_update",
    "cgpt_kpart",
    "cgpt_kpart_start",
    "cgpt_kpart_size",

    # weston
    "weston_pixman_type",

    # keymaps
    "keymaps",
]

# Valid types for the 'chassis' atribute in deviceinfo
# See https://www.freedesktop.org/software/systemd/man/machine-info.html
deviceinfo_chassis_types = [
    "desktop",
    "laptop",
    "convertible",
    "server",
    "tablet",
    "handset",
    "watch",
    "embedded",
    "vm",
]


def sanity_check(deviceinfo: dict[str, str], device_name: str):
    try:
        _pmos_sanity_check(deviceinfo, device_name)
    except RuntimeError as err:
        raise Exception(f"{device_name}: The postmarketOS checker for deviceinfo files has run into an issue.\n"
                        "Here at kupfer, we usually don't maintain our own deviceinfo files "
                        "and instead often download them postmarketOS in our PKGBUILDs.\n"
                        "Please make sure your PKGBUILDs.git is up to date. (run `kupferbootstrap packages update`)\n"
                        "If the problem persists, please open an issue for this device's deviceinfo file "
                        "in the kupfer pkgbuilds git repo on Gitlab.\n\n"
                        "postmarketOS error message (referenced file may not exist until you run makepkg in that directory):\n"
                        f"{err}")


def _pmos_sanity_check(info: dict[str, str], device_name: str):
    # Resolve path for more readable error messages
    path = os.path.join(config.get_path('pkgbuilds'), 'device', device_name, 'deviceinfo')

    # Legacy errors
    if "flash_methods" in info:
        raise RuntimeError("deviceinfo_flash_methods has been renamed to"
                           " deviceinfo_flash_method. Please adjust your"
                           " deviceinfo file: " + path)
    if "external_disk" in info or "external_disk_install" in info:
        raise RuntimeError("Instead of deviceinfo_external_disk and"
                           " deviceinfo_external_disk_install, please use the"
                           " new variable deviceinfo_external_storage in your"
                           " deviceinfo file: " + path)
    if "msm_refresher" in info:
        raise RuntimeError("It is enough to specify 'msm-fb-refresher' in the"
                           " depends of your device's package now. Please"
                           " delete the deviceinfo_msm_refresher line in: " + path)
    if "flash_fastboot_vendor_id" in info:
        raise RuntimeError("Fastboot doesn't allow specifying the vendor ID"
                           " anymore (#1830). Try removing the"
                           " 'deviceinfo_flash_fastboot_vendor_id' line in: " + path + " (if you are sure that "
                           " you need this, then we can probably bring it back to fastboot, just"
                           " let us know in the postmarketOS issues!)")
    if "nonfree" in info:
        raise RuntimeError("deviceinfo_nonfree is unused. "
                           "Please delete it in: " + path)
    if "dev_keyboard" in info:
        raise RuntimeError("deviceinfo_dev_keyboard is unused. "
                           "Please delete it in: " + path)
    if "date" in info:
        raise RuntimeError("deviceinfo_date was replaced by deviceinfo_year. "
                           "Set it to the release year in: " + path)

    # "codename" is required
    codename = os.path.basename(os.path.dirname(path))
    if codename.startswith("device-"):
        codename = codename[7:]
    # kupfer prepends the SoC
    codename_alternative = codename.split('-', maxsplit=1)[1] if codename.count('-') > 1 else codename
    _codename = info.get('codename', None)
    if not _codename or not (_codename in [codename, codename_alternative] or codename.startswith(_codename) or
                             codename_alternative.startswith(_codename)):
        raise RuntimeError(f"Please add 'deviceinfo_codename=\"{codename}\"' "
                           f"to: {path}")

    # "chassis" is required
    chassis_types = deviceinfo_chassis_types
    if "chassis" not in info or not info["chassis"]:
        logging.info("NOTE: the most commonly used chassis types in"
                     " postmarketOS are 'handset' (for phones) and 'tablet'.")
        raise RuntimeError(f"Please add 'deviceinfo_chassis' to: {path}")

    # "arch" is required
    if "arch" not in info or not info["arch"]:
        raise RuntimeError(f"Please add 'deviceinfo_arch' to: {path}")

    # "chassis" validation
    chassis_type = info["chassis"]
    if chassis_type not in chassis_types:
        raise RuntimeError(f"Unknown chassis type '{chassis_type}', should"
                           f" be one of {', '.join(chassis_types)}. Fix this"
                           f" and try again: {path}")


def parse_kernel_suffix(deviceinfo: dict[str, str], kernel: str = 'mainline') -> dict[str, str]:
    """
    Remove the kernel suffix (as selected in 'pmbootstrap init') from
    deviceinfo variables. Related:
    https://wiki.postmarketos.org/wiki/Device_specific_package#Multiple_kernels

    :param info: deviceinfo dict, e.g.:
                 {"a": "first",
                  "b_mainline": "second",
                  "b_downstream": "third"}
    :param device: which device info belongs to
    :param kernel: which kernel suffix to remove (e.g. "mainline")
    :returns: info, but with the configured kernel suffix removed, e.g:
              {"a": "first",
               "b": "second",
               "b_downstream": "third"}
    """
    # Do nothing if the configured kernel isn't available in the kernel (e.g.
    # after switching from device with multiple kernels to device with only one
    # kernel)
    # kernels = pmb.parse._apkbuild.kernels(args, device)
    if not kernel:  # or kernel not in kernels:
        logging.debug(f"parse_kernel_suffix: {kernel} not set, skipping")
        return deviceinfo

    ret = copy.copy(deviceinfo)

    suffix_kernel = kernel.replace("-", "_")
    for key in deviceinfo_attributes:
        key_kernel = f"{key}_{suffix_kernel}"
        if key_kernel not in ret:
            continue

        # Move ret[key_kernel] to ret[key]
        logging.debug(f"parse_kernel_suffix: {key_kernel} => {key}")
        ret[key] = ret[key_kernel]
        del (ret[key_kernel])

    return ret


def parse_deviceinfo(deviceinfo_lines: list[str], device_name: str, kernel='mainline') -> DeviceInfo:
    """
    :param device: defaults to args.device
    :param kernel: defaults to args.kernel
    """
    info = {}
    for line in deviceinfo_lines:
        line = line.strip()
        if line.startswith("#") or not line:
            continue
        if "=" not in line:
            raise SyntaxError(f"{device_name}: No '=' found:\n\t{line}")
        split = line.split("=", 1)
        if not split[0].startswith("deviceinfo_"):
            logging.warning(f"{device_name}: Unknown key {split[0]} in deviceinfo:\n{line}")
            continue
        key = split[0][len("deviceinfo_"):]
        value = split[1].replace("\"", "").replace("\n", "")
        info[key] = value

    # Assign empty string as default
    for key in deviceinfo_attributes:
        if key not in info:
            info[key] = ""

    info = parse_kernel_suffix(info, kernel)
    sanity_check(info, device_name)
    if 'arch' in info:
        arch = info['arch']
        info['arch'] = PMOS_ARCHES_OVERRIDES.get(arch, arch)
    dev = DeviceInfo.fromDict(info)
    return dev
