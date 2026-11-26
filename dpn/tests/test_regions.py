"""Testes dos rotulos de regiao/pais extraidos do frontend."""
from dpn.atomos.regions import country_label, region_label


def test_known_labels():
    assert region_label("AMN") == "North America"
    assert country_label("US") == "United States of America"
    assert country_label("UB") == "US West"
    assert country_label("BR") == "Brazil"


def test_unknown_label_falls_back_to_code():
    assert region_label("ZZZ") == "ZZZ"
    assert country_label(None) == "—"
