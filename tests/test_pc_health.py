import dataclasses

import pytest

from waifu.pc_health import Drive, GpuStatus, PcSnapshot


def test_a_snapshot_holds_its_parts_and_cannot_be_changed():
    gpu = GpuStatus("NVIDIA GeForce RTX 5060 Ti", 61, 34, 5.2, 15.9, 88.4)
    snapshot = PcSnapshot(12.5, 18.3, 31.9, (Drive("C:", 24.1, 465.1),), gpu, 7200)

    assert snapshot.drives[0].letter == "C:"
    assert snapshot.gpu is gpu
    with pytest.raises(dataclasses.FrozenInstanceError):
        snapshot.cpu_percent = 99.0  # type: ignore[misc]


def test_a_snapshot_allows_every_part_to_be_unreadable():
    snapshot = PcSnapshot(None, None, None, (), None, None)

    assert (snapshot.cpu_percent, snapshot.gpu, snapshot.uptime_s) == (None, None, None)
