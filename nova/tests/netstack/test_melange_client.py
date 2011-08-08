# vim: tabstop=4 shiftwidth=4 softtabstop=4

# Copyright 2011 Rackspace
# All Rights Reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License"); you may
# not use this file except in compliance with the License. You may obtain
# a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
# WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
# License for the specific language governing permissions and limitations
# under the License.

from nova import flags
from nova import log as logging
from nova import test
from nova.network import melange_client

import json


FLAGS = flags.FLAGS
LOG = logging.getLogger('nova.tests.network')


class TestCreateBlock(test.TestCase):

    def test_create_block_for_a_given_project_id(self):
        network_id = "netwok123"
        cidr = "10.0.0.0/24"
        project_id = "project1"
        mock_client = setup_mock_client(self.mox)
        req_body = dict(ip_block=dict(cidr=cidr,
                                      network_id=network_id,
                                      type='private'))
        mock_client.post("/v0.1/ipam/tenants/project1/ip_blocks",
                         body=json.dumps(req_body),
                         headers=json_content_type()).AndReturn(None)

        self.mox.ReplayAll()

        melange_client.create_block(network_id, cidr, project_id=project_id)

    def test_create_block_wihtout_project_id(self):
        network_id = "network123"
        cidr = "10.0.0.0/24"
        mock_client = setup_mock_client(self.mox)
        req_body = dict(ip_block=dict(cidr=cidr,
                                      network_id=network_id,
                                      type='private'))
        mock_client.post("/v0.1/ipam/ip_blocks",
                         body=json.dumps(req_body),
                         headers=json_content_type()).AndReturn(None)

        self.mox.ReplayAll()

        melange_client.create_block(network_id, cidr, project_id=None)


class TestAllocateIp(test.TestCase):

    def test_allocate_ip_for_a_given_project_id(self):
        network_id = "network1"
        vif_id = "vif1"
        project_id = "project2"
        mac_address = "11:22:33:44:55:66"
        request_body = json.dumps(dict(network=dict(mac_address=mac_address)))
        mock_client = setup_mock_client(self.mox)
        stub_response = ResponseStub({'ip_addresses': [{'id': "123"}]})
        mock_client.post("/v0.1/ipam/tenants/project2/networks/network1/"
                         "ports/vif1/ip_allocations", body=request_body,
                         headers=json_content_type()).AndReturn(stub_response)

        self.mox.ReplayAll()

        ip_addresses = melange_client.allocate_ip(network_id, vif_id,
                        project_id=project_id, mac_address=mac_address)
        self.assertEqual(ip_addresses, [{'id': "123"}])

    def test_allocate_ip_without_a_project_id(self):
        network_id = "network333"
        vif_id = "vif1"
        mock_client = setup_mock_client(self.mox)
        stub_response = ResponseStub({'ip_addresses': [{'id': "123"}]})
        mock_client.post("/v0.1/ipam/networks/network333/"
                         "ports/vif1/ip_allocations", body=None,
                         headers=json_content_type()).AndReturn(stub_response)

        self.mox.ReplayAll()

        ip_addresses = melange_client.allocate_ip(network_id, vif_id,
                                                  project_id=None)
        self.assertEqual(ip_addresses, [{'id': "123"}])


class TestGetAllocatedIps(test.TestCase):

    def test_gets_all_allocated_ips_with_project_id(self):
        network_id = "network123"
        vif_id = "vif1"
        mock_client = setup_mock_client(self.mox)
        stub_response = ResponseStub({'ip_addresses': [{'id': "123"}]})
        mock_client.get("/v0.1/ipam/tenants/tenant321/networks/network123/"
                         "ports/vif1/ip_allocations",
                         headers=json_content_type()).AndReturn(stub_response)

        self.mox.ReplayAll()

        ip_addresses = melange_client.get_allocated_ips(network_id, vif_id,
                                                        project_id="tenant321")
        self.assertEqual(ip_addresses, [{'id': "123"}])

    def test_gets_all_allocated_ips_without_project_id(self):
        network_id = "network123"
        vif_id = "vif1"
        mock_client = setup_mock_client(self.mox)
        stub_response = ResponseStub({'ip_addresses': [{'id': "123"}]})
        mock_client.get("/v0.1/ipam/networks/network123/"
                         "ports/vif1/ip_allocations",
                         headers=json_content_type()).AndReturn(stub_response)

        self.mox.ReplayAll()

        ip_addresses = melange_client.get_allocated_ips(network_id, vif_id,
                                                        project_id=None)
        self.assertEqual(ip_addresses, [{'id': "123"}])


def setup_mock_client(mox):
    mock_client = mox.CreateMockAnything()
    mox.StubOutWithMock(melange_client, 'Client')
    melange_client.Client(FLAGS.melange_host,
                          FLAGS.melange_port).AndReturn(mock_client)
    return mock_client


def json_content_type():
    return {'Content-type': "application/json"}


class ResponseStub():

    def __init__(self, response_data):
        self.response_data = response_data

    def read(self):
        return json.dumps(self.response_data)
