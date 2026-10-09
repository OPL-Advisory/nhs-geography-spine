from nhs_geo_spine.normalise import normalise_postcode


def test_normalise_postcode():
    assert normalise_postcode(" sw1a 2aa ") == ("SW1A 2AA", "SW1A2AA")


def test_invalid_postcode():
    assert normalise_postcode("not a postcode") == (None, None)
