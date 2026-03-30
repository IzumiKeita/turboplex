"""Windows-specific process management using Job Objects."""

import subprocess
import sys


def kill_tree_windows(pid: int) -> None:
    """Kill process tree on Windows using taskkill /T /F."""
    if sys.platform != "win32":
        return
    try:
        subprocess.run(
            ["taskkill", "/T", "/F", "/PID", str(pid)],
            capture_output=True,
            timeout=5.0,
        )
    except Exception:
        pass


def win_assign_job_object(p: subprocess.Popen) -> None:
    """Assign process to a Job Object for automatic tree kill on Windows."""
    if sys.platform != "win32" or p.pid is None:
        return
    try:
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.windll.kernel32
        JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x2000

        job = kernel32.CreateJobObjectW(None, None)
        if not job:
            return

        class JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
            _fields_ = [
                ("PerProcessUserTimeLimit", wintypes.LARGE_INTEGER),
                ("PerJobUserTimeLimit", wintypes.LARGE_INTEGER),
                ("LimitFlags", wintypes.DWORD),
                ("MinimumWorkingSetSize", ctypes.c_size_t),
                ("MaximumWorkingSetSize", ctypes.c_size_t),
                ("ActiveProcessLimit", wintypes.DWORD),
                ("Affinity", ctypes.c_void_p),
                ("PriorityClass", wintypes.DWORD),
                ("SchedulingClass", wintypes.DWORD),
            ]

        class IO_COUNTERS(ctypes.Structure):
            _fields_ = [
                ("ReadOperationCount", wintypes.ULARGE_INTEGER),
                ("WriteOperationCount", wintypes.ULARGE_INTEGER),
                ("OtherOperationCount", wintypes.ULARGE_INTEGER),
                ("ReadTransferCount", wintypes.ULARGE_INTEGER),
                ("WriteTransferCount", wintypes.ULARGE_INTEGER),
                ("OtherTransferCount", wintypes.ULARGE_INTEGER),
            ]

        class JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
            _fields_ = [
                ("BasicLimitInformation", JOBOBJECT_BASIC_LIMIT_INFORMATION),
                ("IoInfo", IO_COUNTERS),
                ("ProcessMemoryLimit", ctypes.c_size_t),
                ("JobMemoryLimit", ctypes.c_size_t),
                ("PeakProcessMemoryUsed", ctypes.c_size_t),
                ("PeakJobMemoryUsed", ctypes.c_size_t),
            ]

        info = JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
        info.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE

        kernel32.SetInformationJobObject(
            job,
            9,  # JobObjectExtendedLimitInformation
            ctypes.byref(info),
            ctypes.sizeof(info),
        )

        PROCESS_ALL_ACCESS = 0x1F0FFF
        proc = kernel32.OpenProcess(PROCESS_ALL_ACCESS, False, p.pid)
        if proc:
            kernel32.AssignProcessToJobObject(job, proc)
            kernel32.CloseHandle(proc)

        p._tpx_job_handle = job  # Store handle for later cleanup

    except Exception:
        pass


def win_close_job_object(p: subprocess.Popen) -> None:
    """Close the Job Object, killing all associated processes."""
    if sys.platform != "win32":
        return
    job = getattr(p, "_tpx_job_handle", None)
    if job:
        try:
            import ctypes

            ctypes.windll.kernel32.CloseHandle(job)
            p._tpx_job_handle = None
        except Exception:
            pass
