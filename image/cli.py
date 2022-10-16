from .boot import cmd_boot
from .flash import cmd_flash
from .image import cmd_image

for cmd in [cmd_boot, cmd_flash]:
    cmd_image.add_command(cmd)
