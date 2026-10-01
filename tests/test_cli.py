import os
import subprocess
import sys

from usbfloppymanager.cli import main
from usbfloppymanager.core import Device, Slot
from conftest import make_tree


def test_full_cli_flow(formatted, tmp_path, capsys):
    src = make_tree(tmp_path / "s", {"a.txt": b"hola", "d/b.txt": b"adios"})
    assert main(["format", formatted, "1", "--label", "UNO", "-y"]) == 0
    assert main(["label", formatted, "1", "Dos"]) == 0
    assert main(["put", formatted, "1", os.path.join(src, "a.txt"), "--dest", "x"]) == 0
    assert main(["mkdir", formatted, "1", "nueva carpeta", "--short-names"]) == 0
    capsys.readouterr()
    assert main(["ls", formatted, "1"]) == 0
    out = capsys.readouterr().out
    assert "/X/A.TXT" in out and "NUEVAC~1" in out
    assert main(["list", formatted]) == 0
    assert "DOS" in capsys.readouterr().out
    got = tmp_path / "got"
    assert main(["get", formatted, "1", "/x/a.txt", "-o", str(got)]) == 0
    assert got.read_bytes() == b"hola"
    assert main(["rm", formatted, "1", "/x"]) == 0
    assert main(["export", formatted, "1", str(tmp_path / "e.img")]) == 0
    assert main(["import", formatted, "2", str(tmp_path / "e.img"), "-y"]) == 0
    assert main(["backup", formatted, str(tmp_path / "bk.img")]) == 0
    assert main(["restore", formatted, str(tmp_path / "bk.img"), "-y"]) == 0


def test_batch_cli(formatted, tmp_path, capsys):
    src = make_tree(tmp_path / "s", {"a.txt": b"hola", "d/b.txt": b"adios", "long name here.txt": b"x"})
    assert main(["batch", src, "0", "3", "--dry-run"]) == 0
    out = capsys.readouterr()
    assert "D/B.TXT" in out.out and "no son 8.3" in out.err
    assert main(["batch", "-d", formatted, src, "2", "3", "-y", "--short-names",
                 "--contents-dir", str(tmp_path)]) == 0
    assert (tmp_path / "Contents 002.txt").exists()
    with Device(formatted) as d, Slot(d, 2) as s:
        assert s.read("/A.TXT") == b"hola"


def test_errors_return_1(tmp_path, capsys):
    assert main(["list", str(tmp_path / "noexiste.img")]) == 1
    assert "Error" in capsys.readouterr().err


def test_refuses_non_regular_target(capsys):
    assert main(["format", "/dev/null", "0", "-y", "--slots", "2"]) == 1


def test_broken_pipe(formatted):
    p = subprocess.run(
        f"{sys.executable} -m usbfloppymanager.cli list {formatted} -a | head -n 1",
        shell=True, capture_output=True, text=True, cwd=os.path.dirname(os.path.dirname(__file__)))
    assert "BrokenPipe" not in p.stderr and "Traceback" not in p.stderr


def test_broken_pipe_handled_in_main(monkeypatch, formatted):
    import usbfloppymanager.cli as cli

    def boom(a):
        raise BrokenPipeError()
    monkeypatch.setattr(cli, "cmd_list", boom)
    assert cli.main(["list", formatted]) == 0
