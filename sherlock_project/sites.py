"""Sherlock Sites Information Module

This module supports storing information about websites.
This is the raw data that will be used to search for usernames.
"""
import json
import requests
import secrets


MANIFEST_URL = "https://data.sherlockproject.xyz"
EXCLUSIONS_URL = "https://raw.githubusercontent.com/sherlock-project/sherlock/refs/heads/exclusions/false_positive_exclusions.txt"

class SiteInformation:
    def __init__(self, name, url_home, url_username_format, username_claimed,
                information, is_nsfw):
        """Create Site Information Object.

        Contains information about a specific website.

        Keyword Arguments:
        self                   -- This object.
        name                   -- String which identifies site.
        url_home               -- String containing URL for home of site.
        url_username_format    -- String containing URL for Username format
                                  on site.
                                  NOTE:  The string should contain the
                                         token "{}" where the username should
                                         be substituted.  For example, a string
                                         of "https://somesite.com/users/{}"
                                         indicates that the individual
                                         usernames would show up under the
                                         "https://somesite.com/users/" area of
                                         the website.
        username_claimed       -- String containing username which is known
                                  to be claimed on website.
        information            -- Dictionary containing all known information
                                  about website.
                                  NOTE:  Custom information about how to
                                         actually detect the existence of the
                                         username will be included in this
                                         dictionary.  This information will
                                         be needed by the detection method,
                                         but it is only recorded in this
                                         object for future use.
        is_nsfw                -- Boolean indicating if site is Not Safe For Work.

        Return Value:
        Nothing.
        """

        self.name = name
        self.url_home = url_home
        self.url_username_format = url_username_format

        self.username_claimed = username_claimed
        self.username_unclaimed = secrets.token_urlsafe(32)
        self.information = information
        self.is_nsfw = is_nsfw

    def __str__(self):
        """Convert Object To String.

        Keyword Arguments:
        self                   -- This object.

        Return Value:
        Nicely formatted string to get information about this object.
        """

        return f"{self.name} ({self.url_home})"


class SitesInformation:
    def __init__(
            self,
            data_file_path: str|None = None,
            honor_exclusions: bool = True,
            do_not_exclude: list[str] | None = None,
        ):
        """Create Sites Information Object.

        Contains information about all supported websites.

        Keyword Arguments:
        self                   -- This object.
        data_file_path         -- String which indicates path to data file.
                                  The file name must end in ".json".

                                  There are 3 possible formats:
                                   * Absolute File Format
                                     For example, "c:/stuff/data.json".
                                   * Relative File Format
                                     The current working directory is used
                                     as the context.
                                     For example, "data.json".
                                   * URL Format
                                     For example,
                                     "https://example.com/data.json", or
                                     "http://example.com/data.json".

                                  An exception will be thrown if the path
                                  to the data file is not in the expected
                                  format, or if there was any problem loading
                                  the file.

                                  If this option is not specified, then a
                                  default site list will be used.

        Return Value:
        Nothing.
        """

        if do_not_exclude is None:
            do_not_exclude = []

        data_file_path = self._resolve_data_file_path(data_file_path)
        site_data = self._load_site_data(data_file_path)
        site_data.pop('$schema', None)

        if honor_exclusions:
            self._apply_exclusions(site_data, do_not_exclude)

        self._populate_sites(site_data, data_file_path)

    def _resolve_data_file_path(self, data_file_path: str | None) -> str:
        if not data_file_path:
            return MANIFEST_URL
        return data_file_path

    def _load_site_data(self, data_file_path: str) -> dict:
        if data_file_path.lower().startswith("http"):
            return self._load_from_url(data_file_path)
        return self._load_from_file(data_file_path)

    def _load_from_url(self, url: str) -> dict:
        try:
            response = requests.get(url=url, timeout=30)
        except Exception as error:
            raise FileNotFoundError(
                f"Problem while attempting to access data file URL '{url}':  {error}"
            )
        if response.status_code != 200:
            raise FileNotFoundError(
                f"Bad response while accessing data file URL '{url}'."
            )
        try:
            return response.json()
        except Exception as error:
            raise ValueError(
                f"Problem parsing json contents at '{url}':  {error}."
            )

    def _load_from_file(self, file_path: str) -> dict:
        try:
            with open(file_path, "r", encoding="utf-8") as file:
                try:
                    return json.load(file)
                except Exception as error:
                    raise ValueError(
                        f"Problem parsing json contents at '{file_path}':  {error}."
                    )
        except FileNotFoundError:
            raise FileNotFoundError(
                f"Problem while attempting to access data file '{file_path}'."
            )

    def _apply_exclusions(self, site_data: dict, do_not_exclude: list) -> None:
        try:
            response = requests.get(url=EXCLUSIONS_URL, timeout=10)
            if response.status_code == 200:
                exclusions = [e.strip() for e in response.text.splitlines()]
                for site in do_not_exclude:
                    if site in exclusions:
                        exclusions.remove(site)
                for exclusion in exclusions:
                    site_data.pop(exclusion, None)
        except Exception:
            print("Warning: Could not load exclusions, continuing without them.")

    def _populate_sites(self, site_data: dict, data_file_path: str) -> None:
        self.sites = {}
        for site_name in site_data:
            try:
                self.sites[site_name] = SiteInformation(
                    site_name,
                    site_data[site_name]["urlMain"],
                    site_data[site_name]["url"],
                    site_data[site_name]["username_claimed"],
                    site_data[site_name],
                    site_data[site_name].get("isNSFW", False),
                )
            except KeyError as error:
                raise ValueError(
                    f"Problem parsing json contents at '{data_file_path}':  Missing attribute {error}."
                )
            except TypeError:
                print(f"Encountered TypeError parsing json contents for target '{site_name}' at {data_file_path}\nSkipping target.\n")

    def remove_nsfw_sites(self, do_not_remove: list | None = None):
        """
        Remove NSFW sites from the sites, if isNSFW flag is true for site

        Keyword Arguments:
        self                   -- This object.

        Return Value:
        None
        """
        if do_not_remove is None:
            do_not_remove = []
        sites = {}
        do_not_remove = [site.casefold() for site in do_not_remove]
        for site in self.sites:
            if self.sites[site].is_nsfw and site.casefold() not in do_not_remove:
                continue
            sites[site] = self.sites[site]
        self.sites = sites

    def site_name_list(self):
        """Get Site Name List.

        Keyword Arguments:
        self                   -- This object.

        Return Value:
        List of strings containing names of sites.
        """

        return sorted([site.name for site in self], key=str.lower)

    def __iter__(self):
        """Iterator For Object.

        Keyword Arguments:
        self                   -- This object.

        Return Value:
        Iterator for sites object.
        """

        for site_name in self.sites:
            yield self.sites[site_name]

    def __len__(self):
        """Length For Object.

        Keyword Arguments:
        self                   -- This object.

        Return Value:
        Length of sites object.
        """
        return len(self.sites)
