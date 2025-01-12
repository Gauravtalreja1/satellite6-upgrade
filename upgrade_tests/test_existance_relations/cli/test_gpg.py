"""Upgrade TestSuite for validating gpg keys existence post upgrade

:Requirement: Upgraded Satellite

:CaseAutomation: Automated

:CaseLevel: System

:CaseComponent: GPGKeys

:TestType: nonfunctional

:CaseImportance: High

:SubType1: installability

:Upstream: No
"""
import pytest

from upgrade_tests.helpers.common import dont_run_to_upgrade
from upgrade_tests.helpers.common import existence
from upgrade_tests.helpers.existence import compare_postupgrade
from upgrade_tests.helpers.existence import pytest_ids

# Required Data
component = 'gpg'
gpg_name = compare_postupgrade(component, 'name')

# Tests


@dont_run_to_upgrade(['6.8', '6.9', '6.10', '6.11'])
@pytest.mark.parametrize("pre,post", gpg_name, ids=pytest_ids(gpg_name))
def test_positive_gpg_keys_by_name(pre, post):
    """Test all gpg keys are existing after upgrade by names

    :id: upgrade-23b96c3e-2510-4886-91e6-9864f0d5e3e5

    :expectedresults: All gpg keys should be retained post upgrade by names
    """
    assert existence(pre, post)
