from comm_gateway.otp import extract_otp_code


def test_extracts_code_after_cue_word():
    assert extract_otp_code("Your verification code is 123456") == "123456"
    assert extract_otp_code("G-8391 is your Google code") == "8391"
    assert extract_otp_code("Use OTP 4821 to sign in") == "4821"
    assert extract_otp_code("Code: 90 210") == "90210"


def test_ignores_phone_numbers_and_prose():
    # A phone number in the body must not be read as a code.
    assert extract_otp_code("Call us at +1 (415) 555-0132 for help") is None
    # Multiple bare numbers with no cue word -> ambiguous -> no code.
    assert extract_otp_code("Order 12 shipped, arrives in 3 days, total 4500") is None
    assert extract_otp_code("Hey, are we still on for tonight?") is None
    assert extract_otp_code(None) is None


def test_bare_code_only_when_isolated():
    # One isolated numeric run, no cue word: trusted.
    assert extract_otp_code("558210") == "558210"
