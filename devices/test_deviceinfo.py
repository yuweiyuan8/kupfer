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


def test_parse_variant_deviceinfo():
    config.try_load_file()
    # {'variant1': 'AAAAA', 'variant2': 'BBBBB', 'variant3': 'CCCCC'}
    variants = {f"variant{i+1}": chr(ord('A') + i) * 5 for i in range(0, 3)}
    field = "dev_touchscreen_calibration"
    text = deviceinfo_text + '\n'.join([""] + [f"deviceinfo_{field}_{variant}={value}" for variant, value in variants.items()])
    for variant, result in variants.items():
        d = parse_deviceinfo(text.split('\n'), 'device-bq-paella', kernel=variant)
        # note: the python code from pmb only strips one variant, the shell code in packaging strips all variants
        assert f'{field}_{variant}' not in d
        assert field in d
        assert d[field] == result


def test_get_deviceinfo_from_repo():
    config.try_load_file()
    dev = get_device('sdm845-oneplus-enchilada')
    assert dev
    info = dev.parse_deviceinfo()
    assert info


def test_get_variant_deviceinfo_from_repo():
    config.try_load_file()
    dev = get_device('sdm845-xiaomi-beryllium-ebbg')
    assert dev
    info = dev.parse_deviceinfo()
    assert info
    assert 'dtb' in info  # variant-specific variable, check it has been stripped down from 'dtb_ebbg' to 'dtb'
    assert 'dtb_tianma' not in info
    assert info.dtb
