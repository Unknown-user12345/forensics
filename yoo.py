import subprocess, sys
# USB HID keyboard codes mapping (lowercase)

if len(sys.argv) != 2:
    print(f"Usage: {sys.argv[0]} <pcap/pcapng>")
    sys.exit(1)

pcap = sys.argv[1]


hid_map_lower = {
    0x04: 'a', 0x05: 'b', 0x06: 'c', 0x07: 'd', 0x08: 'e', 0x09: 'f',
    0x0a: 'g', 0x0b: 'h', 0x0c: 'i', 0x0d: 'j', 0x0e: 'k', 0x0f: 'l',
    0x10: 'm', 0x11: 'n', 0x12: 'o', 0x13: 'p', 0x14: 'q', 0x15: 'r',
    0x16: 's', 0x17: 't', 0x18: 'u', 0x19: 'v', 0x1a: 'w', 0x1b: 'x',
    0x1c: 'y', 0x1d: 'z',
    0x1e: '1', 0x1f: '2', 0x20: '3', 0x21: '4', 0x22: '5', 0x23: '6',
    0x24: '7', 0x25: '8', 0x26: '9', 0x27: '0',
    0x28: '\n', 0x2c: ' ', 0x2d: '-', 0x2e: '=', 0x2f: '[', 0x30: ']',
    0x31: '\\', 0x33: ';', 0x34: "'", 0x35: '`', 0x36: ',', 0x37: '.',
    0x38: '/', 0x39: 'CAPS', 0x4f: 'RIGHT', 0x50: 'LEFT', 0x51: 'DOWN', 0x52: 'UP',
    0x2a: 'BS', 0x2b: 'TAB'
}
# Upper case
hid_map_upper = {
    0x04: 'A', 0x05: 'B', 0x06: 'C', 0x07: 'D', 0x08: 'E', 0x09: 'F',
    0x0a: 'G', 0x0b: 'H', 0x0c: 'I', 0x0d: 'J', 0x0e: 'K', 0x0f: 'L',
    0x10: 'M', 0x11: 'N', 0x12: 'O', 0x13: 'P', 0x14: 'Q', 0x15: 'R',
    0x16: 'S', 0x17: 'T', 0x18: 'U', 0x19: 'V', 0x1a: 'W', 0x1b: 'X',
    0x1c: 'Y', 0x1d: 'Z',
    0x1e: '!', 0x1f: '@', 0x20: '#', 0x21: '$', 0x22: '%', 0x23: '^',
    0x24: '&', 0x25: '*', 0x26: '(', 0x27: ')',
    0x28: '\n', 0x2c: ' ', 0x2d: '_', 0x2e: '+', 0x2f: '{', 0x30: '}',
    0x31: '|', 0x33: ':', 0x34: '"', 0x35: '~', 0x36: '<', 0x37: '>',
    0x38: '?', 0x39: 'CAPS'
}
# Get the USB data
result = subprocess.run(
    ['tshark', '-r', pcap,
     '-Y', 'usb.capdata', 
     '-T', 'fields', '-e', 'usb.capdata'],
    capture_output=True, text=True
)

lines = [l.strip() for l in result.stdout.split('\n') if l.strip() and not l.startswith('Running') and not l.startswith('**') and not l.startswith('Reading')]
# Also get the first byte (modifier keys) for shift detection
result2 = subprocess.run(
    ['tshark', '-r', pcap, 
     '-Y', 'usb.capdata', 
     '-T', 'fields', '-e', 'usb.capdata'],
    capture_output=True, text=True
)
all_lines = [l.strip() for l in result2.stdout.split('\n') if l.strip() and not l.startswith('Running') and not l.startswith('**') and not l.startswith('Reading')]
keystrokes = []
for line in lines:
    if len(line) >= 12:
        modifier_hex = line[0:2]  # First byte is modifier keys
        key_hex = line[4:6]  # Third byte is key code
        try:
            modifier = int(modifier_hex, 16)
            key_code = int(key_hex, 16)
        except ValueError:
            continue
        
        if key_code > 0:
            if modifier & 0x22:  # Left or Right Shift
                char = hid_map_upper.get(key_code, f'[0x{key_code:02x}]')
            else:
                char = hid_map_lower.get(key_code, f'[0x{key_code:02x}]')
            if char not in ['CAPS', 'RIGHT', 'LEFT', 'DOWN', 'UP', 'BS', 'TAB']:
                keystrokes.append(char)
message = ''.join(keystrokes)
print(f"Decoded message (with shift):")
print(message)
