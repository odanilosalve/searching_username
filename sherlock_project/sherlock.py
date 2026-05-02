#! /usr/bin/env python3

"""
Sherlock: Find Usernames Across Social Networks Module

This module contains the main logic to search for usernames at social
networks.
"""

import sys

try:
    from sherlock_project.__init__ import import_error_test_var # noqa: F401
except ImportError:
    print("Did you run Sherlock with `python3 sherlock/sherlock.py ...`?")
    print("This is an outdated method. Please see https://sherlockproject.xyz/installation for up to date instructions.")
    sys.exit(1)

import csv
import signal
import pandas as pd # pyright: ignore[reportMissingModuleSource]
import os
import re
from argparse import ArgumentParser, RawDescriptionHelpFormatter
from json import loads as json_loads
from time import monotonic
from typing import Optional

import requests # pyright: ignore[reportMissingModuleSource]
from requests_futures.sessions import FuturesSession # pyright: ignore[reportMissingImports]

from sherlock_project.__init__ import (
    __longname__,
    __shortname__,
    __version__,
    forge_api_latest_release,
)

from sherlock_project.result import QueryStatus
from sherlock_project.result import QueryResult
from sherlock_project.notify import QueryNotify
from sherlock_project.notify import QueryNotifyPrint
from sherlock_project.sites import SitesInformation
from colorama import init # pyright: ignore[reportMissingModuleSource]
from argparse import ArgumentTypeError


def get_hook_response(hooks, response_time) -> None:
    """Install response time hook into hooks dictionary."""
    if isinstance(hooks["response"], list):
        hooks["response"].insert(0, response_time)
        return
    if isinstance(hooks["response"], tuple):
        hooks["response"] = list(hooks["response"])
        hooks["response"].insert(0, response_time)
        return
    # Must have previously contained a single hook function
    hooks["response"] = [response_time, hooks["response"]]


def get_request_method(session, request_method, url: str) -> callable:
    """Determine the HTTP request method to use."""
    if request_method is None:
        return None
    if request_method == "GET":
        return session.get
    if request_method == "HEAD":
        return session.head
    if request_method == "POST":
        return session.post
    if request_method == "PUT":
        return session.put
    raise RuntimeError(f"Unsupported request_method for {url}")


def get_probe_url(url_probe, url: str, username: str) -> str:
    """Determine the URL to probe for username existence."""
    if url_probe is None:
        return url
    return interpolate_string(url_probe, username)


def get_request_function(session, request, net_info) -> callable:
    """Determine the request function based on error type."""
    if request is not None:
        return request
    if net_info["errorType"] == "status_code":
        return session.head
    return session.get


def get_allow_redirects(error_type: str) -> bool:
    """Determine if redirects should be allowed."""
    return error_type != "response_url"


def make_request(request, url_probe, headers, proxy, allow_redirects, timeout, request_payload):
    """Make the actual HTTP request."""
    if proxy is not None:
        proxies = {"http": proxy, "https": proxy}
        return request(
            url=url_probe, headers=headers, proxies=proxies,
            allow_redirects=allow_redirects, timeout=timeout, json=request_payload
        )
    return request(
        url=url_probe, headers=headers, allow_redirects=allow_redirects,
        timeout=timeout, json=request_payload
    )


def process_site_request(session, net_info, url, headers, username, proxy, timeout):
    """Process a single site's request setup and execution."""
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
        """Request URL.

        This extends the FuturesSession request method to calculate a response
        time metric to each request.

        It is taken (almost) directly from the following Stack Overflow answer:
        https://github.com/ross/requests-futures#working-in-the-background

        Keyword Arguments:
        self                   -- This object.
        method                 -- String containing method desired for request.
        url                    -- String containing URL for request.
        hooks                  -- Dictionary containing hooks to execute after
                                   request finishes.
        args                   -- Arguments.
        kwargs                 -- Keyword arguments.

        Return Value:
        Request object.
        """
        # Record the start time for the request.
        if hooks is None:
            hooks = {}
        start = monotonic()

        def response_time(resp, *args, **kwargs):
            """Response Time Hook.

            Keyword Arguments:
            resp                   -- Response object.
            args                   -- Arguments.
            kwargs                 -- Keyword arguments.

            Return Value:
            Nothing.
            """
            resp.elapsed = monotonic() - start

            

        # Install hook to execute when response completes.
        # Make sure that the time measurement hook is first, so we will not
        # track any later hook's execution time.
        try:
            get_hook_response(hooks, response_time)
        except KeyError:
            # No response hook was already defined, so install it ourselves.
            hooks["response"] = [response_time]

        return super(SherlockFuturesSession, self).request(
            method, url, hooks=hooks, *args, **kwargs
        )


def get_response(request_future):
    # Default for Response object if some failure occurs.
    response = None

    error_context = "General Unknown Error"
    exception_text = None
    try:
        response = request_future.result()
        if response.status_code:
            # Status code exists in response object
            error_context = None
    except requests.exceptions.HTTPError as errh:
        error_context = "HTTP Error"
        exception_text = str(errh)
    except requests.exceptions.ProxyError as errp:
        error_context = "Proxy Error"
        exception_text = str(errp)
    except requests.exceptions.ConnectionError as errc:
        error_context = "Error Connecting"
        exception_text = str(errc)
    except requests.exceptions.Timeout as errt:
        error_context = "Timeout Error"
        exception_text = str(errt)
    except requests.exceptions.RequestException as err:
        error_context = "Unknown Error"
        exception_text = str(err)

    return response, error_context, exception_text


def interpolate_string(input_object, username):
    if isinstance(input_object, str):
        return input_object.replace("{}", username)
    elif isinstance(input_object, dict):
        return {k: interpolate_string(v, username) for k, v in input_object.items()}
    elif isinstance(input_object, list):
        return [interpolate_string(i, username) for i in input_object]
    return input_object


def check_for_parameter(username):
    """checks if {?} exists in the username
    if exist it means that sherlock is looking for more multiple username"""
    return "{?}" in username


CHECK_SYMBOLS = ["_", "-", "."]


def multiple_usernames(username):
    """replace the parameter with with symbols and return a list of usernames"""
    all_usernames = []
    for i in CHECK_SYMBOLS:
        all_usernames.append(username.replace("{?}", i))
    return all_usernames


def check_waf_hits(r, waf_hit_msgs) -> bool:
    """Check if response contains WAF fingerprints."""
    return any(hitMsg in r.text for hitMsg in waf_hit_msgs)


def process_message_error(r, net_info):
    """Process error detection via message in HTML."""
    errors = net_info.get("errorMsg")

    if isinstance(errors, str) and errors in r.text:
        return QueryStatus.AVAILABLE

    if isinstance(errors, list):
        for error in errors:
            if error in r.text:
                return QueryStatus.AVAILABLE

    return QueryStatus.CLAIMED


def process_status_code(r, net_info, query_status):
    """Process error detection via HTTP status code."""
    if query_status == QueryStatus.AVAILABLE:
        return QueryStatus.AVAILABLE

    error_codes = net_info.get("errorCode")
    result = QueryStatus.CLAIMED

    if isinstance(error_codes, int):
        error_codes = [error_codes]

    if (error_codes is not None and r.status_code in error_codes) or r.status_code >= 300 or r.status_code < 200:
        result = QueryStatus.AVAILABLE

    return result


def process_response_url(r, query_status):
    """Process error detection via response URL redirect."""
    if query_status == QueryStatus.AVAILABLE:
        return QueryStatus.AVAILABLE

    if 200 <= r.status_code < 300:
        return QueryStatus.CLAIMED
    return QueryStatus.AVAILABLE


def determine_query_status(r, error_text, error_type, net_info, waf_hit_msgs) -> tuple:
    """Determine the query status based on various detection methods."""
    query_status = QueryStatus.UNKNOWN

    if error_text is not None:
        return query_status, error_text

    if check_waf_hits(r, waf_hit_msgs):
        return QueryStatus.WAF, None

    # Unknown error type check
    if any(errtype not in ["message", "status_code", "response_url"] for errtype in error_type):
        return QueryStatus.UNKNOWN, f"Unknown error type '{error_type}'"

    # Process by error type
    if "message" in error_type:
        query_status = process_message_error(r, net_info)

    query_status = process_status_code(r, net_info, query_status)
    query_status = process_response_url(r, query_status)

    return query_status, None


def process_site_result(results_site, social_network, net_info, url, username, session, headers, proxy, timeout, query_notify):
    """Process a single site's request and store the future."""
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
    future = process_site_request(session, net_info, url, headers, username, proxy, timeout)
    net_info["request_future"] = future
    return True


def process_response(results_site, social_network, net_info, r, error_text, error_type, username, dump_response, query_notify):
    """Process the response from a site request."""
    query_status = QueryStatus.UNKNOWN
    error_context = None

    waf_hit_msgs = [
        r'.loading-spinner{visibility:hidden}body.no-js .challenge-running{display:none}body.dark{background-color:#222;color:#d9d9d9}body.dark a{color:#fff}body.dark a:hover{color:#ee730a;text-decoration:underline}body.dark .lds-ring div{border-color:#999 transparent transparent}body.dark .font-red{color:#b20f03}body.dark',
        r'<span id="challenge-error-text">',
        r'AwsWafIntegration.forceRefreshToken',
        r'{return l.onPageView}}),Object.defineProperty(r,"perimeterxIdentifiers",{enumerable:',
    ]

    query_status, error_context = determine_query_status(r, error_text, error_type, net_info, waf_hit_msgs)

    if dump_response:
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
        print("VERDICT       : " + str(query_status))
        print("+++++++++++++++++++++")

    result = QueryResult(
        username=username, site_name=social_network,
        site_url_user=results_site.get("url_user"),
        status=query_status,
        query_time=getattr(r, 'elapsed', None),
        context=error_context
    )
    query_notify.update(result)

    results_site["status"] = result
    results_site["http_status"] = getattr(r, 'status_code', "?")
    raw_text = getattr(r, 'text', None)
    results_site["response_text"] = ""
    if raw_text is not None:
        results_site["response_text"] = raw_text.encode(r.encoding or 'UTF-8')
    return results_site


def sherlock(
    username: str,
    site_data: dict[str, dict[str, str]],
    query_notify: QueryNotify,
    dump_response: bool = False,
    proxy: Optional[str] = None,
    timeout: int = 60,
) -> dict[str, dict[str, str | QueryResult]]:
    """Run Sherlock Analysis.

    Checks for existence of username on various social media sites.

    Keyword Arguments:
    username               -- String indicating username that report
                               should be created against.
    site_data              -- Dictionary containing all of the site data.
    query_notify           -- Object with base type of QueryNotify().
                               This will be used to notify the caller about
                               query results.
    proxy                  -- String indicating the proxy URL
    timeout                -- Time in seconds to wait before timing out request.
                               Default is 60 seconds.

    Return Value:
    Dictionary containing results from report. Key of dictionary is the name
    of the social network site, and the value is another dictionary with
    the following keys:
        url_main:      URL of main site.
        url_user:      URL of user on site (if account exists).
        status:        QueryResult() object indicating results of test for
                        account existence.
        http_status:   HTTP status code of query which checked for existence on
                        site.
        response_text: Text that came back from request.  May be None if
                        there was an HTTP error when checking for existence.
    """
    # Notify caller that we are starting the query.
    query_notify.start(username)

    # Normal requests
    underlying_session = requests.session()

    max_workers = min(len(site_data), 20)

    # Create multi-threaded session for all requests.
    session = SherlockFuturesSession(max_workers=max_workers, session=underlying_session)

    # Results from analysis of all sites
    results_total = {}

    # First create futures for all requests.
    for social_network, net_info in site_data.items():
        results_site = {"url_main": net_info.get("urlMain")}
        headers = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64; rv:129.0) Gecko/20100101 Firefox/129.0"}
        processed_username_for_interpolation = str(username.replace(' ', '%20'))
        url = interpolate_string(net_info["url"], processed_username_for_interpolation)

        process_site_result(results_site, social_network, net_info, url, username, session, headers, proxy, timeout, query_notify)
        results_total[social_network] = results_site

    # Process responses
    for social_network, net_info in site_data.items():
        results_site = results_total.get(social_network)
        if results_site is None:
            continue
        if results_site.get("status") is not None:
            continue

        error_type = net_info["errorType"]
        if isinstance(error_type, str):
            error_type = [error_type]

        future = net_info["request_future"]
        r, error_text, _ = get_response(request_future=future)

        results_total[social_network] = process_response(
            results_site, social_network, net_info, r, error_text, error_type, username, dump_response, query_notify
        )

    return results_total


def timeout_check(value):
    """Check Timeout Argument.

    Checks timeout for validity.

    Keyword Arguments:
    value                  -- Time in seconds to wait before timing out request.

    Return Value:
    Floating point number representing the time (in seconds) that should be
    used for the timeout.

    NOTE:  Will raise an exception if the timeout in invalid.
    """

    float_value = float(value)

    if float_value <= 0:
        raise ArgumentTypeError(
            f"Invalid timeout value: {value}. Timeout must be a positive number."
        )

    return float_value


def handler(signal_received, frame):
    """Exit gracefully without throwing errors

    Source: https://www.devdungeon.com/content/python-catch-sigint-ctrl-c
    """
    sys.exit(0)


def check_for_updates():
    """Check for newer version of Sherlock and notify user."""
    try:
        latest_release_raw = requests.get(forge_api_latest_release, timeout=10).text
        latest_release_json = json_loads(latest_release_raw)
        latest_remote_tag = latest_release_json["tag_name"]

        if latest_remote_tag[1:] != __version__:
            print(
                f"Update available! {__version__} --> {latest_remote_tag[1:]}"
                f"\n{latest_release_json['html_url']}"
            )
    except Exception as error:
        print(f"A problem occurred while checking for an update: {error}")


def setup_color_output(no_color: bool) -> None:
    """Setup colorama color output based on user preference."""
    if no_color:
        init(strip=True, convert=False)
        return
    init(autoreset=True)


def load_site_information(args):
    """Load site information from local file or remote source."""
    try:
        if args.local:
            return SitesInformation(
                os.path.join(os.path.dirname(__file__), "resources/data.json"),
                honor_exclusions=False,
            )
        else:
            json_file_location = args.json_file
            if args.json_file and args.json_file.isnumeric():
                pull_number = args.json_file
                pull_url = f"https://api.github.com/repos/sherlock-project/sherlock/pulls/{pull_number}"
                pull_request_raw = requests.get(pull_url, timeout=10).text
                pull_request_json = json_loads(pull_request_raw)

                if "message" in pull_request_json:
                    print(f"ERROR: Pull request #{pull_number} not found.")
                    sys.exit(1)

                head_commit_sha = pull_request_json["head"]["sha"]
                json_file_location = f"https://raw.githubusercontent.com/sherlock-project/sherlock/{head_commit_sha}/sherlock_project/resources/data.json"

            return SitesInformation(
                data_file_path=json_file_location,
                honor_exclusions=not args.ignore_exclusions,
                do_not_exclude=args.site_list,
            )
    except Exception as error:
        print(f"ERROR:  {error}")
        sys.exit(1)


def build_site_data(site_data_all, site_list) -> tuple:
    """Build filtered site data and collect missing sites."""
    site_data = {}
    site_missing = []

    for site in site_list:
        counter = 0
        for existing_site in site_data_all:
            if site.lower() == existing_site.lower():
                site_data[existing_site] = site_data_all[existing_site]
                counter += 1
        if counter == 0:
            site_missing.append(f"'{site}'")

    return site_data, site_missing


def filter_sites(sites, site_list, nsfw: bool) -> dict:
    """Filter sites based on user preferences."""
    if not nsfw:
        sites.remove_nsfw_sites(do_not_remove=site_list)

    site_data_all = {site.name: site.information for site in sites}

    if not site_list:
        return site_data_all

    site_data, site_missing = build_site_data(site_data_all, site_list)

    if site_missing:
        print(f"Error: Desired sites not found: {', '.join(site_missing)}.")

    if not site_data:
        sys.exit(1)

    return site_data


def get_result_file(args, username: str) -> str:
    """Determine the output file path based on arguments."""
    result_file = f"{username}.txt"
    if args.output:
        return args.output
    if args.folderoutput:
        os.makedirs(args.folderoutput, exist_ok=True)
        return os.path.join(args.folderoutput, f"{username}.txt")
    return result_file


def write_txt_output(results, result_file: str) -> None:
    """Write results to a text file."""
    with open(result_file, "w", encoding="utf-8") as file:
        exists_counter = 0
        for website_name in results:
            dictionary = results[website_name]
            if dictionary.get("status").status == QueryStatus.CLAIMED:
                exists_counter += 1
                file.write(dictionary["url_user"] + "\n")
        file.write(f"Total Websites Username Detected On : {exists_counter}\n")


def write_csv_output(results, args, username: str) -> None:
    """Write results to a CSV file."""
    result_file = f"{username}.csv"
    if args.folderoutput:
        os.makedirs(args.folderoutput, exist_ok=True)
        result_file = os.path.join(args.folderoutput, result_file)

    with open(result_file, "w", newline="", encoding="utf-8") as csv_report:
        writer = csv.writer(csv_report)
        writer.writerow(["username", "name", "url_main", "url_user", "exists", "http_status", "response_time_s"])
        for site in results:
            if args.print_found and not args.print_all and results[site]["status"].status != QueryStatus.CLAIMED:
                continue
            response_time_s = results[site]["status"].query_time
            if response_time_s is None:
                response_time_s = ""
            writer.writerow([
                username, site, results[site]["url_main"], results[site]["url_user"],
                str(results[site]["status"].status), results[site]["http_status"], response_time_s
            ])


def write_xlsx_output(results, args, username: str) -> None:
    """Write results to an XLSX file."""
    usernames = []
    names = []
    url_main = []
    url_user = []
    exists = []
    http_status = []
    response_time_s = []

    for site in results:
        if args.print_found and not args.print_all and results[site]["status"].status != QueryStatus.CLAIMED:
            continue
        rt = results[site]["status"].query_time
        if rt is None:
            response_time_s.append("")
        if rt is not None:
            response_time_s.append(rt)
        usernames.append(username)
        names.append(site)
        url_main.append(results[site]["url_main"])
        url_user.append(results[site]["url_user"])
        exists.append(str(results[site]["status"].status))
        http_status.append(results[site]["http_status"])

    data_frame = pd.DataFrame({
        "username": usernames, "name": names,
        "url_main": [f'=HYPERLINK("{u}")' for u in url_main],
        "url_user": [f'=HYPERLINK("{u}")' for u in url_user],
        "exists": exists, "http_status": http_status, "response_time_s": response_time_s,
    })
    data_frame.to_excel(f"{username}.xlsx", sheet_name="sheet1", index=False)


def process_usernames(args) -> list:
    """Process usernames including multiple username expansion."""
    all_usernames = []
    for username in args.username:
        if not check_for_parameter(username):
            all_usernames.append(username)
            continue
        for name in multiple_usernames(username):
            all_usernames.append(name)
    return all_usernames


def validate_output_args(args) -> None:
    """Validate output-related arguments."""
    if args.output is not None and args.folderoutput is not None:
        print("You can only use one of the output methods.")
        sys.exit(1)
    if args.output is not None and len(args.username) != 1:
        print("You can only use --output with a single username")
        sys.exit(1)


def main():
    parser = ArgumentParser(
        formatter_class=RawDescriptionHelpFormatter,
        description=f"{__longname__} (Version {__version__})",
    )
    parser.add_argument("--version", action="version", version=f"{__shortname__} v{__version__}", help="Display version information and dependencies.")
    parser.add_argument("--verbose", "-v", "-d", "--debug", action="store_true", dest="verbose", default=False, help="Display extra debugging information and metrics.")
    parser.add_argument("--folderoutput", "-fo", dest="folderoutput", help="If using multiple usernames, the output of the results will be saved to this folder.")
    parser.add_argument("--output", "-o", dest="output", help="If using single username, the output of the result will be saved to this file.")
    parser.add_argument("--csv", action="store_true", dest="csv", default=False, help="Create Comma-Separated Values (CSV) File.")
    parser.add_argument("--xlsx", action="store_true", dest="xlsx", default=False, help="Create the standard file for the modern Microsoft Excel spreadsheet (xlsx).")
    parser.add_argument("--site", action="append", metavar="SITE_NAME", dest="site_list", default=[], help="Limit analysis to just the listed sites. Add multiple options to specify more than one site.")
    parser.add_argument("--proxy", "-p", metavar="PROXY_URL", action="store", dest="proxy", default=None, help="Make requests over a proxy. e.g. socks5://127.0.0.1:1080")
    parser.add_argument("--dump-response", action="store_true", dest="dump_response", default=False, help="Dump the HTTP response to stdout for targeted debugging.")
    parser.add_argument("--json", "-j", metavar="JSON_FILE", dest="json_file", default=None, help="Load data from a JSON file or an online, valid, JSON file. Upstream PR numbers also accepted.")
    parser.add_argument("--timeout", action="store", metavar="TIMEOUT", dest="timeout", type=timeout_check, default=60, help="Time (in seconds) to wait for response to requests (Default: 60)")
    parser.add_argument("--print-all", action="store_true", dest="print_all", default=False, help="Output sites where the username was not found.")
    parser.add_argument("--print-found", action="store_true", dest="print_found", default=True, help="Output sites where the username was found (also if exported as file).")
    parser.add_argument("--no-color", action="store_true", dest="no_color", default=False, help="Don't color terminal output")
    parser.add_argument("username", nargs="+", metavar="USERNAMES", action="store", help="One or more usernames to check with social networks. Check similar usernames using {?} (replace to '_', '-', '.').")
    parser.add_argument("--browse", "-b", action="store_true", dest="browse", default=False, help="Browse to all results on default browser.")
    parser.add_argument("--local", "-l", action="store_true", default=False, help="Force the use of the local data.json file.")
    parser.add_argument("--nsfw", action="store_true", default=False, help="Include checking of NSFW sites from default list.")
    parser.add_argument("--txt", action="store_true", dest="output_txt", default=False, help="Enable creation of a txt file")
    parser.add_argument("--ignore-exclusions", action="store_true", dest="ignore_exclusions", default=False, help="Ignore upstream exclusions (may return more false positives)")

    args = parser.parse_args()

    signal.signal(signal.SIGINT, handler)
    check_for_updates()

    if args.proxy is not None:
        print("Using the proxy: " + args.proxy)

    setup_color_output(args.no_color)
    validate_output_args(args)

    sites = load_site_information(args)
    site_data = filter_sites(sites, args.site_list, args.nsfw)

    query_notify = QueryNotifyPrint(result=None, verbose=args.verbose, print_all=args.print_all, browse=args.browse)
    all_usernames = process_usernames(args)

    for username in all_usernames:
        results = sherlock(username, site_data, query_notify, dump_response=args.dump_response, proxy=args.proxy, timeout=args.timeout)
        result_file = get_result_file(args, username)

        if args.output_txt:
            write_txt_output(results, result_file)
        if args.csv:
            write_csv_output(results, args, username)
        if args.xlsx:
            write_xlsx_output(results, args, username)
        print()

    query_notify.finish()


if __name__ == "__main__":
    main()
