"""Protect newly created setup-code files before writing their secret bytes."""
import os


def protect(path, fd):
    if os.name != 'nt':
        os.fchmod(fd, 0o600)
        return
    import ctypes
    from ctypes import wintypes
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    security = ctypes.WinDLL('advapi32', use_last_error=True)
    kernel.GetCurrentProcess.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.LocalFree.argtypes = [wintypes.HLOCAL]
    security.OpenProcessToken.argtypes = [wintypes.HANDLE, wintypes.DWORD, ctypes.POINTER(wintypes.HANDLE)]
    security.GetTokenInformation.argtypes = [wintypes.HANDLE, ctypes.c_int, wintypes.LPVOID, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)]
    security.ConvertSidToStringSidW.argtypes = [wintypes.LPVOID, ctypes.POINTER(wintypes.LPWSTR)]
    security.ConvertStringSecurityDescriptorToSecurityDescriptorW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, ctypes.POINTER(wintypes.LPVOID), ctypes.POINTER(wintypes.DWORD)]
    security.SetFileSecurityW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.LPVOID]
    token, sid_text, descriptor = wintypes.HANDLE(), wintypes.LPWSTR(), wintypes.LPVOID()
    def checked(ok):
        if not ok:
            raise OSError('Local setup code permissions unavailable')
    checked(security.OpenProcessToken(kernel.GetCurrentProcess(), 0x0008, ctypes.byref(token)))
    try:
        size = wintypes.DWORD()
        security.GetTokenInformation(token, 1, None, 0, ctypes.byref(size))
        checked(0 < size.value < 65536)
        data = ctypes.create_string_buffer(size.value)
        checked(security.GetTokenInformation(token, 1, data, size, ctypes.byref(size)))
        sid = ctypes.cast(data, ctypes.POINTER(wintypes.LPVOID))[0]
        checked(security.ConvertSidToStringSidW(sid, ctypes.byref(sid_text)))
        # Protected DACL grants only this launcher identity and LocalSystem.
        sddl = f'D:P(A;;FA;;;SY)(A;;FA;;;{sid_text.value})'
        checked(security.ConvertStringSecurityDescriptorToSecurityDescriptorW(sddl, 1, ctypes.byref(descriptor), None))
        checked(security.SetFileSecurityW(str(path), 0x80000004, descriptor))
    finally:
        kernel.CloseHandle(token)
        if sid_text:
            kernel.LocalFree(ctypes.cast(sid_text, wintypes.HLOCAL))
        if descriptor:
            kernel.LocalFree(descriptor)
