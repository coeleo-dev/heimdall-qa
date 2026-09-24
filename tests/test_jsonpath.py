from heimdall_qa.jsonpath import lookup


def test_jsonpath_nested_index_and_object():
    """The two shapes a pack reads a body with: an index and a named field."""
    body = {
        "quantities": [{"rating": {"status": "SUCCESS", "amount": 0.003}}],
        "conversion": {"test_drive_consumed_percent": 12.5},
    }
    assert lookup(body, "$.quantities[0].rating.status") == "SUCCESS"
    assert lookup(body, "$.conversion.test_drive_consumed_percent") == 12.5
