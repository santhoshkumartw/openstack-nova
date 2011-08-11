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
from nova.db import api as db_api
from mox import IgnoreArg
from netaddr import IPNetwork

FLAGS = flags.FLAGS
LOG = logging.getLogger('nova.tests.network')
admin_context = context.get_admin_context()


class TestCreateNetworks(test.TestCase):

    def setUp(self):
        super(TestCreateNetworks, self).setUp()
        self.mox.StubOutWithMock(melange_client, 'create_block')
        self._stub_out_quantum_network_create_calls()

    def test_creates_network_sized_v4_subnet_in_melange(self):
        self.flags(use_ipv6=False)
        melange_client.create_block(IgnoreArg(), "10.1.1.0/26", "project1",
                                    None, None)
        self.mox.ReplayAll()

        self._create_quantum_manager_network(cidr="10.1.1.0/24",
                                             num_networks=1, network_size=64,
                                             project_id="project1")

    def test_create_v4block_with_dns(self):
        self.flags(use_ipv6=False)
        melange_client.create_block(IgnoreArg(), "10.1.1.0/26", "project1",
                                    "10.2.3.4", "10.3.4.5")
        self.mox.ReplayAll()

        self._create_quantum_manager_network(cidr="10.1.1.0/24",
                                             num_networks=1, network_size=64,
                                             project_id="project1",
                                             dns1="10.2.3.4", dns2="10.3.4.5")

    def test_creates_multiple_ipv4_melange_blocks_for_a_single_network(self):
        self.flags(use_ipv6=False)
        melange_client.create_block(IgnoreArg(), "10.1.0.0/24",
                                    "project1", None, None)
        melange_client.create_block(IgnoreArg(), "10.1.1.0/24",
                                    "project1", None, None)
        melange_client.create_block(IgnoreArg(), "10.1.2.0/24",
                                    "project1", None, None)
        self.mox.ReplayAll()

        self._create_quantum_manager_network(cidr="10.1.0.0/20",
                                             num_networks=3, network_size=256,
                                             project_id="project1")

    def test_always_creates_64_prefix_len_ipv6_melange_blocks(self):
        self.flags(use_ipv6=True)

        melange_client.create_block(IgnoreArg(), "10.1.1.0/26",
                                    "project1", None, None)
        melange_client.create_block(IgnoreArg(), "fe::/64",
                                    "project1", None, None)

        melange_client.create_block(IgnoreArg(), "10.1.1.0/26",
                                    "project1", None, None)
        melange_client.create_block(IgnoreArg(), "c0::/64",
                                    "project1", None, None)

        self.mox.ReplayAll()

        self._create_quantum_manager_network(cidr="10.1.1.0/24",
                                             num_networks=1, network_size=64,
                                             cidr_v6="fe::/60",
                                             project_id="project1")

        self._create_quantum_manager_network(cidr="10.1.1.0/24",
                                             num_networks=1, network_size=64,
                                             cidr_v6="fe::/10",
                                             project_id="project1")

    def test_creates_multiple_melange_blocks_for_a_single_network(self):
        self.flags(use_ipv6=True)
        melange_client.create_block(IgnoreArg(), "10.1.0.0/24",
                                    "project1", None, None)
        melange_client.create_block(IgnoreArg(), "fe::/64",
                                    "project1", None, None)

        melange_client.create_block(IgnoreArg(), "10.1.1.0/24",
                                    "project1", None, None)
        melange_client.create_block(IgnoreArg(), "fe:0:0:1::/64",
                                    "project1", None, None)

        self.mox.ReplayAll()

        self._create_quantum_manager_network(cidr="10.1.0.0/20",
                                             num_networks=2, network_size=256,
                                             cidr_v6="fe::/60",
                                             project_id="project1")

    def _stub_out_quantum_network_create_calls(self):
        self.mox.StubOutWithMock(quantum, 'create_network')
        quantum.create_network(IgnoreArg(),
                             IgnoreArg()).MultipleTimes().AndReturn("network1")

    def _create_quantum_manager_network(self, **network_params):
        default_params = dict(context=admin_context,
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
        params = dict(default_params.items() + network_params.items())
        return QuantumManager().create_networks(**params)


class TestAllocateForInstance(test.TestCase):

    def setUp(self):
        super(TestAllocateForInstance, self).setUp()
        self.instance_id = db_api.instance_create(admin_context, {}).id
        self._stub_out_quantum_port_and_iface_create_calls()
        self.mox.StubOutWithMock(melange_client, 'allocate_ip')

    def test_allocates_v4_ips_for_private_network(self):
        private_network = db_api.network_create_safe(admin_context,
                            dict(label="private", project_id="project1",
                                 priority=1))
        private_noise_network = db_api.network_create_safe(admin_context,
                            dict(label="private", project_id="another_project",
                                 priority=1))

        private_v4block = dict(netmask="255.255.255.0", cidr="10.1.1.0/24",
                               gateway="10.1.1.1", broadcast="10.1.1.255",
                               dns1="1.2.3.4", dns2="2.3.4.5")
        private_v4ip = dict(address="10.1.1.2", version=4,
                            ip_block=private_v4block)
        melange_client.allocate_ip(private_network.id, IgnoreArg(),
                                   project_id="project1",
                                   mac_address=IgnoreArg())\
                                   .InAnyOrder().AndReturn([private_v4ip])
        self.mox.ReplayAll()

        net_info = QuantumManager().allocate_for_instance(admin_context,
                                               instance_id=self.instance_id,
                                               host=None,
                                               project_id="project1",
                                               instance_type_id=1,
                                               vpn=None)

        self.assertEqual(len(net_info), 1)
        assert_network_info_has_ip(self, net_info[0], private_v4ip,
                                         private_network)

    def test_allocates_v4_ips_for_public_network(self):
        public_network = db_api.network_create_safe(admin_context,
                                    dict(label="public", project_id=None,
                                         priority=1))
        private_noise_network = db_api.network_create_safe(admin_context,
                        dict(label="private", project_id="another_project",
                             priority=1))

        public_v4block = dict(netmask="255.255.255.0", cidr="10.1.1.0/24",
                               gateway="10.1.1.1", broadcast="10.1.1.255",
                               dns1="1.2.3.4", dns2="2.3.4.5")
        public_v4ip = dict(address="10.1.1.2", version=4,
                            ip_block=public_v4block)
        melange_client.allocate_ip(public_network.id, IgnoreArg(),
                                   project_id=None,
                                   mac_address=IgnoreArg())\
                                   .InAnyOrder().AndReturn([public_v4ip])

        self.mox.ReplayAll()

        net_info = QuantumManager().allocate_for_instance(admin_context,
                                               instance_id=self.instance_id,
                                               host="localhost",
                                               project_id="project1",
                                               instance_type_id=1,
                                               vpn="vpn_address")

        self.assertEqual(len(net_info), 1)
        assert_network_info_has_ip(self, net_info[0], public_v4ip,
                                         public_network)

    def test_allocates_public_and_private_network_ips_from_melange(self):
        network_params = dict(label="private", project_id="some_other_project",
                              priority=1)
        private_noise_network = db_api.network_create_safe(admin_context,
                                                           network_params)

        private_nw, private_ip = self._setup_network_and_melange_ip("10.1.1.2",
                                                        "10.1.1.0/24",
                                                        net_label="private",
                                                        project_id="project1")
        public_nw, public_ip = self._setup_network_and_melange_ip("77.1.1.2",
                                                       "77.1.1.0/24",
                                                       net_label="public",
                                                       project_id=None)
        self.mox.ReplayAll()

        net_info = QuantumManager().allocate_for_instance(admin_context,
                                               instance_id=self.instance_id,
                                               host=None,
                                               project_id="project1",
                                               instance_type_id=1,
                                               vpn=None)
        [private_net, public_net] = net_info

        assert_network_info_has_ip(self, private_net, private_ip, private_nw)
        assert_network_info_has_ip(self, public_net, public_ip, public_nw)

    def test_allocates_v6_ips_from_melange(self):
        quantum_mgr = QuantumManager()
        mac_address = "11:22:33:44:55:66"
        self._stub_out_mac_address_generation(mac_address, quantum_mgr)
        network = db_api.network_create_safe(admin_context,
                                             dict(project_id="project1",
                                                  cidr_v6="fe::/96",
                                                  priority=1))

        v4_block = dict(netmask="255.255.255.0", cidr="10.1.1.0/24",
                               gateway="10.1.1.1", broadcast="10.1.1.255",
                               dns1="1.2.3.4", dns2="2.3.4.5")
        allocated_v4ip = dict(address="10.1.1.2", version=4,
                              ip_block=v4_block)

        v6_block = dict(netmask="f:f:f:f::", cidr="fe::/96",
                        gateway="fe::1", broadcast="fe::ffff:ffff")
        allocated_v6ip = dict(address="fe::2", version=6, ip_block=v6_block)
        v6_block_prefix_length = 96

        melange_client.allocate_ip(network.id, IgnoreArg(),
                                   project_id="project1",
                                   mac_address=mac_address)\
                                   .AndReturn([allocated_v4ip, allocated_v6ip])
        self.mox.ReplayAll()

        [net_info] = quantum_mgr.allocate_for_instance(admin_context,
                                               instance_id=self.instance_id,
                                               host="localhost",
                                               project_id="project1",
                                               instance_type_id=1,
                                               vpn="vpn_address")
        vif_config_net_params = net_info[1]

        assert_network_info_has_ip(self, net_info, allocated_v4ip, network)
        self.assertEqual(vif_config_net_params['ip6s'],
                         [{'ip': 'fe::2',
                           'netmask': v6_block_prefix_length,
                           'enabled': '1'}])
        self.assertEqual(vif_config_net_params['gateway6'], "fe::1")

    def _stub_out_mac_address_generation(self, stub_mac_address,
                                         network_manager):
        self.mox.StubOutWithMock(network_manager, 'generate_mac_address')
        network_manager.generate_mac_address().AndReturn(stub_mac_address)

    def _stub_out_quantum_port_and_iface_create_calls(self):
        self.mox.StubOutWithMock(quantum, 'create_port')
        self.mox.StubOutWithMock(quantum, 'plug_iface')

        quantum.create_port(IgnoreArg(), IgnoreArg()).\
                                MultipleTimes().AndReturn("port_id")
        quantum.plug_iface(IgnoreArg(), IgnoreArg(),
                           IgnoreArg(), IgnoreArg()).MultipleTimes()

    def _setup_network_and_melange_ip(self, address, cidr,
                                      net_label=None, project_id=None):
        ip_block = IPNetwork(cidr)
        network = db_api.network_create_safe(admin_context,
                                  dict(label='private',
                                       project_id="project1", priority=1))

        block = dict(netmask=ip_block.netmask, cidr=cidr, gateway=ip_block[1],
                     broadcast=ip_block.broadcast, dns1="1.2.3.4",
                     dns2="2.3.4.5")
        ip = dict(address=address, version=ip_block.version, ip_block=block)

        melange_client.allocate_ip(network.id, IgnoreArg(),
                                   project_id="project1",
                                   mac_address=IgnoreArg())\
                                   .InAnyOrder().AndReturn([ip])
        return network, ip


class TestGetIps(test.TestCase):

    def test_get_all_allocated_ips_for_an_interface(self):
        quantum_mgr = QuantumManager()
        interface = dict(network_id="network123", id="vif_id",
                         network=dict(project_id="project1"))
        self.mox.StubOutWithMock(melange_client, 'get_allocated_ips')
        allocated_v4ip = dict(address="10.1.1.2", version=4)
        allocated_v6ip = dict(address="fe::2", version=6)

        melange_client.get_allocated_ips("network123", "vif_id",
                                         project_id="project1").AndReturn([
            allocated_v4ip, allocated_v6ip])
        self.mox.ReplayAll()

        ips = quantum_mgr.get_ips(interface)
        self.assertEqual(ips, [allocated_v4ip, allocated_v6ip])


class TestGetNetworkInfo(test.TestCase):

    def test_get_network_info(self):
        quantum_mgr = QuantumManager()
        admin_context = context.get_admin_context()
        instance = db_api.instance_create(admin_context, {})

        network1 = db_api.network_create_safe(admin_context,
                                              dict(label="private1",
                                                   project_id="project1",
                                                   priority=1))
        network2 = db_api.network_create_safe(admin_context,
                                              dict(label="private2",
                                                   project_id="project1",
                                                   priority=1))

        vif1 = db_api.virtual_interface_create(admin_context,
                                         dict(address="11:22:33:44:55:66",
                                              instance_id=instance['id'],
                                              network_id=network1['id']))
        vif2 = db_api.virtual_interface_create(admin_context,
                                         dict(address="66:22:33:44:55:66",
                                              instance_id=instance['id'],
                                              network_id=network2['id']))

        self.mox.StubOutWithMock(melange_client, 'get_allocated_ips')
        block1 = dict(netmask="255.255.255.0", cidr="10.1.1.0/24",
                      gateway="10.1.1.1", broadcast="10.1.1.255",
                      dns1="1.2.3.4", dns2="2.3.4.5")
        ip1 = dict(address="10.1.1.2", version=4, ip_block=block1)
        block2 = dict(netmask="255.255.255.0", cidr="77.1.1.0/24",
                      gateway="77.1.1.1", broadcast="77.1.1.255",
                      dns1="1.2.3.4", dns2=None)
        ip2 = dict(address="77.1.1.2", version=4, ip_block=block2)

        melange_client.get_allocated_ips(network1['id'], vif1['id'],
                                         project_id="project1").\
                                         AndReturn([ip1])

        melange_client.get_allocated_ips(network2['id'], vif2['id'],
                                         project_id="project1").\
                                         AndReturn([ip2])

        self.mox.ReplayAll()

        net_info = quantum_mgr.get_instance_nw_info(admin_context,
                                                    instance['id'], 1, None)
        assert_network_info_has_ip(self, net_info[0], ip1, network1)
        assert_network_info_has_ip(self, net_info[1], ip2, network2)


class TestDeallocateForInstance(test.TestCase):

    def test_deallocates_ips_from_melange(self):
        quantum_mgr = QuantumManager()
        admin_context = context.get_admin_context()
        project_id = "project1"

        instance_id = db_api.instance_create(admin_context, dict())['id']
        network1 = db_api.network_create_safe(admin_context,
                                             dict(instance_id=instance_id,
                                                  priority=1,
                                                  project_id=project_id))
        network2 = db_api.network_create_safe(admin_context,
                                             dict(instance_id=instance_id,
                                                  priority=2,
                                                  project_id=project_id))

        vif1 = db_api.virtual_interface_create(admin_context,
                                              dict(instance_id=instance_id,
                                              network_id=network1['id'],
                                              project_id=project_id))
        vif2 = db_api.virtual_interface_create(admin_context,
                                              dict(instance_id=instance_id,
                                              network_id=network2['id'],
                                              project_id=project_id))
        self._setup_quantum_mocks()

        self.mox.StubOutWithMock(melange_client, "deallocate_ips")
        melange_client.deallocate_ips(network1['id'], vif1['id'],
                                      project_id=project_id)
        melange_client.deallocate_ips(network2['id'], vif2['id'],
                                      project_id=project_id)

        self.mox.ReplayAll()

        quantum_mgr.deallocate_for_instance(admin_context,
                                            instance_id=instance_id,
                                            project_id=project_id)

        vifs_left = db_api.virtual_interface_get_by_instance(admin_context,
                                                             instance_id)
        self.assertEqual(len(vifs_left), 0)

    def _setup_quantum_mocks(self):
        self.mox.StubOutWithMock(quantum, "get_port_by_attachment")
        self.mox.StubOutWithMock(quantum, "unplug_iface")
        self.mox.StubOutWithMock(quantum, "delete_port")

        quantum.get_port_by_attachment(IgnoreArg(), IgnoreArg(), IgnoreArg()).\
                                           MultipleTimes().AndReturn("port_id")
        quantum.unplug_iface(IgnoreArg(), IgnoreArg(), IgnoreArg()).\
                                          MultipleTimes()
        quantum.delete_port(IgnoreArg(), IgnoreArg(), IgnoreArg()).\
                                         MultipleTimes()


def assert_network_info_has_ip(test, actual_network_info,
                               expected_ip, expected_network):
    (network_info, vif_config_net_params) = actual_network_info
    expected_ip_block = expected_ip['ip_block']
    expected_dns = []
    if expected_ip_block['dns1']:
        expected_dns.append(expected_ip_block['dns1'])
    if expected_ip_block['dns2']:
        expected_dns.append(expected_ip_block['dns2'])

    test.assertEqual(vif_config_net_params['label'],
                     expected_network['label'])
    test.assertEqual(vif_config_net_params['gateway'],
                     expected_ip_block['gateway'])
    test.assertEqual(vif_config_net_params['broadcast'],
                     expected_ip_block['broadcast'])
    test.assertEqual(vif_config_net_params['dns'], expected_dns)
    test.assertEqual(vif_config_net_params['ips'],
                     [{'ip': expected_ip['address'],
                       'netmask': expected_ip_block['netmask'],
                       'enabled': '1'}])
