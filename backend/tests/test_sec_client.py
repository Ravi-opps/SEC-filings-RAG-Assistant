from unittest.mock import Mock

import pytest
import requests

from sec_client import (
    SecClient,
    SecConnectionError,
    SecHTTPError,
    SecInvalidJSONError,
    SecTimeoutError,
    submissions_url,
)


def make_response(status=200, json_data=None, content=b"", headers=None):
    response = Mock()
    response.status_code = status
    response.content = content
    response.headers = headers or {}
    if json_data is not None:
        response.json.return_value = json_data
    else:
        response.json.side_effect = ValueError("not json")
    if status >= 400:
        response.raise_for_status.side_effect = requests.HTTPError(
            f"HTTP {status}"
        )
    else:
        response.raise_for_status.return_value = None
    return response


def test_http_success_returns_json():
    client = SecClient()
    client.session.get = Mock(return_value=make_response(json_data={"ok": True}))
    assert client.get_json("https://example.test/data.json") == {"ok": True}


def test_http_success_returns_document_bytes_and_content_type():
    client = SecClient()
    response = make_response(
        content=b"<html>ok</html>", headers={"Content-Type": "text/html"}
    )
    client.session.get = Mock(return_value=response)
    content, content_type = client.get_document("https://example.test/doc")
    assert content == b"<html>ok</html>"
    assert content_type == "text/html"


def test_http_error_is_converted_to_domain_error():
    client = SecClient()
    client.session.get = Mock(return_value=make_response(status=404))
    with pytest.raises(SecHTTPError):
        client.get_json("https://example.test/missing.json")


def test_timeout_is_converted_to_domain_error():
    client = SecClient()
    client.session.get = Mock(side_effect=requests.Timeout("timed out"))
    with pytest.raises(SecTimeoutError):
        client.get_json("https://example.test/slow.json")


def test_connection_failure_is_converted_to_domain_error():
    client = SecClient()
    client.session.get = Mock(side_effect=requests.ConnectionError("no route"))
    with pytest.raises(SecConnectionError):
        client.get_json("https://example.test/down.json")


def test_invalid_json_is_converted_to_domain_error():
    client = SecClient()
    client.session.get = Mock(return_value=make_response(content=b"not json"))
    with pytest.raises(SecInvalidJSONError):
        client.get_json("https://example.test/bad.json")


def test_ticker_map_is_cached():
    client = SecClient()
    client.session.get = Mock(return_value=make_response(json_data={"0": {}}))
    assert client.get_ticker_map() == {"0": {}}
    assert client.get_ticker_map() == {"0": {}}
    assert client.session.get.call_count == 1


def test_submissions_url_pads_cik():
    assert (
        submissions_url(320193)
        == "https://data.sec.gov/submissions/CIK0000320193.json"
    )


def test_user_agent_identifies_application_and_contact():
    client = SecClient()
    user_agent = client.session.headers["User-Agent"]
    assert "RAG assistant" in user_agent
    assert "@" in user_agent
    assert client.timeout > 0
