# -*- coding: utf-8 -*-
"""5000/5001 端口归属核验：监听 PID、进程工作目录（PEB）、命令行、双绑定检查。"""
import ctypes
import json
import re
import subprocess
import sys

import requests

sys.stdout.reconfigure(encoding="utf-8")

PROCESS_QUERY_INFORMATION = 0x0400
PROCESS_VM_READ = 0x0010
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
ntdll = ctypes.WinDLL("ntdll")


class PBI(ctypes.Structure):
    _fields_ = [("ExitStatus", ctypes.c_ulong), ("PebBaseAddress", ctypes.c_void_p),
                ("AffinityMask", ctypes.c_size_t), ("BasePriority", ctypes.c_long),
                ("UniqueProcessId", ctypes.c_size_t),
                ("InheritedFromUniqueProcessId", ctypes.c_size_t)]


def read_mem(h, addr, size):
    buf = ctypes.create_string_buffer(size)
    n = ctypes.c_size_t(0)
    kernel32.ReadProcessMemory(h, ctypes.c_void_p(addr), buf, size, ctypes.byref(n))
    return buf.raw[:n.value]


def read_ptr(h, addr):
    raw = read_mem(h, addr, 8)
    return ctypes.c_uint64.from_buffer_copy(raw).value if len(raw) == 8 else 0


def probe(pid):
    h = kernel32.OpenProcess(PROCESS_QUERY_INFORMATION | PROCESS_VM_READ, False, pid)
    if not h:
        return {"pid": pid, "error": f"OpenProcess err={ctypes.get_last_error()}"}
    try:
        pbi = PBI()
        ntdll.NtQueryInformationProcess(h, 0, ctypes.byref(pbi), ctypes.sizeof(pbi), None)
        params = read_ptr(h, pbi.PebBaseAddress + 0x20)
        # CURDIR: UNICODE_STRING at +0x38, Buffer at +0x40
        ln = ctypes.c_uint16.from_buffer_copy(read_mem(h, params + 0x38, 2)).value
        buf = read_ptr(h, params + 0x40)
        cwd = read_mem(h, buf, ln).decode("utf-16-le", errors="replace") if buf and ln else ""
        return {"pid": pid, "peb": hex(pbi.PebBaseAddress), "cwd": cwd}
    finally:
        kernel32.CloseHandle(h)


def listeners():
    out = subprocess.check_output(["netstat", "-ano"], text=True, errors="replace")
    res = {}
    for line in out.splitlines():
        m = re.search(r"TCP\s+\S+:(\d+)\s+\S+\s+LISTENING\s+(\d+)", line)
        if m:
            res.setdefault(int(m.group(1)), set()).add(int(m.group(2)))
    return {p: sorted(v) for p, v in res.items() if p in (5000, 5001)}


ls = listeners()
out = {"listeners": ls, "ports": {}}
for port in (5000, 5001):
    pids = ls.get(port, [])
    infos = []
    for pid in pids:
        info = probe(pid)
        try:
            ps = subprocess.check_output(
                ["powershell", "-NoProfile", "-Command",
                 f"(Get-CimInstance Win32_Process -Filter 'ProcessId={pid}').CommandLine"],
                text=True, errors="replace").strip()
            info["cmdline"] = ps
        except Exception as e:
            info["cmdline_error"] = str(e)
        infos.append(info)
    try:
        code = requests.get(f"http://127.0.0.1:{port}/", timeout=5).status_code
    except Exception as e:
        code = f"ERR:{e}"
    out["ports"][str(port)] = {"listener_count": len(pids), "processes": infos,
                               "http": code}

out["checks"] = {
    "single_listener_each": all(v["listener_count"] == 1 for v in out["ports"].values()),
    "5000_cwd_fin_ver": any("fin_ver" in (p.get("cwd", "").lower())
                            for p in out["ports"]["5000"]["processes"]),
    "5001_cwd_noreset": any("noreset" in (p.get("cwd", "").lower())
                            for p in out["ports"]["5001"]["processes"]),
    "http_200_both": out["ports"]["5000"]["http"] == 200 and out["ports"]["5001"]["http"] == 200,
    "different_pids": (out["ports"]["5000"]["processes"][0].get("pid")
                       != out["ports"]["5001"]["processes"][0].get("pid")),
}
with open(r"D:\bully_simulation_pack_v2_20260713\test_evidence\nr_ports.json", "w",
          encoding="utf-8") as f:
    json.dump(out, f, ensure_ascii=False, indent=2)
print(json.dumps(out, ensure_ascii=False, indent=2))
