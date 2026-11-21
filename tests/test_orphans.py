"""If SphereScan is killed outright, Spirula must not be left running. Runs on Linux, macOS and Windows."""
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).parent.parent

PARENT = r'''
import subprocess, sys, time
sys.path.insert(0, sys.argv[1])
from spherescan.util import spawn_engine
child = "import os,sys,time; open(sys.argv[1],'w').write(str(os.getpid())); time.sleep(120)"
p = spawn_engine([sys.executable, "-c", child, sys.argv[2]], stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
time.sleep(120)
'''


def alive(pid: int) -> bool:
    if os.name == "nt":
        import ctypes
        k32 = ctypes.windll.kernel32
        h = k32.OpenProcess(0x1000, False, pid)           # PROCESS_QUERY_LIMITED_INFORMATION
        if not h:
            return False
        code = ctypes.c_ulong()
        k32.GetExitCodeProcess(h, ctypes.byref(code))
        k32.CloseHandle(h)
        return code.value == 259                           # STILL_ACTIVE
    out = subprocess.run(["ps", "-p", str(pid), "-o", "stat="], capture_output=True, text=True).stdout.strip()
    return bool(out) and not out.startswith("Z")


class Orphans(unittest.TestCase):
    def test_engine_dies_when_sphereScan_is_killed(self):
        with tempfile.TemporaryDirectory() as t:
            pidfile = Path(t) / "child.pid"
            parent = subprocess.Popen([sys.executable, "-c", PARENT, str(ROOT), str(pidfile)])
            try:
                for _ in range(100):
                    if pidfile.exists() and pidfile.read_text().strip():
                        break
                    time.sleep(0.1)
                self.assertTrue(pidfile.exists(), "the engine never started")
                child = int(pidfile.read_text())
                self.assertTrue(alive(child))
                parent.kill()                                  # SIGKILL / TerminateProcess: no chance to clean up
                parent.wait()
                for _ in range(100):
                    if not alive(child):
                        break
                    time.sleep(0.1)
                self.assertFalse(alive(child), "Spirula was left running after SphereScan was killed")
            finally:
                if parent.poll() is None:
                    parent.kill()


if __name__ == "__main__":
    unittest.main()
