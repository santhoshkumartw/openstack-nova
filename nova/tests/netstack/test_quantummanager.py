# vim: tabstop=4 shiftwidth=4 softtabstop=4

# Copyright 2011 OpenStack LLC.
# All Rights Reserved.
#
#    Licensed under the Apache License, Version 2.0 (the "License"); you may
#    not use this file except in compliance with the License. You may obtain
#    a copy of the License at
#
#         http://www.apache.org/licenses/LICENSE-2.0
#
#    Unless required by applicable law or agreed to in writing, software
#    distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
#    WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
#    License for the specific language governing permissions and limitations
#    under the License.

from nova import flags
from nova import log as logging
from nova import test
from nova import context
from nova.network.quantummanager import QuantumManager
from nova.network import quantum
from nova.network import melange_client

from mox import IgnoreArg


FLAGS = flags.FLAGS
LOG = logging.getLogger('nova.tests.network')


class TestQuantumManager(test.TestCase):

    def setUp(self):
        super(TestQuantumManager, self).setUp()
        self.mox.StubOutWithMock(melange_client, 'create_block')
        self._stub_out_and_ignore_quantum_client_calls()

    def test_create_networks_creates_network_sized_v4_subnet_in_melange(self):
        self.flags(use_ipv6=False)
        melange_client.create_block(IgnoreArg(), "10.1.1.0/26", "project1")
        self.mox.ReplayAll()

        create_quantum_network(cidr="10.1.1.0/24", num_networks=1,
                               network_size=64, project_id="project1")

    def test_create_networks_creates_multiple_ipv4_melange_blocks(self):
        self.flags(use_ipv6=False)
        melange_client.create_block(IgnoreArg(), "10.1.0.0/24", "project1")
        melange_client.create_block(IgnoreArg(), "10.1.1.0/24", "project1")
        melange_client.create_block(IgnoreArg(), "10.1.2.0/24", "project1")
        self.mox.ReplayAll()

        create_quantum_network(cidr="10.1.0.0/20", num_networks=3,
                               network_size=256, project_id="project1")

    def test_create_networks_creates_ipv6_melange_blocks(self):
        self.flags(use_ipv6=True)
        melange_client.create_block(IgnoreArg(), "10.1.1.0/26", "project1")
        melange_client.create_block(IgnoreArg(), "fe::/64", "project1")
        self.mox.ReplayAll()

        create_quantum_network(cidr="10.1.1.0/24", num_networks=1,
                               network_size=64, cidr_v6="fe::/60",
                               project_id="project1")

    def _stub_out_and_ignore_quantum_client_calls(self):
        self.mox.StubOutWithMock(quantum, 'create_network')
        self.mox.StubOutWithMock(quantum, 'get_network_by_name')
        quantum.create_network(IgnoreArg(),
                             IgnoreArg()).MultipleTimes().AndReturn("network1")
        quantum.get_network_by_name(IgnoreArg(),
                                   IgnoreArg()).MultipleTimes().AndReturn(None)


def create_quantum_network(**kwargs):
    default_params = dict(context=context.get_admin_context(),
                          label="label",
                          cidr="169.1.1.0/24",
                          multi_host=False,
                          num_networks=1,
                          network_size=64,
                          vlan_start=0,
                          vpn_start=0,
                          cidr_v6=None,
                          gateway_v6=None,
                          bridge="river kwai",
                          bridge_interface="too far",
                          dns1=None,
                          dns2=None,
                          project_id="project1",
                          priority=1)
    params = dict(default_params.items() + kwargs.items())
    return QuantumManager().create_networks(**params)
