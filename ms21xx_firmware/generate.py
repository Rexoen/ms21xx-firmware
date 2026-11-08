from argparse import ArgumentParser, Namespace
from dataclasses import dataclass
from pathlib import Path
from typing import Tuple

BASE_DIR = Path(__file__).parent
FIRMWARES_DIR = BASE_DIR / "firmwares"


@dataclass(frozen=True)
class Chip:
    template: Path
    vid_offset: int
    pid_offset: int
    header_ranges: Tuple[Tuple[int, int], ...]
    serial_headers: Tuple[str, ...] = ()


# The first 48 bytes are the descriptor header, followed by the code section
# and two 16-bit checksums. MS2109 and MS2109S share the same layout; MS2130
# moves VID/PID and excludes bytes 11..15 from the header checksum.
CHIPS = {
    "ms2109": Chip(
        template=FIRMWARES_DIR / "ms2109.bin",
        vid_offset=0x06,
        pid_offset=0x08,
        header_ranges=((2, 48),),
    ),
    "ms2109s": Chip(
        template=FIRMWARES_DIR / "ms2109s.bin",
        vid_offset=0x06,
        pid_offset=0x08,
        header_ranges=((2, 48),),
    ),
    "ms2130": Chip(
        template=FIRMWARES_DIR / "ms2130.bin",
        vid_offset=0x04,
        pid_offset=0x06,
        header_ranges=((2, 11), (16, 48)),
        serial_headers=("60402120", "60402160"),
    ),
}


def checksum(data: bytes, mask: int = 0xFFFF):
    return sum(data) & mask


def checksum_as_bytes(data: bytes, mask: int = 0xFFFF):
    return checksum(data, mask).to_bytes(2, byteorder='big')


def replace_at_index(data: str, idx: int, value: str):
    idx = idx * 2
    data_before = data[:idx]
    data_after = data[idx + len(value) :]
    return data_before + value + data_after


def pretty_print(data: str):
    while data:
        line = data[:32]
        while line:
            print(line[:2], end=" ")
            line = line[2:]
        data = data[32:]
        print()


def patch_serial(data: str, header: str, value: str):
    serial_idx = data.index(header) // 2 + 4
    serial_data = value.encode("utf-16-le")
    serial_len = (len(serial_data) + 2).to_bytes(1, byteorder='big')
    # 03 means that the descriptor is in string format
    data = replace_at_index(data, serial_idx, serial_len.hex() + "03")
    data = replace_at_index(data, serial_idx + 2, serial_data.hex())
    return data


def load_firmware(template: Path) -> bytes:
    with open(template, "rb") as fp:
        data = fp.read()

    # Bytes 2..3 hold the length of the code section. EEPROM dumps are padded
    # with 0xff up to the EEPROM size, but the checksums live immediately
    # after the code section, so the padding must be dropped.
    code_len = int.from_bytes(data[2:4], "big")
    firmware_len = 48 + code_len + 4
    if len(data) < firmware_len:
        raise ValueError(f"{template} is shorter than its header declares")
    return data[:firmware_len]


def generate_firmware(args: Namespace, chip: Chip) -> bytes:
    # convert to string for easier manipulation
    data = load_firmware(chip.template).hex()

    # modify the firmware data
    video_data = args.video.encode()
    video_len = (len(video_data) + 1).to_bytes(1, byteorder='big')
    audio_data = args.audio.encode()
    audio_len = (len(audio_data) + 1).to_bytes(1, byteorder='big')

    data = replace_at_index(data, chip.vid_offset, args.vid)
    data = replace_at_index(data, chip.pid_offset, args.pid)
    data = replace_at_index(data, 0x0C, args.firmware_version)
    data = replace_at_index(data, 0x10, video_len.hex())
    data = replace_at_index(data, 0x11, video_data.hex())
    data = replace_at_index(data, 0x20, audio_len.hex())
    data = replace_at_index(data, 0x21, audio_data.hex())
    if args.edid:
        edid_idx = data.index("00ffffffffffff00") // 2
        data = replace_at_index(data, edid_idx, args.edid)

    if args.serial:
        if chip.serial_headers:
            # we are modifiying USB String Descriptors here
            for header in chip.serial_headers:
                data = patch_serial(data, header, args.serial)
        else:
            print(f"WARNING: Serial is not supported in {args.chip} yet.")

    # change the data back to bytes
    data = bytes.fromhex(data)

    # replace the checksum of firmware with correct checksum
    header = b"".join(data[start:end] for start, end in chip.header_ranges)
    code = data[48:-4]
    data = data[:-4] + checksum_as_bytes(header) + checksum_as_bytes(code)
    return data


def main():
    parser = ArgumentParser()
    parser.add_argument("--chip", default="ms2130", choices=list(CHIPS))
    parser.add_argument("--output", default="output.bin")
    parser.add_argument("--vid", default="ffff")
    parser.add_argument("--pid", default="ffff")
    parser.add_argument("--firmware-version", default="ffffffff")
    parser.add_argument("--video", default="USB Video")
    parser.add_argument("--audio", default="USB Audio")
    parser.add_argument("--edid", default="")
    parser.add_argument("--serial", default="")
    args = parser.parse_args()

    # change hex values to lowercase
    args.vid = args.vid.lower()
    args.pid = args.pid.lower()
    args.firmware_version = args.firmware_version.lower()
    args.edid = args.edid.lower()

    # argument validation
    # TODO: write human readable error messages
    # TODO: check hex values are in range (0-f)
    assert len(args.vid) == 4
    assert len(args.pid) == 4
    assert len(args.firmware_version) == 8
    assert len(args.video) <= 15
    assert len(args.audio) <= 15
    assert len(args.serial) <= 30
    if args.edid:
        assert len(args.edid) == 512
        assert args.edid[:16] == "00ffffffffffff00"
        edid_hex = bytes.fromhex(args.edid)
        edid_checksum = checksum(edid_hex, mask=0xFF)
        assert edid_checksum == 0

    # data generation
    with open(args.output, "wb") as fp:
        fp.write(generate_firmware(args, CHIPS[args.chip]))


if __name__ == "__main__":
    main()
