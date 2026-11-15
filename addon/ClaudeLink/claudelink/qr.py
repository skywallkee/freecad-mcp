"""Minimal QR code encoder (ISO/IEC 18004): byte mode, error correction M, versions 1-6 (up to 106 bytes).
Pure Python so the addon needs no extra packages. qr_matrix(text) -> list of rows of bools (True = dark),
without the quiet zone."""

# version: (data codewords per block, number of blocks, ec codewords per block)
_BLOCKS_M = {1: (16, 1, 10), 2: (28, 1, 16), 3: (44, 1, 26), 4: (32, 2, 18), 5: (43, 2, 24), 6: (27, 4, 16)}
_ALIGN = {1: [], 2: [6, 18], 3: [6, 22], 4: [6, 26], 5: [6, 30], 6: [6, 34]}
_REMAINDER = {1: 0, 2: 7, 3: 7, 4: 7, 5: 7, 6: 7}


def _gf_mul(x, y):
    z = 0
    for i in reversed(range(8)):
        z = (z << 1) ^ ((z >> 7) * 0x11D)
        z ^= ((y >> i) & 1) * x
    return z


def _rs_divisor(degree):
    res = [0] * (degree - 1) + [1]
    root = 1
    for _ in range(degree):
        for j in range(degree):
            res[j] = _gf_mul(res[j], root)
            if j + 1 < degree:
                res[j] ^= res[j + 1]
        root = _gf_mul(root, 0x02)
    return res


def _rs_remainder(data, divisor):
    res = [0] * len(divisor)
    for b in data:
        factor = b ^ res.pop(0)
        res.append(0)
        for i, c in enumerate(divisor):
            res[i] ^= _gf_mul(c, factor)
    return res


def _codewords(data, ver):
    nd, nb, ne = _BLOCKS_M[ver]
    cap = nd * nb
    bits = []

    def put(v, n):
        bits.extend((v >> i) & 1 for i in reversed(range(n)))
    put(0b0100, 4)
    put(len(data), 8)
    for b in data:
        put(b, 8)
    put(0, min(4, cap * 8 - len(bits)))
    while len(bits) % 8:
        bits.append(0)
    cw = [int("".join(map(str, bits[i:i + 8])), 2) for i in range(0, len(bits), 8)]
    pad = 0xEC
    while len(cw) < cap:
        cw.append(pad)
        pad ^= 0xEC ^ 0x11
    div = _rs_divisor(ne)
    blocks = [cw[k * nd:(k + 1) * nd] for k in range(nb)]
    ecs = [_rs_remainder(b, div) for b in blocks]
    out = [b[i] for i in range(nd) for b in blocks] + [e[i] for i in range(ne) for e in ecs]
    return out


def _mask_fn(m):
    return [lambda x, y: (x + y) % 2 == 0, lambda x, y: y % 2 == 0, lambda x, y: x % 3 == 0,
            lambda x, y: (x + y) % 3 == 0, lambda x, y: (x // 3 + y // 2) % 2 == 0,
            lambda x, y: x * y % 2 + x * y % 3 == 0, lambda x, y: (x * y % 2 + x * y % 3) % 2 == 0,
            lambda x, y: ((x + y) % 2 + x * y % 3) % 2 == 0][m]


def _format_bits(mask):
    data = (0 << 3) | mask          # error correction level M = 0b00
    rem = data
    for _ in range(10):
        rem = (rem << 1) ^ ((rem >> 9) * 0x537)
    return ((data << 10) | rem) ^ 0x5412


def _penalty(mod, n):
    p = 0
    for rows in (mod, [list(c) for c in zip(*mod)]):
        for row in rows:
            run, prev = 0, None
            for v in row:
                if v == prev:
                    run += 1
                else:
                    if run >= 5:
                        p += 3 + run - 5
                    run, prev = 1, v
            if run >= 5:
                p += 3 + run - 5
            s = "".join("1" if v else "0" for v in row)
            p += 40 * (s.count("10111010000") + s.count("00001011101"))
    for y in range(n - 1):
        for x in range(n - 1):
            if mod[y][x] == mod[y][x + 1] == mod[y + 1][x] == mod[y + 1][x + 1]:
                p += 3
    dark = sum(sum(r) for r in mod)
    p += 10 * (abs(dark * 20 - n * n * 10) // (n * n))
    return p


def qr_matrix(text):
    data = text.encode("utf-8") if isinstance(text, str) else bytes(text)
    ver = next((v for v in range(1, 7) if len(data) <= _BLOCKS_M[v][0] * _BLOCKS_M[v][1] - 2), None)
    if ver is None:
        raise ValueError("QR text too long (max 106 bytes for this encoder)")
    n = 17 + 4 * ver
    mod = [[False] * n for _ in range(n)]
    fn = [[False] * n for _ in range(n)]

    def setf(x, y, v):
        mod[y][x] = v
        fn[y][x] = True
    for i in range(n):                      # timing patterns
        setf(6, i, i % 2 == 0)
        setf(i, 6, i % 2 == 0)
    for cx, cy in ((3, 3), (n - 4, 3), (3, n - 4)):   # finders + separators
        for dy in range(-4, 5):
            for dx in range(-4, 5):
                x, y = cx + dx, cy + dy
                if 0 <= x < n and 0 <= y < n:
                    d = max(abs(dx), abs(dy))
                    setf(x, y, d != 2 and d != 4)
    al = _ALIGN[ver]
    for i, ax in enumerate(al):
        for j, ay in enumerate(al):
            if (i == 0 and j == 0) or (i == 0 and j == len(al) - 1) or (i == len(al) - 1 and j == 0):
                continue
            for dy in range(-2, 3):
                for dx in range(-2, 3):
                    setf(ax + dx, ay + dy, max(abs(dx), abs(dy)) != 1)
    # reserve format areas (written per mask below)
    for i in range(9):
        fn[8][i] = fn[i][8] = True
    for i in range(8):
        fn[8][n - 1 - i] = fn[n - 1 - i][8] = True
    cw = _codewords(data, ver)
    bits = [(b >> (7 - k)) & 1 for b in cw for k in range(8)] + [0] * _REMAINDER[ver]
    i = 0
    right = n - 1
    while right >= 1:
        if right == 6:
            right = 5
        for vert in range(n):
            for j in range(2):
                x = right - j
                upward = ((right + 1) & 2) == 0
                y = n - 1 - vert if upward else vert
                if not fn[y][x] and i < len(bits):
                    mod[y][x] = bool(bits[i])
                    i += 1
        right -= 2
    best = None
    for m in range(8):
        f = _mask_fn(m)
        t = [[(mod[y][x] ^ f(x, y)) if not fn[y][x] else mod[y][x] for x in range(n)] for y in range(n)]
        fb = _format_bits(m)
        g = lambda k: bool((fb >> k) & 1)
        for k in range(6):
            t[k][8] = g(k)
        t[7][8] = g(6)
        t[8][8] = g(7)
        t[8][7] = g(8)
        for k in range(9, 15):
            t[8][14 - k] = g(k)
        for k in range(8):
            t[8][n - 1 - k] = g(k)
        for k in range(8, 15):
            t[n - 15 + k][8] = g(k)
        t[n - 8][8] = True                 # dark module
        sc = _penalty(t, n)
        if best is None or sc < best[0]:
            best = (sc, t)
    return best[1]
