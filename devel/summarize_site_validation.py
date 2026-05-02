#!/usr/bin/env python
# This module summarizes the results of site validation tests queued by
# workflow validate_modified_targets for presentation in Issue comments.

from defusedxml import ElementTree as ET # pyright: ignore[reportMissingModuleSource]
import sys
from pathlib import Path

_PASS = ":heavy_check_mark: &nbsp; Pass"
_FAIL = ":x: &nbsp; Fail"

_TEST_FIELD = {
    "test_false_neg": "F- Check",
    "test_false_pos": "F+ Check",
}


def _result_message(testcase) -> str:
    """Return the pass or fail display string for a testcase element."""
    if testcase.find('failure') is None and testcase.find('error') is None:
        return _PASS
    return _FAIL


def _parse_testcase(testcase, results: dict) -> bool:
    """Record one testcase result and return True if an error element was found."""
    name: str = testcase.get('name')
    test_name, site_name = name.split('[')[0], name.split('[')[1].rstrip(']')

    results.setdefault(site_name, {})

    field = _TEST_FIELD.get(test_name)
    if field:
        results[site_name][field] = _result_message(testcase)

    return testcase.find('error') is not None


def summarize_junit_xml(xml_path: Path) -> str:
    tree = ET.parse(xml_path)
    root = tree.getroot()
    suite = root.find('testsuite')

    if suite is None:
        raise ValueError("Invalid JUnit XML: No testsuite found")

    results: dict[str, dict[str, str]] = {}
    error_flags: list[bool] = []
    for tc in suite.findall('testcase'):
        error_flags.append(_parse_testcase(tc, results))

    summary_lines: list[str] = [
        "#### Automatic validation of changes\n",
        "| Target | F+ Check | F- Check |",
        "|---|---|---|",
        *[
            f"| {site} | {data.get('F+ Check', 'Error!')} | {data.get('F- Check', 'Error!')} |"
            for site, data in results.items()
        ],
    ]

    if int(suite.get('failures', 0)) > 0:
        summary_lines.append(
            "\n___\n"
            "\nFailures were detected on at least one updated target. Commits containing accuracy failures"
            " will often not be merged (unless a rationale is provided, such as false negatives due to regional differences)."
        )

    if any(error_flags):
        summary_lines.append(
            "\n___\n"
            "\n**Errors were detected during validation. Please review the workflow logs.**"
        )

    return "\n".join(summary_lines)


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Usage: summarize_site_validation.py <junit-xml-file>")
        sys.exit(1)

    xml_path: Path = Path(sys.argv[1])
    if not xml_path.is_file():
        print(f"Error: File '{xml_path}' does not exist.")
        sys.exit(1)

    summary: str = summarize_junit_xml(xml_path)
    print(summary)
