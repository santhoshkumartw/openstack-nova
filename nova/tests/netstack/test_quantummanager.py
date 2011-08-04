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
from nova.network import manager
from nova.network import quantum
from nova.network import melange_client
from nova.db import api as db_api
from mox import IgnoreArg


FLAGS = flags.FLAGS
LOG = logging.getLogger('nova.tests.network')


class TestCreateNetworks(test.TestCase):

    def setUp(self):
        super(TestCreateNetworks, self).setUp()
        self.mox.StubOutWithMock(melange_client, 'create_block')
        self._stub_out_and_ignore_quantum_client_calls()

    def test_creates_network_sized_v4_subnet_in_melange(self):
        self.flags(use_ipv6=False)
        melange_client.create_block(IgnoreArg(), "10.1.1.0/26", "project1")
        self.mox.ReplayAll()

        create_quantum_network(cidr="10.1.1.0/24", num_networks=1,
                               network_size=64, project_id="project1")

    def test_creates_multiple_ipv4_melange_blocks(self):
        self.flags(use_ipv6=False)
        melange_client.create_block(IgnoreArg(), "10.1.0.0/24", "project1")
        melange_client.create_block(IgnoreArg(), "10.1.1.0/24", "project1")
        melange_client.create_block(IgnoreArg(), "10.1.2.0/24", "project1")
        self.mox.ReplayAll()

        create_quantum_network(cidr="10.1.0.0/20", num_networks=3,
                               network_size=256, project_id="project1")

    def test_creates_ipv6_melange_blocks(self):
        self.flags(use_ipv6=True)
        melange_client.create_block(IgnoreArg(), "10.1.1.0/26", "project1")
        melange_client.create_block(IgnoreArg(), "fe::/64", "project1")
        self.mox.ReplayAll()

        create_quantum_network(cidr="10.1.1.0/24", num_networks=1,
                               network_size=64, cidr_v6="fe::/60",
                               project_id="project1")

    def test_creates_multiple_ipv6_melange_blocks(self):
        self.flags(use_ipv6=True)
        melange_client.create_block(IgnoreArg(), "10.1.0.0/24", "project1")
        melange_client.create_block(IgnoreArg(), "fe::/64", "project1")

        melange_client.create_block(IgnoreArg(), "10.1.1.0/24", "project1")
        melange_client.create_block(IgnoreArg(), "fe:0:0:1::/64", "project1")

        self.mox.ReplayAll()

        create_quantum_network(cidr="10.1.0.0/20", num_networks=2,
                               cidr_v6="fe::/60", network_size=256,
                               project_id="project1")

    def _stub_out_and_ignore_quantum_client_calls(self):
        self.mox.StubOutWithMock(quantum, 'create_network')
        self.mox.StubOutWithMock(quantum, 'get_network_by_name')
        quantum.create_network(IgnoreArg(),
                             IgnoreArg()).MultipleTimes().AndReturn("network1")
        quantum.get_network_by_name(IgnoreArg(),
                                   IgnoreArg()).MultipleTimes().AndReturn(None)


class TestAllocateForInstance(test.TestCase):

    def test_allocates_v4_ips_from_melange(self):
        quantum_mgr = QuantumManager()
        admin_context = context.get_admin_context()
        instance = db_api.instance_create(admin_context, {})

        private_network = db_api.network_create_safe(admin_context,
                                  dict(label='private', project_id="project1"))
        private_noise_network = db_api.network_create_safe(admin_context,
                                  dict(label='private',
                                       project_id="some_other_project"))
        public_network = db_api.network_create_safe(admin_context,
                                  dict(label='public', priority=1))

        self.mox.StubOutWithMock(melange_client, 'allocate_ip')
        private_v4ip = dict(address="10.1.1.2", netmask="255.255.255.0",
                            gateway="10.1.1.1")
        public_v4ip = dict(address="77.1.1.2", netmask="255.255.0.0",
                           gateway="77.1.1.1")

        melange_client.allocate_ip(private_network.id, IgnoreArg(),
                                   project_id="project1",
                                   mac_address=IgnoreArg())\
                                   .InAnyOrder().AndReturn([private_v4ip])
        melange_client.allocate_ip(public_network.id, IgnoreArg(),
                                   project_id=None,
                                   mac_address=IgnoreArg())\
                                   .InAnyOrder().AndReturn([public_v4ip])
        self.mox.ReplayAll()

        net_info = quantum_mgr.allocate_for_instance(admin_context,
                                               instance_id=instance.id,
                                               host="localhost",
                                               project_id="project1",
                                               instance_type_id=1,
                                               vpn="vpn_address")
        [(private_net, private_net_info),
         (public_net, public_net_info)] = net_info

        self.assertEqual(private_net_info['label'], 'private')
        self.assertEqual(private_net_info['ips'], [{'ip': '10.1.1.2',
                                            'netmask': '255.255.255.0',
                                            'enabled': '1'}])

        self.assertEqual(public_net_info['label'], 'public')
        self.assertEqual(public_net_info['ips'], [{'ip': '77.1.1.2',
                                            'netmask': '255.255.0.0',
                                            'enabled': '1'}])

    def test_allocates_v6_ips_from_melange(self):
        quantum_mgr = QuantumManager()
        mac_address = "11:22:33:44:55:66"
        self._stub_out_mac_address_generation(mac_address, quantum_mgr)
        admin_context = context.get_admin_context()
        instance = db_api.instance_create(admin_context, {})

        network = db_api.network_create_safe(admin_context,
                                             dict(project_id="project1",
                                                  cidr_v6="fe::/64"))

        self.mox.StubOutWithMock(melange_client, 'allocate_ip')
        allocated_v4ip = dict(address="10.1.1.2", netmask="255.255.255.0",
                              gateway="10.1.1.1")
        allocated_v6ip = dict(address="fe::2", netmask="f:f:f:f::",
                              gateway="fe::1")

        melange_client.allocate_ip(network.id, IgnoreArg(),
                                   project_id="project1",
                                   mac_address=mac_address)\
                                   .AndReturn([allocated_v4ip, allocated_v6ip])
        self.mox.ReplayAll()

        [(net, net_info)] = quantum_mgr.allocate_for_instance(admin_context,
                                               instance_id=instance.id,
                                               host="localhost",
                                               project_id="project1",
                                               instance_type_id=1,
                                               vpn="vpn_address")

        self.assertEqual(net_info['ips'], [{'ip': '10.1.1.2',
                                            'netmask': '255.255.255.0',
                                            'enabled': '1'}])
        self.assertEqual(net_info['ip6s'], [{'ip': 'fe::2',
                                            'netmask': 'f:f:f:f::',
                                            'enabled': '1'}])

    def _stub_out_mac_address_generation(self, stub_mac_address,
                                         network_manager):
        self.mox.StubOutWithMock(network_manager, 'generate_mac_address')
        network_manager.generate_mac_address().AndReturn(stub_mac_address)


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
