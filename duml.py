"""DUML framing with DJI's actual checksums, validated against published frames.

Two checksums, and neither is a textbook CRC:

* Header byte [3]: a byte-wise table lookup, seed 0x77, no shift register --
  DJI's proprietary 256-entry table. Found by exhaustive search over 8-bit
  polynomials (poly 0x8C, reflected, init 0x77), which is exactly what this
  table implements.
* Trailer: CRC-16/X.25 reflected table (poly 0x8408) seeded with 0x3692 and
  *no* final xor. The usual "xorout 0xFFFF" convention is what made my earlier
  guesses fail.

Both are confirmed against every reference frame below.
"""

import io
import sys

if getattr(sys.stdout, 'encoding', '').lower() not in ('utf-8', 'utf8'):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

# DJI header checksum table (the "2A103" table from the reverse-engineering
# tooling). Validated here as CRC8 poly 0x8C, reflected, init 0x77.
HDR_TAB = [
    0x00, 0x5E, 0xBC, 0xE2, 0x61, 0x3F, 0xDD, 0x83, 0xC2, 0x9C, 0x7E, 0x20, 0xA3, 0xFD, 0x1F, 0x41,
    0x9D, 0xC3, 0x21, 0x7F, 0xFC, 0xA2, 0x40, 0x1E, 0x5F, 0x01, 0xE3, 0xBD, 0x3E, 0x60, 0x82, 0xDC,
    0x23, 0x7D, 0x9F, 0xC1, 0x42, 0x1C, 0xFE, 0xA0, 0xE1, 0xBF, 0x5D, 0x03, 0x80, 0xDE, 0x3C, 0x62,
    0xBE, 0xE0, 0x02, 0x5C, 0xDF, 0x81, 0x63, 0x3D, 0x7C, 0x22, 0xC0, 0x9E, 0x1D, 0x43, 0xA1, 0xFF,
    0x46, 0x18, 0xFA, 0xA4, 0x27, 0x79, 0x9B, 0xC5, 0x84, 0xDA, 0x38, 0x66, 0xE5, 0xBB, 0x59, 0x07,
    0xDB, 0x85, 0x67, 0x39, 0xBA, 0xE4, 0x06, 0x58, 0x19, 0x47, 0xA5, 0xFB, 0x78, 0x26, 0xC4, 0x9A,
    0x65, 0x3B, 0xD9, 0x87, 0x04, 0x5A, 0xB8, 0xE6, 0xA7, 0xF9, 0x1B, 0x45, 0xC6, 0x98, 0x7A, 0x24,
    0xF8, 0xA6, 0x44, 0x1A, 0x99, 0xC7, 0x25, 0x7B, 0x3A, 0x64, 0x86, 0xD8, 0x5B, 0x05, 0xE7, 0xB9,
    0x8C, 0xD2, 0x30, 0x6E, 0xED, 0xB3, 0x51, 0x0F, 0x4E, 0x10, 0xF2, 0xAC, 0x2F, 0x71, 0x93, 0xCD,
    0x11, 0x4F, 0xAD, 0xF3, 0x70, 0x2E, 0xCC, 0x92, 0xD3, 0x8D, 0x6F, 0x31, 0xB2, 0xEC, 0x0E, 0x50,
    0xAF, 0xF1, 0x13, 0x4D, 0xCE, 0x90, 0x72, 0x2C, 0x6D, 0x33, 0xD1, 0x8F, 0x0C, 0x52, 0xB0, 0xEE,
    0x32, 0x6C, 0x8E, 0xD0, 0x53, 0x0D, 0xEF, 0xB1, 0xF0, 0xAE, 0x4C, 0x12, 0x91, 0xCF, 0x2D, 0x73,
    0xCA, 0x94, 0x76, 0x28, 0xAB, 0xF5, 0x17, 0x49, 0x08, 0x56, 0xB4, 0xEA, 0x69, 0x37, 0xD5, 0x8B,
    0x57, 0x09, 0xEB, 0xB5, 0x36, 0x68, 0x8A, 0xD4, 0x95, 0xCB, 0x29, 0x77, 0xF4, 0xAA, 0x48, 0x16,
    0xE9, 0xB7, 0x55, 0x0B, 0x88, 0xD6, 0x34, 0x6A, 0x2B, 0x75, 0x97, 0xC9, 0x4A, 0x14, 0xF6, 0xA8,
    0x74, 0x2A, 0xC8, 0x96, 0x15, 0x4B, 0xA9, 0xF7, 0xB6, 0xE8, 0x0A, 0x54, 0xD7, 0x89, 0x6B, 0x35,
]

PACKET_TYPE_PROTO_CMD = 0x40   # app -> device, request
PACKET_TYPE_PROTO_CMD_SET = 0xC0  # device -> app, response

TARGET_APP = 0x02
TARGET_CAMERA = 0x01
TARGET_GIMBAL = 0x03
TARGET_DM368 = 0x08
TARGET_WIFI = 0x07
TARGET_SESSION_F0 = 0xF0
TARGET_SESSION_1C = 0x1C
TARGET_SYSTEM = 0x28


def header_checksum(seed, data):
    c = seed & 0xFF
    for b in data:
        c = HDR_TAB[(b ^ c) & 0xFF]
    return c


def frame_checksum(data, seed=0x3692):
    """CRC-16/X.25 (poly 0x8408 reflected) with DJI's 0x3692 seed.

    The seed is the initial register value, not a final xor -- that is the detail
    that made every earlier guess miss. Validated against all reference frames.
    """
    crc = seed & 0xFFFF
    for b in data:
        crc ^= b
        for _ in range(8):
            crc = (crc >> 1) ^ 0x8408 if crc & 1 else crc >> 1
    return crc & 0xFFFF


def build(sender, receiver, seq, cmd_type, cmd_set, cmd_id, payload=b''):
    """Assemble a complete DUML frame."""
    length = 11 + len(payload) + 2
    head = bytes([0x55, length & 0xFF, 0x04])
    crc8 = header_checksum(0x77, head)
    body = (head + bytes([crc8, sender & 0xFF, receiver & 0xFF])
            + (seq & 0xFFFF).to_bytes(2, 'little')
            + bytes([cmd_type & 0xFF, cmd_set & 0xFF, cmd_id & 0xFF])
            + payload)
    if len(body) != length - 2:
        raise ValueError('length mismatch %d vs %d' % (len(body), length - 2))
    crc16 = frame_checksum(body)
    return body + crc16.to_bytes(2, 'little')


def parse(frame):
    if len(frame) < 13 or frame[0] != 0x55:
        raise ValueError('not a DUML frame')
    if frame[1] != len(frame):
        raise ValueError('declared length %d != actual %d' % (frame[1], len(frame)))
    if header_checksum(0x77, frame[0:3]) != frame[3]:
        raise ValueError('header checksum mismatch')
    return {
        'len': frame[1], 'version': frame[2], 'sender': frame[4],
        'receiver': frame[5], 'seq': int.from_bytes(frame[6:8], 'little'),
        'cmd_type': frame[8], 'cmd_set': frame[9], 'cmd_id': frame[10],
        'payload': frame[11:-2].hex(), 'crc16': frame[-2:].hex(),
        'crc16_ok': frame_checksum(frame[:-2]) == int.from_bytes(frame[-2:], 'little'),
    }


SAMPLES = {
    'start_recording': '550e046602010204400202014e61',
    'stop_recording': '550e04660201020440020200c770',
    'get_param_0009': '551104920201020440028e00010900778d',
    'register_0088': '551c041b022800a0400088170008237b41505000000000000002d9e6',
    'gimbal_init_03da': '551204c7020300a04003da05ffffffff4490',
    'cam_status_query_02a0': '550d0433020100a00002a0f5c3',
    'set_efov_wide': '551304030201020440028e01010900010189d4',
    'get_version_0000': '550d0433024800a0800000017e',
}


def self_test():
    bad = 0
    for name, h in SAMPLES.items():
        f = bytes.fromhex(h)
        hc = header_checksum(0x77, f[0:3])
        fc = frame_checksum(f[:-2])
        want = int.from_bytes(f[-2:], 'little')
        ok = hc == f[3] and fc == want
        bad += 0 if ok else 1
        print('%-22s hdr %02x/%02x  frame %04x/%04x  %s' % (
            name, hc, f[3], fc, want, 'OK' if ok else 'MISMATCH'))
    return bad


if __name__ == '__main__':
    print('--- validate against reference frames ---')
    bad = self_test()
    print('\nmismatches: %d' % bad)

    print('\n--- regenerate the reference frames from their fields ---')
    cases = [
        ('start_recording', (0x02, 0x01, 0x0402, 0x40, 0x02, 0x02, '01')),
        ('stop_recording', (0x02, 0x01, 0x0402, 0x40, 0x02, 0x02, '00')),
        ('get_param_0009', (0x02, 0x01, 0x0402, 0x40, 0x02, 0x8e, '00010900')),
        ('gimbal_init_03da', (0x02, 0x03, 0xa000, 0x40, 0x03, 0xda, '05ffffffff')),
        ('register_0088', (0x02, 0x28, 0xa000, 0x40, 0x00, 0x88,
                           '170008237b41505000000000000002')),
    ]
    for name, (s, r, seq, ct, cs, ci, pl) in cases:
        mine = build(s, r, seq, ct, cs, ci, bytes.fromhex(pl))
        ref = bytes.fromhex(SAMPLES[name])
        print('%-22s %s' % (name, 'MATCH' if mine == ref else
                            'DIFFERS mine=%s ref=%s' % (mine.hex(), ref.hex())))
