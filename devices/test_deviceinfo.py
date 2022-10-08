from config.state import config

from .deviceinfo import DeviceInfo, parse_deviceinfo
from .device import get_device

deviceinfo_text = """
# Reference: <https://postmarketos.org/deviceinfo>
# Please use double quotes only. You can source this file in shell scripts.

deviceinfo_format_version="0"
deviceinfo_name="BQ Aquaris X5"
deviceinfo_manufacturer="BQ"
deviceinfo_codename="bq-paella"
deviceinfo_year="2015"
deviceinfo_dtb="qcom/msm8916-longcheer-l8910"
deviceinfo_append_dtb="true"
deviceinfo_modules_initfs="smb1360 panel-longcheer-yushun-nt35520 panel-longcheer-truly-otm1288a msm himax-hx852x"
deviceinfo_arch="aarch64"

# Device related
deviceinfo_gpu_accelerated="true"
deviceinfo_chassis="handset"
deviceinfo_keyboard="false"
deviceinfo_external_storage="true"
deviceinfo_screen_width="720"
deviceinfo_screen_height="1280"
deviceinfo_getty="ttyMSM0;115200"

# Bootloader related
deviceinfo_flash_method="fastboot"
deviceinfo_kernel_cmdline="earlycon console=ttyMSM0,115200 PMOS_NO_OUTPUT_REDIRECT"
deviceinfo_generate_bootimg="true"
deviceinfo_flash_offset_base="0x80000000"
deviceinfo_flash_offset_kernel="0x00080000"
deviceinfo_flash_offset_ramdisk="0x02000000"
deviceinfo_flash_offset_second="0x00f00000"
deviceinfo_flash_offset_tags="0x01e00000"
deviceinfo_flash_pagesize="2048"
deviceinfo_flash_sparse="true"
"""


def test_parse_deviceinfo():
    config.try_load_file()
    d = parse_deviceinfo(deviceinfo_text.split('\n'), 'device-bq-paella')
    assert isinstance(d, DeviceInfo)
    assert d
    assert d.arch
    assert d.chassis
    assert d.flash_method
    assert d.flash_pagesize
    # test that fields not listed in the class definition make it into the object
    assert d.dtb
    assert d.gpu_accelerated


def test_get_deviceinfo_from_repo():
    config.try_load_file()
    dev = get_device('sdm845-oneplus-enchilada')
    assert dev
    info = dev.parse_deviceinfo()
    assert info
