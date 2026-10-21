from liara_agent.registrar import guide, is_irnic


def codes(g):
    return [s["code"] for s in g["steps"]]


def test_ir_domains_and_their_second_levels_are_irnics():
    assert all(is_irnic(d) for d in ("shop.ir", "shop.co.ir", "uni.ac.ir", "SHOP.IR."))
    assert not is_irnic("shop.com") and not is_irnic("ir.example.com")


def test_an_ir_domain_on_liara_dns_walks_through_the_irnic_panel_with_liaras_nameservers():
    g = guide({"name": "shop.ir", "zone": {"name_servers": ["ns1.x.example", "ns2.x.example"], "active": False}, "records": []})
    assert g["kind"] == "irnic" and g["mode"] == "liara_dns" and g["panel_url"].startswith("https://new.nic.ir/")
    assert codes(g) == ["irnic_login", "irnic_domains", "irnic_manage", "irnic_dns_section", "irnic_edit_rows", "irnic_enter_ns", "irnic_ip_empty", "irnic_secret", "irnic_apply", "wait", "verify"]
    assert g["steps"][5]["params"]["nameservers"] == "ns1.x.example, ns2.x.example" and g["wait_hours"]["max"] >= 24 and g["sources"]


def test_nothing_is_left_to_do_at_the_registrar_once_liara_serves_the_zone():
    g = guide({"name": "shop.ir", "zone": {"name_servers": ["ns1.x"], "active": True}, "records": []})
    assert codes(g) == ["done_nameservers", "wait", "verify"]


def test_nameservers_not_known_yet_say_so_instead_of_guessing():
    g = guide({"name": "shop.ir", "zone": {"name_servers": [], "active": False}, "records": []})
    assert "ns_pending" in codes(g) and "irnic_enter_ns" not in codes(g)


def test_a_domain_with_its_dns_elsewhere_gets_the_records_to_add_there():
    g = guide({"name": "shop.com", "zone": None, "records": [{"type": "CNAME", "name": "@", "value": "shop-web.liara.run"}]})
    assert g["kind"] == "generic" and g["mode"] == "own_dns" and g["panel_url"] is None
    assert codes(g) == ["reg_login", "reg_find_ns", "reg_own_ns", "records_at_provider", "wait", "verify"]
    assert g["records"] == [{"type": "CNAME", "name": "@", "value": "shop-web.liara.run"}] and g["steps"][3]["params"]["count"] == 1
