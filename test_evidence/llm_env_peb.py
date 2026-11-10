# -*- coding: utf-8 -*-
"""只读读取指定 PID 的进程环境变量（通过 PEB / RTL_USER_PROCESS_PARAMETERS）。

用途：验收 5000 端口进程的 NO_PROXY 实际生效值（不重启、不修改）。
仅 OpenProcess(QUERY_INFORMATION|VM_READ) + ReadProcessMemory。
"""
import ctypes
import ctypes.wintypes as wt
import json
import sys

sys.stdout.reconfigure(encoding="utf-8")

PROCESS_QUERY_INFORMATION = 0x0400
PROCESS_VM_READ = 0x0010
STATUS_SUCCESS = 0

kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
ntdll = ctypes.WinDLL("ntdll")


class PROCESS_BASIC_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("ExitStatus", ctypes.c_ulong),
        ("PebBaseAddress", ctypes.c_void_p),
        ("AffinityMask", ctypes.c_size_t),
        ("BasePriority", ctypes.c_long),
        ("UniqueProcessId", ctypes.c_size_t),
        ("InheritedFromUniqueProcessId", ctypes.c_size_t),
    ]


def read_mem(h, addr, size):
    buf = ctypes.create_string_buffer(size)
    n = ctypes.c_size_t(0)
    ok = kernel32.ReadProcessMemory(h, ctypes.c_void_p(addr), buf, size, ctypes.byref(n))
    if not ok:
        raise OSError(f"ReadProcessMemory failed addr=0x{addr:x} err={ctypes.get_last_error()}")
    return buf.raw[:n.value]


def read_ptr(h, addr):
    return ctypes.c_uint64.from_buffer_copy(read_mem(h, addr, 8)).value


def get_env(pid):
    h = kernel32.OpenProcess(PROCESS_QUERY_INFORMATION | PROCESS_VM_READ, False, pid)
    if not h:
        raise OSError(f"OpenProcess failed err={ctypes.get_last_error()}")
    try:
        pbi = PROCESS_BASIC_INFORMATION()
        ret = ntdll.NtQueryInformationProcess(
            h, 0, ctypes.byref(pbi), ctypes.sizeof(pbi), None)
        if ret != STATUS_SUCCESS:
            raise OSError(f"NtQueryInformationProcess status=0x{ret:x}")
        peb = pbi.PebBaseAddress
        params = read_ptr(h, peb + 0x20)
        env_ptr = read_ptr(h, params + 0x80)
        try:
            cmd_ptr = read_ptr(h, params + 0x70)
            cmd_len = ctypes.c_uint16.from_buffer_copy(read_mem(h, params + 0x68, 2)).value
            cmd_raw = read_mem(h, cmd_ptr, min(cmd_len, 4096)) if cmd_ptr and cmd_len else b""
        except Exception:
            cmd_raw = b""
        # 环境块：连续 UTF-16LE 字符串，以 \x00\x00 结束
        raw = read_mem(h, env_ptr, 256 * 1024)
        text = raw.decode("utf-16-le", errors="replace")
        text = text.split("\x00\x00")[0]
        env = {}
        for entry in text.split("\x00"):
            if "=" in entry:
                k, v = entry.split("=", 1)
                env[k] = v
        return {
            "pid": pid,
            "cmdline": cmd_raw.decode("utf-16-le", errors="replace"),
            "peb": hex(peb),
            "env_count": len(env),
            "NO_PROXY": env.get("NO_PROXY"),
            "no_proxy": env.get("no_proxy"),
            "HTTP_PROXY": env.get("HTTP_PROXY"),
            "HTTPS_PROXY": env.get("HTTPS_PROXY"),
            "has_bracket_ipv6_in_no_proxy": any(
                "[" in (env.get(k) or "") for k in ("NO_PROXY", "no_proxy")),
            "all_proxy_keys": {k: v for k, v in env.items()
                               if "PROXY" in k.upper() or "proxy" in k},
        }
    finally:
        kernel32.CloseHandle(h)


if __name__ == "__main__":
    pid = int(sys.argv[1]) if len(sys.argv) > 1 else 35524
    out = get_env(pid)
    with open(r"D:\bully_simulation_pack_v2_20260713\test_evidence\llm_env_peb.json",
              "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print(json.dumps(out, ensure_ascii=False, indent=2))
