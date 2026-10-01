import json

import pytest

from usbfloppymanager import devices
from usbfloppymanager.core import Device, GotekError
from usbfloppymanager.devices import (TargetInfo, evaluate_target, is_windows_raw_path, parse_mounts,
                                      parse_windows_disks, windows_disk_number)
from usbfloppymanager import winraw


def test_system_disk_always_rejected():
    for force in (False, True):
        with pytest.raises(GotekError, match="sistema"):
            evaluate_target(TargetInfo("/dev/sda", system=True), force)


def test_non_removable_needs_force():
    t = TargetInfo("/dev/sdb", removable=False)
    with pytest.raises(GotekError, match="extraíble"):
        evaluate_target(t, False)
    evaluate_target(t, True)


def test_partition_and_mounted_need_force():
    with pytest.raises(GotekError, match="partición"):
        evaluate_target(TargetInfo("/dev/sdb1", is_partition=True), False)
    with pytest.raises(GotekError, match="montados"):
        evaluate_target(TargetInfo("/dev/sdb", mounted=["/media/x"]), False)
    evaluate_target(TargetInfo("/dev/sdb", mounted=["/media/x"]), True)


def test_ok_removable():
    evaluate_target(TargetInfo("/dev/sdb"), False)


def test_parse_mounts():
    text = "/dev/sda2 / ext4 rw 0 0\n/dev/sdb1 /media/mi\\040usb vfat rw 0 0\nproc /proc proc rw 0 0\n"
    assert parse_mounts(text)[:2] == [("/dev/sda2", "/"), ("/dev/sdb1", "/media/mi usb")]


def test_image_files_always_ok(img):
    devices.check_writable_target(img, False)
    with Device(img, writable=True):
        pass


def test_windows_paths():
    assert is_windows_raw_path(r"\\.\PhysicalDrive3") and windows_disk_number(r"\\.\PHYSICALDRIVE12") == 12
    assert not is_windows_raw_path("/dev/sdb") and windows_disk_number("x.img") is None


def test_parse_windows_disks():
    raw = json.dumps([
        {"DeviceID": r"\\.\PHYSICALDRIVE0", "Index": 0, "Size": 500, "Model": "NVMe", "InterfaceType": "SCSI",
         "MediaType": "Fixed hard disk media"},
        {"DeviceID": r"\\.\PHYSICALDRIVE2", "Index": 2, "Size": 8000, "Model": "USB Stick", "InterfaceType": "USB",
         "MediaType": "Removable Media"}])
    devs = parse_windows_disks(raw, system_disk=0)
    assert [d.path for d in devs] == [r"\\.\PHYSICALDRIVE2"]
    allv = parse_windows_disks(raw, system_disk=0, include_fixed=True)
    assert [d.system for d in allv] == [True, False]
    single = parse_windows_disks(json.dumps(json.loads(raw)[1]))
    assert len(single) == 1 and parse_windows_disks("") == []


def test_winraw_pure():
    import struct
    assert winraw.parse_length_info(struct.pack("<q", 8_000_000_000)) == 8_000_000_000
    winraw.check_aligned(3 * 1536 * 1024, 1440 * 1024)
    with pytest.raises(GotekError):
        winraw.check_aligned(100, 512)


def test_geometry_is_4k_aligned():
    from usbfloppymanager.core import SLOT_SIZE, STRIDE
    assert STRIDE % winraw.ALIGN == 0 and SLOT_SIZE % winraw.ALIGN == 0


def test_list_usb_devices_runs():
    assert isinstance(devices.list_usb_devices(), list)
    assert isinstance(devices.list_usb_devices(include_fixed=True), list)


def test_permission_hint_mentions_how_to_fix():
    assert "sudo" in devices.permission_hint("/dev/sdb") or "administrador" in devices.permission_hint("x")


def test_unmount_noop_for_image_files(img):
    assert devices.unmount_target(img) == []
    with Device(img, writable=True, unmount=True):
        pass


def test_unmount_refuses_system_disk(monkeypatch):
    monkeypatch.setattr(devices.stat, "S_ISBLK", lambda m: True)
    monkeypatch.setattr(devices.sys, "platform", "linux")
    monkeypatch.setattr(devices, "_linux_disk_name", lambda n: "sda")
    monkeypatch.setattr(devices, "linux_mount_pairs", lambda d, t=None: [("/dev/sda1", "/")])
    with pytest.raises(GotekError, match="sistema"):
        devices.unmount_target("/dev/null")


def test_unmount_runs_udisks_then_umount(monkeypatch):
    calls = []

    class R:
        def __init__(self, rc): self.returncode, self.stderr, self.stdout = rc, "boom", ""

    def fake_run(cmd, **k):
        calls.append(cmd[0])
        return R(1 if cmd[0] == "udisksctl" else 0)   # udisks falla -> se prueba umount
    monkeypatch.setattr(devices.stat, "S_ISBLK", lambda m: True)
    monkeypatch.setattr(devices.sys, "platform", "linux")
    monkeypatch.setattr(devices, "_linux_disk_name", lambda n: "sdz")
    monkeypatch.setattr(devices, "linux_mount_pairs", lambda d, t=None: [("/dev/sdz", "/media/x/FD000")])
    monkeypatch.setattr(devices.subprocess, "run", fake_run)
    assert devices.unmount_target("/dev/null") == ["/media/x/FD000"]
    assert calls == ["udisksctl", "umount"]


def test_unmount_failure_reports(monkeypatch):
    class R:
        returncode, stderr, stdout = 1, "target is busy", ""
    monkeypatch.setattr(devices.stat, "S_ISBLK", lambda m: True)
    monkeypatch.setattr(devices.sys, "platform", "linux")
    monkeypatch.setattr(devices, "_linux_disk_name", lambda n: "sdz")
    monkeypatch.setattr(devices, "linux_mount_pairs", lambda d, t=None: [("/dev/sdz", "/media/x")])
    monkeypatch.setattr(devices.subprocess, "run", lambda *a, **k: R())
    with pytest.raises(GotekError, match="busy"):
        devices.unmount_target("/dev/null")
