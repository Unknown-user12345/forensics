def compute_game_reward():
    # Arrays from the Java code
    segA = [208, 65, 233, 187, 99, 200, 17, 208, 80, 49, 1, 2]
    segB = [185, 120, 59, 34, 147, 114, 250, 161, 89, 120, 51, 67]
    segC = [158, 255, 15, 237, 102, 61, 141, 172, 124, 85, 13, 183]

    # Mask array (TypedValues.TYPE_TARGET resolved to 101)
    mask = [75, 101, 121, 33, 57, 90, 114, 81]

    # Step 1: XOR each segment with its constant and combine
    raw = bytearray()
    for val in segA:
        raw.append(val ^ 17)
    for val in segB:
        raw.append(val ^ 51)
    for val in segC:
        raw.append(val ^ 85)

    # Step 2: Bit rotation left by 5 and XOR with cyclic mask
    out = bytearray()
    for i, v in enumerate(raw):
        v = v & 0xFF
        # rotate left by 5: (v << 5) | (v >>> 3) masked to 8 bits
        u = ((v << 5) | (v >> 3)) & 0xFF
        m = mask[i % len(mask)]
        out.append(m ^ u)

    # Decode as UTF-8
    return out.decode('utf-8')

if __name__ == "__main__":
    flag = compute_game_reward()
    print("Flag:", flag)
