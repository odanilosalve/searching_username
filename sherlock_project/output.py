"""Sherlock Output Module

This module provides output writers for Sherlock search results.
"""
import csv
import os

import pandas as pd # pyright: ignore[reportMissingModuleSource]

from sherlock_project.result import QueryStatus


def write_txt_output(results: dict, result_file: str) -> None:
    """Write results to a text file."""
    with open(result_file, "w", encoding="utf-8") as file:
        exists_counter = 0
        for website_name in results:
            dictionary = results[website_name]
            if dictionary.get("status").status == QueryStatus.CLAIMED:
                exists_counter += 1
                file.write(dictionary["url_user"] + "\n")
        file.write(f"Total Websites Username Detected On : {exists_counter}\n")


def write_csv_output(results: dict, args, username: str) -> None:
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


def write_xlsx_output(results: dict, args, username: str) -> None:
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
        else:
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
