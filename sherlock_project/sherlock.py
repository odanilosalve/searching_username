#! /usr/bin/env python3

"""
Sherlock: Find Usernames Across Social Networks Module

This module contains the core engine for searching usernames across social networks.
"""

import sys

try:
    from sherlock_project.__init__ import import_error_test_var # noqa: F401
except ImportError:
    print("Did you run Sherlock with `python3 sherlock/sherlock.py ...`?")
    print("This is an outdated method. Please see https://sherlockproject.xyz/installation for up to date instructions.")
    sys.exit(1)

import re
from time import monotonic
from typing import Optional

import requests # pyright: ignore[reportMissingModuleSource]
from requests_futures.sessions import FuturesSession # pyright: ignore[reportMissingImports]

from sherlock_project.result import ErrorType, QueryResult, QueryStatus
from sherlock_project.notify import QueryNotify


USER_AGENT = "Mozilla/5.0 (X11; Linux x86_64; rv:129.0) Gecko/20100101 Firefox/129.0"

WAF_HIT_MSGS = [
    r'.loading-spinner{visibility:hidden}body.no-js .challenge-running{display:none}body.dark{background-color:#222;color:#d9d9d9}body.dark a{color:#fff}body.dark a:hover{color:#ee730a;text-decoration:underline}body.dark .lds-ring div{border-color:#999 transparent transparent}body.dark .font-red{color:#b20f03}body.dark',
    r'<span id="challenge-error-text">',
    r'AwsWafIntegration.forceRefreshToken',
    r'{return l.onPageView}}),Object.defineProperty(r,"perimeterxIdentifiers",{enumerable:',
]

CHECK_SYMBOLS = ["_", "-", "."]


def get_hook_response(hooks: dict, response_time) -> None:
    """Insert a response-time hook at the front of the hooks list."""
    if isinstance(hooks["response"], list):
        hooks["response"].insert(0, response_time)
        return
    if isinstance(hooks["response"], tuple):
        hooks["response"] = list(hooks["response"])
        hooks["response"].insert(0, response_time)
        return
    hooks["response"] = [response_time, hooks["response"]]


def get_request_method(session, request_method: str | None, url: str):
    """Return the session method matching request_method, or None."""
    if request_method is None:
        return None
    methods = {"GET": session.get, "HEAD": session.head, "POST": session.post, "PUT": session.put}
    if request_method in methods:
        return methods[request_method]
    raise RuntimeError(f"Unsupported request_method for {url}")


def get_probe_url(url_probe: str | None, url: str, username: str) -> str:
    """Return url_probe with username substituted, or the original url."""
    if url_probe is None:
        return url
    return interpolate_string(url_probe, username)


def get_request_function(session, request, net_info: dict):
    """Return the appropriate request callable based on error type."""
    if request is not None:
        return request
    if net_info["errorType"] == ErrorType.STATUS_CODE:
        return session.head
    return session.get


def get_allow_redirects(error_type: str) -> bool:
    """Return False only when error detection relies on the final response URL."""
    return error_type != ErrorType.RESPONSE_URL


def make_request(request, url_probe: str, headers: dict, proxy: str | None,
                 allow_redirects: bool, timeout: int, request_payload):
    """Execute the HTTP request, injecting proxy settings when provided."""
    kwargs = dict(
        url=url_probe, headers=headers,
        allow_redirects=allow_redirects, timeout=timeout, json=request_payload,
    )
    if proxy is not None:
        kwargs["proxies"] = {"http": proxy, "https": proxy}
    return request(**kwargs)


def process_site_request(session, net_info: dict, url: str, headers: dict,
                         username: str, proxy: str | None, timeout: int):
    """Build and dispatch the async HTTP request for one site."""
    url_probe = net_info.get("urlProbe")
    request_method = net_info.get("request_method")
    request_payload = net_info.get("request_payload")

    request = get_request_method(session, request_method, url)
    if request_payload is not None:
        request_payload = interpolate_string(request_payload, str(username))

    url_probe = get_probe_url(url_probe, url, username)
    request = get_request_function(session, request, net_info)
    allow_redirects = get_allow_redirects(net_info["errorType"])

    return make_request(request, url_probe, headers, proxy, allow_redirects, timeout, request_payload)


class SherlockFuturesSession(FuturesSession):
    def request(self, method, url, hooks=None, *args, **kwargs):
        """Wrap FuturesSession.request to record elapsed time on every response."""
        if hooks is None:
            hooks = {}
        start = monotonic()

        def response_time(resp, *args, **kwargs):
            resp.elapsed = monotonic() - start

        try:
            get_hook_response(hooks, response_time)
        except KeyError:
            hooks["response"] = [response_time]

        return super().request(method, url, hooks=hooks, *args, **kwargs)


def get_response(request_future) -> tuple:
    """Resolve a future and return (response, error_context)."""
    response = None
    error_context = "General Unknown Error"
    try:
        response = request_future.result()
        if response.status_code:
            error_context = None
    except requests.exceptions.HTTPError:
        error_context = "HTTP Error"
    except requests.exceptions.ProxyError:
        error_context = "Proxy Error"
    except requests.exceptions.ConnectionError:
        error_context = "Error Connecting"
    except requests.exceptions.Timeout:
        error_context = "Timeout Error"
    except requests.exceptions.RequestException:
        error_context = "Unknown Error"
    return response, error_context


def interpolate_string(input_object, username: str):
    """Recursively replace '{}' with username in strings, dicts, and lists."""
    if isinstance(input_object, str):
        return input_object.replace("{}", username)
    if isinstance(input_object, dict):
        return {k: interpolate_string(v, username) for k, v in input_object.items()}
    if isinstance(input_object, list):
        return [interpolate_string(i, username) for i in input_object]
    return input_object


def check_for_parameter(username: str) -> bool:
    """Return True if the username contains a {?} expansion placeholder."""
    return "{?}" in username


def multiple_usernames(username: str) -> list[str]:
    """Expand {?} into each CHECK_SYMBOLS variant and return the list."""
    return [username.replace("{?}", symbol) for symbol in CHECK_SYMBOLS]


def check_waf_hits(r, waf_hit_msgs: list) -> bool:
    """Return True if any known WAF fingerprint appears in the response body."""
    return any(msg in r.text for msg in waf_hit_msgs)


def process_message_error(r, net_info: dict) -> QueryStatus:
    """Detect username availability via an error message in the response body."""
    errors = net_info.get("errorMsg")
    if isinstance(errors, str):
        return QueryStatus.AVAILABLE if errors in r.text else QueryStatus.CLAIMED
    if isinstance(errors, list) and any(e in r.text for e in errors):
        return QueryStatus.AVAILABLE
    return QueryStatus.CLAIMED


def process_status_code(r, net_info: dict, query_status: QueryStatus) -> QueryStatus:
    """Detect username availability via HTTP status code."""
    if query_status == QueryStatus.AVAILABLE:
        return QueryStatus.AVAILABLE

    error_codes = net_info.get("errorCode")
    if isinstance(error_codes, int):
        error_codes = [error_codes]

    if (error_codes is not None and r.status_code in error_codes) \
            or not (200 <= r.status_code < 300):
        return QueryStatus.AVAILABLE

    return QueryStatus.CLAIMED


def process_response_url(r, query_status: QueryStatus) -> QueryStatus:
    """Detect username availability via the final response URL after redirects."""
    if query_status == QueryStatus.AVAILABLE:
        return QueryStatus.AVAILABLE
    return QueryStatus.CLAIMED if 200 <= r.status_code < 300 else QueryStatus.AVAILABLE


def determine_query_status(r, error_text: str | None, error_type: list,
                           net_info: dict, waf_hit_msgs: list) -> tuple:
    """Determine the final QueryStatus and optional error context for a response."""
    if error_text is not None:
        return QueryStatus.UNKNOWN, error_text

    if check_waf_hits(r, waf_hit_msgs):
        return QueryStatus.WAF, None

    if any(errtype not in list(ErrorType) for errtype in error_type):
        return QueryStatus.UNKNOWN, f"Unknown error type '{error_type}'"

    query_status = QueryStatus.UNKNOWN
    if ErrorType.MESSAGE in error_type:
        query_status = process_message_error(r, net_info)

    query_status = process_status_code(r, net_info, query_status)
    query_status = process_response_url(r, query_status)

    return query_status, None


def process_site_result(results_site: dict, social_network: str, net_info: dict,
                        url: str, username: str, session, headers: dict,
                        proxy: str | None, timeout: int, query_notify: QueryNotify) -> bool:
    """Validate username regex, then dispatch the async request for one site."""
    if "headers" in net_info:
        headers.update(net_info["headers"])

    regex_check = net_info.get("regexCheck")
    if regex_check and re.search(regex_check, username) is None:
        results_site["status"] = QueryResult(username, social_network, url, QueryStatus.ILLEGAL)
        results_site["url_user"] = ""
        results_site["http_status"] = ""
        results_site["response_text"] = ""
        query_notify.update(results_site["status"])
        return False

    results_site["url_user"] = url
    net_info["request_future"] = process_site_request(
        session, net_info, url, headers, username, proxy, timeout
    )
    return True


def _dump_response_debug(social_network: str, username: str, results_site: dict,
                         error_type: list, net_info: dict, r) -> None:
    """Print raw response details for debugging (--dump-response flag)."""
    print("+++++++++++++++++++++")
    print(f"TARGET NAME   : {social_network}")
    print(f"USERNAME      : {username}")
    print(f"TARGET URL    : {results_site.get('url_user')}")
    print(f"TEST METHOD   : {error_type}")
    try:
        print(f"STATUS CODES  : {net_info['errorCode']}")
    except KeyError:
        pass
    print("Results...")
    try:
        print(f"RESPONSE CODE : {r.status_code}")
    except Exception:
        pass
    try:
        print(f"ERROR TEXT    : {net_info['errorMsg']}")
    except KeyError:
        pass
    print(">>>>> BEGIN RESPONSE TEXT")
    try:
        print(r.text)
    except Exception:
        pass
    print("<<<<< END RESPONSE TEXT")


def process_response(results_site: dict, social_network: str, net_info: dict,
                     r, error_text: str | None, error_type: list, username: str,
                     dump_response: bool, query_notify: QueryNotify) -> dict:
    """Evaluate the HTTP response and record the QueryResult in results_site."""
    query_status, error_context = determine_query_status(
        r, error_text, error_type, net_info, WAF_HIT_MSGS
    )

    if dump_response:
        _dump_response_debug(social_network, username, results_site, error_type, net_info, r)
        print("VERDICT       : " + str(query_status))
        print("+++++++++++++++++++++")

    result = QueryResult(
        username=username,
        site_name=social_network,
        site_url_user=results_site.get("url_user"),
        status=query_status,
        query_time=getattr(r, 'elapsed', None),
        context=error_context,
    )
    query_notify.update(result)

    results_site["status"] = result
    results_site["http_status"] = getattr(r, 'status_code', "?")
    raw_text = getattr(r, 'text', None)
    results_site["response_text"] = raw_text.encode(r.encoding or 'UTF-8') if raw_text else ""
    return results_site


def sherlock(
    username: str,
    site_data: dict[str, dict],
    query_notify: QueryNotify,
    dump_response: bool = False,
    proxy: Optional[str] = None,
    timeout: int = 60,
    session: Optional[requests.Session] = None,
) -> dict[str, dict]:
    """Search for username across all sites in site_data.

    Returns a dict keyed by site name with url_main, url_user, status,
    http_status, and response_text for each site.
    """
    query_notify.start(username)

    underlying_session = session or requests.session()
    futures_session = SherlockFuturesSession(
        max_workers=min(len(site_data), 20),
        session=underlying_session,
    )

    results_total: dict = {}

    for social_network, net_info in site_data.items():
        results_site: dict = {"url_main": net_info.get("urlMain")}
        headers = {"User-Agent": USER_AGENT}
        encoded_username = username.replace(' ', '%20')
        url = interpolate_string(net_info["url"], encoded_username)

        process_site_result(
            results_site, social_network, net_info, url,
            username, futures_session, headers, proxy, timeout, query_notify,
        )
        results_total[social_network] = results_site

    for social_network, net_info in site_data.items():
        results_site = results_total.get(social_network)
        if results_site is None or results_site.get("status") is not None:
            continue

        error_type = net_info["errorType"]
        if isinstance(error_type, str):
            error_type = [error_type]

        r, error_text = get_response(request_future=net_info["request_future"])

        results_total[social_network] = process_response(
            results_site, social_network, net_info, r,
            error_text, error_type, username, dump_response, query_notify,
        )

    return results_total
