"""Owned process groups; Windows workers enter a kill-on-close Job before resume.

No process-name matching, taskkill, or termination of recovered journal PIDs.
Windows Job handles are non-inheritable: runtime death closes the ownership handle.
"""
import asyncio
import ctypes
import os
import signal
from ctypes import wintypes as w


if os.name == 'nt':
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    for name, args, result in [
        ('CreateJobObjectW', [w.LPVOID, w.LPCWSTR], w.HANDLE),
        ('OpenProcess', [w.DWORD, w.BOOL, w.DWORD], w.HANDLE),
        ('OpenThread', [w.DWORD, w.BOOL, w.DWORD], w.HANDLE),
        ('CloseHandle', [w.HANDLE], w.BOOL),
        ('AssignProcessToJobObject', [w.HANDLE, w.HANDLE], w.BOOL),
        ('SetInformationJobObject', [w.HANDLE, ctypes.c_int, w.LPVOID, w.DWORD], w.BOOL),
        ('TerminateJobObject', [w.HANDLE, w.UINT], w.BOOL),
        ('ResumeThread', [w.HANDLE], w.DWORD),
        ('CreateToolhelp32Snapshot', [w.DWORD, w.DWORD], w.HANDLE),
        ('GetProcessTimes', [w.HANDLE, w.LPVOID, w.LPVOID, w.LPVOID, w.LPVOID], w.BOOL),
        ('GetExitCodeProcess', [w.HANDLE, w.LPVOID], w.BOOL),
        ('QueryInformationJobObject', [w.HANDLE, ctypes.c_int, w.LPVOID, w.DWORD, w.LPVOID], w.BOOL),
    ]:
        fn = getattr(kernel, name); fn.argtypes = args; fn.restype = result

    class Limits(ctypes.Structure):
        _fields_ = [('process_time', ctypes.c_int64), ('job_time', ctypes.c_int64), ('flags', w.DWORD),
                    ('min_working', ctypes.c_size_t), ('max_working', ctypes.c_size_t), ('active_limit', w.DWORD),
                    ('affinity', ctypes.c_size_t), ('priority', w.DWORD), ('scheduling', w.DWORD)]

    class ExtendedLimits(ctypes.Structure):
        _fields_ = [('basic', Limits), ('io', ctypes.c_uint64 * 6), ('process_memory', ctypes.c_size_t),
                    ('job_memory', ctypes.c_size_t), ('peak_process', ctypes.c_size_t), ('peak_job', ctypes.c_size_t)]

    class ThreadEntry(ctypes.Structure):
        _fields_ = [('size', w.DWORD), ('usage', w.DWORD), ('tid', w.DWORD), ('pid', w.DWORD),
                    ('base_priority', w.LONG), ('delta_priority', w.LONG), ('flags', w.DWORD)]

    class Accounting(ctypes.Structure):
        _fields_ = [('times', ctypes.c_int64 * 4), ('faults', w.DWORD), ('total', w.DWORD), ('active', w.DWORD), ('terminated', w.DWORD)]

    for name in ('Thread32First', 'Thread32Next'):
        getattr(kernel, name).argtypes = [w.HANDLE, ctypes.POINTER(ThreadEntry)]
        getattr(kernel, name).restype = w.BOOL


def _check(value):
    if not value: raise ctypes.WinError(ctypes.get_last_error())
    return value


def attach_windows_job(pid):
    job = None
    try:
        job = _check(kernel.CreateJobObjectW(None, None))
        limits = ExtendedLimits(); limits.basic.flags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        _check(kernel.SetInformationJobObject(job, 9, ctypes.byref(limits), ctypes.sizeof(limits)))
        handle = _check(kernel.OpenProcess(0x0100 | 0x0001, False, pid))
        try: _check(kernel.AssignProcessToJobObject(job, handle))
        finally: kernel.CloseHandle(handle)
        # The child cannot spawn descendants before assignment: it is suspended.
        snapshot = kernel.CreateToolhelp32Snapshot(0x4, 0)
        if snapshot == ctypes.c_void_p(-1).value: raise ctypes.WinError(ctypes.get_last_error())
        try:
            entry = ThreadEntry(); entry.size = ctypes.sizeof(entry)
            more = kernel.Thread32First(snapshot, ctypes.byref(entry)); resumed = False
            while more:
                if entry.pid == pid:
                    thread = _check(kernel.OpenThread(0x2, False, entry.tid))
                    try:
                        if kernel.ResumeThread(thread) == 0xFFFFFFFF: raise ctypes.WinError(ctypes.get_last_error())
                        resumed = True
                    finally: kernel.CloseHandle(thread)
                more = kernel.Thread32Next(snapshot, ctypes.byref(entry))
            if not resumed: raise RuntimeError('owned worker thread not found')
        finally: kernel.CloseHandle(snapshot)
        return job
    except BaseException:
        if job:
            kernel.TerminateJobObject(job, 1)
            kernel.CloseHandle(job)
        raise


class OwnedProcess:
    def __init__(self, process, job=None):
        self.process = process
        self.job = job
        self.identity = self.identify(process.pid)
        self.identity['ownership'] = 'windows_job' if job else 'posix_session'
        self._cleanup = None
        self._termination_lock = asyncio.Lock()

    @staticmethod
    def identify(pid):
        if not pid: return {}
        if os.name == 'nt':
            handle = kernel.OpenProcess(0x1000, False, pid)
            if not handle:
                return {} if ctypes.get_last_error() == 87 else {'pid': pid, 'created': None}
            try:
                created, exited, kern, user = (ctypes.c_uint64() for _ in range(4))
                code = w.DWORD()
                if not kernel.GetProcessTimes(handle, ctypes.byref(created), ctypes.byref(exited), ctypes.byref(kern), ctypes.byref(user)):
                    return {'pid': pid, 'created': None}
                _check(kernel.GetExitCodeProcess(handle, ctypes.byref(code)))
                return {'pid': pid, 'created': created.value, 'alive': code.value == 259}
            finally: kernel.CloseHandle(handle)
        try:
            with open(f'/proc/{pid}/stat', encoding='utf-8') as stream:
                fields = stream.read().rsplit(')', 1)[1].split()
            return {'pid': pid, 'created': fields[19], 'alive': fields[0] != 'Z'}
        except FileNotFoundError: return {}
        except OSError: return {'pid': pid, 'created': None}

    @staticmethod
    def identity_alive(identity):
        if not identity: return False
        current = OwnedProcess.identify(identity['pid'])
        if not current: return False
        if not current.get('created') or not identity.get('created'): return None
        return current['created'] == identity['created'] and current['alive']

    @classmethod
    async def spawn(cls, command, *, cwd=None, env=None):
        options = {'creationflags': 0x08000000 | 0x4} if os.name == 'nt' else {'start_new_session': True}
        launch = asyncio.create_task(asyncio.create_subprocess_exec(*command, cwd=cwd, env=env, stdout=asyncio.subprocess.PIPE,
                                                                    stderr=asyncio.subprocess.STDOUT, **options))
        try:
            process = await asyncio.shield(launch)
        except asyncio.CancelledError:
            # Retrieve the child even if shutdown races creation; never abandon it.
            process = await launch
            if process.returncode is None:
                if os.name == 'nt': process.kill()  # Still suspended, no descendants.
                else: os.killpg(process.pid, signal.SIGKILL)
            await process.wait()
            raise
        job = None
        try:
            if os.name == 'nt':
                job = attach_windows_job(process.pid)
            return cls(process, job)
        except BaseException:
            if job:
                kernel.TerminateJobObject(job, 1); kernel.CloseHandle(job)
            if process.returncode is None: process.kill()
            await process.wait()
            raise

    async def terminate(self):
        async with self._termination_lock:
            return await self._terminate_locked()

    async def _terminate_locked(self):
        if self._cleanup is not None: return self._cleanup
        try:
            if self.job:
                _check(kernel.TerminateJobObject(self.job, 1))
                deadline = asyncio.get_running_loop().time() + 3
                while True:
                    accounting = Accounting()
                    _check(kernel.QueryInformationJobObject(self.job, 1, ctypes.byref(accounting), ctypes.sizeof(accounting), None))
                    if not accounting.active: break
                    if asyncio.get_running_loop().time() >= deadline: raise TimeoutError('owned descendants still alive')
                    await asyncio.sleep(0.01)
            elif self.process.returncode is None:
                if self.identity_alive(self.identity) is not True:
                    return {'reaped': False, 'status': 'ownership_unverified'}
                os.killpg(self.process.pid, signal.SIGKILL)
            await asyncio.wait_for(self.process.wait(), 3)
            self._cleanup = {'reaped': True, 'status': 'owned_tree_reaped', 'identity': self.identity}
        except (OSError, TimeoutError) as exc:
            self._cleanup = {'reaped': False, 'status': 'orphaned', 'error': str(exc), 'identity': self.identity}
        finally:
            if self.job:
                kernel.CloseHandle(self.job); self.job = None
        return self._cleanup
