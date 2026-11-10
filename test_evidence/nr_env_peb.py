# -*- coding: utf-8 -*-
"""读取 5001 进程环境（PEB），分块读取避免跨页失败；只读。"""
import ctypes
import json
import sys

sys.stdout.reconfigure(encoding="utf-8")

PROCESS_QUERY_INFORMATION = 0x0400
PROCESS_VM_READ = 0x0010
k32 = ctypes.WinDLL("kernel32", use_last_error=True)
ntdll = ctypes.WinDLL("ntdll")


class PBI(ctypes.Structure):
    _fields_ = [("ExitStatus", ctypes.c_ulong), ("PebBaseAddress", ctypes.c_void_p),
                ("AffinityMask", ctypes.c_size_t), ("BasePriority", ctypes.c_long),
                ("UniqueProcessId", ctypes.c_size_t),
                ("InheritedFromUniqueProcessId", ctypes.c_size_t)]


def read_partial(h, addr, size):
    buf = ctypes.create_string_buffer(size)
    n = ctypes.c_size_t(0)
    k32.ReadProcessMemory(h, ctypes.c_void_p(addr), buf, size, ctypes.byref(n))
    return buf.raw[:n.value], bool(n.value)


def read_ptr(h, addr):
    raw, ok = read_partial(h, addr, 8)
    return ctypes.c_uint64.from_buffer_copy(raw).value if ok and len(raw) == 8 else 0


def env_block(h, addr, limit=128 * 1024, chunk=4096):
    out = b""
    while len(out) < limit:
        raw, ok = read_partial(h, addr + len(out), min(chunk, limit - len(out)))
        out += raw
        if not ok or len(raw) == 0:
            break
    return out


pid = int(sys.argv[1]) if len(sys.argv) > 1 else 43668
h = k32.OpenProcess(PROCESS_QUERY_INFORMATION | PROCESS_VM_READ, False, pid)
if not h:
    raise SystemExit(f"OpenProcess err={ctypes.get_last_error()}")
try:
    pbi = PBI()
    ntdll.NtQueryInformationProcess(h, 0, ctypes.byref(pbi), ctypes.sizeof(pbi), None)
    params = read_ptr(h, pbi.PebBaseAddress + 0x20)
    env_ptr = read_ptr(h, params + 0x80)
    raw = env_block(h, env_ptr)
    text = raw.decode("utf-16-le", errors="replace").split("\x00\x00")[0]
    env = {}
    for entry in text.split("\x00"):
        if "=" in entry:
            k, v = entry.split("=", 1)
            env[k] = v
    out = {"pid": pid, "env_count": len(env), "NO_PROXY": env.get("NO_PROXY"),
           "has_bracket_ipv6": "[" in (env.get("NO_PROXY") or ""),
           "proxy_keys": {k: v for k, v in env.items() if "PROXY" in k.upper()},
           "cwd_hint_PWD": env.get("PWD")}
finally:
    k32.CloseHandle(h)

with open(r"D:\bully_simulation_pack_v2_20260713\test_evidence\nr_env_peb.json", "w",
          encoding="utf-8") as f:
    json.dump(out, f, ensure_ascii=False, indent=2)
print(json.dumps(out, ensure_ascii=False, indent=2))
