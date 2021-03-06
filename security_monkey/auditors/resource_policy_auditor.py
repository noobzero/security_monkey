#     Copyright 2014 Netflix, Inc.
#
#     Licensed under the Apache License, Version 2.0 (the "License");
#     you may not use this file except in compliance with the License.
#     You may obtain a copy of the License at
#
#         http://www.apache.org/licenses/LICENSE-2.0
#
#     Unless required by applicable law or agreed to in writing, software
#     distributed under the License is distributed on an "AS IS" BASIS,
#     WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#     See the License for the specific language governing permissions and
#     limitations under the License.
"""
.. module: security_monkey.auditors.resource_policy_auditor
    :platform: Unix

.. version:: $$VERSION$$
.. moduleauthor:: Patrick Kelley <patrick@netflix.com>

"""
from security_monkey import app
from security_monkey.auditor import Auditor, Entity
from security_monkey.datastore import Account

from policyuniverse.arn import ARN
from policyuniverse.policy import Policy
from policyuniverse.statement import Statement

import json
import dpath.util
from dpath.exceptions import PathNotFound
import ipaddr


class ResourcePolicyAuditor(Auditor):

    def __init__(self, accounts=None, debug=False):
        super(ResourcePolicyAuditor, self).__init__(accounts=accounts, debug=debug)
        self.policy_keys = ['Policy']

    def load_policies(self, item):
        """For a given item, return a list of all resource policies.
        
        Most items only have a single resource policy, typically found 
        inside the config with the key, "Policy".
        
        Some technologies have multiple resource policies.  A lambda function
        is an example of an item with multiple resource policies.
        
        The lambda function auditor can define a list of `policy_keys`.  Each
        item in this list is the dpath to one of the resource policies.
        
        The `policy_keys` defaults to ['Policy'] unless overriden by a subclass.
        
        Returns:
            list of Policy objects
        """
        policies = list()
        for key in self.policy_keys:
            try:
                policy = dpath.util.values(item.config, key, separator='$')
                if isinstance(policy, list):
                    for p in policy:
                        if not p:
                            continue
                        if isinstance(p, list):
                            policies.extend([Policy(pp) for pp in p])
                        else:
                            policies.append(Policy(p))
                else:
                    policies.append(Policy(policy))
            except PathNotFound:
                continue
        return policies

    def check_internet_accessible(self, item):
        policies = self.load_policies(item)
        for policy in policies:
            if policy.is_internet_accessible():
                entity = Entity(category='principal', value='*')
                actions = list(policy.internet_accessible_actions())
                self.record_internet_access(item, entity, actions)

    def check_friendly_cross_account(self, item):
        policies = self.load_policies(item)
        for policy in policies:
            for statement in policy.statements:
                if statement.effect != 'Allow':
                    continue
                for who in statement.whos_allowed():
                    entity = Entity.from_tuple(who)
                    if 'FRIENDLY' in self.inspect_entity(entity, item):
                        self.record_friendly_access(item, entity, list(statement.actions))

    def check_thirdparty_cross_account(self, item):
        policies = self.load_policies(item)
        for policy in policies:
            for statement in policy.statements:
                if statement.effect != 'Allow':
                    continue
                for who in statement.whos_allowed():
                    entity = Entity.from_tuple(who)
                    if 'THIRDPARTY' in self.inspect_entity(entity, item):
                        self.record_thirdparty_access(item, entity, list(statement.actions))

    def check_unknown_cross_account(self, item):
        policies = self.load_policies(item)
        for policy in policies:
            if policy.is_internet_accessible():
                continue
            for statement in policy.statements:
                if statement.effect != 'Allow':
                    continue
                for who in statement.whos_allowed():
                    if who.value == '*' and who.category == 'principal':
                        continue

                    # Ignore Service Principals
                    if who.category == 'principal':
                        arn = ARN(who.value)
                        if arn.service:
                            continue

                    entity = Entity.from_tuple(who)
                    if 'UNKNOWN' in self.inspect_entity(entity, item):
                        self.record_unknown_access(item, entity, list(statement.actions))

    def check_root_cross_account(self, item):
        policies = self.load_policies(item)
        for policy in policies:
            for statement in policy.statements:
                if statement.effect != 'Allow':
                    continue
                for who in statement.whos_allowed():
                    if who.category not in ['arn', 'principal']:
                        continue
                    if who.value == '*':
                        continue
                    arn = ARN(who.value)
                    entity = Entity.from_tuple(who)
                    if arn.root and self.inspect_entity(entity, item).intersection(set(['FRIENDLY', 'THIRDPARTY', 'UNKNOWN'])):
                        self.record_cross_account_root(item, entity, list(statement.actions))
