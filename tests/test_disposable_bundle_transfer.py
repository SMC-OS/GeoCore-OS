import importlib.util
import os
from pathlib import Path

spec = importlib.util.spec_from_file_location("bundle_transfer", Path(__file__).resolve().parents[1] / "scripts/disposable/bundle_transfer.py")
bt = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bt)

DATA = os.urandom(20000)


def lines(data=DATA):
    return list(bt.encode(data))


def test_round_trip_with_log_noise_and_duplicates():
    noisy = "\n".join("2026-11-15T10:00:00Z deploy " + ln for ln in lines() + lines()[:5])
    out, problems = bt.decode(noisy)
    assert out == DATA and not problems


def test_missing_chunk_fails():
    ls = lines()
    del ls[3]
    out, problems = bt.decode("\n".join(ls))
    assert out is None and any("missing" in p for p in problems)


def test_corrupted_chunk_fails():
    ls = lines()
    ls[2] = ls[2][:-8] + "AAAAAAAA"
    out, problems = bt.decode("\n".join(ls))
    assert out is None and problems


def test_missing_trailer_fails():
    out, problems = bt.decode("\n".join(lines()[:-1]))
    assert out is None and any("trailer" in p for p in problems)


def test_trailer_for_other_data_fails():
    ls = lines()
    other = lines(os.urandom(20000))
    out, problems = bt.decode("\n".join(ls[:-1] + [other[-1]]))
    assert out is None and problems


def test_empty_log_fails():
    assert bt.decode("nothing")[0] is None
