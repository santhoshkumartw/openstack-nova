# vim: tabstop=4 shiftwidth=4 softtabstop=4

# Copyright 2011 Nicira Networks, Inc
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

import math
import netaddr

from nova import context
from nova import db
from nova import exception
from nova import flags
from nova import ipv6
from nova import log as logging
from nova import manager
from nova import quota
from nova import utils
from nova import rpc
from nova.network import api as network_api
from nova.network import quantum
from nova.network import melange_client as melange
from nova.network import manager
import random

# FIXME: clean-up imports

LOG = logging.getLogger("quantum_manager")

FLAGS = flags.FLAGS


class QuantumManager(manager.FlatManager):
    def create_networks(self, context, label, cidr, multi_host, num_networks,
                        network_size, cidr_v6, gateway_v6, bridge,
                        bridge_interface, dns1=None, dns2=None, **kwargs):
        """Create networks based on parameters."""

        # FIXME: enforce that this is called only for a single network

        # FIXME: decomp out most of this function, likely by calling
        # FlatManager.create_networks, then once that is complete,
        # calling Quantum and patching up the "bridge" field in the newly
        # created network row.

        if "priority" not in kwargs:
            raise Exception("QuantumManager requires each network to"
                                            " have a priority")

        fixed_net = netaddr.IPNetwork(cidr)
        if FLAGS.use_ipv6:
            fixed_net_v6 = netaddr.IPNetwork(cidr_v6)
            significant_bits_v6 = 64
            network_size_v6 = 1 << 64

        for index in range(num_networks):
            start = index * network_size
            significant_bits = 32 - int(math.log(network_size, 2))
            cidr = '%s/%s' % (fixed_net[start], significant_bits)
            project_net = netaddr.IPNetwork(cidr)
            net = {}
            net['bridge'] = bridge
            net['bridge_interface'] = bridge_interface
            net['dns1'] = dns1
            net['dns2'] = dns2
            net['multi_host'] = multi_host
            net['dhcp_start'] = str(project_net[2])
            net['priority'] = int(kwargs["priority"])
            if kwargs["project_id"] not in [None, "0"]:
                net['project_id'] = kwargs["project_id"]
            if num_networks > 1:
                net['label'] = '%s_%d' % (label, index)
            else:
                net['label'] = label

            if FLAGS.use_ipv6:
                start_v6 = index * network_size_v6
                cidr_v6 = '%s/%s' % (fixed_net_v6[start_v6],
                                     significant_bits_v6)

            if kwargs.get('vpn', False):
                # this bit here is for vlan-manager
                del net['dns1']
                del net['dns2']
                vlan = kwargs['vlan_start'] + index
                net['vpn_private_address'] = str(project_net[2])
                net['dhcp_start'] = str(project_net[3])
                net['vlan'] = vlan
                net['bridge'] = 'br%s' % vlan

                # NOTE(vish): This makes ports unique accross the cloud, a more
                #             robust solution would be to make them uniq per ip
                net['vpn_public_port'] = kwargs['vpn_start'] + index

            # Populate the quantum network uuid if we have it.  We're
            # currently using the bridge column for this since we don't have
            # another place to put it.
            existing_id = kwargs.get("existing_net_id", None)
            if existing_id:
                try:
                    network_exists = quantum.get_network(
                      FLAGS.quantum_default_tenant_id, existing_id)
                except:
                    txt = "Unable to find quantum network with uuid: %s" % \
                      (existing_id)
                    raise Exception(txt)
                net["bridge"] = existing_id
            else:
                # If the uuid wasn't provided and the project is specified
                # then we should try to create this network via quantum.

                tenant_id = kwargs["project_id"] or \
                            FLAGS.quantum_default_tenant_id
                quantum_net_id = quantum.create_network(tenant_id, label)
                net["bridge"] = quantum_net_id
                LOG.info(_("Quantum network uuid for network"
                           " \"%(label)s\": %(quantum_net_id)s") % locals())

            network = self.db.network_create_safe(context, net)
            project_id = kwargs.get("project_id", None)
            if project_id == '0':
                project_id = None

            if cidr:
                melange.create_block(network['id'], cidr, project_id)
            if cidr_v6:
                melange.create_block(network['id'], cidr_v6, project_id)

    def _allocate_fixed_ips(self, context, instance_id, host, networks,
                            **kwargs):
        vifs = self.db.virtual_interface_get_by_instance(context, instance_id)

        def allocate_ip(vif):
            network_for_vif = lambda net: net['id'] == vif['network_id']
            project_id = filter(network_for_vif, networks)[0].project_id

            return melange.allocate_ip(vif['network_id'],
                                       vif['id'], project_id=project_id,
                                       mac_address=vif['address'])

        return dict((vif['id'], allocate_ip(vif))  for vif in vifs)

    def _get_networks_for_instance(self, context, instance_id, project_id):
        """Determine & return which networks an instance should connect to."""

        # get all networks with this project_id, as well as all networks
        # where the project-id is not set (these are shared networks)
        networks = self.db.project_get_networks(context, project_id, False)

        networks.extend(self.db.project_get_networks(context, None, False))

        networks = filter(lambda x: x["priority"] != None
                           and x["priority"] != 0, networks)
        return sorted(networks, key=lambda x: x["priority"])

    def allocate_for_instance(self, context, **kwargs):
        """Handles allocating the various network resources for an instance.

        rpc.called by network_api
        """
        instance_id = kwargs.pop('instance_id')
        host = kwargs.pop('host')
        project_id = kwargs.pop('project_id')
        type_id = kwargs.pop('instance_type_id')
        vpn = kwargs.pop('vpn')
        admin_context = context.elevated()
        LOG.debug(_("network allocations for instance %s"), instance_id,
                                                            context=context)
        networks = self._get_networks_for_instance(admin_context, instance_id,
                                                                  project_id)
        # Create a port via quantum and attach the vif
        tenant_id = project_id
        for n in networks:
            vif_id = "nova-" + str(instance_id) + "-" + str(n['id'])
            quantum_net_id = n['bridge']
            LOG.debug("Using quantum_net_id: %s" % quantum_net_id)
            port_id = quantum.create_port(tenant_id, quantum_net_id)
            quantum.plug_iface(tenant_id, quantum_net_id, port_id, vif_id)

            # TODO: also communicate "interface-binding" and "tenant-id"
            # to Quantum

        LOG.warn(networks)
        self._allocate_mac_addresses(context, instance_id, networks)
        ips = self._allocate_fixed_ips(admin_context, instance_id, host,
                                       networks, vpn=vpn)
        vifs = self.db.virtual_interface_get_by_instance(context, instance_id)
        return self._construct_instance_nw_info(context, instance_id, type_id,
                                         host, ips, vifs)

    def get_instance_nw_info(self, context, instance_id, instance_type_id,
                             host, **kwargs):
        """Creates network info list for instance.

        called by allocate_for_instance and netowrk_api
        context needs to be elevated
        :returns: network info list [(network,info),(network,info)...]
        where network = dict containing pertinent data from a network db object
        and info = dict containing pertinent networking data
        """
        vifs = self.db.virtual_interface_get_by_instance(context, instance_id)
        ips = dict((vif['id'], self.get_ips(vif))  for vif in vifs)
        return self._construct_instance_nw_info(context, instance_id,
                                                instance_type_id,
                                                host, ips, vifs)

    def _construct_instance_nw_info(self, context, instance_id,
                                    instance_type_id, host, ips, vifs):
        # TODO(tr3buchet) should handle floating IPs as well?
        #fixed_ips = self.db.fixed_ip_get_by_instance(context, instance_id)
        flavor = self.db.instance_type_get(context, instance_type_id)
        network_info = []
        # a vif has an address, instance_id, and network_id
        # it is also joined to the instance and network given by those IDs
        for vif in vifs:
            ips_for_vif = ips[vif["id"]]
            LOG.debug(ips_for_vif)
            v4_ips = [ip for ip in ips_for_vif
                      if ip['version'] == 4]
            v6_ips = [ip for ip in ips_for_vif
                      if ip['version'] == 6]
            v4ip_block = v4_ips[0]['ip_block']
            network = vif['network']

            # TODO(tr3buchet) eventually "enabled" should be determined
            def ip_dict(ip, netmask=None):
                return {
                    "ip": ip['address'],
                    "netmask": (netmask if netmask
                                else ip['ip_block']['netmask']),
                    "enabled": "1"}

            def ip6_dict(ip):
                block = netaddr.IPNetwork(ip['ip_block']['cidr'])
                return ip_dict(ip, netmask=block._prefixlen)

            network_dict = {
                'bridge': network['bridge'],
                'id': network['id'],
                'cidr': v4ip_block['cidr'],
                'injected': True,
                'vlan': network['vlan'],
                'bridge_interface': network['bridge_interface'],
                'multi_host': network['multi_host']}
            info = {
                'label': network['label'],
                'gateway': v4ip_block['gateway'],
                'broadcast': v4ip_block['broadcast'],
                'mac': vif['address'],
                'rxtx_cap': flavor['rxtx_cap'],
                'dns': [],
                'ips': [ip_dict(ip) for ip in v4_ips],
                'should_create_bridge': self.SHOULD_CREATE_BRIDGE,
                'should_create_vlan': self.SHOULD_CREATE_VLAN}

            if v6_ips:
                v6ip_block = v6_ips[0]['ip_block']
                info['ip6s'] = [ip6_dict(ip) for ip in v6_ips]
                info['gateway6'] = v6ip_block['gateway']
                network_dict['cidr_v6'] = v6ip_block['cidr']

            if network['dns1']:
                info['dns'].append(network['dns1'])
            if network['dns2']:
                info['dns'].append(network['dns2'])

            network_info.append((network_dict, info))
        return network_info

    def deallocate_for_instance(self, context, **kwargs):
        instance_id = kwargs.get('instance_id')
        project_id = kwargs.pop('project_id', None)
        admin_context = context.elevated()
        networks = self._get_networks_for_instance(admin_context, instance_id,
                                                                  project_id)
        vifs = self.db.virtual_interface_get_by_instance(context, instance_id)
        for n in networks:
            vif_id = "nova-" + str(instance_id) + "-" + str(n['id'])
            # Un-attach the vif and delete the port
            tenant_id = project_id or FLAGS.quantum_default_tenant_id
            quantum_net_id = n['bridge']
            LOG.debug("Using quantum_net_id: %s" % quantum_net_id)
            attachment = vif_id
            port_id = quantum.get_port_by_attachment(tenant_id,
                                            quantum_net_id, attachment)

            # FIXME: tell Quantum that this interface-binding is no
            # longer valid.

            if not port_id:
                LOG.error("Unable to find port with attachment: %s" % \
                                                        (attachment))
            else:
                quantum.unplug_iface(tenant_id, quantum_net_id, port_id)
                quantum.delete_port(tenant_id, quantum_net_id, port_id)

            vif = filter(lambda vif: vif['network_id'] == n['id'], vifs)[0]
            melange.deallocate_ips(n['id'], vif['id'],
                                   project_id=n['project_id'])

        self.db.virtual_interface_delete_by_instance(context, instance_id)

    def get_ips(self, interface):
        project_id = interface['network']['project_id']
        return melange.get_allocated_ips(interface['network_id'],
                                        interface['id'],
                                        project_id=project_id)
