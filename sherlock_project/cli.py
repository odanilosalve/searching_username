"""Sherlock CLI Module

This module contains the command-line interface for Sherlock.
"""

import sys
import signal
import os
from argparse import ArgumentParser, ArgumentTypeError, RawDescriptionHelpFormatter
from json import loads as json_loads

import requests # pyright: ignore[reportMissingModuleSource]
from colorama import init # pyright: ignore[reportMissingModuleSource]

from sherlock_project.__init__ import __longname__, __shortname__, __version__
from sherlock_project.notify import QueryNotifyPrint
from sherlock_project.output import write_txt_output, write_csv_output, write_xlsx_output
from sherlock_project.sherlock import sherlock, check_for_parameter, multiple_usernames
from sherlock_project.sites import SitesInformation


FORGE_API_LATEST_RELEASE = "https://api.github.com/repos/sherlock-project/sherlock/releases/latest"


def timeout_check(value) -> float:
    """Validate and return the timeout value as a positive float."""
    float_value = float(value)
    if float_value <= 0:
        raise ArgumentTypeError(
            f"Invalid timeout value: {value}. Timeout must be a positive number."
        )
    return float_value


def handler(signal_received, frame):
    """Exit gracefully on SIGINT."""
    sys.exit(0)


def check_for_updates() -> None:
    """Check for a newer version of Sherlock and notify the user."""
    try:
        latest_release_raw = requests.get(FORGE_API_LATEST_RELEASE, timeout=10).text
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
    """Configure colorama based on user preference."""
    if no_color:
        init(strip=True, convert=False)
        return
    init(autoreset=True)


def load_site_information(args) -> SitesInformation:
    """Load site data from a local file or remote source."""
    try:
        if args.local:
            return SitesInformation(
                os.path.join(os.path.dirname(__file__), "resources/data.json"),
                honor_exclusions=False,
            )

        json_file_location = args.json_file
        if args.json_file and args.json_file.isdigit():
            pull_number = args.json_file
            pull_url = f"https://api.github.com/repos/sherlock-project/sherlock/pulls/{pull_number}"
            pull_request_json = json_loads(requests.get(pull_url, timeout=10).text)
            if "message" in pull_request_json:
                print(f"ERROR: Pull request #{pull_number} not found.")
                sys.exit(1)
            head_commit_sha = pull_request_json["head"]["sha"]
            json_file_location = (
                f"https://raw.githubusercontent.com/sherlock-project/sherlock"
                f"/{head_commit_sha}/sherlock_project/resources/data.json"
            )

        return SitesInformation(
            data_file_path=json_file_location,
            honor_exclusions=not args.ignore_exclusions,
            do_not_exclude=args.site_list,
        )
    except Exception as error:
        print(f"ERROR:  {error}")
        sys.exit(1)


def build_site_data(site_data_all: dict, site_list: list) -> tuple[dict, list]:
    """Return filtered site data and a list of unrecognised site names."""
    lowered = {k.lower(): k for k in site_data_all}
    site_data: dict = {}
    site_missing: list = []
    for site in site_list:
        key = lowered.get(site.lower())
        if key:
            site_data[key] = site_data_all[key]
        else:
            site_missing.append(f"'{site}'")
    return site_data, site_missing


def filter_sites(sites: SitesInformation, site_list: list, nsfw: bool) -> dict:
    """Apply NSFW and site-list filters, then return the site data dict."""
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
    """Determine the output file path from arguments."""
    if args.output:
        return args.output
    if args.folderoutput:
        os.makedirs(args.folderoutput, exist_ok=True)
        return os.path.join(args.folderoutput, f"{username}.txt")
    return f"{username}.txt"


def process_usernames(args) -> list[str]:
    """Expand {?} placeholders and return the full username list."""
    all_usernames: list[str] = []
    for username in args.username:
        if check_for_parameter(username):
            all_usernames.extend(multiple_usernames(username))
        else:
            all_usernames.append(username)
    return all_usernames


def validate_output_args(args) -> None:
    """Exit with an error message if output arguments are inconsistent."""
    if args.output is not None and args.folderoutput is not None:
        print("You can only use one of the output methods.")
        sys.exit(1)
    if args.output is not None and len(args.username) != 1:
        print("You can only use --output with a single username")
        sys.exit(1)


def main() -> None:
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

    for username in process_usernames(args):
        results = sherlock(
            username, site_data, query_notify,
            dump_response=args.dump_response,
            proxy=args.proxy,
            timeout=args.timeout,
        )
        result_file = get_result_file(args, username)

        if args.output_txt:
            write_txt_output(results, result_file)
        if args.csv:
            write_csv_output(results, args, username)
        if args.xlsx:
            write_xlsx_output(results, args, username)
        print()

    query_notify.finish()
