"""Platform ICMP probing.

On Windows this uses the IP Helper API (``IcmpSendEcho`` /
``Icmp6SendEcho2`` in iphlpapi.dll), which works without Administrator
rights and reports the responding router address when the probe's TTL
expires in transit — exactly what an mtr-style tracer needs.

On POSIX (used for development and CI tests) it falls back to raw ICMP
sockets, which require root.
"""

from __future__ import annotations

import ctypes
import ipaddress
import os
import select
import socket
import struct
import sys
import time
from dataclasses import dataclass
from enum import Enum
from typing import Optional


class ProbeStatus(Enum):
    REPLY = "reply"            # echo reply — destination reached
    TTL_EXPIRED = "ttl_expired"  # intermediate hop answered
    TIMEOUT = "timeout"
    UNREACHABLE = "unreachable"
    ERROR = "error"


@dataclass
class ProbeResult:
    status: ProbeStatus
    responder: Optional[str] = None
    rtt_ms: Optional[float] = None
    detail: str = ""

    @property
    def answered(self) -> bool:
        return self.status in (ProbeStatus.REPLY, ProbeStatus.TTL_EXPIRED)


def resolve_target(target: str, family_hint: str = "auto") -> tuple[str, int]:
    """Resolve an IP or hostname to (address, family).

    family_hint: "auto" | "ipv4" | "ipv6".
    Raises socket.gaierror if the name cannot be resolved.
    """
    fam = {"ipv4": socket.AF_INET, "ipv6": socket.AF_INET6}.get(
        family_hint, socket.AF_UNSPEC
    )
    infos = socket.getaddrinfo(target, None, fam)
    # Prefer IPv4 in auto mode for the widest hop visibility.
    if family_hint == "auto":
        infos.sort(key=lambda i: 0 if i[0] == socket.AF_INET else 1)
    family, _, _, _, sockaddr = infos[0]
    return sockaddr[0], family


# --------------------------------------------------------------------------
# Windows implementation (IP Helper API)
# --------------------------------------------------------------------------

if sys.platform == "win32":
    import ctypes.wintypes as wintypes

    _iphlpapi = ctypes.windll.iphlpapi
    _ws2_32 = ctypes.windll.ws2_32

    IP_SUCCESS = 0
    IP_DEST_NET_UNREACHABLE = 11002
    IP_DEST_HOST_UNREACHABLE = 11003
    IP_DEST_PORT_UNREACHABLE = 11005
    IP_REQ_TIMED_OUT = 11010
    IP_TTL_EXPIRED_TRANSIT = 11013

    class IP_OPTION_INFORMATION(ctypes.Structure):
        _fields_ = [
            ("Ttl", ctypes.c_ubyte),
            ("Tos", ctypes.c_ubyte),
            ("Flags", ctypes.c_ubyte),
            ("OptionsSize", ctypes.c_ubyte),
            ("OptionsData", ctypes.POINTER(ctypes.c_ubyte)),
        ]

    class ICMP_ECHO_REPLY(ctypes.Structure):
        _fields_ = [
            ("Address", ctypes.c_ulong),
            ("Status", ctypes.c_ulong),
            ("RoundTripTime", ctypes.c_ulong),
            ("DataSize", ctypes.c_ushort),
            ("Reserved", ctypes.c_ushort),
            ("Data", ctypes.c_void_p),
            ("Options", IP_OPTION_INFORMATION),
        ]

    class SOCKADDR_IN6(ctypes.Structure):
        _fields_ = [
            ("sin6_family", ctypes.c_short),
            ("sin6_port", ctypes.c_ushort),
            ("sin6_flowinfo", ctypes.c_ulong),
            ("sin6_addr", ctypes.c_ubyte * 16),
            ("sin6_scope_id", ctypes.c_ulong),
        ]

    class IPV6_ADDRESS_EX(ctypes.Structure):
        _pack_ = 1
        _fields_ = [
            ("sin6_port", ctypes.c_ushort),
            ("sin6_flowinfo", ctypes.c_ulong),
            ("sin6_addr", ctypes.c_ushort * 8),
            ("sin6_scope_id", ctypes.c_ulong),
        ]

    class ICMPV6_ECHO_REPLY(ctypes.Structure):
        _pack_ = 1
        _fields_ = [
            ("Address", IPV6_ADDRESS_EX),
            ("Status", ctypes.c_ulong),
            ("RoundTripTime", ctypes.c_uint),
        ]

    def _status_to_result(status: int) -> ProbeStatus:
        if status == IP_SUCCESS:
            return ProbeStatus.REPLY
        if status == IP_TTL_EXPIRED_TRANSIT:
            return ProbeStatus.TTL_EXPIRED
        if status == IP_REQ_TIMED_OUT:
            return ProbeStatus.TIMEOUT
        if status in (
            IP_DEST_NET_UNREACHABLE,
            IP_DEST_HOST_UNREACHABLE,
            IP_DEST_PORT_UNREACHABLE,
        ):
            return ProbeStatus.UNREACHABLE
        return ProbeStatus.ERROR

    def _probe_v4(dest_ip: str, ttl: int, timeout_ms: int, size: int) -> ProbeResult:
        handle = _iphlpapi.IcmpCreateFile()
        if handle == ctypes.c_void_p(-1).value:
            return ProbeResult(ProbeStatus.ERROR, detail="IcmpCreateFile failed")
        try:
            payload = (b"AdvancedMTR!" * (size // 12 + 1))[:size]
            opts = IP_OPTION_INFORMATION(Ttl=ttl)
            reply_size = ctypes.sizeof(ICMP_ECHO_REPLY) + size + 8
            reply_buf = ctypes.create_string_buffer(reply_size)
            dest = _ws2_32.inet_addr(dest_ip.encode("ascii"))
            start = time.perf_counter()
            count = _iphlpapi.IcmpSendEcho(
                handle,
                dest,
                payload,
                len(payload),
                ctypes.byref(opts),
                reply_buf,
                reply_size,
                timeout_ms,
            )
            elapsed_ms = (time.perf_counter() - start) * 1000.0
            reply = ctypes.cast(
                reply_buf, ctypes.POINTER(ICMP_ECHO_REPLY)
            ).contents
            if count == 0:
                err = ctypes.GetLastError()
                if err == IP_REQ_TIMED_OUT:
                    return ProbeResult(ProbeStatus.TIMEOUT)
                # For TTL-expired and unreachable, IcmpSendEcho still fills
                # the reply buffer but may return 0 on some systems; check
                # the status field before giving up.
                status = _status_to_result(reply.Status)
                if status in (ProbeStatus.TTL_EXPIRED, ProbeStatus.UNREACHABLE):
                    addr = socket.inet_ntoa(struct.pack("<L", reply.Address))
                    rtt = float(reply.RoundTripTime) or elapsed_ms
                    return ProbeResult(status, addr, rtt)
                return ProbeResult(ProbeStatus.TIMEOUT, detail=f"err={err}")
            status = _status_to_result(reply.Status)
            if status in (ProbeStatus.REPLY, ProbeStatus.TTL_EXPIRED,
                          ProbeStatus.UNREACHABLE):
                addr = socket.inet_ntoa(struct.pack("<L", reply.Address))
                rtt = float(reply.RoundTripTime)
                if status == ProbeStatus.REPLY and rtt == 0.0:
                    rtt = min(elapsed_ms, 0.5)
                return ProbeResult(status, addr, rtt)
            if status == ProbeStatus.TIMEOUT:
                return ProbeResult(ProbeStatus.TIMEOUT)
            return ProbeResult(ProbeStatus.ERROR, detail=f"status={reply.Status}")
        finally:
            _iphlpapi.IcmpCloseHandle(handle)

    def _probe_v6(dest_ip: str, ttl: int, timeout_ms: int, size: int) -> ProbeResult:
        handle = _iphlpapi.Icmp6CreateFile()
        if handle == ctypes.c_void_p(-1).value:
            return ProbeResult(ProbeStatus.ERROR, detail="Icmp6CreateFile failed")
        try:
            payload = (b"AdvancedMTR!" * (size // 12 + 1))[:size]
            opts = IP_OPTION_INFORMATION(Ttl=ttl)
            reply_size = ctypes.sizeof(ICMPV6_ECHO_REPLY) + size + 8
            reply_buf = ctypes.create_string_buffer(reply_size)

            src = SOCKADDR_IN6(sin6_family=socket.AF_INET6)
            dst = SOCKADDR_IN6(sin6_family=socket.AF_INET6)
            packed = socket.inet_pton(socket.AF_INET6, dest_ip)
            ctypes.memmove(dst.sin6_addr, packed, 16)

            count = _iphlpapi.Icmp6SendEcho2(
                handle, None, None, None,
                ctypes.byref(src), ctypes.byref(dst),
                payload, len(payload),
                ctypes.byref(opts),
                reply_buf, reply_size, timeout_ms,
            )
            reply = ctypes.cast(
                reply_buf, ctypes.POINTER(ICMPV6_ECHO_REPLY)
            ).contents
            status = _status_to_result(reply.Status)
            if count == 0 and status not in (
                ProbeStatus.TTL_EXPIRED, ProbeStatus.UNREACHABLE
            ):
                return ProbeResult(ProbeStatus.TIMEOUT)
            if status in (ProbeStatus.REPLY, ProbeStatus.TTL_EXPIRED,
                          ProbeStatus.UNREACHABLE):
                raw = struct.pack("!8H", *reply.Address.sin6_addr)
                addr = socket.inet_ntop(socket.AF_INET6, raw)
                return ProbeResult(status, addr, float(reply.RoundTripTime))
            if status == ProbeStatus.TIMEOUT:
                return ProbeResult(ProbeStatus.TIMEOUT)
            return ProbeResult(ProbeStatus.ERROR, detail=f"status={reply.Status}")
        finally:
            _iphlpapi.IcmpCloseHandle(handle)

    def probe(dest_ip: str, ttl: int, timeout_ms: int = 1000,
              size: int = 56) -> ProbeResult:
        if ipaddress.ip_address(dest_ip).version == 6:
            return _probe_v6(dest_ip, ttl, timeout_ms, size)
        return _probe_v4(dest_ip, ttl, timeout_ms, size)


# --------------------------------------------------------------------------
# POSIX fallback (raw sockets; requires root) — used for dev and CI
# --------------------------------------------------------------------------

else:
    _ICMP_ECHO_REQUEST = 8
    _ICMP_ECHO_REPLY = 0
    _ICMP_TIME_EXCEEDED = 11
    _ICMP_DEST_UNREACH = 3

    def _checksum(data: bytes) -> int:
        if len(data) % 2:
            data += b"\x00"
        total = sum(struct.unpack(f"!{len(data)//2}H", data))
        total = (total >> 16) + (total & 0xFFFF)
        total += total >> 16
        return ~total & 0xFFFF

    def _probe_v4_posix(dest_ip: str, ttl: int, timeout_ms: int,
                        size: int) -> ProbeResult:
        ident = os.getpid() & 0xFFFF
        seq = (ttl << 8 | int(time.time() * 1000) & 0xFF) & 0xFFFF
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_RAW,
                                 socket.IPPROTO_ICMP)
        except OSError as exc:
            return ProbeResult(
                ProbeStatus.ERROR,
                detail=f"Cannot open raw ICMP socket ({exc}); "
                       "root privileges are required on this platform",
            )
        try:
            sock.setsockopt(socket.IPPROTO_IP, socket.IP_TTL, ttl)
            payload = (b"AdvancedMTR!" * (size // 12 + 1))[:size]
            header = struct.pack("!BBHHH", _ICMP_ECHO_REQUEST, 0, 0, ident, seq)
            csum = _checksum(header + payload)
            packet = struct.pack("!BBHHH", _ICMP_ECHO_REQUEST, 0, csum,
                                 ident, seq) + payload
            start = time.perf_counter()
            sock.sendto(packet, (dest_ip, 0))
            deadline = start + timeout_ms / 1000.0
            while True:
                remaining = deadline - time.perf_counter()
                if remaining <= 0:
                    return ProbeResult(ProbeStatus.TIMEOUT)
                ready, _, _ = select.select([sock], [], [], remaining)
                if not ready:
                    return ProbeResult(ProbeStatus.TIMEOUT)
                data, addr = sock.recvfrom(2048)
                rtt = (time.perf_counter() - start) * 1000.0
                ihl = (data[0] & 0x0F) * 4
                icmp_type, _code = data[ihl], data[ihl + 1]
                if icmp_type == _ICMP_ECHO_REPLY:
                    r_ident, r_seq = struct.unpack("!HH", data[ihl + 4: ihl + 8])
                    if r_ident == ident and r_seq == seq:
                        return ProbeResult(ProbeStatus.REPLY, addr[0], rtt)
                elif icmp_type in (_ICMP_TIME_EXCEEDED, _ICMP_DEST_UNREACH):
                    # Original datagram is embedded after the ICMP header +
                    # inner IP header; match our ident/seq inside it.
                    inner = data[ihl + 8:]
                    if len(inner) >= 28:
                        inner_ihl = (inner[0] & 0x0F) * 4
                        i_ident, i_seq = struct.unpack(
                            "!HH", inner[inner_ihl + 4: inner_ihl + 8]
                        )
                        if i_ident == ident and i_seq == seq:
                            st = (ProbeStatus.TTL_EXPIRED
                                  if icmp_type == _ICMP_TIME_EXCEEDED
                                  else ProbeStatus.UNREACHABLE)
                            return ProbeResult(st, addr[0], rtt)
                # Unrelated packet — keep waiting until the deadline.
        finally:
            sock.close()

    def probe(dest_ip: str, ttl: int, timeout_ms: int = 1000,
              size: int = 56) -> ProbeResult:
        if ipaddress.ip_address(dest_ip).version == 6:
            return ProbeResult(
                ProbeStatus.ERROR,
                detail="IPv6 probing is only implemented on Windows",
            )
        return _probe_v4_posix(dest_ip, ttl, timeout_ms, size)
