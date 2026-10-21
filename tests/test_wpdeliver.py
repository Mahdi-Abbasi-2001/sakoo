import ftplib
import ssl

import pytest

from liara_agent import wpdeliver as wp


def site(tmp_path):
    for rel, text in {"wp-content/themes/mine/style.css": "a", "wp-content/themes/mine/node_modules/x.js": "x", "wp-content/plugins/p/p.php": "<?php",
                      "wp-content/uploads/2026/pic.jpg": "img", "wp-content/plugins/p/dump.sql": "sql", "wp-config.php": "SECRET", ".env": "S=1",
                      "wp-content/themes/mine/.env": "S=2", "wp-content/cache/c.html": "c"}.items():
        f = tmp_path / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(text)
    (tmp_path / "wp-content/themes/link").symlink_to("/etc/passwd")
    return tmp_path


def test_only_code_is_collected_never_config_secrets_dumps_or_symlinks(tmp_path):
    plan = wp.collect(site(tmp_path))
    paths = {f["path"] for f in plan["files"]}
    assert paths == {"wp-content/themes/mine/style.css", "wp-content/plugins/p/p.php"}
    assert "uploads" not in str(plan["parts"])
    assert "wp-content/uploads/2026/pic.jpg" in {f["path"] for f in wp.collect(tmp_path, include_uploads=True)["files"]}


def test_a_site_in_a_subfolder_and_a_project_without_wordpress(tmp_path):
    (tmp_path / "site/wp-content/themes/t").mkdir(parents=True)
    (tmp_path / "site/wp-content/themes/t/s.css").write_text("x")
    assert wp.collect(tmp_path)["files"][0]["path"] == "wp-content/themes/t/s.css"
    empty = tmp_path / "other"
    empty.mkdir()
    with pytest.raises(wp.WpError) as e:
        wp.collect(empty)
    assert e.value.code == "wp_no_content"


class FakeFTP:
    instances = []

    def __init__(self, context=None, timeout=None):
        self.cmds, self.stored, self.mkds = [], {}, []
        self.fail_login = False
        FakeFTP.instances.append(self)

    def connect(self, host, port): self.cmds.append(("connect", host, port))
    def login(self, u, p):
        if self.fail_login: raise ftplib.error_perm("530 Login incorrect")
        self.cmds.append(("login", u))
    def prot_p(self): self.cmds.append(("prot_p",))
    def cwd(self, d): self.cmds.append(("cwd", d))
    def mkd(self, d):
        if d in self.mkds: raise ftplib.error_perm("550 exists")
        self.mkds.append(d)
    def storbinary(self, cmd, fh): self.stored[cmd] = fh.read()
    def quit(self): self.cmds.append(("quit",))


public = lambda h: ["93.184.216.34"]


def test_upload_creates_directories_once_replaces_files_and_reports_progress(tmp_path):
    plan = wp.collect(site(tmp_path))
    seen = []
    out = wp.upload(plan, host="ftp.example.com", port=21, user="u", password="p", progress=lambda a, b: seen.append((a, b)), factory=FakeFTP, resolve=public)
    ftp = FakeFTP.instances[-1]
    assert out == {"sent": 2, "bytes": 6} and seen[-1] == (2, 2)
    assert ("prot_p",) in ftp.cmds and ftp.cmds[-1] == ("quit",)
    assert ftp.mkds[:3] == ["wp-content", "wp-content/themes", "wp-content/themes/mine"] and ftp.stored["STOR wp-content/themes/mine/style.css"] == b"a"
    assert len(ftp.mkds) == len(set(ftp.mkds))


def test_failures_are_coded_and_the_password_never_appears(tmp_path):
    plan = wp.collect(site(tmp_path))
    class Bad(FakeFTP):
        def __init__(self, **kw):
            super().__init__(**kw); self.fail_login = True
    with pytest.raises(wp.WpError) as e:
        wp.upload(plan, host="ftp.example.com", port=21, user="u", password="hunter2hunter2", factory=Bad, resolve=public)
    assert e.value.code == "wp_login_failed" and "hunter2" not in str(e.value)
    class Cert(FakeFTP):
        def connect(self, host, port): raise ssl.SSLCertVerificationError("bad")
    with pytest.raises(wp.WpError) as e:
        wp.upload(plan, host="ftp.example.com", port=21, user="u", password="p", factory=Cert, resolve=public)
    assert e.value.code == "wp_tls_failed"


@pytest.mark.parametrize("ip", ["127.0.0.1", "10.1.2.3", "169.254.169.254", "192.168.0.9"])
def test_the_server_never_connects_to_a_private_address(tmp_path, ip):
    plan = wp.collect(site(tmp_path))
    with pytest.raises(wp.WpError) as e:
        wp.upload(plan, host="ftp.example.com", port=21, user="u", password="p", factory=FakeFTP, resolve=lambda h: [ip])
    assert e.value.code == "wp_host_private"
    with pytest.raises(wp.WpError):
        wp.check_host("a b/c", public)
