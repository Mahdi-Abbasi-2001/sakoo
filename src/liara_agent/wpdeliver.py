"""Delivering a WordPress project's own code (themes, plugins, translations, optionally uploads) to a managed Liara WordPress site over FTPS.

Liara's managed WordPress has no deploy API: its docs give three ways to get files in (an FTPS access made in the console, the console's file manager, the
Duplicator plugin). This is the FTPS way. The WordPress core files stay what Liara installed, `wp-config.php` is never touched (it holds the site's own database
settings), and the database is not part of this: a dump is imported by the person in phpMyAdmin.
"""
from __future__ import annotations

import ftplib
import ipaddress
import socket
import ssl
from pathlib import Path
from typing import Callable

PARTS = ("themes", "plugins", "mu-plugins", "languages")  # under wp-content; uploads are opt-in (they are large and are content, not code)
SKIP_DIRS = {".git", "node_modules", ".svn", "cache", "upgrade", "backups", "ai1wm-backups", "updraft"}
SKIP_FILES = {".DS_Store", "debug.log", ".env"}
SKIP_SUFFIX = (".sql", ".sql.gz", ".log", ".wpress", ".zip")
MAX_FILES, MAX_BYTES = 20_000, 400 * 1024 * 1024


class WpError(Exception):
    def __init__(self, code: str, message: str, **params):
        super().__init__(message)
        self.code, self.params = code, params


def wp_content(root: str | Path) -> Path | None:
    """The wp-content folder: at the project root, one level down (a repo with the site in a subfolder), or the project root itself when it is wp-content."""
    r = Path(root)
    for c in (r / "wp-content", *sorted(p / "wp-content" for p in r.iterdir() if p.is_dir() and p.name not in SKIP_DIRS)):
        if c.is_dir():
            return c
    return r if (r / "themes").is_dir() or (r / "plugins").is_dir() else None


def collect(root: str | Path, include_uploads: bool = False) -> dict:
    """The files that would be sent: [{path (relative to the site root, starting wp-content/), size}], plus totals. Symlinks and secrets are never included."""
    base = wp_content(root)
    if base is None:
        raise WpError("wp_no_content", "this project has no wp-content folder")
    parts = [*PARTS, *(("uploads",) if include_uploads else ())]
    files: list[dict] = []
    total = 0
    for part in parts:
        top = base / part
        if not top.is_dir():
            continue
        for p in sorted(top.rglob("*")):
            rel = p.relative_to(top)
            if p.is_symlink() or not p.is_file() or any(x in SKIP_DIRS for x in rel.parts) or p.name in SKIP_FILES or p.name.endswith(SKIP_SUFFIX):
                continue
            size = p.stat().st_size
            files.append({"path": f"wp-content/{part}/{rel.as_posix()}", "size": size, "_src": str(p)})
            total += size
            if len(files) > MAX_FILES or total > MAX_BYTES:
                raise WpError("wp_too_large", "too many or too large files to send over FTPS from here", files=len(files), mb=total // 1048576)
    if not files:
        raise WpError("wp_nothing_to_send", "there are no theme, plugin or language files to send")
    return {"files": files, "total_bytes": total, "parts": [p for p in parts if (base / p).is_dir()], "uploads": include_uploads}


class ImplicitFTPS(ftplib.FTP_TLS):
    """FTPS that starts TLS at once (port 990), as opposed to the explicit kind that upgrades after connecting (port 21)."""

    def connect(self, host="", port=0, timeout=-999, source_address=None):
        super().connect(host, port, timeout, source_address)
        self.sock = self.context.wrap_socket(self.sock, server_hostname=host)
        self.file = self.sock.makefile("r", encoding=self.encoding)
        return self.welcome


def check_host(host: str, resolve: Callable[[str], list[str]] | None = None) -> None:
    """The server connects to an address a user typed: only public addresses (never the server's own network or a cloud metadata address)."""
    if not host or len(host) > 253 or any(c in host for c in " /@:"):
        raise WpError("wp_bad_host", "that is not a host name")
    try:
        ips = (resolve or (lambda h: sorted({i[4][0] for i in socket.getaddrinfo(h, None, type=socket.SOCK_STREAM)})))(host)
    except OSError:
        ips = []
    if not ips:
        raise WpError("wp_host_unresolved", "that host does not resolve")
    if any(not ipaddress.ip_address(ip).is_global for ip in ips):
        raise WpError("wp_host_private", "that host points inside a private network")


def upload(plan: dict, *, host: str, port: int, user: str, password: str, implicit: bool = False, remote_root: str = "",
           progress: Callable[[int, int], None] = lambda done, total: None, factory=None, resolve=None) -> dict:
    """Send the planned files. Directories are created as needed; existing files are replaced. Returns {sent, bytes}."""
    check_host(host, resolve)
    if not 1 <= port <= 65535:
        raise WpError("wp_bad_host", "that is not a valid port")
    ftp = (factory or (ImplicitFTPS if implicit else ftplib.FTP_TLS))(context=ssl.create_default_context(), timeout=30)
    try:
        ftp.connect(host, port)
        ftp.login(user, password)
        ftp.prot_p()
        if remote_root:
            ftp.cwd(remote_root)
        made: set[str] = set()
        sent = nbytes = 0
        for f in plan["files"]:
            d = f["path"].rsplit("/", 1)[0]
            parts = d.split("/")
            for i in range(1, len(parts) + 1):
                sub = "/".join(parts[:i])
                if sub not in made:
                    try:
                        ftp.mkd(sub)
                    except ftplib.error_perm:  # already there
                        pass
                    made.add(sub)
            with open(f["_src"], "rb") as fh:
                ftp.storbinary(f"STOR {f['path']}", fh)
            sent += 1
            nbytes += f["size"]
            progress(sent, len(plan["files"]))
        return {"sent": sent, "bytes": nbytes}
    except ssl.SSLCertVerificationError:
        raise WpError("wp_tls_failed", "the server's certificate could not be verified")
    except ftplib.error_perm as e:
        raise WpError("wp_login_failed" if str(e).startswith("530") else "wp_ftp_refused", "the server refused the login" if str(e).startswith("530") else "the server refused a file")
    except (OSError, ftplib.Error, EOFError):
        raise WpError("wp_ftp_unreachable", "could not talk to the FTPS server")
    finally:
        try:
            ftp.quit()
        except Exception:
            pass
