"""Chunked, checksummed transfer of a recovery bundle through a text channel (deployment logs / service variables).

encode:  bundle_transfer.py encode <file> > lines.txt           -> BUNDLE|<seq>|<total>|<sha8>|<base64> ... then BUNDLE_DONE|<sha256>|<bytes>
decode:  bundle_transfer.py decode <log-or-lines.txt> <out-file>  -> verifies every chunk digest, completeness and the whole-file SHA-256
The payload carries synthetic data only. Nothing here prints a credential. A missing, duplicated-with-different-content, reordered or
corrupted chunk is detected and fails the decode (exit 1).
"""
import base64
import hashlib
import re
import sys

CHUNK = 1500  # raw bytes per line before base64 (about 2 KB of text)
LINE = re.compile(r"BUNDLE\|(\d+)\|(\d+)\|([0-9a-f]{8})\|([A-Za-z0-9+/=]+)")
DONE = re.compile(r"BUNDLE_DONE\|([0-9a-f]{64})\|(\d+)")


def encode(data: bytes):
    total = (len(data) + CHUNK - 1) // CHUNK
    for i in range(total):
        piece = data[i * CHUNK:(i + 1) * CHUNK]
        yield f"BUNDLE|{i}|{total}|{hashlib.sha256(piece).hexdigest()[:8]}|{base64.b64encode(piece).decode()}"
    yield f"BUNDLE_DONE|{hashlib.sha256(data).hexdigest()}|{len(data)}"


def decode(text: str):
    """Return (data, problems). Lines may be embedded in other log text (timestamps, prefixes)."""
    chunks, problems, total_seen, done = {}, [], set(), None
    for m in LINE.finditer(text):
        seq, total, digest, payload = int(m.group(1)), int(m.group(2)), m.group(3), m.group(4)
        piece = base64.b64decode(payload)
        if hashlib.sha256(piece).hexdigest()[:8] != digest:
            problems.append(f"chunk {seq} digest mismatch")
            continue
        total_seen.add(total)
        if seq in chunks and chunks[seq] != piece:
            problems.append(f"chunk {seq} appears twice with different content")
        chunks[seq] = piece
    d = DONE.search(text)
    if d:
        done = (d.group(1), int(d.group(2)))
    if len(total_seen) != 1:
        problems.append(f"inconsistent or missing chunk totals: {sorted(total_seen)}")
        return None, problems
    total = next(iter(total_seen))
    missing = [i for i in range(total) if i not in chunks]
    if missing:
        problems.append(f"{len(missing)} chunk(s) missing, first {missing[:5]}")
    if done is None:
        problems.append("BUNDLE_DONE trailer missing")
    if problems:
        return None, problems
    data = b"".join(chunks[i] for i in range(total))
    if hashlib.sha256(data).hexdigest() != done[0] or len(data) != done[1]:
        return None, ["whole-bundle SHA-256 or size does not match the trailer"]
    return data, []


def main(argv):
    if len(argv) >= 3 and argv[1] == "encode":
        for line in encode(open(argv[2], "rb").read()):
            print(line)
        return 0
    if len(argv) >= 4 and argv[1] == "decode":
        data, problems = decode(open(argv[2], errors="replace").read())
        if problems:
            print("FAIL " + "; ".join(problems))
            return 1
        open(argv[3], "wb").write(data)
        print(f"PASS bundle verified bytes={len(data)} sha256={hashlib.sha256(data).hexdigest()}")
        return 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
