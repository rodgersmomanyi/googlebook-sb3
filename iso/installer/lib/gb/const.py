"""Constants shared by all installer modules (DESIGN.md 8.3). Pure."""

SECTOR = 512
ALIGN = 1048576
CHUNK = 536870912
RESERVE = 1073741824
USERDATA_MIN = 8589934592
HEAD_TAIL = 67108864
BIG_BLANK = 268435456
GIB = 1 << 30
MIB = 1 << 20
IO_BLOCK = 4 << 20                  # read/write unit (4 MiB)

HEADTAIL_HEAD_LBAS = 2048           # headtail_sha256: LBA 0..2047 ...
HEADTAIL_TAIL_LBAS = 2081           # ... followed by the last 2081 LBAs
GPT_PRIMARY_LBAS = 34               # LBA 0..33 (MBR, header, 32 sectors of entries)
GPT_BACKUP_LBAS = 33                # last 33 LBAs (32 sectors of entries, backup header)
GPT_PRIMARY_BYTES = 17408
GPT_BACKUP_BYTES = 16896
ENTRY_SIZE = 128

TYPE_ESP = "c12a7328-f81f-11d2-ba4b-00a0c93ec93b"
TYPE_LINUX = "0fc63daf-8483-4772-8e79-3d69d8477de4"
TYPE_VBMETA = "88434509-d9d1-487d-b82c-15ef964cbd4b"
TYPE_BASIC = "ebd0a0a2-b9e5-4433-87c0-68b6b72699c7"
TYPE_MSR = "e3c9e316-0b5c-4db8-817d-f92df00215ae"
TYPE_WINRE = "de94bba4-06d1-4d40-a16a-bfd50179d6ac"
TYPE_LDM_META = "5808c8aa-7e8f-42e0-85d2-e1e90434cfb3"
TYPE_LDM_DATA = "af9b60a0-1431-4f62-bc68-3311714a69ad"
TYPE_STORAGE_SPACES = "e75caf8f-f680-4cee-afa3-b001e56efc2d"
WINDOWS_ONLY_TYPES = (TYPE_MSR, TYPE_WINRE, TYPE_LDM_META, TYPE_LDM_DATA, TYPE_STORAGE_SPACES)
DYNAMIC_TYPES = (TYPE_LDM_META, TYPE_LDM_DATA, TYPE_STORAGE_SPACES)
ZERO_GUID = "00000000-0000-0000-0000-000000000000"

EFI_GLOBAL = "8be4df61-93ca-11d2-aa0d-00e098032b8c"
LOAD_OPTION_ACTIVE = 0x00000001
EFIVARS = "/sys/firmware/efi/efivars"

GB_NAMES = ("EFI-SYSTEM", "boot_a", "boot_b", "init_boot_a", "init_boot_b", "vendor_boot_a", "vendor_boot_b",
            "vbmeta_a", "vbmeta_b", "pvmfw_a", "pvmfw_b", "misc", "metadata", "frp_persist",
            "desktop_security_persist", "desktop_security_storage", "super", "userdata")
SOURCED_OBJECTS = ("boot_a", "boot_b", "init_boot_a", "init_boot_b", "vendor_boot_a", "vendor_boot_b",
                   "vbmeta_a", "vbmeta_b", "pvmfw_a", "pvmfw_b", "super")
OBJECT_IDS = ("esp-usb", "esp-internal") + SOURCED_OBJECTS
ALLOWED_NEW_ATTRS = 0x01FF000000000000

VOLUME_LABEL = "GBSB3INST"
BOOT_LABEL = "Googlebook OS"
LOADER = "\\EFI\\BOOT\\BOOTX64.EFI"
WINDOWS_LOADER = "\\EFI\\Microsoft\\Boot\\bootmgfw.efi"
VMWARE_VENDOR = "VMware, Inc."

# the only ioctl request numbers installer code may use (3.2)
BLKGETSIZE64 = 0x80081272
BLKSSZGET = 0x1268
BLKRRPART = 0x125f
BLKFLSBUF = 0x1261
BLKROSET = 0x125d
BLKROGET = 0x125e
BLKGETDISKSEQ = 0x80081280
ALLOWED_IOCTLS = (BLKGETSIZE64, BLKSSZGET, BLKRRPART, BLKFLSBUF, BLKROSET, BLKROGET, BLKGETDISKSEQ)

# runtime paths (3.4)
RUN = "/run/gb"
LOG_DIR = "/run/gb/log"
PROBE_DIR = "/run/gb/probe"
BACKUP_DIR = "/run/gb/backup"
STATE_DIR = "/run/gb/state"
ESP_MNT = "/run/gb/esp"
SAVELOG_MNT = "/run/gb/savelog"
PAYLOAD_LINK = "/run/gb/payload"
PAYLOAD_JSON = "/run/gb/payload.json"
MARKERS_FLAG = "/run/gb/markers"
JOURNAL = "/run/gb/journal.json"
BUILD_ENV = "/etc/gb/build.env"
RELEASE_FILE = "/etc/gb-installer-release"

ISO_MIN_BYTES = 64 << 20            # isofile candidates: size >= 64 MiB
WALK_DEPTH = 6
WALK_SECONDS = 10.0
WALK_SKIP = ("system volume information", "$recycle.bin", "windows", "program files", "program files (x86)",
             "programdata", "users")

# GPT slot attribute bytes Android may change on the vbmeta entries (bits 48..56)
VBMETA_ATTR_MASK = 0x01FF000000000000
