import pytest # pyright: ignore[reportMissingImports]
from sherlock_project import sherlock
from sherlock_interactives import Interactives
from sherlock_interactives import InteractivesSubprocessError


def _to_dict(sites_obj) -> dict:
    return {site.name: site.information for site in sites_obj}


def test_remove_nsfw(sites_obj):
    nsfw_target: str = 'Pornhub'
    assert nsfw_target in _to_dict(sites_obj)
    sites_obj.remove_nsfw_sites()
    assert nsfw_target not in _to_dict(sites_obj)


# Parametrized sites should *not* include Motherless, which is acting as the control
@pytest.mark.parametrize('nsfwsites', [
    ['Pornhub'],
    ['Pornhub', 'Xvideos'],
])
def test_nsfw_explicit_selection(sites_obj, nsfwsites):
    site_dict = _to_dict(sites_obj)
    for site in nsfwsites:
        assert site in site_dict
    sites_obj.remove_nsfw_sites(do_not_remove=nsfwsites)
    site_dict = _to_dict(sites_obj)
    for site in nsfwsites:
        assert site in site_dict
        assert 'Motherless' not in site_dict

def test_wildcard_username_expansion():
    assert sherlock.check_for_parameter('test{?}test') is True
    assert sherlock.check_for_parameter('test{.}test') is False
    assert sherlock.check_for_parameter('test{}test') is False
    assert sherlock.check_for_parameter('testtest') is False
    assert sherlock.check_for_parameter('test{?test') is False
    assert sherlock.check_for_parameter('test?}test') is False
    assert sherlock.multiple_usernames('test{?}test') == ["test_test" , "test-test" , "test.test"]


@pytest.mark.parametrize('cliargs', [
    '',
    '--site urghrtuight --egiotr',
    '--',
])
def test_no_usernames_provided(cliargs):
    with pytest.raises(InteractivesSubprocessError, match=r"error: the following arguments are required: USERNAMES"):
        Interactives.run_cli(cliargs)
